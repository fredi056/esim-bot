"""Offline integration checks. Extract definitions, never import bot startup.

SQLite uses shared in-memory databases; all bank/supplier/Telegram calls are fakes.
Run: python -m unittest discover -s tests -v
"""
import ast
import hashlib
import html
import io
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import tempfile
import time
import unittest
import os
from contextlib import closing, redirect_stdout
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
                       DB_PATH=self.uri, MINI_APP_URL='https://example.com',
                       AVITO_SOURCE_CODE='avito_manual',AVITO_TOKEN_PREFIX='avito_',
                       AVITO_LINK_TTL_SECONDS=7*24*60*60,AVITO_TOKEN_BYTES=24)
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(ROOT/'bot.py'), 'exec'), self.ns)
        self.ns['_payment_db'] = lambda: sqlite3.connect(self.uri, uri=True)
        self.ns.update(conn=self.db,cursor=self.db.cursor(),avito_sale_mode={},EMOJI={})
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
                        payment_provider='tochka',payment_operation_id='op1',payment_status='CREATED',
                        supplier_product_id=796,supplier_variation_id=809)
        defaults.update(fields)
        columns = ','.join(defaults)
        cur = self.db.execute(f"INSERT INTO orders({columns}) VALUES({','.join('?' for _ in defaults)})", tuple(defaults.values()))
        self.db.commit()
        return cur.lastrowid

    def value(self, oid, column):
        return self.db.execute(f'SELECT {column} FROM orders WHERE id=?', (oid,)).fetchone()[0]

    def payload(self):
        return {'country':'Vietnam','tariff':'5GB / 30 дней','displayed_price':920,
                'legal_acceptance':{'offer_version':'1','personal_data_consent_version':'1','accepted_at':'now'}}

    def avito_items(self, count, offset=0):
        result=[]
        for index,item in enumerate(self.ns['SUPPLIER_CATALOG'][offset:offset+count]):
            result.append({'catalog':item,'display_country':item['country'],'supplier_tariff':'slug',
                           'days':item['refill_days'],'megabytes':item['refill_mb'],
                           'lpa_code':f'LPA:1$host$code{offset+index}',
                           'iccid':str(8900000000000000000+offset+index)})
        return result

    def banana_avito_block(self, catalog_item, index, *, iccid=None, include_lpa=True):
        iccid = iccid or str(8948010020008591000 + index)
        lines = [
            f"{catalog_item['country']} / Страна {index} - tariff-{index}",
            f"{catalog_item['refill_days']} дн / {catalog_item['refill_mb']} МБ",
        ]
        if include_lpa:
            lines.append(f"LPA:1$smdp.io$CODE-{index}")
        lines.append(f"ICCID: {iccid}")
        return '\n'.join(lines)

    def current_banana_avito_block(self, iccid, activation_code):
        return f"""Turkey / Турция - 10 GB / 30 days / mb 10240
30 дн / 10240 МБ

📱 Установка через QR:

⚡ Автоматическая установка:

📲 Android (ручной ввод):
LPA:1$rsp-eu.simlessly.com${activation_code}

🍏 iPhone (ввести вручную):
SM-DP+ Address: rsp-eu.simlessly.com
Код активации: {activation_code}

**ICCID:** {iccid}"""

    def claim_avito(self, deep_link, user_id):
        token=deep_link.split('start=avito_',1)[1]
        message=SimpleNamespace(from_user=SimpleNamespace(id=user_id,username='client',first_name='Client'))
        return self.call('claim_external_sale',token,message)

    def test_parse_one_two_and_three_banana_blocks(self):
        catalog = self.ns['SUPPLIER_CATALOG'][:3]
        for count in (1, 2, 3):
            with self.subTest(count=count):
                text = '\n\n'.join(self.banana_avito_block(item, index) for index, item in enumerate(catalog[:count], 1))
                parsed = self.call('parse_banana_avito_messages',text)
                self.assertEqual(len(parsed),count)
                self.assertEqual([item['iccid'] for item in parsed],
                                 [str(8948010020008591000 + index) for index in range(1,count+1)])
                self.assertEqual([item['lpa_code'] for item in parsed],
                                 [f'LPA:1$smdp.io$CODE-{index}' for index in range(1,count+1)])
        self.assertEqual(self.call('parse_banana_avito_message',text)['iccid'],parsed[0]['iccid'])

    def test_parse_current_banana_avito_format(self):
        activation='09D8F4C77E054DCA803ED99B3D678763'
        parsed=self.call('parse_banana_avito_messages',
                         self.current_banana_avito_block('8997250230001320742',activation))
        self.assertEqual(len(parsed),1)
        self.assertEqual(parsed[0]['catalog']['country'],'Turkey')
        self.assertEqual(parsed[0]['supplier_tariff'],'10 GB / 30 days / mb 10240')
        self.assertEqual(parsed[0]['days'],30)
        self.assertEqual(parsed[0]['megabytes'],10240)
        self.assertEqual(parsed[0]['iccid'],'8997250230001320742')
        self.assertEqual(parsed[0]['lpa_code'],f'LPA:1$rsp-eu.simlessly.com${activation}')

    def test_parse_two_current_banana_avito_blocks(self):
        blocks = [
            self.current_banana_avito_block('8997250230001320742','09D8F4C77E054DCA803ED99B3D678763'),
            self.current_banana_avito_block('8997250230001320743','19D8F4C77E054DCA803ED99B3D678764'),
        ]
        parsed=self.call('parse_banana_avito_messages','\n\n'.join(blocks))
        self.assertEqual([item['iccid'] for item in parsed],
                         ['8997250230001320742','8997250230001320743'])
        self.assertEqual([item['lpa_code'] for item in parsed],[
            'LPA:1$rsp-eu.simlessly.com$09D8F4C77E054DCA803ED99B3D678763',
            'LPA:1$rsp-eu.simlessly.com$19D8F4C77E054DCA803ED99B3D678764',
        ])

    def test_parse_markdown_iccid_variants(self):
        catalog=self.ns['SUPPLIER_CATALOG'][0]
        base=self.banana_avito_block(catalog,1)
        iccid='8948010020008591001'
        for value in (f'ICCID: {iccid}',f'**ICCID:** {iccid}',f'ICCID: **{iccid}**'):
            with self.subTest(value=value):
                parsed=self.call('parse_banana_avito_messages',
                                 re.sub(r'ICCID: \d+',value,base))
                self.assertEqual(parsed[0]['iccid'],iccid)

    def test_parse_banana_blocks_skips_only_damaged_block(self):
        catalog = self.ns['SUPPLIER_CATALOG'][:2]
        valid = self.banana_avito_block(catalog[0],1)
        damaged = self.banana_avito_block(catalog[1],2,include_lpa=False)
        parsed = self.call('parse_banana_avito_messages',valid+'\n\n'+damaged)
        self.assertEqual(len(parsed),1)
        self.assertEqual(parsed[0]['iccid'],'8948010020008591001')

    def test_handle_avito_message_adds_two_items_from_one_message(self):
        catalog = self.ns['SUPPLIER_CATALOG'][:2]
        self.ns['avito_sale_mode'][99]={'step':'banana_message','items':[]}
        self.ns['avito_sale_session_keyboard']=Mock(return_value='keyboard')
        message=SimpleNamespace(from_user=SimpleNamespace(id=99),chat=SimpleNamespace(id=99),
                                text='\n\n'.join(self.banana_avito_block(item,index)
                                                for index,item in enumerate(catalog,1)))
        self.call('handle_avito_banana_message',message)
        self.assertEqual(len(self.ns['avito_sale_mode'][99]['items']),2)
        self.assertIn('✅ Добавлено eSIM: 2',self.telegram.send_message.call_args.args[1])

    def test_handle_avito_message_skips_duplicate_iccid(self):
        catalog = self.ns['SUPPLIER_CATALOG'][:2]
        duplicate='8948010020008591999'
        self.ns['avito_sale_mode'][99]={'step':'banana_message','items':[]}
        self.ns['avito_sale_session_keyboard']=Mock(return_value='keyboard')
        message=SimpleNamespace(from_user=SimpleNamespace(id=99),chat=SimpleNamespace(id=99),
                                text='\n\n'.join(self.banana_avito_block(item,index,iccid=duplicate)
                                                for index,item in enumerate(catalog,1)))
        self.call('handle_avito_banana_message',message)
        self.assertEqual(len(self.ns['avito_sale_mode'][99]['items']),1)
        self.assertIn('⚠️ Пропущено дублей: 1',self.telegram.send_message.call_args.args[1])

    def test_avito_batches_of_one_two_and_three_claim_all_orders(self):
        offset=0
        for count,user_id in ((1,101),(2,102),(3,103)):
            with self.subTest(count=count):
                deep_link,order_ids=self.call('create_imported_avito_batch',self.avito_items(count,offset),99)
                result=self.claim_avito(deep_link,user_id)
                self.assertEqual(result['status'],'claimed')
                self.assertEqual(result['sale']['esim_count'],count)
                owners=[self.value(order_id,'user_id') for order_id in order_ids]
                self.assertEqual(owners,[user_id]*count)
            offset+=count

    def test_avito_duplicate_iccid_in_session_and_orders_is_rejected(self):
        parsed=self.avito_items(1)[0]
        self.assertTrue(self.call('avito_iccid_already_added',[parsed],parsed['iccid'],self.db.cursor()))
        self.order(supplier_iccid=parsed['iccid'])
        self.assertTrue(self.call('avito_iccid_already_added',[],parsed['iccid'],self.db.cursor()))

    def test_avito_partial_claim_rolls_back_every_order_and_sale(self):
        deep_link,order_ids=self.call('create_imported_avito_batch',self.avito_items(2,10),99)
        self.db.execute('UPDATE orders SET user_id=777 WHERE id=?',(order_ids[1],)); self.db.commit()
        result=self.claim_avito(deep_link,222)
        self.assertEqual(result['status'],'unavailable')
        self.assertEqual(self.value(order_ids[0],'user_id'),0)
        self.assertEqual(self.value(order_ids[1],'user_id'),777)
        self.assertEqual(self.db.execute('SELECT status FROM external_sales').fetchone()[0],'created')

    def test_avito_legacy_order_id_link_still_claims(self):
        oid=self.order(user_id=0,status='paid',supplier_status='issued',supplier_iccid='8900000000000000999')
        deep_link,_sale=self.call('create_external_sale','Turkey','1GB / 7 дней',0,99,oid)
        self.assertEqual(self.claim_avito(deep_link,333)['status'],'claimed')
        self.assertEqual(self.value(oid,'user_id'),333)

    def test_account_returns_all_esims_after_avito_claim(self):
        deep_link,order_ids=self.call('create_imported_avito_batch',self.avito_items(3,20),99)
        self.assertEqual(self.claim_avito(deep_link,444)['status'],'claimed')
        handle=tempfile.NamedTemporaryFile(suffix='.db',delete=False)
        path=handle.name; handle.close()
        try:
            disk=sqlite3.connect(path); self.db.backup(disk); disk.close()
            result=read_account(path,{'id':444,'first_name':'Client'},lambda uid:'ref',lambda uid:'share',lambda:'text','support')
            self.assertEqual({esim['id'] for esim in result['esims']},set(order_ids))
        finally:
            os.unlink(path)

    def set_active_partner(self, user_id, partner_user_id, *, until=None, first_source=''):
        self.db.execute(
            "INSERT INTO partners(code,name,telegram_user_id,commission_rate,is_active,created_at) "
            "VALUES('partner20','Partner',?,20,1,?)",
            (partner_user_id,int(time.time())),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO users(user_id,balance) VALUES(?,0)", (user_id,),
        )
        self.db.execute(
            "UPDATE users SET active_partner_code='partner20',active_partner_until=?,first_source=? WHERE user_id=?",
            (until if until is not None else int(time.time())+3600,first_source,user_id),
        )
        self.db.commit()

    def create_payment_order(self, user_id):
        result=self.call('create_mini_app_payment',{'id':user_id},self.payload())
        return result['order_id']

    def test_admin_order_excludes_active_partner(self):
        self.set_active_partner(99,50)
        oid=self.create_payment_order(99)
        self.assertEqual(self.value(oid,'partner_code'),'')
        self.assertEqual(self.value(oid,'partner_rate'),0)
        self.assertEqual(self.value(oid,'partner_commission'),0)

    def test_partner_self_purchase_has_no_commission(self):
        self.set_active_partner(1,1)
        oid=self.create_payment_order(1)
        self.assertEqual(self.value(oid,'partner_code'),'')
        self.assertEqual(self.value(oid,'partner_commission'),0)

    def test_regular_partner_client_keeps_twenty_percent_commission(self):
        self.set_active_partner(1,50)
        oid=self.create_payment_order(1)
        self.assertEqual(self.value(oid,'partner_code'),'partner20')
        self.assertEqual(self.value(oid,'partner_rate'),20)
        self.assertEqual(self.value(oid,'partner_commission'),184)

    def test_expired_partner_window_has_no_commission(self):
        self.set_active_partner(1,50,until=int(time.time())-1)
        oid=self.create_payment_order(1)
        self.assertEqual(self.value(oid,'partner_code'),'')
        self.assertEqual(self.value(oid,'partner_commission'),0)

    def test_ad_source_is_preserved_without_partner(self):
        self.db.execute("UPDATE users SET first_source='ad_campaign' WHERE user_id=1")
        self.db.commit()
        oid=self.create_payment_order(1)
        self.assertEqual(self.value(oid,'source_code'),'ad_campaign')
        self.assertEqual(self.value(oid,'partner_code'),'')
        self.assertEqual(self.value(oid,'partner_commission'),0)

    def test_amount_mismatch_is_not_paid(self):
        oid=self.order()
        self.bank.get_payment.return_value={'status':'APPROVED','amount':919}
        result=self.call('read_mini_app_payment',{'id':1},oid)
        self.assertEqual(result['status'],'payment_pending')
        self.assertEqual(self.value(oid,'status'),'payment_pending')

    def test_startup_cancels_only_obsolete_paid_test_orders(self):
        for order_id in (41,42):
            self.order(id=order_id,status='paid',supplier_status='error',
                       supplier_last_error='old',supplier_request_id='request',supplier_requested_at=123)
            self.db.execute(
                "INSERT INTO reminder_jobs(user_id,order_id,reminder_type,scheduled_at,status,created_at) "
                "VALUES(1,?,'payment_30m',1,'pending',1)", (order_id,),
            )
        other=self.order(id=43,status='paid',supplier_status='processing')
        self.db.commit()
        self.call('cancel_obsolete_test_orders',self.db)
        self.assertEqual([self.value(order_id,'status') for order_id in (41,42)],['cancel','cancel'])
        self.assertEqual([self.value(order_id,'supplier_status') for order_id in (41,42)],['cancelled','cancelled'])
        self.assertEqual(self.value(other,'status'),'paid')
        statuses=[row[0] for row in self.db.execute(
            'SELECT status FROM reminder_jobs WHERE order_id IN (41,42) ORDER BY order_id'
        )]
        self.assertEqual(statuses,['cancelled','cancelled'])

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
        oid=self.order(payment_operation_id='',payment_url='',payment_status='CREATING')
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['order_id'],oid)
        self.bank.create_payment.assert_not_called()

    def test_duplicate_after_twenty_minutes_reuses_link(self):
        oid=self.order(created_at=int(time.time())-3600,payment_url='https://bank.example/pay')
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['order_id'],oid)
        self.bank.create_payment.assert_not_called()

    def test_wrong_price_is_rejected(self):
        body=self.payload(); body['displayed_price']=1
        with self.assertRaises(ApiError): self.call('create_mini_app_payment',{'id':1},body)
        self.bank.create_payment.assert_not_called()

    def test_new_vietnam_purchase_gets_bank_link(self):
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.assertEqual(result['status'],'payment_pending')
        self.assertEqual(self.value(result['order_id'],'supplier_product_id'),796)
        self.assertEqual(self.value(result['order_id'],'supplier_variation_id'),809)
        self.assertEqual(self.bank.create_payment.call_count,1)
        self.assertEqual(self.bank.create_payment.call_args.args[1],920)

    def test_standard_purchase_uses_catalog_without_supplier_lookup(self):
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.supplier.assert_not_called()

    def test_admin_supplier_test_uses_live_standard_package(self):
        body={
            'country':'Технический тест','tariff':'Тех тариф','displayed_price':14,
            'plan_type':'supplier_test','legal_acceptance':self.payload()['legal_acceptance'],
        }
        result=self.call('create_mini_app_payment',{'id':99},body)
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.assertEqual(self.value(result['order_id'],'supplier_product_id'),317)
        self.assertEqual(self.value(result['order_id'],'supplier_variation_id'),330)
        self.assertEqual(self.bank.create_payment.call_args.args[1],14)
        self.supplier.assert_not_called()

    def test_active_refillable_line_allows_topup(self):
        self.assertTrue(self.call('_supplier_line_allows_topup',{
            'refillable':True,'status':'active',
        }))

    def test_standard_purchase_through_issue_and_delivery(self):
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        oid=result['order_id']
        self.bank.get_payment.return_value={'status':'APPROVED','amount':920}
        self.assertEqual(self.call('read_mini_app_payment',{'id':1},oid)['status'],'paid')
        self.supplier.create_line.return_value={'sim_card':{'iccid':'8985201234567890123',
            'lpa_code':'LPA:1$host$code','status':'active','remaining_usage_kb':5242880,'allowed_usage_kb':5242880,'remaining_days':30}}
        self.telegram.send_photo.return_value=SimpleNamespace(photo=[SimpleNamespace(file_id='qr1')])
        self.assertTrue(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.value(oid,'supplier_status'),'issued')
        self.assertEqual(self.value(oid,'supplier_iccid'),'8985201234567890123')
        self.assertEqual(self.value(oid,'supplier_line_provider'),'')
        self.assertIsNone(self.value(oid,'supplier_refillable'))
        self.assertGreater(self.value(oid,'supplier_delivered_at'),0)
        self.assertTrue(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.supplier.create_line.call_count,1)
        self.supplier.create_line.assert_called_once_with(oid,809,period_days=None)
        self.assertEqual(self.telegram.send_photo.call_count,1)

    def test_item_id_prefers_variation_and_falls_back_to_product(self):
        self.assertEqual(self.call('_supplier_item_id',317,330),330)
        self.assertEqual(self.call('_supplier_item_id',317,331),331)
        self.assertEqual(self.call('_supplier_item_id',317,0),317)

    def test_async_issue_polls_saved_request_without_second_post(self):
        oid=self.order(status='paid',country='Turkey',tariff='1GB / 7 дней',
                       supplier_product_id=317,supplier_variation_id=330)
        self.supplier.create_line.return_value={'request_id':'req-41','status':'processing'}
        self.assertFalse(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.value(oid,'supplier_request_id'),'req-41')
        self.assertEqual(self.value(oid,'supplier_status'),'processing')

        self.db.execute('UPDATE orders SET supplier_requested_at=0 WHERE id=?',(oid,)); self.db.commit()
        self.supplier.get_request.return_value={'request_id':'req-41','status':'processing'}
        self.assertFalse(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.supplier.create_line.call_count,1)
        self.supplier.get_request.assert_called_once_with(oid,'req-41')

        self.db.execute('UPDATE orders SET supplier_requested_at=0 WHERE id=?',(oid,)); self.db.commit()
        self.supplier.get_request.return_value={'request_id':'req-41','status':'completed','sim_card':{
            'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code','status':'active'}}
        self.telegram.send_photo.return_value=SimpleNamespace(photo=[SimpleNamespace(file_id='qr1')])
        self.assertTrue(self.call('provision_paid_supplier_order',oid))
        self.assertEqual(self.supplier.create_line.call_count,1)
        self.assertEqual(self.supplier.get_request.call_count,2)
        self.supplier.create_line.assert_called_once_with(oid,330,period_days=None)
        self.assertEqual(self.value(oid,'supplier_status'),'issued')

    def test_bank_setup_failure_does_not_wait_for_nonexistent_payment(self):
        oid=self.order(payment_operation_id='')
        self.bank.create_payment.side_effect=TochkaError('tochka_retailer_ambiguous')
        with self.assertRaises(ApiError) as caught:
            self.call('_create_bank_payment_for_order',oid,1,920,'test','https://x','https://x')
        self.assertEqual(caught.exception.code,'bank_setup_required')
        self.assertEqual(self.value(oid,'status'),'payment_error')

    def test_catalogued_supplier_product_does_not_call_supplier_before_payment(self):
        result=self.call('create_mini_app_payment',{'id':1},self.payload())
        self.assertEqual(result['payment_url'],'https://bank.example/pay')
        self.supplier.assert_not_called()
        self.bank.create_payment.assert_called_once()

    def test_unmapped_tariff_stops_before_payment(self):
        body={'country':'Vietnam','tariff':'50GB / 90 дней','displayed_price':12390,
              'legal_acceptance':self.payload()['legal_acceptance']}
        with self.assertRaises(ApiError) as caught:
            self.call('create_mini_app_payment',{'id':1},body)
        self.assertEqual(caught.exception.code,'supplier_product_unavailable')
        self.bank.create_payment.assert_not_called()

    def test_old_unmapped_pending_link_is_not_reopened(self):
        oid=self.order(tariff='50GB / 90 дней',price=12390,pay_amount=12390,
                       supplier_product_id=0,payment_url='https://bank.example/old')
        self.bank.get_payment.return_value={'status':'CREATED','amount':12390}
        result=self.call('read_mini_app_payment',{'id':1},oid)
        self.assertEqual(result['status'],'payment_error')
        self.assertEqual(result['payment_status'],'SUPPLIER_UNMAPPED')
        self.assertEqual(result['payment_url'],'')

    def test_topup_create_checks_existing_line_and_price(self):
        parent=self.issued()
        self.supplier.get_details.return_value={'sim_card':{'iccid':'8985201234567890123','status':'active'}}
        body={'option_id':'p796v809','legal_acceptance':self.payload()['legal_acceptance']}
        result=self.call('create_mini_app_topup',{'id':1},parent,body)
        self.assertEqual(self.value(result['order_id'],'order_kind'),'topup')
        self.assertEqual(self.value(result['order_id'],'parent_order_id'),parent)
        self.assertEqual(result['payment_url'],'https://bank.example/pay')

    def test_expired_line_cannot_create_topup_payment(self):
        parent=self.issued()
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
        self.supplier.get_details.side_effect=BananaError('banana_unavailable')
        self.assertTrue(self.call('apply_paid_supplier_topup',oid))
        self.assertEqual(self.value(oid,'supplier_status'),'issued')
        self.assertTrue(self.call('apply_paid_supplier_topup',oid))
        self.assertEqual(self.supplier.refill.call_count,1)
        self.supplier.refill.assert_called_once_with(oid,'8985201234567890123',809)


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

    def test_banana_v2_request_ids_stay_stable_and_unique(self):
        create_41=BananaClient.request_id(41,330,BananaClient.CREATE_REQUEST_ID_VERSION)
        create_42=BananaClient.request_id(42,331,BananaClient.CREATE_REQUEST_ID_VERSION)
        self.assertEqual(create_41,hashlib.sha256(b'41/330/standard-v2').hexdigest())
        self.assertEqual(create_41,BananaClient.request_id(41,330,'standard-v2'))
        self.assertNotEqual(create_41,create_42)
        self.assertNotEqual(create_41,BananaClient.request_id(41,1,'standard'))

        refill_41=BananaClient.request_id(41,330,BananaClient.REFILL_REQUEST_ID_VERSION)
        self.assertEqual(refill_41,hashlib.sha256(b'41/330/topup-v2').hexdigest())
        self.assertEqual(refill_41,BananaClient.request_id(41,330,'topup-v2'))
        self.assertNotEqual(refill_41,create_41)

    def test_banana_create_line_retry_reuses_request_id_and_safe_logs(self):
        client=BananaClient()
        response={'sim_card':{'iccid':'8985201234567890123','lpa_code':'LPA:1$host$secret'}}
        client._request=Mock(return_value=(response,200))
        output=io.StringIO()
        with redirect_stdout(output):
            client.create_line(41,330)
            client.create_line(41,330)
        self.assertEqual(client._request.call_count,2)
        first_headers=client._request.call_args_list[0].args[3]
        second_headers=client._request.call_args_list[1].args[3]
        self.assertEqual(first_headers['X-Partner-Request-ID'],second_headers['X-Partner-Request-ID'])
        self.assertEqual(
            first_headers['X-Partner-Request-ID'],
            hashlib.sha256(b'41/330/standard-v2').hexdigest(),
        )
        self.assertEqual(first_headers['X-Partner-Order-ID'],'41')
        self.assertNotIn('8985201234567890123',output.getvalue())
        self.assertNotIn('LPA:1$host$secret',output.getvalue())
        self.assertEqual(output.getvalue().count('BANANA_CREATE_OK order_id=41 http_status=200'),2)

    def test_banana_create_line_error_log_includes_safe_supplier_detail(self):
        client=BananaClient()
        client._request=Mock(side_effect=BananaError(
            'banana_http_404','Supplier operation is not allowed.',
            http_status=404,supplier_code='operation_not_allowed',
        ))
        output=io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError):
            client.create_line(41,330)
        self.assertIn(
            'BANANA_CREATE_ERROR order_id=41 item_id=330 '
            'http_status=404 supplier_code="operation_not_allowed" '
            'message="Supplier operation is not allowed."',
            output.getvalue(),
        )

    def test_banana_create_line_error_log_redacts_installation_secrets(self):
        client=BananaClient()
        client._request=Mock(side_effect=BananaError(
            'banana_http_422',
            'Failed\nLPA:1$host$secret activation_code=very-secret ICCID 8985201234567890123',
            http_status=422,supplier_code='invalid_product',
        ))
        output=io.StringIO()
        with redirect_stdout(output), self.assertRaises(BananaError):
            client.create_line(41,330)
        logged=output.getvalue()
        self.assertIn('[REDACTED_LPA]',logged)
        self.assertIn('activation_code=[REDACTED_ACTIVATION_CODE]',logged)
        self.assertIn('[REDACTED_ICCID]',logged)
        self.assertNotIn('8985201234567890123',output.getvalue())
        self.assertNotIn('LPA:1$host$secret',output.getvalue())
        self.assertNotIn('very-secret',output.getvalue())

    def test_banana_create_ordinary_payload_uses_only_item_id_and_count(self):
        client=BananaClient()
        client._request=Mock(return_value=({
            'sim_card':{'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code'},
        },200))
        client.create_line(41,330)
        self.assertEqual(client._request.call_args.args[2],{'item_id':330,'count':1})

    def test_banana_create_unlimited_payload_includes_period_days(self):
        client=BananaClient()
        client._request=Mock(return_value=({
            'sim_card':{'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code'},
        },200))
        client.create_line(41,900,period_days=7)
        self.assertEqual(client._request.call_args.args[2],{
            'item_id':900,'count':1,'period_days':7,
        })

    def test_banana_create_accepts_async_response(self):
        client=BananaClient()
        client._request=Mock(return_value=({'request_id':'req-41','status':'processing'},202))
        self.assertEqual(client.create_line(41,330),{
            'request_id':'req-41','status':'processing',
        })

    def test_banana_create_unwraps_sync_wrapper(self):
        client=BananaClient()
        card={'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code'}
        client._request=Mock(return_value=({
            'success':True,'errorCode':0,'errorMsg':'','obj':{'sim_card':card},
        },200))
        self.assertEqual(client.create_line(41,330),{'sim_card':card})

    def test_banana_create_normalizes_direct_wrapped_sim_card(self):
        client=BananaClient()
        card={'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code'}
        client._request=Mock(return_value=({'success':True,'obj':card},200))
        self.assertEqual(client.create_line(41,330),{'sim_card':card})

    def test_banana_create_unwraps_async_wrapper(self):
        client=BananaClient()
        client._request=Mock(return_value=({
            'success':True,'obj':{'request_id':'abc/123=','status':'processing'},
        },200))
        self.assertEqual(client.create_line(42,331),{
            'request_id':'abc/123=','status':'processing',
        })

    def test_banana_wrapper_error_preserves_supplier_code(self):
        client=BananaClient()
        with self.assertRaises(BananaError) as caught:
            client._unwrap_response({
                'success':False,'errorCode':'some_code','errorMsg':'some error','obj':None,
            })
        self.assertEqual(caught.exception.code,'some_code')
        self.assertEqual(caught.exception.detail,'some error')
        self.assertEqual(caught.exception.supplier_code,'some_code')

    def test_banana_request_reference_accepts_documented_open_charset(self):
        client=BananaClient()
        accepted=(
            ('req-abc_123','req-abc_123'),
            ('folder/request','folder/request'),
            ('request==','request=='),
            ('https://banana.example/request?id=1&next=%2Fdone#result',
             'https://banana.example/request?id=1&next=%2Fdone#result'),
            (1234567890,'1234567890'),
        )
        for raw, expected in accepted:
            with self.subTest(raw=raw):
                self.assertEqual(client._request_reference(raw),expected)

    def test_banana_request_reference_rejects_empty_and_control_characters(self):
        client=BananaClient()
        for raw in (None,'','   ','request\n','request\tid','request\x00id','request\x7fid'):
            with self.subTest(raw=raw), self.assertRaises(BananaError):
                client._request_reference(raw)

    def test_banana_create_response_diagnostic_is_safe(self):
        client=BananaClient()
        request_id='https://banana.example/request/abc==?next=%2Fdone#result'
        client._request=Mock(return_value=({
            'request_id':request_id,'status':'processing','metadata':{'ignored':'raw'},
        },200))
        output=io.StringIO()
        with redirect_stdout(output):
            result=client.create_line(41,330)
        logged=output.getvalue()
        self.assertEqual(result,{'request_id':request_id,'status':'processing'})
        self.assertIn(
            'BANANA_CREATE_RESPONSE order_id=41 http_status=200 '
            'keys=metadata,request_id,status status=processing '
            'request_id_present=true request_id_type=str '
            f'request_id_length={len(request_id)}',
            logged,
        )
        self.assertNotIn(request_id,logged)
        self.assertNotIn("{'ignored': 'raw'}",logged)

    def test_banana_get_request_returns_completed_sim_card(self):
        client=BananaClient()
        client._request=Mock(return_value={
            'request_id':'req-41','status':'completed','result':{'sim_cards':[{
                'iccid':'8985201234567890123','lpa_code':'LPA:1$host$code',
            }]},
        })
        result=client.get_request(41,'req-41')
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['sim_card']['iccid'],'8985201234567890123')

    def test_banana_refill_ordinary_payload_uses_only_item_id(self):
        client=BananaClient()
        client._request=Mock(return_value={'success':True,'iccid':'8985201234567890123'})
        client.refill(1,'8985201234567890123',807)
        payload=client._request.call_args.args[2]
        self.assertEqual(payload,{'item_id':807})
        headers=client._request.call_args.args[3]
        self.assertEqual(
            headers['X-Partner-Request-ID'],
            hashlib.sha256(b'1/807/topup-v2').hexdigest(),
        )

    def test_banana_refill_unlimited_payload_includes_period_days(self):
        client=BananaClient()
        client._request=Mock(return_value={'success':True,'iccid':'8985201234567890123'})
        client.refill(1,'8985201234567890123',900,period_days=7)
        self.assertEqual(client._request.call_args.args[2],{'item_id':900,'period_days':7})

    def test_banana_rejects_false_refill_success(self):
        client=BananaClient();client._request=Mock(return_value={'success':False})
        with self.assertRaises(BananaError):client.refill(1,'8985201234567890123',2)

    def test_no_runtime_resolve_endpoint_or_method(self):
        source=(ROOT/'banana_api.py').read_text(encoding='utf-8') + (ROOT/'bot.py').read_text(encoding='utf-8')
        self.assertFalse(hasattr(BananaClient,'resolve_' + 'product'))
        self.assertNotIn('/product/' + 'resolve',source)


    def test_details_reject_foreign_iccid(self):
        client=BananaClient();client._request=Mock(return_value={'sim_card':{'iccid':'8985201234567890000'}})
        with self.assertRaises(BananaError):client.get_details('8985201234567890123')


if __name__ == '__main__':
    unittest.main()
