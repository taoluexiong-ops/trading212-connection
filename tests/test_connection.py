import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / 'app/Trading212 Connection.app/Contents/Resources/backend.py'
sys.path.insert(0,str(BACKEND.parent))
spec = importlib.util.spec_from_file_location('backend',BACKEND)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class FakeKeychain:
    storage = {}
    def __init__(self, account, service=m.KEYCHAIN_SERVICE):
        self.key=(service,account)
    def save(self,key,secret):
        self.storage[self.key]={'key':key,'secret':secret}
    def read(self):
        if self.key not in self.storage:
            raise m.SafeError('missing credential')
        return self.storage[self.key]
    def delete(self):
        self.storage.pop(self.key,None)


class Response:
    status=200
    def __init__(self,payload,headers=None):
        self.payload=payload
        self.headers=headers or {}
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def read(self,_): return json.dumps(self.payload).encode()


class ConnectionFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=m.Store(self.temp.name)
        self.manager=m.Manager(self.store)
        self.keypatch=patch.object(m,'Keychain',FakeKeychain)
        self.keypatch.start()
        FakeKeychain.storage={}
        self.responses=[]
        self.requests=[]
        def open_request(req,timeout):
            self.requests.append(req)
            if self.responses:
                response=self.responses.pop(0)
                if isinstance(response,Exception): raise response
                return response
            if req.full_url.endswith('/account/summary'):
                return Response({'id':111,'currency':'GBP','totalValue':10000},
                    {'x-ratelimit-limit':'1','x-ratelimit-remaining':'0','x-ratelimit-used':'1','x-ratelimit-period':'5','x-ratelimit-reset':str(int(time.time())+5)})
            return Response([])
        self.openpatch=patch.object(self.manager.broker.opener,'open',side_effect=open_request)
        self.openpatch.start()
        self.conn=self.manager.new_connection('Test Invest','live','test-api-key','test-api-secret')
        self.requests.clear()
        self.store.db.execute('DELETE FROM pacing')
        self.store.db.execute('DELETE FROM rate_limits')
        self.store.db.commit()

    def tearDown(self):
        self.openpatch.stop()
        self.keypatch.stop()
        self.store.db.close()
        self.temp.cleanup()

    def order(self,client_id='client-request-0001',**kwargs):
        return dict(clientRequestId=client_id,orderType='limit',side='buy',ticker='AAPL_US_EQ',quantity=1,limitPrice=100,**kwargs)

    def rpc(self,name,args):
        return m.rpc(self.manager,{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}})


