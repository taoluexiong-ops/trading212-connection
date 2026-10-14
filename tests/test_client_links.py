import json
from pathlib import Path
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_connection import m, ConnectionFixture, Response
from client_links import ClientLinks,ClientError,SERVER_NAME


class TestClientLinks(ConnectionFixture):
    def setUp(self):
        super().setUp()
        self.home=Path(self.temp.name)/'user-home'
        self.home.mkdir()
        self.backend=self.home/'Applications/Trading212 Connection.app/Contents/Resources/backend.py'
        self.backend.parent.mkdir(parents=True)
        self.backend.write_text('# fake backend')
        self.code=self.home/'.local/bin/claude';self.code.parent.mkdir(parents=True)
        self.code.write_text('#!/bin/sh\nexit 0\n');self.code.chmod(0o755)
        self.desktop=self.home/'Applications/Claude.app';self.desktop.mkdir()
        self.codex=self.home/'bin/codex';self.codex.parent.mkdir();self.codex.write_text('fake');self.codex.chmod(0o755)
        self.commands=[]
        def finder(name):return str(self.code if name=='claude' else self.codex)
        def runner(args,**kwargs):
            self.commands.append(args)
            client='codex' if args[0]==str(self.codex) else 'claude-code'
            if args[2]=='add':
                i=args.index('--')
                entry={'command':args[i+1],'args':args[i+2:]}
            else:entry=None
            path=self.links.config_path(client);path.parent.mkdir(parents=True,exist_ok=True)
            if client=='codex':
                path.write_text('[mcp_servers.trading212-connection]\ncommand = '+json.dumps(entry['command'])+'\nargs = '+json.dumps(entry['args'])+'\n' if entry else '')
            else:
                root=json.loads(path.read_text()) if path.exists() else {'settings':{'keep':True},'mcpServers':{'other':{'command':'keep'}}}
                if entry:root['mcpServers'][SERVER_NAME]=entry
                else:root['mcpServers'].pop(SERVER_NAME,None)
                path.write_text(json.dumps(root))
            return type('Run',(),{'returncode':0})()
        self.environment=patch.dict(os.environ,{},clear=True);self.environment.start()
        self.links=ClientLinks(self.store,home=self.home,backend=self.backend,runner=runner,finder=finder)

    def tearDown(self):
        self.environment.stop()
        super().tearDown()

    def bind(self,client,orders=False,cancel=False,connection=None):
        self.store.db.execute('UPDATE clients SET enabled=1,connection_id=?,allow_orders=?,allow_cancel=? WHERE id=?',
            (connection or self.conn['id'],int(orders),int(cancel),client));self.store.db.commit()
        return m.Manager(self.store,client_id=client)

    def test_desktop_add_disconnect_preserves_unrelated_config_and_backs_up(self):
        path=self.links.config_path('claude-desktop');path.parent.mkdir(parents=True)
        original={'preferences':{'theme':'dark'},'mcpServers':{'other':{'command':'other','env':{'SECRET':'not-to-log'}}}}
        path.write_text(json.dumps(original))
        self.links.connect('claude-desktop',self.conn['id'])
        data=json.loads(path.read_text())
        self.assertEqual(data['preferences'],original['preferences'])
        self.assertEqual(data['mcpServers']['other'],original['mcpServers']['other'])
        entry=data['mcpServers'][SERVER_NAME]
        self.assertEqual(entry['args'][-2:],['--client','claude-desktop'])
        self.assertNotIn('env',entry)
        self.assertFalse(self.store.client('claude-desktop')['allow_orders'])
        self.assertEqual(os.stat(path).st_mode&0o777,0o600)
        backups=list((path.parent/'Trading212 Connection Backups').glob('*.json'))
        self.assertEqual(json.loads(backups[0].read_text()),original)
        self.links.disconnect('claude-desktop')
        self.assertEqual(json.loads(path.read_text()),original)
        self.assertFalse(self.store.client('claude-desktop')['enabled'])

    def test_invalid_or_foreign_desktop_config_not_overwritten(self):
        path=self.links.config_path('claude-desktop');path.parent.mkdir(parents=True)
        for original in ('not json',json.dumps({'mcpServers':{SERVER_NAME:{'command':'other'}}})):
            path.write_text(original)
            with self.assertRaises(ClientError):self.links.connect('claude-desktop',self.conn['id'])
            self.assertEqual(path.read_text(),original)

    def test_cli_registration_and_idempotence(self):
        for client in ('codex','claude-code'):
            self.links.connect(client,self.conn['id'])
            self.assertTrue(self.links.configured(client,self.links.entry(client)))
            count=len(self.commands)
            self.links.connect(client,self.conn['id'])
            self.assertEqual(len(self.commands),count)
            self.links.disconnect(client)
            self.assertIsNone(self.links.entry(client))
        add=next(c for c in self.commands if c[0]==str(self.code) and c[2]=='add')
        self.assertIn('--scope',add);self.assertIn('user',add)
        self.assertNotIn('test-api-secret',' '.join(add))

    def test_client_cannot_access_another_account(self):
        client=self.bind('claude-code')
        with self.assertRaises(m.SafeError):client.read('get_positions',{'connectionId':'other-account'})
        self.assertEqual(self.requests,[])
        payload=m.mcp_call(client,'list_connections',{})
        self.assertEqual([r['id'] for r in payload['connections']],[self.conn['id']])

    def test_disconnect_revokes_pending_and_blocks_live_process(self):
        client=self.bind('claude-code',orders=True)
        self.manager.permissions(self.conn['id'],True,False)
        action=client.request_action('order',self.order())
        self.links.disconnect('claude-code')
        self.assertEqual(self.manager.action(action['id'])['state'],'revoked')
        with self.assertRaises(m.SafeError):client.read('get_positions',{})
        with self.assertRaises(m.SafeError):client.action(action['id'])
        with self.assertRaises(m.SafeError):self.manager.decide(action['id'],True)
        self.assertEqual(self.requests,[])

    def test_account_and_client_permissions_both_required(self):
        client=self.bind('claude-code')
        self.manager.permissions(self.conn['id'],True,False)
        with self.assertRaises(m.SafeError):client.request_action('order',self.order())
        self.links.permissions('claude-code',True,False)
        action=client.request_action('order',self.order())
        self.assertEqual(action['client_id'],'claude-code')
        self.links.permissions('claude-code',False,False)
        self.assertEqual(self.manager.action(action['id'])['state'],'revoked')
        self.assertEqual(self.requests,[])

    def test_client_cannot_read_other_clients_actions(self):
        code=self.bind('claude-code',orders=True)
        codex=self.bind('codex',orders=True)
        self.manager.permissions(self.conn['id'],True,False)
        action=code.request_action('order',self.order())
        with self.assertRaises(m.SafeError):codex.action(action['id'])
        with self.assertRaises(m.SafeError):codex.request_action('order',self.order())

    def test_client_source_and_statistics_separate(self):
        code=self.bind('claude-code')
        codex=self.bind('codex')
        with patch.object(code.broker.opener,'open',return_value=Response([])):
            code.read('get_positions',{})
        self.assertEqual(code.usage()['stats']['requests'],1)
        self.assertEqual(codex.usage()['stats']['requests'],0)
        self.assertEqual(self.store.overview()['logs'][0]['client_id'],'claude-code')

    def test_client_permission_rechecked_at_confirmation(self):
        code=self.bind('claude-code',orders=True)
        self.manager.permissions(self.conn['id'],True,False)
        action=code.request_action('order',self.order())
        self.store.db.execute("UPDATE clients SET allow_orders=0 WHERE id='claude-code'");self.store.db.commit()
        with self.assertRaises(m.SafeError):self.manager.decide(action['id'],True)
        self.assertEqual(self.requests,[])

    def test_handshake_and_disabled_client(self):
        code=m.Manager(self.store,client_id='claude-code')
        reply=m.rpc(code,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}})
        self.assertEqual(reply['result']['serverInfo']['version'],'1.2.2')
        self.assertIsNotNone(self.store.client('claude-code')['last_seen'])
        reply=m.rpc(code,{'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'get_positions','arguments':{}}})
        self.assertTrue(reply['result']['isError'])
        self.assertEqual(self.requests,[])

    def test_upgrade_preserves_account_and_disables_new_clients(self):
        old_dir=Path(self.temp.name)/'old-version';old_dir.mkdir()
        old_db=sqlite3.connect(str(old_dir/'connections.sqlite3'))
        self.store.db.backup(old_db)
        old_db.execute('DROP TABLE clients')
        old_db.execute("DELETE FROM settings WHERE name='hub-migrated'")
        old_db.execute('ALTER TABLE events DROP COLUMN client_id')
        old_db.execute('ALTER TABLE actions DROP COLUMN client_id')
        old_db.commit();old_db.close()
        upgraded=m.Store(old_dir)
        self.assertEqual(upgraded.active(),self.conn['id'])
        self.assertEqual(upgraded.connection()['key_account'],self.store.connection()['key_account'])
        self.assertEqual(upgraded.client('codex')['connection_id'],self.conn['id'])
        self.assertTrue(upgraded.client('codex')['enabled'])
        self.assertFalse(upgraded.client('claude-code')['enabled'])
        self.assertFalse(upgraded.client('claude-desktop')['enabled'])
        self.assertFalse(upgraded.client('codex')['allow_orders'])
        upgraded.db.close()

    def test_failed_cli_configuration_keeps_client_disabled(self):
        self.links.runner=lambda *args,**kwargs:type('Run',(),{'returncode':1})()
        with self.assertRaises(ClientError):self.links.connect('claude-code',self.conn['id'])
        self.assertFalse(self.store.client('claude-code')['enabled'])
        self.assertIsNotNone(self.store.client('claude-code')['last_error'])


if __name__=='__main__':unittest.main(verbosity=2)
