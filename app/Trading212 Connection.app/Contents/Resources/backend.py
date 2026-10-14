#!/usr/bin/env python3
"""Trading212 Connection: native manager backend and permission-enforced MCP."""
import argparse
import base64
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.dont_write_bytecode = True
from client_links import ClientLinks, ClientError, CLIENT_LABELS
TITLE = 'Trading212 Connection'
VERSION = '1.2.2'
STATE_DIR = Path.home() / 'Library/Application Support' / TITLE
KEYCHAIN_SERVICE = 'codex.trading212.connection'
READ_PATHS = {
    'get_account_summary': ('/api/v0/equity/account/summary', 5),
    'get_positions': ('/api/v0/equity/positions', 1),
    'get_pending_orders': ('/api/v0/equity/orders', 5),
    'get_order_history': ('/api/v0/equity/history/orders', 3),
    'get_dividends': ('/api/v0/equity/history/dividends', 3),
    'get_transactions': ('/api/v0/equity/history/transactions', 3),
}
ORDER_FIELDS = {'market': {'ticker', 'quantity', 'extendedHours'},
                'limit': {'ticker', 'quantity', 'limitPrice', 'timeValidity'},
                'stop': {'ticker', 'quantity', 'stopPrice', 'timeValidity'},
                'stop_limit': {'ticker', 'quantity', 'limitPrice', 'stopPrice', 'timeValidity'}}


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


class SafeError(Exception):
    def __init__(self, message, status=0, uncertain=False):
        super().__init__(message)
        self.status = status
        self.uncertain = uncertain


class Keychain:
    def __init__(self, account, service=KEYCHAIN_SERVICE):
        self.account, self.service = account.encode(), service.encode()
        self.lib = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        u32, ptr, text = ctypes.c_uint32, ctypes.c_void_p, ctypes.c_char_p
        self.lib.SecKeychainFindGenericPassword.argtypes = [ptr, u32, text, u32, text,
            ctypes.POINTER(u32), ctypes.POINTER(ptr), ctypes.POINTER(ptr)]
        self.lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
        self.lib.SecKeychainItemFreeContent.argtypes = [ptr, ptr]
        self.lib.SecKeychainAddGenericPassword.argtypes = [ptr, u32, text, u32, text, u32, ptr, ctypes.POINTER(ptr)]
        self.lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
        self.lib.SecKeychainItemDelete.argtypes = [ptr]
        self.lib.SecKeychainItemDelete.restype = ctypes.c_int32
        self.core = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.core.CFRelease.argtypes = [ptr]

    def find(self, length=None, data=None, item=None):
        return self.lib.SecKeychainFindGenericPassword(None, len(self.service), self.service,
            len(self.account), self.account, length, data, item)

    def read(self):
        length, data = ctypes.c_uint32(), ctypes.c_void_p()
        code = self.find(ctypes.byref(length), ctypes.byref(data))
        if code:
            raise SafeError('钥匙串凭证不可读取。请检查钥匙串访问授权或重新添加连接（状态码 %s）。' % code)
        try:
            value = json.loads(ctypes.string_at(data, length.value).decode())
            if not isinstance(value, dict) or not all(isinstance(value.get(k), str) and value[k] for k in ('key', 'secret')):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise SafeError('钥匙串凭证格式无效，请重新添加连接。')
        finally:
            self.lib.SecKeychainItemFreeContent(None, data)

    def save(self, key, secret):
        payload = json.dumps({'key': key, 'secret': secret}).encode()
        code = self.lib.SecKeychainAddGenericPassword(None, len(self.service), self.service,
                    len(self.account), self.account, len(payload), payload, None)
        if code:
            raise SafeError('保存钥匙串失败（状态码 %s），连接未创建。' % code)

    def delete(self):
        item = ctypes.c_void_p()
        code = self.find(item=ctypes.byref(item))
        if code == -25300:
            return
        if code:
            raise SafeError('无法移除钥匙串条目（状态码 %s）。连接已停用，请再次删除。' % code)
        try:
            code = self.lib.SecKeychainItemDelete(item)
            if code:
                raise SafeError('无法移除钥匙串条目（状态码 %s）。连接已停用，请再次删除。' % code)
        finally:
            self.core.CFRelease(item)