class TestConnection(ConnectionFixture):
    def test_new_connection_default_read_only_with_full_api_key(self):
        self.assertFalse(self.conn['allow_orders'])
        self.assertFalse(self.conn['allow_cancel'])
        result=self.rpc('request_order',self.order())
        self.assertTrue(result['result']['isError'])
        result=self.rpc('request_cancellation',{'clientRequestId':'cancel-request-001','orderId':123})
        self.assertTrue(result['result']['isError'])
        self.assertEqual(self.requests,[])

    def test_same_credentials_reuse_existing_connection(self):
        repeated=self.manager.new_connection('Repeated','live','test-api-key','test-api-secret')
        self.assertEqual(repeated['id'],self.conn['id'])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM connections').fetchone()[0],1)
        self.assertFalse(repeated['allow_orders'])
        self.assertFalse(repeated['allow_cancel'])

    def test_no_mcp_permission_or_confirm_routes(self):
        names={t['name'] for t in m.tools()}
        self.assertNotIn('permissions',names)
        self.assertNotIn('decide',names)
        for name in ('permissions','approve_order','execute_order','delete_connection'):
            self.assertTrue(self.rpc(name,{'allow_orders':True})['result']['isError'])
        self.assertFalse(self.store.connection()['allow_orders'])
        self.assertEqual(self.requests,[])

    def test_native_parent_required_and_stdout_does_not_expose_credentials(self):
        process=subprocess.run([sys.executable,str(BACKEND),'--control'],input=json.dumps({'operation':'permissions','allow_orders':True}),capture_output=True,text=True)
        self.assertNotEqual(process.returncode,0)
        result=json.loads(process.stdout)
        self.assertFalse(result['ok'])
        self.assertIn('原生窗口',result['error'])

    def test_open_permissions_only_queues_until_manual_decision(self):
        self.manager.permissions(self.conn['id'],True,False)
        action=self.manager.request_action('order',self.order())
        self.assertEqual(action['state'],'pending')
        self.assertEqual(self.requests,[])
        self.responses=[Response({'id':999,'status':'NEW'})]
        accepted=self.manager.decide(action['id'],True)
        self.assertEqual(accepted['state'],'accepted')
        self.assertEqual(len(self.requests),1)
        req=self.requests[0]
        self.assertEqual(req.get_method(),'POST')
        self.assertEqual(req.full_url,'https://live.trading212.com/api/v0/equity/orders/limit')
        self.assertEqual(json.loads(req.data),{'ticker':'AAPL_US_EQ','quantity':1,'limitPrice':100,'timeValidity':'DAY'})
        with self.assertRaises(m.SafeError): self.manager.decide(action['id'],True)
        self.assertEqual(len(self.requests),1)

    def test_dedup_same_request_and_reject_changed_payload(self):
        self.manager.permissions(self.conn['id'],True,False)
        first=self.manager.request_action('order',self.order())
        second=self.manager.request_action('order',self.order())
        self.assertEqual(first['id'],second['id'])
        args=self.order();args['quantity']=2
        with self.assertRaises(m.SafeError):self.manager.request_action('order',args)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM actions').fetchone()[0],1)

    def test_cancel_permission_independent_and_payload(self):
        self.manager.permissions(self.conn['id'],True,False)
        args={'clientRequestId':'cancel-request-001','orderId':123}
        with self.assertRaises(m.SafeError):self.manager.request_action('cancel',args)
        self.manager.permissions(self.conn['id'],False,True)
        action=self.manager.request_action('cancel',args)
        self.responses=[Response(None)]
        self.manager.decide(action['id'],True)
        self.assertEqual(self.requests[0].get_method(),'DELETE')
        self.assertTrue(self.requests[0].full_url.endswith('/orders/123'))
        self.assertIsNone(self.requests[0].data)

    def test_sell_quantity_transformation_all_order_types(self):
        self.manager.permissions(self.conn['id'],True,False)
        for kind in m.ORDER_FIELDS:
            args={'clientRequestId':'request-'+kind,'orderType':kind,'side':'sell','ticker':'AAPL_US_EQ','quantity':0.5}
            if 'limitPrice' in m.ORDER_FIELDS[kind]:args['limitPrice']=100
            if 'stopPrice' in m.ORDER_FIELDS[kind]:args['stopPrice']=105
            action=self.manager.request_action('order',args)
            self.responses=[Response({'id':42})]
            self.assertEqual(self.manager.decide(action['id'],True)['state'],'accepted')
            self.assertEqual(json.loads(self.requests[-1].data)['quantity'],-0.5)

    def test_disable_pause_delete_and_expiry_stop_pending_actions(self):
        self.manager.permissions(self.conn['id'],True,False)
        action=self.manager.request_action('order',self.order())
        self.manager.permissions(self.conn['id'],False,False)
        self.assertEqual(self.manager.action(action['id'])['state'],'revoked')
        with self.assertRaises(m.SafeError):self.manager.decide(action['id'],True)
        self.manager.permissions(self.conn['id'],True,False)
        action=self.manager.request_action('order',self.order(client_id='client-expire-0001'))
        self.store.db.execute('UPDATE actions SET expires_at=? WHERE id=?',(time.time()-1,action['id']));self.store.db.commit()
        with self.assertRaises(m.SafeError):self.manager.decide(action['id'],True)
        self.assertEqual(self.manager.action(action['id'])['state'],'expired')
        self.manager.set_enabled(self.conn['id'],False)
        self.assertFalse(self.store.connection()['allow_orders'])
        with self.assertRaises(m.SafeError):self.manager.read('get_positions',{})
        self.manager.set_enabled(self.conn['id'],True)
        self.assertFalse(self.store.connection()['allow_orders'])
        self.manager.delete(self.conn['id'])
        self.assertIsNone(self.store.active())
        self.assertEqual(FakeKeychain.storage,{})
        self.assertEqual(self.requests,[])

    def test_delete_failure_still_disables_connection(self):
        with patch.object(FakeKeychain,'delete',side_effect=m.SafeError('blocked')):
            with self.assertRaises(m.SafeError):self.manager.delete(self.conn['id'])
        self.assertFalse(self.store.connection()['enabled'])

    def test_timeout_unknown_never_retried(self):
        self.manager.permissions(self.conn['id'],True,False)
        action=self.manager.request_action('order',self.order())
        self.responses=[URLError('test-api-secret')]
        result=self.manager.decide(action['id'],True)
        self.assertEqual(result['state'],'unknown')
        self.assertNotIn('test-api-secret',json.dumps(result))
        with self.assertRaises(m.SafeError):self.manager.decide(action['id'],True)
        self.assertEqual(len(self.requests),1)

    def test_permission_and_numeric_validation_before_network(self):
        self.manager.permissions(self.conn['id'],True,False)
        for quantity in (True,0,-1,float('nan'),float('inf')):
            args=self.order();args['quantity']=quantity
            with self.assertRaises(m.SafeError):self.manager.request_action('order',args)
        args=self.order();args['url']='https://attacker.example'
        with self.assertRaises(m.SafeError):self.manager.request_action('order',args)
        args=self.order();args['extendedHours']=True
        with self.assertRaises(m.SafeError):self.manager.request_action('order',args)
        self.assertEqual(self.requests,[])

    def test_response_headers_usage_and_error_redaction(self):
        self.responses=[Response([],{'x-ratelimit-limit':'20','x-ratelimit-remaining':'19','x-ratelimit-used':'1','x-ratelimit-period':'60','x-ratelimit-reset':str(int(time.time())+60)})]
        self.manager.read('get_dividends',{})
        result=self.store.overview()
        rates={r['endpoint']:r for r in result['rateLimits']}
        self.assertEqual(rates['/api/v0/equity/history/dividends']['remaining'],19)
        self.assertEqual(result['stats']['requests'],3)
        self.responses=[HTTPError('url',403,'test-api-secret',{},io.BytesIO(b'test-api-key'))]
        with self.assertRaises(m.SafeError):self.manager.read('get_pending_orders',{})
        raw=json.dumps(self.store.overview())
        self.assertNotIn('test-api-secret',raw)
        self.assertNotIn('test-api-key',raw)
        self.assertNotIn('totalValue',raw)
        self.assertEqual(self.store.overview()['stats']['failures'],1)

    def test_429_shared_pacing_and_whitelist(self):
        self.responses=[HTTPError('url',429,'secret',{'Retry-After':'30'},io.BytesIO(b'secret'))]
        with self.assertRaises(m.SafeError):self.manager.read('get_positions',{})
        with self.assertRaises(m.SafeError):self.manager.read('get_positions',{})
        self.assertEqual(len(self.requests),1)
        conn=self.store.connection()
        with self.assertRaises(m.SafeError):self.manager.broker.request(conn,'GET','https://attacker.example')
        with self.assertRaises(m.SafeError):self.manager.broker.request(conn,'POST','/api/v0/equity/orders/market',body={})
        self.assertIsNone(m.NoRedirect().redirect_request(None,None,302,'',{},'https://attacker.example'))

    def test_read_pagination_argument_filter_and_keychain_storage(self):
        self.responses=[Response({'items':[],'nextPagePath':'/api/v0/equity/history/orders?cursor=123&limit=20'})]
        result=self.manager.read('get_order_history',{'ticker':'AAPL_US_EQ'})
        self.assertEqual(result['nextCursor'],123)
        self.assertIn('ticker=AAPL_US_EQ',self.requests[0].full_url)
        for args in ({'limit':51},{'limit':True},{'ticker':'../orders'},{'url':'http://evil'}):
            with self.assertRaises(m.SafeError):self.manager.read('get_order_history',args)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(os.stat(self.store.directory).st_mode&0o777,0o700)
        self.assertEqual(os.stat(self.store.file).st_mode&0o777,0o600)
        self.assertNotIn(b'test-api-secret',self.store.file.read_bytes())

    def test_rpc_lifecycle_and_scope(self):
        init=m.rpc(self.manager,{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})
        self.assertEqual(init['result']['serverInfo']['title'],'Trading212 Connection')
        self.assertEqual(init['result']['protocolVersion'],'2025-11-25')
        self.assertIsNone(m.rpc(self.manager,{'jsonrpc':'2.0','method':'notifications/initialized'}))
        listing=m.rpc(self.manager,{'jsonrpc':'2.0','id':2,'method':'tools/list'})
        self.assertEqual(len(listing['result']['tools']),13)
        for row in listing['result']['tools']:
            self.assertNotIn('secret',row['inputSchema']['properties'])
            self.assertNotIn('key',row['inputSchema']['properties'])
        self.assertEqual(m.rpc(self.manager,{'jsonrpc':'2.0','id':3,'method':'permissions'})['error']['code'],-32601)

    def test_order_fields_match_official_api(self):
        schema=json.loads((ROOT / 'tests/fixtures/trading212-order-schema.json').read_text())
        names={'market':'MarketRequest','limit':'LimitRequest','stop':'StopRequest','stop_limit':'StopLimitRequest'}
        for kind,fields in m.ORDER_FIELDS.items():
            self.assertEqual(fields,set(schema['components']['schemas'][names[kind]]['properties']))
            self.assertIn('post',schema['paths']['/api/v0/equity/orders/'+kind])
        self.assertIn('delete',schema['paths']['/api/v0/equity/orders/{id}'])


if __name__=='__main__':unittest.main(verbosity=2)
