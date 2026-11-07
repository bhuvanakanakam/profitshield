import copy
import csv
import io
import json
import re
import socket
import unittest
from unittest.mock import patch

import server
from input_security import (BODY_LIMIT, CSV_LIMIT, RequestLimiter, number, upload,
                            validate_origin, validate_payload, validate_tool_args)
from test_http_security import HTTPFixture, valid_payload


def pos_csv(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['Order', 'Date', 'Time', 'Gross Sales', 'Itemization'])
    writer.writerows(rows)
    return out.getvalue()


class InputHTTPTests(HTTPFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with patch.object(server, 'KEY', ''):
            server.init_db()

    def test_malformed_json_rejected_before_dispatch(self):
        for raw in [b'{', b'[]', b'null', b'false', b'NaN', b'{"text":NaN}', b'{"text":Infinity}',
                    b'{"text":1e999}', b'{"text":"a","text":"b"}', b'\xff', b'['*1100+b']'*1100]:
            with self.subTest(raw=raw[:30]), patch.object(server, 'import_csv') as importer:
                self.assertEqual(self.request('POST', '/api/import', raw=raw)[0], 400)
                importer.assert_not_called()

    def test_invalid_shapes_dates_and_values(self):
        invalid = [
            ('/api/import', {'text': [], 'filename': 'square.csv'}),
            ('/api/import', {'text': 'a', 'channel': [], 'filename': 'x.csv'}),
            ('/api/invoices', {'date': '2026-02-30', 'lines': []}),
            ('/api/invoices', {'date': 42, 'lines': []}),
            ('/api/menu/apply', {'changes': [{'sku': 'unknown', 'price': 10}]}),
            ('/api/menu/apply', {'changes': [{'sku': 'soda', 'price': True}]}),
            ('/api/menu/apply', {'changes': [{'sku': 'soda', 'price': -1}]}),
            ('/api/menu/apply', {'changes': [{'sku': 'soda', 'price': 10}], 'channels': ['unknown']}),
            ('/api/ask', {'question': 'why', 'facts': [], 'week': '2026-10-26', 'prev_week': '2026-10-19'}),
            ('/api/reset', {'unexpected': True}),
        ]
        invoice = valid_payload('/api/invoices')
        for field, value in [('qty', float('nan')), ('qty', -1), ('qty', True),
                             ('unit_price', float('inf')), ('unit', '<img src=x onerror=alert(1)>'),
                             ('ingredient', 'unknown'), ('page', 1.5), ('confidence', 2), ('total', 'oops')]:
            p = copy.deepcopy(invoice)
            p['lines'][0][field] = value
            invalid.append(('/api/invoices', p))
        for route, p in invalid:
            with self.subTest(route=route, payload=p):
                self.assertEqual(self.request('POST', route, p)[0], 400)

    def test_cross_origin_and_non_json_writes_denied(self):
        for headers in [{'Origin': ''}, {'Origin': 'null'}, {'Origin': 'https://attacker.test'},
                        {'Origin': server.PUBLIC_ORIGIN+'.attacker.test'}, {'Sec-Fetch-Site': 'cross-site'}]:
            for route in ['/api/reset', '/api/import', '/api/menu/apply', '/api/square/seed', '/api/ask']:
                with self.subTest(route=route, headers=headers), patch.object(server, 'reset') as reset:
                    self.assertEqual(self.request('POST', route, {}, headers=headers)[0], 403)
                    reset.assert_not_called()
        for content_type in ['', 'text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data']:
            self.assertEqual(self.request('POST', '/api/reset', {}, headers={'Content-Type': content_type})[0], 415)
        with patch.object(server, 'reset') as reset:
            self.assertEqual(self.request('POST', '/api/reset', {}, headers={'Content-Type': 'application/json; charset=utf-8'})[0], 200)
            reset.assert_called_once()

    def test_oversized_bodies_and_unsupported_encodings(self):
        for headers in [{'Content-Length': str(BODY_LIMIT+1)}, {'Transfer-Encoding': 'chunked'},
                        {'Content-Encoding': 'gzip'}]:
            with self.subTest(headers=headers), patch.object(server, 'reset') as reset:
                self.assertEqual(self.request('POST', '/api/reset', {}, headers=headers)[0], 413)
                reset.assert_not_called()
        p = {'text': 'x'*(CSV_LIMIT+1), 'filename': 'square.csv'}
        with patch.object(server, 'import_csv') as importer:
            self.assertEqual(self.request('POST', '/api/import', p)[0], 400)
            importer.assert_not_called()

    def test_ambiguous_content_lengths_rejected(self):
        for length_headers in ['', 'Content-Length: -1\r\n', 'Content-Length: 2\r\nContent-Length: 2\r\n']:
            raw = ('POST /api/reset HTTP/1.0\r\nHost: localhost\r\nOrigin: '+self.auth.origin+
                   '\r\nCookie: '+self.auth.cookie_name+'='+self.token+'\r\nX-CSRF-Token: '+self.auth.csrf(self.token)+'\r\nContent-Type: application/json\r\n'+length_headers+'\r\n{}').encode()
            with socket.create_connection(self.httpd.server_address, timeout=5) as conn:
                conn.sendall(raw)
                response = conn.recv(4096)
            self.assertIn(b' 400 ', response.split(b'\r\n')[0])

    def test_new_review_routes_cannot_bypass_input_security(self):
        routes = {'/api/menu/preview': 'preview_menu', '/api/menu/apply': 'apply_menu',
                  '/api/square/seed/preview': 'preview_seed', '/api/square/seed': 'confirm_seed'}
        for route, function in routes.items():
            with self.subTest(route=route), patch.object(server, function) as dispatch:
                self.assertEqual(self.request('POST', route, {}, headers={'Origin': 'https://attacker.test'})[0], 403)
                self.assertEqual(self.request('POST', route, {}, headers={'Content-Type': 'text/plain'})[0], 415)
                self.assertEqual(self.request('POST', route, raw=b'[]')[0], 400)
                dispatch.assert_not_called()

    def test_throttling_returns_retry_after_before_dispatch(self):
        with patch.object(server, 'REQUEST_LIMITER', RequestLimiter(2, 100, 100)), patch.object(server, 'reset') as reset:
            self.assertEqual(self.request('POST', '/api/reset', {})[0], 200)
            self.assertEqual(self.request('POST', '/api/reset', {})[0], 200)
            status, headers, _ = self.request('POST', '/api/reset', {}, headers={'X-Forwarded-For': '203.0.113.10'})
            self.assertEqual(status, 429)
            self.assertEqual(headers['Retry-After'], '60')
            self.assertEqual(reset.call_count, 2)

    def test_headers_and_script_nonce(self):
        nonces = []
        for method, path in [('GET', '/'), ('HEAD', '/'), ('GET', '/.env')]:
            status, headers, body = self.request(method, path)
            policy = headers['Content-Security-Policy']
            nonce = re.search(r"script-src 'nonce-([^']+)'", policy)[1]
            nonces.append(nonce)
            self.assertIn("script-src-attr 'none'", policy)
            self.assertIn("object-src 'none'", policy)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(headers['X-Frame-Options'], 'DENY')
            self.assertIn('Permissions-Policy', headers)
            if method == 'GET' and path == '/':
                self.assertIn(('nonce="'+nonce+'"').encode(), body)
        self.assertEqual(len(set(nonces)), len(nonces))

    def test_bad_csv_rolls_back_entire_import(self):
        with server.db() as con:
            before = con.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        for bad in ['oops', 'NaN', 'Infinity', '1e999']:
            content = pos_csv([['INPUT-GOOD', '2026-10-30', '12:00', '10', '1x Soda (2 L)'],
                               ['INPUT-BAD', '2026-10-30', '12:00', bad, '1x Soda (2 L)']])
            self.assertEqual(self.request('POST', '/api/import', {'text': content, 'filename': 'square.csv'})[0], 400)
            with server.db() as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM orders').fetchone()[0], before)
        for date, clock in [('2026-02-30', '12:00'), ('', '12:00'), ('2026-10-30', '99:00')]:
            with self.assertRaises(ValueError):
                server.import_csv(pos_csv([['BAD', date, clock, 10, '1x Soda (2 L)']]), 'square.csv', allow_ai=False)

    def test_csv_structure_and_row_limits(self):
        for content in ['Order,Order\na,b', 'Order,Date\na,b,extra', 'Order,Date\nonly-one', ' ,Date\na,b']:
            with self.assertRaises(ValueError):
                server.import_csv(content, 'square.csv', allow_ai=False)
        with patch.object(server, 'ROW_LIMIT', 1), self.assertRaises(ValueError):
            server.import_csv(pos_csv([['a','2026-10-30','12:00',10,''], ['b','2026-10-30','12:00',10,'']]),
                              'square.csv', allow_ai=False)

    def test_xss_order_id_stored_as_text(self):
        attack = '<img src=x onerror="window.__xss=1">'
        content = pos_csv([[attack, '2026-10-30', '12:00', 10, '1x Soda (2 L)']])
        status, _, _ = self.request('POST', '/api/import', {'text': content, 'filename': 'square.csv'})
        self.assertEqual(status, 200)
        with server.db() as con:
            self.assertEqual(con.execute('SELECT order_id FROM orders WHERE order_id=?', (attack,)).fetchone()[0], attack)

    def test_model_output_is_validated_before_ui(self):
        good = {'vendor': 'Test', 'date': '2026-10-30', 'lines': [
            {'description': 'Dough', 'qty': 1, 'unit': 'ea', 'unit_price': 2, 'ingredient': 'dough'}]}
        for field, value in [('unit', '<svg onload=alert(1)>'), ('qty', float('nan')),
                             ('confidence', 'NaN'), ('page', '<img src=x>'), ('ingredient', 'unknown')]:
            bad = copy.deepcopy(good)
            bad['lines'][0][field] = value
            with self.subTest(field=field), patch.object(server, 'call_openrouter', return_value=json.dumps(bad)):
                with self.assertRaises(ValueError):
                    server.extract(valid_payload('/api/extract'))
        with patch.object(server, 'call_openrouter', return_value=json.dumps(good)):
            self.assertEqual(server.extract(valid_payload('/api/extract'))['lines'][0]['qty'], 1)

    def test_sample_imports_unchanged(self):
        with patch.object(server, 'DB_PATH', self.root / 'sample-check.db'), patch.object(server, 'KEY', ''):
            server.init_db()
            data = server.app_data()
            self.assertEqual(len(data['orders']), 1776)
            self.assertEqual(len(data['invoices']), 9)
            self.assertEqual(len(data['errors']), 16)
            source = server.SAMPLES / 'grubhub_export_unusual_columns.csv'
            res = server.import_csv(source.read_text(encoding='utf-8-sig'), source.name, allow_ai=False)
            self.assertEqual(res['new_rows'], 0)
            self.assertEqual(res['redacted'], 100)


class ValidationUnitTests(unittest.TestCase):
    def test_finite_numbers_only(self):
        for value in [True, float('nan'), float('inf'), -float('inf'), 10**1000, -1]:
            with self.assertRaises(ValueError):
                number(value, 'test')
        for value in ['NaN', 'Infinity', '-Infinity', 'oops', '1e999', True]:
            with self.assertRaises(ValueError):
                server.num(value)
        self.assertEqual(server.num('($1,234.50)'), -1234.5)
        self.assertEqual(server.num(''), 0)

    def test_upload_media_and_size_validation(self):
        upload(valid_payload('/api/extract')['image'])
        for value in ['https://attacker.test/file', 'data:image/svg+xml;base64,PHN2Zz4=',
                      'data:application/pdf;base64,!!!!', 'data:image/png;base64,dGVzdA==',
                      'data:image/jpeg;base64,'+'A'*((8*1024*1024+2)//3*4+4)]:
            with self.assertRaises(ValueError):
                upload(value)

    def test_tool_arguments(self):
        for name, args in [('simulate_price_change', {'increase': float('nan')}),
                           ('simulate_price_change', {'channels': ['unknown']}),
                           ('find_price_changes', {'min_pct': True}), ('menu_margins', {'sql': 'DROP TABLE orders'})]:
            with self.assertRaises(ValueError):
                validate_tool_args(name, args, server.MENU)
        validate_tool_args('simulate_price_change', {'increase': .75, 'items': ['marg']}, server.MENU)

    def test_nested_facts_and_invalid_unicode(self):
        for facts in [{'summary': {'profit_this': True}}, {'findings': ['bad']},
                      {'findings': [{'id': '<img src=x>'}]}, {'menu_margins': [{'item': []}]},
                      {'channels': [{'channel': 'unknown'}]}, {'week_start': '2026-10-27'},
                      {'summary': {'profit_this': float('nan')}}]:
            payload = valid_payload('/api/ask')
            payload['facts'] = facts
            with self.subTest(facts=facts), self.assertRaises(ValueError):
                validate_payload('/api/ask', payload, server.MENU, server.INGREDIENTS)
        with self.assertRaises(ValueError):
            validate_payload('/api/import', {'text': '\ud800', 'filename': 'x.csv'}, server.MENU, server.INGREDIENTS)

    def test_origin_configuration(self):
        self.assertEqual(validate_origin('https://profitshield.test'), 'https://profitshield.test')
        for value in ['http://remote.test', 'https://user:pass@host', 'https://host/path', 'null', '']:
            with self.assertRaises(ValueError):
                validate_origin(value)

    def test_concurrent_requests_cannot_bypass_peer_budget(self):
        from concurrent.futures import ThreadPoolExecutor
        limiter = RequestLimiter(per_peer=5, global_limit=100, expensive=100)
        with ThreadPoolExecutor(max_workers=20) as pool:
            allowed = list(pool.map(lambda _: limiter.allow('peer', now=0), range(50)))
        self.assertEqual(sum(allowed), 5)

    def test_limiter_windows_global_expensive_and_capacity(self):
        limit = RequestLimiter(per_peer=2, global_limit=3, expensive=1)
        self.assertTrue(limit.allow('a', costly=True, now=0))
        self.assertFalse(limit.allow('b', costly=True, now=1))
        self.assertTrue(limit.allow('a', now=2))
        self.assertFalse(limit.allow('a', now=3))
        self.assertTrue(limit.allow('b', now=4))
        self.assertFalse(limit.allow('c', now=5))
        self.assertTrue(limit.allow('a', costly=True, now=61))
        tiny = RequestLimiter(peers=1)
        self.assertTrue(tiny.allow('a', now=0))
        self.assertFalse(tiny.allow('b', now=0))
        self.assertLessEqual(len(tiny.windows), 1)


if __name__ == '__main__':
    unittest.main()
