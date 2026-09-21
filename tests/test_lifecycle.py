"""Offline integration checks. Extract definitions, never import bot startup.

SQLite uses shared in-memory databases; all bank/supplier/Telegram calls are fakes.
Run: python -m unittest discover -s tests -v
"""
import ast
import hashlib
import html
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import Mock
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from account_api import ApiError, read_account, safe_install_url
from banana_api import BananaClient, BananaError
from tochka_api import TochkaClient, TochkaError

TREE = ast.parse((ROOT / 'bot.py').read_text(encoding='utf-8'))


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        self.uri = 'file:test_' + secrets.token_hex(10) + '?mode=memory&cache=shared'
        self.db = sqlite3.connect(self.uri, uri=True)
        for node in TREE.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr == 'execute' and call.args:
                    try:
                        sql = ast.literal_eval(call.args[0])
                    except (ValueError, TypeError):
                        continue
                    if isinstance(sql, str) and sql.strip().startswith('CREATE TABLE'):
                        self.db.execute(sql)
                if isinstance(call.func, ast.Name) and call.func.id == 'add_column_if_not_exists':
                    table, column, definition = map(ast.literal_eval, call.args)
                    if column not in [r[1] for r in self.db.execute(f'PRAGMA table_info({table})')]:
                        self.db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        self.db.commit()
        definitions = []
        for node in TREE.body:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
                definitions.append(node)
        self.ns = dict(globals())
        self.ns.update(__file__=str(ROOT/'bot.py'), ADMIN_ID=99, REF_BONUS=100,
                       DEFAULT_PARTNER_RATE=20, ADMIN_ESIM_15M_DELAY=900, TOCHKA_PAYMENTS_ENABLED=True,
                       DB_PATH=self.uri, MINI_APP_URL='https://example.com')
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(ROOT/'bot.py'), 'exec'), self.ns)
        self.ns['_payment_db'] = lambda: sqlite3.connect(self.uri, uri=True)
        self.bank = Mock(configured=True)
        self.supplier = Mock(configured=True)
        self.telegram = Mock()
        self.ns.update(tochka=self.bank, banana=self.supplier, bot=self.telegram,
                       threading=SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None)))
        self.ns['COUNTRY_PRICES'] = json.loads((ROOT/'country_prices.json').read_text(encoding='utf-8'))
        self.ns['SUPPLIER_CATALOG_FILE'] = ROOT/'supplier_catalog.json'
        self.ns['UNLIMITED_FILE'] = ROOT/'unlimited_catalog.json'
        self.ns['UNLIMITED_PLANS'] = self.ns['load_unlimited_catalog']()
        self.ns['SUPPLIER_CATALOG'] = self.ns['load_supplier_catalog']()
        for name in ['_notify_admin_throttled','_notify_admin_safe','schedule_reminder',
                     'schedule_referral_bonus_awarded_job','schedule_partner_sale_job','main_keyboard']:
            self.ns[name] = Mock()
        self.ns['format_user_for_admin'] = str
        self.ns['read_mini_app_account'] = lambda user: {'esims': []}
        self.bank.create_payment.return_value = {'operationId':'op1','paymentLink':'https://bank.example/pay','status':'CREATED'}
        self.db.execute('INSERT INTO users(user_id,balance) VALUES(1,0)')
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def call(self, name, *args, **kwargs):
        return self.ns[name](*args, **kwargs)

    def order(self, **fields):
        defaults = dict(user_id=1,text='test',price=920,pay_amount=920,status='payment_pending',
                        country='Vietnam',tariff='5GB / 30 дней',created_at=int(time.time()),
                        payment_provider='tochka',payment_operation_id='op1',payment_status='CREATED')
        defaults.update(fields)
        columns = ','.join(defaults)
        cur = self.db.execute(f"INSERT INTO orders({columns}) VALUES({','.join('?' for _ in defaults)})", tuple(defaults.values()))
        self.db.commit()
        return cur.lastrowid

    def value(self, oid, column):
        return self.db.execute(f'SELECT {column} FROM orders WHERE id=?', (oid,)).fetchone()[0]

    def product(self, unlimited=False, refillable=True):
        return {'product_id':796,'variation_id':809,'partner_provider':'supplier_unlimited' if unlimited else 'supplier_standard',
                'unlimited':unlimited,'refillable':refillable,'refill_mb':0 if unlimited else 5120,
                'refill_days':1 if unlimited else 30,'period_min':1,'period_max':30}

    def payload(self):
        return {'country':'Vietnam','tariff':'5GB / 30 дней','displayed_price':920,
                'legal_acceptance':{'offer_version':'1','personal_data_consent_version':'1','accepted_at':'now'}}

    def test_amount_mismatch_is_not_paid(self):
        oid=self.order()
        self.bank.get_payment.return_value={'status':'APPROVED','amount':919}
        result=self.call('read_mini_app_payment',{'id':1},oid)
        self.assertEqual(result['status'],'payment_pending')
        self.assertEqual(self.value(oid,'status'),'payment_pending')

    def test_paid_duplicates_verify_amount_and_operation(self):
        oid=self.order(status='paid')
        self.assertFalse(self.call('_mark_bank_order_paid',oid,'other',920))
        self.assertFalse(self.call('_mark_bank_order_paid',oid,'op1',None))

    def test_late_approved_recovers_old_expired_order(self):
        oid=self.order(status='payment_failed',payment_status='EXPIRED',created_at=int(time.time())-100000)
        self.assertTrue(self.call('_mark_bank_order_paid',oid,'op1',920))
        self.assertEqual(self.value(oid,'status'),'paid')
        self.assertEqual(self.db.execute('SELECT count(*) FROM payment_notifications').fetchone()[0],2)
        self.assertTrue(self.call('_mark_bank_order_paid',oid,'op1',920))
        self.assertEqual(self.db.execute('SELECT count(*) FROM payment_notifications').fetchone()[0],2)

    def test_telegram_failure_does_not_block_settlement(self):
        self.telegram.send_message.side_effect=RuntimeError('offline')
        oid=self.order(supplier_product_id=796)
        self.assertTrue(self.call('_mark_bank_order_paid',oid,'op1',920))
        self.assertEqual(self.value(oid,'status'),'paid')
        self.telegram.send_message.assert_not_called()

    def test_created_is_not_expired_by_local_clock(self):
        oid=self.order(created_at=int(time.time())-100000,payment_url='https://bank.example/pay')
        self.bank.get_payment.return_value={'status':'CREATED'}
        result=self.call('read_mini_app_payment',{'id':1},oid)
        self.assertEqual(result['status'],'payment_pending')
        self.assertEqual(result['payment_url'],'https://bank.example/pay')

    def test_bank_expired_closes_order(self):
        oid=self.order()
        self.bank.get_payment.return_value={'status':'EXPIRED'}
        self.call('_sync_bank_payment',oid)
        self.assertEqual(self.value(oid,'status'),'payment_failed')

    def test_bank_outage_keeps_pending(self):
        oid=self.order()
        self.bank.get_payment.side_effect=TochkaError('tochka_unavailable')
        self.assertEqual(self.call('read_mini_app_payment',{'id':1},oid)['status'],'payment_pending')

    def test_status_never_reads_foreign_order(self):
        oid=self.order(user_id=2)
        with self.assertRaises(ApiError): self.call('read_mini_app_payment',{'id':1},oid)
        self.bank.get_payment.assert_not_called()

    def test_status_checks_are_throttled(self):
        oid=self.order()
        self.bank.get_payment.return_value={'status':'CREATED'}
        self.call('_sync_bank_payment',oid); self.call('_sync_bank_payment',oid)
        self.assertEqual(self.bank.get_payment.call_count,1)

    def test_create_timeout_recovers_same_order(self):
        oid=self.order(payment_operation_id='',payment_link_id='esimlime-1')
        self.bank.create_payment.side_effect=TochkaError('tochka_unavailable')
        result=self.call('_create_bank_payment_for_order',oid,1,920,'test','https://x','https://x')
        self.assertEqual(result['payment_status'],'UNKNOWN')
        self.db.execute('UPDATE orders SET created_at=? WHERE id=?',(int(time.time())-120,oid)); self.db.commit()
        self.bank.find_payment.return_value={'operationId':'op1','paymentLink':'https://bank.example/pay','status':'APPROVED','amount':920}
        self.call('_sync_bank_payment',oid)
        self.assertEqual(self.value(oid,'status'),'paid')

    def test_webhook_before_create_response_is_accepted(self):
        oid=self.order(payment_operation_id='')
        self.assertTrue(self.call('_mark_bank_order_paid',oid,'op1',920))
        self.assertEqual(self.value(oid,'payment_operation_id'),'op1')

    def test_duplicate_during_creation_does_not_create_another_payment(self):
        self.supplier.resolve_product.return_value=self.product()
        oid=self.order(payment_operation_id='',payment_url='',payment_status='CREATING')
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['order_id'],oid)
        self.bank.create_payment.assert_not_called()

    def test_duplicate_after_twenty_minutes_reuses_link(self):
        self.supplier.resolve_product.return_value=self.product()
        oid=self.order(created_at=int(time.time())-3600,payment_url='https://bank.example/pay')
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['order_id'],oid)
        self.bank.create_payment.assert_not_called()

    def test_wrong_price_is_rejected(self):
        body=self.payload(); body['displayed_price']=1
        with self.assertRaises(ApiError): self.call('create_mini_app_payment',{'id':1},body)
        self.bank.create_payment.assert_not_called()

    def test_new_vietnam_purchase_gets_bank_link(self):
        self.supplier.resolve_product.return_value=self.product()
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.assertEqual(result['status'],'payment_pending')
        self.assertEqual(self.value(result['order_id'],'supplier_product_id'),796)
        self.assertEqual(self.bank.create_payment.call_count,1)
        self.assertEqual(self.bank.create_payment.call_args.args[1],920)

    def test_standard_purchase_through_issue_and_delivery(self):
        self.supplier.resolve_product.return_value=self.product()
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        oid=result['order_id']
        self.bank.get_payment.return_value={'status':'APPROVED','amount':920}
        self.assertEqual(self.call('read_mini_app_payment',{'id':1},oid)['status'],'paid')
        self.supplier.create_line.return_value={'sim_card':{'iccid':'8985201234567890123',
            'lpa_code':'LPA:1$host$code','status':'active','remaining_usage_kb':5242880,'allowed_usage_kb':5242880,'remaining_days':30}}
        self.telegram.send_photo.return_value=SimpleNamespace(photo=[SimpleNamespace(file_id='qr1')])
        self.assertTrue(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.value(oid,'supplier_status'),'issued')
        self.assertGreater(self.value(oid,'supplier_delivered_at'),0)
        self.assertTrue(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.supplier.create_line.call_count,1)
        self.assertEqual(self.telegram.send_photo.call_count,1)

    def test_bank_setup_failure_does_not_wait_for_nonexistent_payment(self):
        oid=self.order(payment_operation_id='')
        self.bank.create_payment.side_effect=TochkaError('tochka_retailer_ambiguous')
        with self.assertRaises(ApiError) as caught:
            self.call('_create_bank_payment_for_order',oid,1,920,'test','https://x','https://x')
        self.assertEqual(caught.exception.code,'bank_setup_required')
        self.assertEqual(self.value(oid,'status'),'payment_error')

    def test_missing_supplier_product_stops_before_payment(self):
        self.supplier.resolve_product.side_effect=BananaError('banana_http_404')
        with self.assertRaises(ApiError):self.call('create_mini_app_payment',{'id':1},self.payload())
        self.bank.create_payment.assert_not_called()

    def test_topup_create_checks_existing_line_and_price(self):
        parent=self.issued()
        self.supplier.resolve_product.return_value=self.product()
        self.supplier.get_details.return_value={'sim_card':{'iccid':'8985201234567890123','status':'active'}}
        body={'option_id':'p796v809','legal_acceptance':self.payload()['legal_acceptance']}
        result=self.call('create_mini_app_topup',{'id':1},parent,body)
        self.assertEqual(self.value(result['order_id'],'order_kind'),'topup')
        self.assertEqual(self.value(result['order_id'],'parent_order_id'),parent)
        self.assertEqual(result['payment_url'],'https://bank.example/pay')

    def test_expired_line_cannot_create_topup_payment(self):
        parent=self.issued()
        self.supplier.resolve_product.return_value=self.product()
        self.supplier.get_details.return_value={'sim_card':{'iccid':'8985201234567890123','status':'expired'}}
        with self.assertRaises(ApiError):
            self.call('create_mini_app_topup',{'id':1},parent,{'option_id':'p796v809','legal_acceptance':self.payload()['legal_acceptance']})
        self.bank.create_payment.assert_not_called()

    def test_standard_catalog_all_mapped_tariffs_validate(self):
        for item in self.ns['SUPPLIER_CATALOG']:
            if item['product_id']==40: continue
            payload=dict(country=item['country'],tariff=item['tariff'],displayed_price=item['price'])
            result=self.call('_validated_api_order',payload,1)
            self.assertEqual(result['supplier_product_id'],item['product_id'])
            self.assertEqual(result['supplier_variation_id'],item['variation_id'])


    def issued(self, **fields):
        defaults=dict(status='paid',supplier_status='issued',supplier_product_id=796,supplier_variation_id=809,
                      supplier_iccid='8985201234567890123',supplier_line_provider='supplier_standard',
                      supplier_refillable=1,supplier_provider_status='active',supplier_remaining_usage_kb=1024,
                      supplier_allowed_usage_kb=2048,supplier_remaining_days=4,esim_sent_at=1)
        defaults.update(fields)
        return self.order(**defaults)

    def test_partial_balance_preserves_existing_amounts(self):
        oid=self.issued()
        self.call('_persist_supplier_details',oid,{'iccid':'8985201234567890123','status':'active'})
        self.assertEqual(self.value(oid,'supplier_remaining_usage_kb'),1024)
        self.assertEqual(self.value(oid,'supplier_remaining_days'),4)

    def test_other_iccid_cannot_overwrite_balance(self):
        oid=self.issued()
        with self.assertRaises(BananaError):
            self.call('_persist_supplier_details',oid,{'iccid':'8985201234567890999','remaining_usage_kb':1})
        self.assertEqual(self.value(oid,'supplier_remaining_usage_kb'),1024)

    def test_expired_or_not_refillable_line_has_no_topups(self):
        for fields in [dict(supplier_provider_status='expired'),dict(supplier_refillable=0),dict(supplier_expire_at='2020-01-01T00:00:00Z')]:
            oid=self.issued(**fields)
            self.assertEqual(self.call('_topup_options_for_order',self.db,1,oid),[])


    def test_successful_refill_survives_balance_failure(self):
        parent=self.issued()
        oid=self.order(status='paid',order_kind='topup',parent_order_id=parent,supplier_iccid='8985201234567890123',
                       supplier_product_id=796,supplier_variation_id=809,topup_mb=5120,topup_days=30)
        self.supplier.resolve_product.return_value=self.product()
        self.supplier.get_details.side_effect=BananaError('banana_unavailable')
        self.assertTrue(self.call('apply_paid_supplier_topup',oid))
        self.assertEqual(self.value(oid,'supplier_status'),'issued')
        self.assertTrue(self.call('apply_paid_supplier_topup',oid))
        self.assertEqual(self.supplier.refill.call_count,1)


    def test_photo_is_not_repeated_after_instruction_failure(self):
        oid=self.issued(supplier_lpa_code='LPA:1$host$code',install_url='https://esimsetup.apple.com/esim_qrcode_provisioning?carddata=LPA')
        self.telegram.send_photo.return_value=SimpleNamespace(photo=[SimpleNamespace(file_id='photo1')])
        self.telegram.send_message.side_effect=RuntimeError('telegram offline')
        self.assertFalse(self.call('deliver_supplier_order',oid))
        self.assertGreater(self.value(oid,'supplier_delivery_photo_at'),0)
        self.db.execute('UPDATE orders SET supplier_delivery_next_at=0 WHERE id=?',(oid,));self.db.commit()
        self.telegram.send_message.side_effect=None
        self.assertTrue(self.call('deliver_supplier_order',oid))
        self.assertEqual(self.telegram.send_photo.call_count,1)

    def test_simultaneous_delivery_lease_has_one_owner(self):
        oid=self.issued()
        self.assertTrue(self.call('_claim_supplier_delivery',oid))
        self.assertFalse(self.call('_claim_supplier_delivery',oid))


