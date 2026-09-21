"""Exercise the real local HTTP API with synthetic signed Telegram data."""
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account_api import ApiError, create_account_server


class HttpAuthTest(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.webhooks = []
        def create(user, body):
            self.created.append(user['id'])
            return {'order_id':7,'status':'payment_pending','payment_status':'CREATED','payment_url':'https://bank.example/pay'}
        def webhook(body):
            self.webhooks.append(body)
            raise ApiError(503, 'temporary_database_failure')
        self.server=create_account_server('127.0.0.1',0,'synthetic-test-token',lambda user: {},lambda *args: None,
                                          create_payment=create,accept_webhook=webhook)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def signed(self, age=0):
        values={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':123,'first_name':'Test'})}
        secret=hmac.new(b'WebAppData',b'synthetic-test-token',hashlib.sha256).digest()
        check='\n'.join(f'{k}={values[k]}' for k in sorted(values))
        values['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
        return urlencode(values)

    def post(self, data, path='/api/payments/create'):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=2)
        try:
            body=data if isinstance(data,str) else json.dumps(data)
            conn.request('POST',path,body,{'Content-Type':'application/json'})
            response=conn.getresponse()
            return response.status,json.loads(response.read())
        finally:
            conn.close()

    def test_signed_telegram_session_can_create_checkout(self):
        status,result=self.post({'init_data':self.signed()})
        self.assertEqual(status,200)
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.assertEqual(self.created,[123])

    def test_missing_or_unsafe_telegram_data_cannot_create_payment(self):
        for payload in [{'init_data':''},{'user':{'id':123}}]:
            status,result=self.post(payload)
            self.assertEqual(status,401)
        self.assertEqual(self.created,[])

    def test_expired_session_requires_fresh_telegram_launch(self):
        status,result=self.post({'init_data':self.signed(age=3601)})
        self.assertEqual(status,401)
        self.assertEqual(result['error'],'telegram_auth_required')
        self.assertEqual(self.created,[])

    def test_duplicate_signed_fields_are_rejected(self):
        status,_=self.post({'init_data':self.signed()+'&auth_date=0'})
        self.assertEqual(status,401)

    def test_webhook_failure_remains_retryable(self):
        status,_=self.post('synthetic.jwt.token','/api/payments/tochka/webhook')
        self.assertEqual(status,503)
        self.assertEqual(self.webhooks,['synthetic.jwt.token'])


if __name__=='__main__':unittest.main()
