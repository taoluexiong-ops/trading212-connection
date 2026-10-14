"""Local AI-client registration. No broker credentials enter client config."""
import ast
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

SERVER_NAME = 'trading212-connection'
CLIENT_LABELS = {'codex':'GPT / Codex', 'claude-code':'Claude Code',
                 'claude-desktop':'Claude Desktop', 'manager':'管理窗口', 'legacy':'旧版 / 未标记'}


class ClientError(Exception):
    pass


class ClientLinks:
    def __init__(self, store, home=None, backend=None, runner=None, finder=None):
        self.store = store
        self.home = Path(home) if home is not None else Path.home()
        self.backend = Path(backend) if backend is not None else self.home/'Applications/Trading212 Connection.app/Contents/Resources/backend.py'
        self.runner = runner or subprocess.run
        self.finder = finder or shutil.which

    def executable(self, client):
        if client == 'codex':
            candidates = [self.finder('codex'),
                '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
                '/Applications/Codex.app/Contents/Resources/codex', '/opt/homebrew/bin/codex','/usr/local/bin/codex']
        elif client == 'claude-code':
            candidates = [self.finder('claude'),self.home/'.local/bin/claude',self.home/'.claude/local/claude',
                          '/opt/homebrew/bin/claude','/usr/local/bin/claude']
        else:
            return None
        return next((str(Path(p)) for p in candidates if p and Path(p).is_file() and os.access(p,os.X_OK)),None)

    def config_path(self, client):
        if client == 'codex':
            return Path(os.environ.get('CODEX_HOME', str(self.home/'.codex')))/'config.toml'
        if client == 'claude-code':
            return self.home/'.claude.json'
        if client == 'claude-desktop':
            return self.home/'Library/Application Support/Claude/claude_desktop_config.json'
        raise ClientError('不支持的AI客户端。')

    def entry(self, client):
        path=self.config_path(client)
        if not path.exists():
            return None
        try:
            raw=path.read_text()
            if client != 'codex':
                root=json.loads(raw)
                if not isinstance(root,dict) or not isinstance(root.get('mcpServers',{}),dict):
                    raise ValueError()
                result=root.get('mcpServers',{}).get(SERVER_NAME)
                if result is not None and not isinstance(result,dict):
                    raise ValueError()
                return result
            # Read only our small generated stdio entry; CLI owns TOML mutations.
            pattern=r'(?ms)^\[mcp_servers\.(?:trading212-connection|"trading212-connection")\]\s*\n(.*?)(?=^\[|\Z)'
            match=re.search(pattern,raw)
            if not match:
                return None
            result={}
            for name,value_pattern in [('command',r'(?m)^command\s*=\s*(.+)$'),('args',r'(?ms)^args\s*=\s*(\[.*?\])')]:
                value=re.search(value_pattern,match.group(1))
                if value:
                    result[name]=ast.literal_eval(value.group(1))
            return result
        except (ValueError,SyntaxError,OSError,UnicodeError):
            raise ClientError('客户端配置无法读取或格式无效；未更改配置。请在对应AI应用修复后重试。')

    def owned(self, entry):
        if not isinstance(entry,dict) or entry.get('command')!='/usr/bin/python3':
            return False
        args=entry.get('args',[])
        return (isinstance(args,list) and len(args)>=2 and isinstance(args[0],str)
                and args[0].endswith('/Trading212 Connection.app/Contents/Resources/backend.py') and '--mcp' in args)

    def expected(self, client):
        return {'command':'/usr/bin/python3','args':[str(self.backend),'--mcp','--client',client]}

    def configured(self, client, entry):
        expected=self.expected(client)
        return self.owned(entry) and entry.get('args')==expected['args']

    def statuses(self):
        output=[]
        for client in ('codex','claude-code','claude-desktop'):
            row=dict(self.store.db.execute('SELECT * FROM clients WHERE id=?',(client,)).fetchone())
            error=None
            try:
                entry=self.entry(client)
            except ClientError as exc:
                entry=None
                error=str(exc)
            configured=self.configured(client,entry)
            installed=(bool(self.executable(client)) if client != 'claude-desktop' else
                       any(p.is_dir() for p in (Path('/Applications/Claude.app'),self.home/'Applications/Claude.app')))
            conn=self.store.db.execute('SELECT name,environment,enabled FROM connections WHERE id=?',(row['connection_id'],)).fetchone()
            output.append({'id':client,'name':CLIENT_LABELS[client], 'installed':installed,
                'configured':configured,'enabled':bool(row['enabled']), 'conflict':entry is not None and not self.owned(entry),
                'connectionId':row['connection_id'],'connectionName':conn['name'] if conn else None,
                'environment':conn['environment'] if conn else None,'accountEnabled':bool(conn['enabled']) if conn else False,
                'allow_orders':bool(row['allow_orders']), 'allow_cancel':bool(row['allow_cancel']),
                'configuredAt':row['configured_at'],'lastSeen':row['last_seen'],'error':error or row['last_error'],
                'configPath':str(self.config_path(client))})
        return output

    @contextlib.contextmanager
    def lock(self):
        path=self.store.directory/'client-config.lock'
        with open(str(path),'a') as handle:
            os.chmod(path,0o600)
            fcntl.flock(handle,fcntl.LOCK_EX)
            yield
            fcntl.flock(handle,fcntl.LOCK_UN)

    def run_cli(self,args):
        try:
            run=self.runner(args,capture_output=True,text=True,timeout=25)
        except (OSError,subprocess.TimeoutExpired):
            raise ClientError('无法执行客户端配置命令。请检查应用安装后重试。')
        if run.returncode:
            raise ClientError('客户端未接受连接配置。请检查对应AI工具的本机配置或管理策略；本工具保持停用。')

    def desktop_update(self, add):
        path=self.config_path('claude-desktop')
        path.parent.mkdir(parents=True,exist_ok=True)
        old=path.read_bytes() if path.exists() else None
        try:
            root=json.loads(old) if old is not None else {}
            if not isinstance(root,dict) or not isinstance(root.get('mcpServers',{}),dict):
                raise ValueError()
        except (ValueError,UnicodeError):
            raise ClientError('Claude Desktop配置无效，未覆盖。请先修复该配置文件。')
        servers=root.setdefault('mcpServers',{})
        current=servers.get(SERVER_NAME)
        if current is not None and not self.owned(current):
            raise ClientError('Claude Desktop存在其他程序创建的同名连接，未覆盖。')
        if old is not None:
            backups=path.parent/'Trading212 Connection Backups'
            backups.mkdir(mode=0o700,exist_ok=True)
            os.chmod(backups,0o700)
            backup=backups/('config-'+str(time.time_ns())+'.json')
            with open(str(backup),'xb') as f:
                os.chmod(backup,0o600)
                f.write(old)
        if add:
            servers[SERVER_NAME]=self.expected('claude-desktop')
        else:
            servers.pop(SERVER_NAME,None)
        handle,name=tempfile.mkstemp(prefix='.trading212-',suffix='.json',dir=str(path.parent))
        try:
            with os.fdopen(handle,'w') as f:
                json.dump(root,f,ensure_ascii=False,indent=2)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.chmod(name,0o600)
            # Reject an intervening external edit instead of replacing it silently.
            actual=path.read_bytes() if path.exists() else None
            if actual != old:
                raise ClientError('Claude配置在写入前发生变化，未覆盖；请重试。')
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)

    def invalidate(self,client):
        self.store.db.execute("UPDATE actions SET state='revoked',error='AI客户端连接或权限已改变',updated_at=? WHERE client_id=? AND state='pending'",
                              (self.store.now(),client))

    def connect(self,client,connection_id):
        if client not in ('codex','claude-code','claude-desktop'):
            raise ClientError('不支持的AI客户端。')
        self.store.connection(connection_id,enabled=True)
        if not self.backend.is_file():
            raise ClientError('固定中转程序尚未安装，请重新安装Trading212 Connection。')
        if client != 'claude-desktop' and not self.executable(client):
            raise ClientError('尚未检测到'+CLIENT_LABELS[client]+'命令行程序。安装后点击刷新即可连接。')
        if client == 'claude-desktop' and not any(p.is_dir() for p in (Path('/Applications/Claude.app'),self.home/'Applications/Claude.app')):
            raise ClientError('尚未检测到Claude Desktop。安装后点击刷新即可连接。')
        with self.lock():
            current=self.entry(client)
            if current is not None and not self.owned(current):
                raise ClientError('客户端存在其他程序创建的同名MCP连接，未覆盖。')
            previous=self.store.db.execute('SELECT * FROM clients WHERE id=?',(client,)).fetchone()
            if self.configured(client,current) and previous['enabled'] and previous['connection_id']==connection_id:
                return {'ok':True,'message':'此AI客户端已配置，无需重复连接；现有权限保持不变。'}
            self.store.db.execute('UPDATE clients SET enabled=0,allow_orders=0,allow_cancel=0 WHERE id=?',(client,))
            self.invalidate(client);self.store.db.commit()
            try:
                if client=='claude-desktop':
                    self.desktop_update(True)
                else:
                    cli=self.executable(client)
                    if current is not None:
                        args=[cli,'mcp','remove']
                        if client=='claude-code':args+=['--scope','user']
                        self.run_cli(args+[SERVER_NAME])
                    if client=='codex':
                        args=[cli,'mcp','add',SERVER_NAME,'--']
                    else:
                        args=[cli,'mcp','add','--scope','user','--transport','stdio',SERVER_NAME,'--']
                    self.run_cli(args+['/usr/bin/python3',str(self.backend),'--mcp','--client',client])
                if not self.configured(client,self.entry(client)):
                    raise ClientError('写入后的客户端配置未通过校验，本工具保持停用。')
                self.store.db.execute('UPDATE clients SET enabled=1,connection_id=?,configured_at=?,last_error=NULL WHERE id=?',
                                      (connection_id,self.store.now(),client))
                self.store.db.commit()
            except ClientError as exc:
                self.store.db.execute('UPDATE clients SET last_error=? WHERE id=?',(str(exc),client))
                self.store.db.commit()
                raise
        return {'ok':True,'message':'已配置'+CLIENT_LABELS[client]+'。重启或重新加载该AI客户端后可用；初始权限为只读。'}

    def disconnect(self,client):
        if client not in ('codex','claude-code','claude-desktop'):
            raise ClientError('不支持的AI客户端。')
        with self.lock():
            self.store.db.execute('UPDATE clients SET enabled=0,allow_orders=0,allow_cancel=0 WHERE id=?',(client,))
            self.invalidate(client);self.store.db.commit()
            current=self.entry(client)
            if self.owned(current):
                if client=='claude-desktop':
                    self.desktop_update(False)
                elif self.executable(client):
                    args=[self.executable(client),'mcp','remove']
                    if client=='claude-code':args+=['--scope','user']
                    self.run_cli(args+[SERVER_NAME])
                else:
                    return {'ok':True,'message':'本工具已立即停用此客户端；客户端命令行缺失，请自行移除残留配置。'}
            self.store.db.execute('UPDATE clients SET last_error=NULL WHERE id=?',(client,))
            self.store.db.commit()
        return {'ok':True,'message':'已断开'+CLIENT_LABELS[client]+'。212账户和密钥仍保留。'}

    def permissions(self,client,orders,cancel):
        if client not in ('codex','claude-code','claude-desktop') or type(orders) is not bool or type(cancel) is not bool:
            raise ClientError('AI客户端或权限参数无效。')
        row=self.store.db.execute('SELECT * FROM clients WHERE id=?',(client,)).fetchone()
        if not row['enabled'] and (orders or cancel):
            raise ClientError('请先连接此AI客户端。')
        if row['connection_id']:
            conn=self.store.connection(row['connection_id'],enabled=True)
            with self.store.account_lock(conn):
                self.store.db.execute('UPDATE clients SET allow_orders=?,allow_cancel=? WHERE id=?',(int(orders),int(cancel),client))
                self.invalidate(client);self.store.db.commit()
        return {'ok':True}