class ClientTest(unittest.TestCase):
    def test_tochka_does_not_accept_another_operation(self):
        client=TochkaClient()
        client._request=Mock(return_value={'Data':{'Operation':[{'operationId':'other','status':'APPROVED','amount':920}]}})
        with self.assertRaises(TochkaError):client.get_payment('wanted')

    def test_tochka_operation_response(self):
        client=TochkaClient()
        client._request=Mock(return_value={'Data':{'Operation':[{'operationId':'wanted','status':'APPROVED','amount':920}]}})
        self.assertEqual(client.get_payment('wanted')['amount'],920)

    def test_banana_request_ids_stay_stable(self):
        self.assertEqual(BananaClient.request_id(123),hashlib.sha256(b'123/1/standard').hexdigest())
        self.assertNotEqual(BananaClient.request_id(123),BananaClient.request_id(124))

    def test_banana_rejects_false_refill_success(self):
        client=BananaClient();client._request=Mock(return_value={'success':False})
        with self.assertRaises(BananaError):client.refill(1,'8985201234567890123',1,2)


    def test_details_reject_foreign_iccid(self):
        client=BananaClient();client._request=Mock(return_value={'sim_card':{'iccid':'8985201234567890000'}})
        with self.assertRaises(BananaError):client.get_details('8985201234567890123')


if __name__ == '__main__':
    unittest.main()
