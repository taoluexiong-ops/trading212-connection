import copy
from pathlib import Path
from unittest.mock import patch
from test_connection import ConnectionFixture, m, FakeKeychain
from client_links import ClientLinks


class AccountNameTests(ConnectionFixture):
    def test_rename_persists_without_changing_identity_permissions_or_history(self):
        self.manager.permissions(self.conn['id'],True,True)
        action=self.manager.request_action('order',self.order())
        self.store.db.execute('UPDATE clients SET enabled=1,connection_id=?,allow_orders=1,allow_cancel=1 WHERE id=?',(self.conn['id'],'codex'))
        self.store.db.commit()
        before=self.store.connection(self.conn['id'])
        tables={table:[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table)]
                for table in ['clients','settings','actions','events','pacing','rate_limits']}
        credentials=copy.deepcopy(FakeKeychain.storage)
        with patch.object(m,'Keychain',side_effect=AssertionError('Rename must not touch credentials')),patch.object(self.manager.broker,'request',side_effect=AssertionError('Rename must not call the API')):
            self.manager.control({'operation':'rename','connectionId':self.conn['id'],'name':'  Trading 212 ISA 我的账户 {1}  '})
        after=self.store.connection(self.conn['id'])
        self.assertEqual(after['name'],'Trading 212 ISA 我的账户 {1}')
        self.assertEqual({k:v for k,v in before.items() if k!='name'},{k:v for k,v in after.items() if k!='name'})
        for table,rows in tables.items():self.assertEqual(rows,[tuple(row) for row in self.store.db.execute('SELECT * FROM '+table)])
        self.assertEqual(credentials,FakeKeychain.storage)
        self.assertEqual(self.manager.action(action['id'])['connection_name'],'Test Invest')
        links=ClientLinks(self.store,home=Path(self.temp.name)/'home',finder=lambda _:None)
        profile=next(x for x in links.statuses() if x['id']=='codex')
        self.assertEqual(profile['connectionName'],after['name'])
        reopened=m.Store(self.temp.name)
        try:self.assertEqual(reopened.connection(self.conn['id'])['name'],after['name'])
        finally:reopened.db.close()
        self.assertNotIn('rename',{x['name'] for x in m.tools()})
        self.assertTrue(self.rpc('rename',{'connectionId':self.conn['id'],'name':'Unexpected'})['result']['isError'])

    def test_invalid_names_and_missing_connection_leave_record_unchanged(self):
        before=self.store.connection(self.conn['id'])
        for name in ['', '   ',None,123,'x'*61,'Bad\nName','Bad\tName','Bad\x00Name']:
            with self.assertRaises(m.SafeError):self.manager.control({'operation':'rename','connectionId':self.conn['id'],'name':name})
            self.assertEqual(before,self.store.connection(self.conn['id']))
        with self.assertRaises(m.SafeError):self.manager.rename_connection('missing','Trading 212')
        self.manager.set_enabled(self.conn['id'],False)
        self.manager.rename_connection(self.conn['id'],'Paused ISA')
        self.assertFalse(self.store.connection(self.conn['id'])['enabled'])
        self.assertEqual(self.store.connection(self.conn['id'])['name'],'Paused ISA')
        self.assertEqual(self.requests,[])

    def test_verification_and_repeat_credentials_keep_custom_name(self):
        self.manager.rename_connection(self.conn['id'],'Trading 212 ISA')
        self.manager.verify(self.conn['id'])
        self.store.db.execute('DELETE FROM pacing');self.store.db.execute('DELETE FROM rate_limits');self.store.db.commit()
        result=self.manager.new_connection('Different default','live','test-api-key','test-api-secret')
        self.assertEqual(result['name'],'Trading 212 ISA')
        self.assertEqual(result['id'],self.conn['id'])