class Store:
    def __init__(self, directory=STATE_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.file = self.directory / 'connections.sqlite3'
        self.db = sqlite3.connect(str(self.file), timeout=25)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS connections (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, environment TEXT NOT NULL,
          key_account TEXT NOT NULL, key_service TEXT NOT NULL, masked_key TEXT NOT NULL,
          account_hash TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          allow_orders INTEGER NOT NULL DEFAULT 0, allow_cancel INTEGER NOT NULL DEFAULT 0,
          currency TEXT, verified_at TEXT, last_error TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY, connection_id TEXT, connection_name TEXT,
          environment TEXT, account_hash TEXT, time TEXT, method TEXT, endpoint TEXT,
          status INTEGER, ok INTEGER, duration_ms INTEGER, error TEXT);
        CREATE INDEX IF NOT EXISTS events_time ON events(time);
        CREATE TABLE IF NOT EXISTS rate_limits (
          account_hash TEXT, method TEXT, endpoint TEXT, limit_value INTEGER,
          remaining INTEGER, used INTEGER, period INTEGER, reset_at REAL, seen_at TEXT,
          PRIMARY KEY (account_hash,method,endpoint));
        CREATE TABLE IF NOT EXISTS pacing (
          account_hash TEXT, method TEXT, endpoint TEXT, next_at REAL,
          PRIMARY KEY (account_hash,method,endpoint));
        CREATE TABLE IF NOT EXISTS actions (
          id TEXT PRIMARY KEY, connection_id TEXT, client_request_id TEXT,
          connection_name TEXT, environment TEXT, kind TEXT, payload TEXT,
          state TEXT, created_at TEXT, expires_at REAL, updated_at TEXT,
          result TEXT, error TEXT,
          UNIQUE(connection_id,client_request_id));
        ''')
        for table in ('events','actions'):
            if 'client_id' not in {r['name'] for r in self.db.execute('PRAGMA table_info('+table+')')}:
                self.db.execute("ALTER TABLE "+table+" ADD COLUMN client_id TEXT NOT NULL DEFAULT 'legacy'")
        self.db.execute('''CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,connection_id TEXT,
            allow_orders INTEGER NOT NULL DEFAULT 0,allow_cancel INTEGER NOT NULL DEFAULT 0,
            configured_at TEXT,last_seen TEXT,last_error TEXT)''')
        for client in ('codex','claude-code','claude-desktop'):
            self.db.execute('INSERT OR IGNORE INTO clients(id) VALUES(?)',(client,))
        if not self.db.execute("SELECT 1 FROM settings WHERE name='hub-migrated'").fetchone():
            active=self.active()
            if active:
                self.db.execute('UPDATE clients SET enabled=1,connection_id=? WHERE id=?',(active,'codex'))
            self.db.execute("UPDATE actions SET state='revoked',error='已升级中转站，请重新创建并确认操作' WHERE state='pending' AND client_id='legacy'")
            self.db.execute("INSERT INTO settings VALUES('hub-migrated','1')")
        self.db.commit()
        os.chmod(self.file, 0o600)

    def now(self):
        return timestamp()

    def client(self,client_id):
        row=self.db.execute('SELECT * FROM clients WHERE id=?',(client_id,)).fetchone()
        if not row:
            raise SafeError('AI客户端不受支持。')
        return dict(row)

    def connection(self, connection_id=None, enabled=False):
        if connection_id is None:
            row = self.db.execute("SELECT value FROM settings WHERE name='active'").fetchone()
            connection_id = row['value'] if row else None
        row = self.db.execute('SELECT * FROM connections WHERE id=?', (connection_id,)).fetchone()
        if not row:
            raise SafeError('尚未选择可用连接，请打开 Trading212 Connection 添加或选择账户。')
        value = dict(row)
        if enabled and not value['enabled']:
            raise SafeError('此连接已暂停，请在 Trading212 Connection 窗口手动恢复。')
        return value

    def active(self):
        row = self.db.execute("SELECT value FROM settings WHERE name='active'").fetchone()
        return row['value'] if row else None

    def public_connection(self, row):
        keys = ('id', 'name', 'environment', 'masked_key', 'enabled', 'allow_orders',
                'allow_cancel', 'currency', 'verified_at', 'last_error', 'created_at')
        result = {key: row[key] for key in keys}
        for key in ('enabled', 'allow_orders', 'allow_cancel'):
            result[key] = bool(result[key])
        result['active'] = row['id'] == self.active()
        return result

    @contextlib.contextmanager
    def account_lock(self, conn):
        lock = self.directory / ('lock-' + conn['account_hash'])
        with open(str(lock), 'a') as handle:
            os.chmod(lock, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def invalidate(self, connection_id, reason):
        self.db.execute("UPDATE actions SET state='revoked',error=?,updated_at=? WHERE connection_id=? AND state='pending'",
                        (reason, timestamp(), connection_id))

    def adopt_observations(self, old_hash, account_hash, draft_id, connection_id, name):
        self.db.execute('UPDATE events SET account_hash=?,connection_id=?,connection_name=? WHERE connection_id=?',
                        (account_hash,connection_id,name,draft_id))
        for row in list(self.db.execute('SELECT * FROM rate_limits WHERE account_hash=?',(old_hash,))):
            self.db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?,?,?,?,?,?)',(account_hash,)+tuple(row)[1:])
        for row in list(self.db.execute('SELECT * FROM pacing WHERE account_hash=?',(old_hash,))):
            self.db.execute('INSERT OR REPLACE INTO pacing VALUES(?,?,?,?)',(account_hash,)+tuple(row)[1:])
        self.db.execute('DELETE FROM rate_limits WHERE account_hash=?',(old_hash,))
        self.db.execute('DELETE FROM pacing WHERE account_hash=?',(old_hash,))

    def overview(self):
        now = time.time()
        self.db.execute("UPDATE actions SET state='expired',error='确认请求已过期',updated_at=? WHERE state='pending' AND expires_at<?",
                        (timestamp(), now))
        self.db.commit()
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
        stats = dict(self.db.execute('''SELECT COUNT(*) requests, COALESCE(SUM(ok),0) successes,
                COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) failures,
                COALESCE(SUM(CASE WHEN status=429 THEN 1 ELSE 0 END),0) rate_limited
                FROM events WHERE time>=?''', (since,)).fetchone())
        stats['success_rate'] = round(100 * stats['successes'] / stats['requests'], 1) if stats['requests'] else None
        stats['pending'] = self.db.execute("SELECT COUNT(*) FROM actions WHERE state='pending'").fetchone()[0]
        connections = [self.public_connection(dict(row)) for row in self.db.execute('SELECT * FROM connections ORDER BY created_at')]
        active = next((r for r in connections if r['active']), None)
        rates = []
        if active:
            account_hash = self.connection(active['id'])['account_hash']
            rates = [dict(r) for r in self.db.execute('SELECT * FROM rate_limits WHERE account_hash=? ORDER BY seen_at DESC', (account_hash,))]
            for r in rates:
                r.pop('account_hash', None)
                r['expired'] = r['reset_at'] is not None and r['reset_at'] <= now
        logs = [dict(r) for r in self.db.execute('SELECT time,connection_name,environment,method,endpoint,status,ok,duration_ms,error,client_id FROM events ORDER BY id DESC LIMIT 100')]
        by_client=[dict(r) for r in self.db.execute('''SELECT client_id,COUNT(*) requests,SUM(ok) successes,
            SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failures FROM events WHERE time>=? GROUP BY client_id''',(since,))]
        for row in by_client:
            row['name']=CLIENT_LABELS.get(row['client_id'],row['client_id'])
        actions = [self.public_action(dict(r)) for r in self.db.execute("SELECT * FROM actions ORDER BY CASE WHEN state='pending' THEN 0 ELSE 1 END, created_at DESC LIMIT 100")]
        return {'title': TITLE, 'version': VERSION, 'activeId': self.active(), 'connections': connections,
                'stats': stats, 'rateLimits': rates, 'logs': logs, 'actions': actions,
                'usageByClient':by_client,'clients':ClientLinks(self).statuses(),
                'usageScope': '本工具最近24小时的请求；额度是212响应头最近一次观测值，不代表所有应用的完整使用记录。'}

    def public_action(self, row):
        return {key: row[key] for key in ('id', 'connection_id', 'connection_name', 'environment', 'kind', 'state', 'created_at', 'expires_at', 'updated_at', 'error','client_id')} | {
            'payload': json.loads(row['payload']), 'result': json.loads(row['result']) if row['result'] else None}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Broker:
    def __init__(self, store, client_id='manager'):
        self.store = store
        self.client_id=client_id
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, conn, method, path, params=None, body=None, credentials=None, interval=0):
        is_read = method == 'GET' and (path in {p[0] for p in READ_PATHS.values()} or re.fullmatch(r'/api/v0/equity/orders/[0-9]+', path))
        is_order = method == 'POST' and path in {'/api/v0/equity/orders/' + key for key in ORDER_FIELDS}
        is_cancel = method == 'DELETE' and re.fullmatch(r'/api/v0/equity/orders/[0-9]+', path)
        if not (is_read or is_order or is_cancel):
            raise SafeError('接口不在工具白名单中。')
        if is_order and not conn.get('allow_orders'):
            raise SafeError('本工具的下单权限处于关闭状态。')
        if is_cancel and not conn.get('allow_cancel'):
            raise SafeError('本工具的撤单权限处于关闭状态。')
        db = self.store.db
        group = re.sub(r'/orders/[0-9]+$', '/orders/{id}', path)
        pacing_key = (conn['account_hash'], method, group)
        now = time.time()
        rate = db.execute('SELECT * FROM rate_limits WHERE account_hash=? AND method=? AND endpoint=?', pacing_key).fetchone()
        if rate and rate['remaining'] == 0 and rate['reset_at'] and rate['reset_at'] > now:
            raise SafeError('212额度尚未重置，请约 %d 秒后重试。' % (rate['reset_at'] - now + 1))
        pacing = db.execute('SELECT next_at FROM pacing WHERE account_hash=? AND method=? AND endpoint=?', pacing_key).fetchone()
        if pacing and pacing['next_at'] > now:
            raise SafeError('请求间隔保护，请约 %d 秒后重试。' % (pacing['next_at'] - now + 1))
        creds = credentials or Keychain(conn['key_account'], conn['key_service']).read()
        if conn['environment'] not in ('live', 'demo'):
            raise SafeError('连接环境无效。')
        origin = 'https://%s.trading212.com' % conn['environment']
        url = origin + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        token = base64.b64encode((creds['key'] + ':' + creds['secret']).encode()).decode('ascii')
        headers = {'Authorization': 'Basic ' + token, 'Accept': 'application/json', 'User-Agent': 'Trading212-Connection/1.0'}
        payload = json.dumps(body, allow_nan=False).encode() if body is not None else None
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        db.execute('INSERT OR REPLACE INTO pacing VALUES(?,?,?,?)', pacing_key + (now + interval,))
        db.commit()
        status, ok, response_headers, error, data = 0, False, {}, None, None
        start = time.monotonic()
        failure = None
        try:
            with self.opener.open(request, timeout=20) as response:
                status = response.status
                response_headers = dict(response.headers)
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise SafeError('API响应超出大小限制。', status, method != 'GET')
            data = json.loads(raw) if raw.strip() else None
            ok = True
        except urllib.error.HTTPError as exc:
            status, response_headers = exc.code, dict(exc.headers)
            uncertain = method != 'GET' and (status >= 500 or status == 408)
            messages = {400: '请求参数未被212接受。', 401: '请检查API Key、Secret及Live/Demo环境。',
                        403: '请检查212密钥权限、IP白名单及账户类型。', 404: '订单或接口不存在。',
                        429: '账户请求限流，请等待额度重置。'}
            error = 'Trading 212 HTTP %s：%s' % (status, messages.get(status, '接口请求失败。'))
            failure = SafeError(error, status, uncertain)
            exc.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            error = '无法连接212，请检查网络和IP白名单。'
            failure = SafeError(error, 0, method != 'GET')
        except (ValueError, UnicodeError):
            error = '212响应无法解析；请在212核对操作结果。'
            failure = SafeError(error, status, method != 'GET')
        except SafeError as exc:
            error, failure = str(exc), exc
        finally:
            duration = int((time.monotonic() - start) * 1000)
            h = {k.lower(): v for k, v in response_headers.items()}
            def integer(key):
                try:
                    return int(h[key])
                except (ValueError, TypeError, KeyError):
                    return None
            limit, remaining, used, period, reset = [integer('x-ratelimit-' + key) for key in ('limit', 'remaining', 'used', 'period', 'reset')]
            if status == 429 and not reset:
                retry = integer('retry-after')
                reset = time.time() + (min(max(retry, 1), 3600) if retry is not None else 60)
                remaining = 0
            if any(x is not None for x in (limit, remaining, used, period, reset)):
                db.execute('INSERT OR REPLACE INTO rate_limits VALUES(?,?,?,?,?,?,?,?,?)',
                    pacing_key + (limit, remaining, used, period, reset, timestamp()))
            db.execute('INSERT INTO events(connection_id,connection_name,environment,account_hash,time,method,endpoint,status,ok,duration_ms,error,client_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (conn['id'], conn['name'], conn['environment'], conn['account_hash'], timestamp(), method, group, status, int(ok), duration, error,self.client_id))
            db.commit()
        if failure:
            raise failure
        return {'environment': conn['environment'], 'source': 'Trading 212 API', 'connection': conn['name'],
                'connectionId': conn['id'], 'fetchedAt': timestamp(), 'data': data}


def string(value, label, max_length=100):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise SafeError(label + '无效。')
    return value.strip()


def connection_name(value):
    name = string(value, '连接名称', 60)
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in name):
        raise SafeError('连接名称无效。')
    return name


def validate_credentials(key, secret):
    for value in (key, secret):
        if not isinstance(value, str) or not value or len(value) > 4096 or ':' in value or any(c.isspace() for c in value):
            raise SafeError('请分别粘贴有效的API Key和API Secret。')


class Manager:
    def __init__(self, store=None, client_id='manager'):
        self.store = store or Store()
        self.client_id=client_id
        self.broker = Broker(self.store,client_id)

    def resolve_connection(self,connection_id=None):
        if self.client_id=='manager':
            return self.store.connection(connection_id,enabled=True)
        profile=self.store.client(self.client_id)
        if not profile['enabled']:
            raise SafeError('此AI客户端已断开，请由用户在Trading212 Connection的AI客户端页连接。')
        assigned=profile['connection_id']
        if not assigned:
            raise SafeError('此AI客户端尚未选择212账户。')
        if connection_id is not None and connection_id!=assigned:
            raise SafeError('此AI客户端只能访问用户为它选择的212账户。')
        return self.store.connection(assigned,enabled=True)

    def check_client_permission(self,client_id,connection_id,permission):
        if client_id=='manager':
            return
        profile=self.store.client(client_id)
        if not profile['enabled'] or profile['connection_id']!=connection_id or not profile[permission]:
            raise SafeError('该AI客户端的相应交易权限未开启，或账户绑定已改变。请在固定窗口手动设置。')

    def usage(self):
        conn=self.resolve_connection()
        since=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=24)).isoformat()
        stats=dict(self.store.db.execute('''SELECT COUNT(*) requests,COALESCE(SUM(ok),0) successes,
            COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) failures,
            COALESCE(SUM(CASE WHEN status=429 THEN 1 ELSE 0 END),0) rate_limited
            FROM events WHERE client_id=? AND time>=?''',(self.client_id,since)).fetchone())
        stats['success_rate']=round(100*stats['successes']/stats['requests'],1) if stats['requests'] else None
        rates=[dict(r) for r in self.store.db.execute('SELECT method,endpoint,limit_value,remaining,used,period,reset_at,seen_at FROM rate_limits WHERE account_hash=?',(conn['account_hash'],))]
        for row in rates:row['expired']=row['reset_at'] is not None and row['reset_at']<=time.time()
        return {'client':self.client_id,'stats':stats,'rateLimits':rates,'usageScope':'此AI客户端经本工具发出的最近24小时请求；212账户额度由各客户端共享。'}

    def new_connection(self, name, environment, key, secret, legacy=False):
        name = connection_name(name)
        if environment not in ('live', 'demo'):
            raise SafeError('请指定实盘或模拟盘。')
        validate_credentials(key, secret)
        conn_id = str(uuid.uuid4())
        conn = {'id': conn_id, 'name': name, 'environment': environment, 'key_account': conn_id,
                'key_service': KEYCHAIN_SERVICE, 'masked_key': '•••• ' + key[-4:],
                'account_hash': hashlib.sha256((environment + ':' + key).encode()).hexdigest(),
                'enabled': 1, 'allow_orders': 0, 'allow_cancel': 0}
        credentials = {'key': key, 'secret': secret}
        summary = self.broker.request(conn, 'GET', READ_PATHS['get_account_summary'][0], credentials=credentials, interval=5)['data']
        self.broker.request(conn, 'GET', READ_PATHS['get_positions'][0], credentials=credentials, interval=1)
        if not isinstance(summary, dict) or 'id' not in summary:
            raise SafeError('212没有返回有效的账户身份，连接未保存。')
        account_hash = hashlib.sha256((environment + ':' + str(summary['id'])).encode()).hexdigest()
        db = self.store.db
        existing = db.execute('SELECT * FROM connections WHERE account_hash=?', (account_hash,)).fetchone()
        if existing:
            existing = dict(existing)
            saved = Keychain(existing['key_account'], existing['key_service']).read()
            if saved == credentials:
                self.store.adopt_observations(conn['account_hash'],account_hash,conn_id,existing['id'],existing['name'])
                db.execute('UPDATE connections SET verified_at=?,last_error=NULL WHERE id=?',(timestamp(),existing['id']))
                db.commit()
                return self.store.public_connection(self.store.connection(existing['id'])) | {'message':'这个账户已连接，读取验证成功。现有连接和权限未更改。'}
            raise SafeError('这个212账户已连接。若要更换API凭证，请先删除原连接，再添加新凭证。')
        if legacy:
            conn['key_account'], conn['key_service'] = environment, 'codex.trading212.readonly'
        else:
            Keychain(conn_id).save(key, secret)
        old_hash = conn['account_hash']
        conn['account_hash'] = account_hash
        conn.update(currency=summary.get('currency'), verified_at=timestamp(), last_error=None, created_at=timestamp())
        try:
            db.execute('INSERT INTO connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', tuple(conn[k] for k in
                ('id','name','environment','key_account','key_service','masked_key','account_hash','enabled','allow_orders','allow_cancel','currency','verified_at','last_error','created_at')))
            self.store.adopt_observations(old_hash,account_hash,conn_id,conn_id,name)
            if not self.store.active():
                db.execute("INSERT OR REPLACE INTO settings VALUES('active',?)", (conn_id,))
            db.commit()
        except Exception:
            db.rollback()
            if not legacy:
                Keychain(conn_id).delete()
            raise
        return self.store.public_connection(conn)

    def read(self, name, arguments):
        conn_id = arguments.pop('connectionId', None)
        conn = self.resolve_connection(conn_id)
        path, interval = READ_PATHS[name]
        allowed = {'ticker'} if name == 'get_positions' else set()
        if name in ('get_order_history', 'get_dividends'):
            allowed = {'cursor', 'ticker', 'limit'}
        if name == 'get_transactions':
            allowed = {'cursor', 'time', 'limit'}
        if set(arguments) - allowed:
            raise SafeError('查询包含不支持的参数。')
        for key, value in arguments.items():
            if key == 'limit' and (type(value) is not int or not 1 <= value <= 50):
                raise SafeError('limit必须在1至50之间。')
            if key == 'ticker' and (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value)):
                raise SafeError('请使用有效的Trading 212 ticker，如AAPL_US_EQ。')
            if key == 'cursor':
                if name == 'get_transactions':
                    string(value, 'cursor', 500)
                elif type(value) is not int or value < 0 or value > 2**63 - 1:
                    raise SafeError('cursor必须是非负整数。')
            if key == 'time':
                try:
                    date = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if date.tzinfo is None:
                        raise ValueError()
                except (ValueError, AttributeError):
                    raise SafeError('time必须是带时区的ISO 8601时间。')
        if 'limit' in allowed:
            arguments.setdefault('limit', 20)
        with self.store.account_lock(conn):
            conn = self.resolve_connection(conn['id'])
            result = self.broker.request(conn, 'GET', path, params=arguments, interval=interval)
        data = result['data']
        if isinstance(data, dict) and isinstance(data.get('nextPagePath'), str):
            parsed = urllib.parse.urlsplit(data['nextPagePath'])
            if not parsed.scheme and not parsed.netloc and parsed.path == path:
                cursor = urllib.parse.parse_qs(parsed.query).get('cursor', [None])[0]
                if cursor:
                    try:
                        result['nextCursor'] = cursor if name == 'get_transactions' else int(cursor)
                    except ValueError:
                        pass
        return result

    def verify(self, connection_id):
        conn = self.store.connection(connection_id, enabled=True)
        try:
            self.read('get_account_summary', {'connectionId': conn['id']})
            self.read('get_positions', {'connectionId': conn['id']})
        except SafeError as exc:
            self.store.db.execute('UPDATE connections SET last_error=? WHERE id=?', (str(exc), conn['id']))
            self.store.db.commit()
            raise
        self.store.db.execute('UPDATE connections SET verified_at=?,last_error=NULL WHERE id=?', (timestamp(), conn['id']))
        self.store.db.commit()
        return {'ok': True, 'message': '账户与持仓读取验证成功。'}

    def permissions(self, connection_id, allow_orders, allow_cancel):
        if type(allow_orders) is not bool or type(allow_cancel) is not bool:
            raise SafeError('权限必须为布尔值。')
        conn = self.store.connection(connection_id)
        with self.store.account_lock(conn):
            conn = self.store.connection(connection_id)
            if not conn['enabled'] and (allow_orders or allow_cancel):
                raise SafeError('请先恢复连接，再开启权限。')
            self.store.db.execute('UPDATE connections SET allow_orders=?,allow_cancel=? WHERE id=?', (int(allow_orders),int(allow_cancel),conn['id']))
            self.store.invalidate(conn['id'], '权限改变，原确认请求已撤销，请重新创建。')
            self.store.db.commit()
        return {'ok': True}

    def set_enabled(self, connection_id, enabled):
        if type(enabled) is not bool:
            raise SafeError('连接状态无效。')
        conn = self.store.connection(connection_id)
        with self.store.account_lock(conn):
            self.store.db.execute('UPDATE connections SET enabled=?,allow_orders=0,allow_cancel=0 WHERE id=?', (int(enabled),conn['id']))
            self.store.db.execute('UPDATE clients SET allow_orders=0,allow_cancel=0 WHERE connection_id=?',(conn['id'],))
            self.store.invalidate(conn['id'], '连接已暂停或恢复，原请求已撤销。')
            self.store.db.commit()
        return {'ok': True}

    def delete(self, connection_id):
        conn = self.store.connection(connection_id)
        with self.store.account_lock(conn):
            self.store.db.execute('UPDATE connections SET enabled=0,allow_orders=0,allow_cancel=0 WHERE id=?', (conn['id'],))
            self.store.invalidate(conn['id'], '连接已删除。')
            self.store.db.commit()
            Keychain(conn['key_account'],conn['key_service']).delete()
            self.store.db.execute('DELETE FROM connections WHERE id=?', (conn['id'],))
            self.store.db.execute('UPDATE clients SET enabled=0,allow_orders=0,allow_cancel=0 WHERE connection_id=?',(conn['id'],))
            if self.store.active() == conn['id']:
                self.store.db.execute("DELETE FROM settings WHERE name='active'")
            self.store.db.commit()
        return {'ok': True}

    def request_action(self, kind, arguments):
        conn = self.resolve_connection(arguments.get('connectionId'))
        permission = 'allow_orders' if kind == 'order' else 'allow_cancel'
        self.check_client_permission(self.client_id,conn['id'],permission)
        if not conn[permission]:
            raise SafeError('请由用户在Trading212 Connection窗口手动开启%s权限。' % ('下单' if kind == 'order' else '撤单'))
        client_id = string(arguments.get('clientRequestId'), 'clientRequestId')
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', client_id):
            raise SafeError('clientRequestId应为8至100位唯一标识，例如UUID。')
        if kind == 'cancel':
            if set(arguments) - {'connectionId','clientRequestId','orderId'}:
                raise SafeError('撤单请求包含额外字段。')
            order_id = arguments.get('orderId')
            if type(order_id) is not int or not 1 <= order_id <= 2**63 - 1:
                raise SafeError('orderId必须是正整数。')
            payload = {'orderId': order_id}
        else:
            allowed = {'connectionId','clientRequestId','orderType','side','quantity','ticker','limitPrice','stopPrice','timeValidity','extendedHours'}
            if set(arguments) - allowed:
                raise SafeError('下单请求包含额外字段。')
            order_type = arguments.get('orderType')
            if order_type not in ORDER_FIELDS or arguments.get('side') not in ('buy','sell'):
                raise SafeError('订单类型或买卖方向无效。')
            ticker = arguments.get('ticker')
            if not isinstance(ticker,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',ticker):
                raise SafeError('Trading 212 ticker格式无效。')
            quantity = arguments.get('quantity')
            if type(quantity) not in (int,float) or not math.isfinite(quantity) or not 0 < quantity <= 1e12:
                raise SafeError('quantity必须是有限的正数；用side指定买卖方向。')
            payload = {'orderType':order_type,'side':arguments['side'],'ticker':ticker,'quantity':quantity}
            expected = ORDER_FIELDS[order_type] - {'ticker','quantity'}
            provided = set(arguments) & {'limitPrice','stopPrice','timeValidity','extendedHours'}
            if provided - expected:
                raise SafeError('此订单类型不支持所提供的价格或时效字段。')
            for price in ('limitPrice','stopPrice'):
                if price in expected:
                    value = arguments.get(price)
                    if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
                        raise SafeError(price+'必须是有限的正数。')
                    payload[price] = value
            if order_type == 'market':
                value = arguments.get('extendedHours',False)
                if type(value) is not bool:
                    raise SafeError('extendedHours必须是布尔值。')
                payload['extendedHours'] = value
            else:
                value = arguments.get('timeValidity','DAY')
                if value not in ('DAY','GOOD_TILL_CANCEL'):
                    raise SafeError('timeValidity无效。')
                payload['timeValidity'] = value
        encoded = json.dumps(payload,sort_keys=True,allow_nan=False)
        db = self.store.db
        with self.store.account_lock(conn):
            conn = self.resolve_connection(conn['id'])
            self.check_client_permission(self.client_id,conn['id'],permission)
            if not conn[permission]:
                raise SafeError('权限已关闭，未创建请求。')
            row = db.execute('SELECT * FROM actions WHERE connection_id=? AND client_request_id=?',(conn['id'],client_id)).fetchone()
            if row:
                if row['client_id']!=self.client_id or row['kind'] != kind or row['payload'] != encoded:
                    raise SafeError('同一clientRequestId不能用于不同操作。')
                return self.action(row['id'])
            if db.execute("SELECT COUNT(*) FROM actions WHERE state='pending' AND expires_at>?",(time.time(),)).fetchone()[0] >= 50:
                raise SafeError('已有50笔待确认操作，请先在窗口处理已有请求。')
            action_id = str(uuid.uuid4())
            db.execute('INSERT INTO actions(id,connection_id,client_request_id,connection_name,environment,kind,payload,state,created_at,expires_at,updated_at,result,error,client_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (action_id,conn['id'],client_id,conn['name'],conn['environment'],kind,encoded,'pending',timestamp(),time.time()+600,timestamp(),None,None,self.client_id))
            db.commit()
            return self.action(action_id)

    def action(self, action_id):
        row = self.store.db.execute('SELECT * FROM actions WHERE id=?',(action_id,)).fetchone()
        if not row:
            raise SafeError('操作请求不存在。')
        if self.client_id!='manager' and row['client_id']!=self.client_id:
            raise SafeError('该操作不属于此AI客户端。')
        if self.client_id!='manager':
            self.resolve_connection(row['connection_id'])
        if row['state']=='pending' and row['expires_at'] < time.time():
            self.store.db.execute("UPDATE actions SET state='expired',error='确认请求已过期',updated_at=? WHERE id=?",(timestamp(),action_id))
            self.store.db.commit()
            row = self.store.db.execute('SELECT * FROM actions WHERE id=?',(action_id,)).fetchone()
        return self.store.public_action(dict(row))

    def decide(self, action_id, approve):
        if type(approve) is not bool:
            raise SafeError('确认状态无效。')
        action = self.action(action_id)
        conn = self.store.connection(action['connection_id'], enabled=True)
        with self.store.account_lock(conn):
            action = self.action(action_id)
            conn = self.store.connection(action['connection_id'], enabled=True)
            if action['state'] != 'pending':
                raise SafeError('该请求已处理或已过期，不会重复提交。')
            if not approve:
                self.store.db.execute("UPDATE actions SET state='rejected',updated_at=? WHERE id=?",(timestamp(),action_id))
                self.store.db.commit()
                return self.action(action_id)
            permission = 'allow_orders' if action['kind']=='order' else 'allow_cancel'
            self.check_client_permission(action['client_id'],conn['id'],permission)
            if not conn[permission]:
                raise SafeError('该账户的相应操作权限已关闭，未提交。')
            if action['environment'] != conn['environment']:
                raise SafeError('环境发生变化，未提交。')
            body = dict(action['payload'])
            if action['kind']=='order':
                order_type = body.pop('orderType')
                side = body.pop('side')
                body['quantity'] *= 1 if side=='buy' else -1
                method,path,interval = 'POST','/api/v0/equity/orders/'+order_type, (1.2 if order_type=='market' else 2)
            else:
                method,path,interval,body = 'DELETE','/api/v0/equity/orders/'+str(body['orderId']),1.2,None
            # Mark before networking. A crash/timeout can never cause automatic resubmission.
            self.store.db.execute("UPDATE actions SET state='submitting',updated_at=? WHERE id=?",(timestamp(),action_id))
            self.store.db.commit()
            state,result,error = 'unknown',None,'提交结果未知，请在212核对，不要重复提交。'
            old_source=self.broker.client_id
            try:
                source_broker=self.broker
                source_broker.client_id=action['client_id']
                result = source_broker.request(conn,method,path,body=body,interval=interval)
                state,error = 'accepted',None
            except SafeError as exc:
                state,error = ('unknown' if exc.uncertain else 'failed'),str(exc)
                if exc.uncertain:
                    error += ' 提交结果未知，请在212核对；本工具不会自动重试。'
            except Exception:
                pass
            finally:
                self.broker.client_id=old_source
            self.store.db.execute('UPDATE actions SET state=?,result=?,error=?,updated_at=? WHERE id=?',
                (state,json.dumps(result,ensure_ascii=False) if result else None,error,timestamp(),action_id))
            self.store.db.commit()
            return self.action(action_id)

    def rename_connection(self, connection_id, name):
        name = connection_name(name)
        conn = self.store.connection(connection_id)
        with self.store.account_lock(conn):
            # This is a local display name, independent of account identity and locale.
            with self.store.db:
                self.store.db.execute('UPDATE connections SET name=? WHERE id=?', (name, conn['id']))
        return {'ok': True, 'message': '连接名称已保存。'}

    def control(self, request):
        op = request.get('operation')
        if op in ('client_connect','client_disconnect','client_permissions'):
            try:
                links=ClientLinks(self.store)
                if op=='client_connect':return links.connect(request.get('clientId'),request.get('connectionId'))
                if op=='client_disconnect':return links.disconnect(request.get('clientId'))
                return links.permissions(request.get('clientId'),request.get('allow_orders'),request.get('allow_cancel'))
            except ClientError as exc:
                raise SafeError(str(exc))
        if op=='overview':
            return self.store.overview()
        if op=='add':
            return self.new_connection(request.get('name'),request.get('environment'),request.get('key'),request.get('secret'))
        if op=='rename':
            return self.rename_connection(request.get('connectionId'),request.get('name'))
        if op=='verify':
            return self.verify(request.get('connectionId'))
        if op=='permissions':
            return self.permissions(request.get('connectionId'),request.get('allow_orders'),request.get('allow_cancel'))
        if op=='enabled':
            return self.set_enabled(request.get('connectionId'),request.get('enabled'))
        if op=='delete':
            return self.delete(request.get('connectionId'))
        if op=='select':
            conn = self.store.connection(request.get('connectionId'),enabled=True)
            self.store.db.execute("INSERT OR REPLACE INTO settings VALUES('active',?)",(conn['id'],))
            self.store.db.commit()
            return {'ok':True}
        if op=='decide':
            return self.decide(request.get('actionId'),request.get('approve'))
        raise SafeError('管理操作不受支持。')


def object_schema(properties=None, required=None):
    result = {'type':'object','properties':properties or {},'additionalProperties':False}
    if required:
        result['required']=required
    return result


def tools():
    connection={'connectionId':{'type':'string','description':'可选连接ID，必须是用户在管理窗口分配给此AI客户端的账户。'}}
    ticker={'type':'string','description':'Trading 212 ticker，如AAPL_US_EQ。'}
    limit={'type':'integer','minimum':1,'maximum':50,'default':20}
    rows=[]
    def add(name,description,props=None,required=None,read=True):
        rows.append({'name':name,'description':description,'inputSchema':object_schema(props,required),
                     'annotations':{'readOnlyHint':read,'destructiveHint':False,'idempotentHint':read,'openWorldHint':False}})
    add('open_connection_manager','打开Trading212 Connection固定窗口，用户可输入密钥、管理连接、权限及API使用记录。不会更改权限。',read=False)
    add('list_connections','列出本机连接及本工具权限，不返回密钥。')
    add('connection_status','读取当前连接、默认只读及额外手动权限状态；不是网络验证。')
    add('get_api_usage','读取本工具最近24小时请求统计及212最近观测的限流额度，不代表所有应用的完整用量。')
    for name in READ_PATHS:
        props=dict(connection)
        if name=='get_positions':
            props['ticker']=ticker
        if name in ('get_order_history','get_dividends'):
            props.update(ticker=ticker,limit=limit,cursor={'type':'integer','minimum':0})
        if name=='get_transactions':
            props.update(limit=limit,cursor={'type':'string'},time={'type':'string','format':'date-time'})
        descriptions={'get_account_summary':'读取账户概览和可用资金。','get_positions':'读取当前持仓。',
                      'get_pending_orders':'读取挂单。','get_order_history':'读取一页历史订单，完整历史需使用nextCursor翻页。',
                      'get_dividends':'读取一页分红，完整历史需使用nextCursor翻页。','get_transactions':'读取一页资金流水，完整历史需使用nextCursor翻页。'}
        add(name,descriptions[name],props)
    request_id={'type':'string','description':'此次操作唯一ID，使用UUID。相同ID重试不会生成第二个请求。'}
    props=dict(connection,clientRequestId=request_id,orderType={'type':'string','enum':list(ORDER_FIELDS)},
        side={'type':'string','enum':['buy','sell']},ticker=ticker,quantity={'type':'number','exclusiveMinimum':0},
        limitPrice={'type':'number','exclusiveMinimum':0},stopPrice={'type':'number','exclusiveMinimum':0},
        timeValidity={'type':'string','enum':['DAY','GOOD_TILL_CANCEL']},extendedHours={'type':'boolean'})
    add('request_order','创建待用户确认的下单请求，不直接下单。仅在用户手动开启下单权限后可用；用户必须在固定窗口逐笔确认。',props,['clientRequestId','orderType','side','ticker','quantity'],False)
    add('request_cancellation','创建待用户确认的撤单请求，不直接撤单。仅在用户手动开启撤单权限后可用，需在固定窗口确认。',dict(connection,clientRequestId=request_id,orderId={'type':'integer','minimum':1}),['clientRequestId','orderId'],False)
    add('get_action_status','查询待确认操作状态。accepted表示212接受请求，不代表成交或保证撤单；unknown/submitting需到212核对，不要重发。',{'actionId':{'type':'string'}},['actionId'])
    return rows


def mcp_call(manager,name,args):
    if not isinstance(args,dict):
        raise SafeError('工具参数无效。')
    definitions={t['name']:t for t in tools()}
    if name not in definitions:
        raise SafeError('未知工具。权限变更及订单确认仅在固定窗口中进行。')
    schema=definitions[name]['inputSchema']
    if set(args)-set(schema['properties']) or any(k not in args for k in schema.get('required',[])):
        raise SafeError('工具参数缺失或包含额外字段。')
    if name in READ_PATHS:
        return manager.read(name,dict(args))
    if name=='open_connection_manager':
        app=Path(__file__).resolve().parents[2]
        subprocess.run(['/usr/bin/open',str(app)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'opened':True,'title':TITLE}
    if name=='list_connections':
        conn=manager.resolve_connection()
        return {'client':manager.client_id,'connections':[manager.store.public_connection(conn)]}
    if name=='connection_status':
        conn=manager.resolve_connection()
        profile=manager.store.client(manager.client_id) if manager.client_id!='manager' else {}
        return manager.store.public_connection(conn) | {'client':manager.client_id,'clientAllowOrders':bool(profile.get('allow_orders')),
            'clientAllowCancel':bool(profile.get('allow_cancel')),'source':TITLE,'mode':'read-only with optional user-enabled operations','networkVerified':False}
    if name=='get_api_usage':
        return manager.usage()
    if name=='request_order':
        return manager.request_action('order',dict(args))
    if name=='request_cancellation':
        return manager.request_action('cancel',dict(args))
    if name=='get_action_status':
        return manager.action(string(args.get('actionId'),'actionId'))


def rpc(manager,message):
    if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):
        return {'jsonrpc':'2.0','id':None,'error':{'code':-32600,'message':'Invalid Request'}}
    if 'id' not in message:
        return None
    method,params=message['method'],message.get('params',{})
    if not isinstance(params,dict):
        return {'jsonrpc':'2.0','id':message['id'],'error':{'code':-32602,'message':'Invalid params'}}
    if method=='initialize':
        if manager.client_id in ('codex','claude-code','claude-desktop'):
            manager.store.db.execute('UPDATE clients SET last_seen=? WHERE id=?',(timestamp(),manager.client_id))
            manager.store.db.commit()
        versions=('2025-11-25','2025-06-18','2025-03-26','2024-11-05')
        requested=params.get('protocolVersion')
        result={'protocolVersion':requested if requested in versions else '2025-06-18',
                'capabilities':{'tools':{'listChanged':False}},'serverInfo':{'name':'trading212-connection','title':TITLE,'version':VERSION},
                'instructions':'Trading212 Connection defaults to read-only, independently of broker API-key scope. Never request/reveal API credentials. Only the native manager lets the human enable permissions and confirm orders/cancellations. request_order and request_cancellation only queue a review; never claim an order was submitted before accepted status, and never claim a fill from accepted. Keep account/environment explicit. Unknown/submitting results must be reconciled on Trading 212 before any new request. Include source and fetchedAt for account data. History is paginated; continue nextCursor with the same filters. API usage covers this connector only.'}
    elif method=='ping':
        result={}
    elif method=='tools/list':
        result={'tools':tools()}
    elif method=='tools/call':
        try:
            result={'content':[{'type':'text','text':json.dumps(mcp_call(manager,params.get('name'),params.get('arguments',{})),ensure_ascii=False,allow_nan=False)}],'isError':False}
        except SafeError as exc:
            result={'content':[{'type':'text','text':str(exc)}],'isError':True}
        except Exception:
            result={'content':[{'type':'text','text':'本地服务错误，请打开管理窗口检查；不要重复提交结果未知的交易。'}],'isError':True}
    else:
        return {'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Method not found'}}
    return {'jsonrpc':'2.0','id':message['id'],'result':result}


def require_native_parent():
    lib=ctypes.CDLL('/usr/lib/libproc.dylib')
    lib.proc_pidpath.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32]
    lib.proc_pidpath.restype=ctypes.c_int
    buffer=ctypes.create_string_buffer(4096)
    expected=Path(__file__).resolve().parents[1]/'MacOS/Trading212Connection'
    if lib.proc_pidpath(os.getppid(),buffer,len(buffer)) <= 0 or Path(os.fsdecode(buffer.value)).resolve()!=expected.resolve():
        raise SafeError('管理操作只能从Trading212 Connection原生窗口调用。')


def main():
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--mcp',action='store_true')
    group.add_argument('--control',action='store_true')
    group.add_argument('--import-legacy',action='store_true')
    group.add_argument('--health',action='store_true')
    parser.add_argument('--client',choices=['codex','claude-code','claude-desktop'],default='codex')
    args=parser.parse_args()
    try:
        if args.control:
            require_native_parent()
        manager=Manager(client_id=args.client if args.mcp else 'manager')
        if args.control:
            data=sys.stdin.buffer.read(65537)
            if len(data)>65536:
                raise SafeError('管理请求过大。')
            request=json.loads(data)
            if not isinstance(request,dict):
                raise SafeError('管理请求无效。')
            print(json.dumps({'ok':True,'data':manager.control(request)},ensure_ascii=False,allow_nan=False))
        elif args.import_legacy:
            if manager.store.db.execute("SELECT 1 FROM connections WHERE key_service='codex.trading212.readonly'").fetchone():
                print(json.dumps({'ok':True,'alreadyImported':True}))
            else:
                creds=Keychain('live','codex.trading212.readonly').read()
                conn=manager.new_connection('我的Trading 212实盘','live',creds['key'],creds['secret'],legacy=True)
                print(json.dumps({'ok':True,'imported':True,'connectionId':conn['id'],'environment':'live','allow_orders':False,'allow_cancel':False}))
        elif args.health:
            manager.verify(manager.store.connection()['id'])
            print(json.dumps({'ok':True,'environment':manager.store.connection()['environment'],'accountAndPositionsVerified':True,'allow_orders':bool(manager.store.connection()['allow_orders']),'allow_cancel':bool(manager.store.connection()['allow_cancel'])}))
        else:
            for line in sys.stdin:
                try:
                    response=rpc(manager,json.loads(line))
                except (ValueError,UnicodeError):
                    response={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
                if response is not None:
                    print(json.dumps(response,ensure_ascii=False),flush=True)
        return 0
    except SafeError as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        return 1
    except Exception:
        print(json.dumps({'ok':False,'error':'本地管理服务发生错误，未返回任何凭证。'},ensure_ascii=False))
        return 1


if __name__=='__main__':
    sys.exit(main())
