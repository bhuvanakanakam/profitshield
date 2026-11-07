"""HTTP regression tests; no credentials, external calls or real database writes.

Run: python3 -m unittest discover -s tests -v
"""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from input_security import RequestLimiter


def valid_payload(route):
    return {
        '/api/import': {'text': 'Order,Date,Time,Gross Sales,Itemization\nx,2026-10-26,12:00,10,1x Margherita pizza', 'filename': 'square.csv'},
        '/api/invoices': {'date': '2026-10-26', 'lines': [{'description': 'Supplies', 'qty': 1, 'unit': 'ea', 'unit_price': 2}]},
        '/api/menu/preview': {'week': '2026-10-26', 'changes': [{'sku': 'soda', 'old_price': 4.5, 'price': 5}], 'channels': ['In-store']},
        '/api/menu/apply': {'plan_id': 'P'*43, 'confirmation': 'APPLY SANDBOX PRICES', 'week': '2026-10-26'},
        '/api/square/seed': {'plan_id': 'P'*43, 'confirmation': 'CREATE 3 SANDBOX ORDERS'},
        '/api/ask': {'question': 'Why?', 'facts': {}, 'week': '2026-10-26', 'prev_week': '2026-10-19'},
        '/api/extract': {'image': 'data:image/jpeg;base64,/9j/dGVzdA=='},
    }.get(route, {})



class HTTPFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.dashboard = (server.ROOT / 'index.html').read_text(encoding='utf-8')
        (cls.root / 'index.html').write_text(cls.dashboard, encoding='utf-8')
        cls.marker = b'PRIVATE-FIXTURE-MUST-NOT-BE-SERVED'
        cls.private_paths = [
            '.env', '.env.example', 'backhouse.db', 'other.sqlite',
            'other.sqlite3', 'backhouse.db-wal', 'backhouse.db-shm',
            'server.py', 'square_actions.py', 'README.md', '.git/config', '.hidden',
            'samples/orders.csv', 'samples/invoice.pdf', 'samples/receipt.jpg',
            'future-asset.js', 'nested/index.html', 'private/financial-report.pdf',
            'backups/restaurant.sqlite3', '.env.production', 'backhouse.db-journal',
        ]
        for name in cls.private_paths:
            target = cls.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(cls.marker)
        cls.root_patch = patch.object(server, 'ROOT', cls.root)
        cls.root_patch.start()
        cls.limiter_patch = patch.object(server, "REQUEST_LIMITER", RequestLimiter(10000, 20000, 10000))
        cls.limiter_patch.start()
        cls.db_patch = patch.object(server, 'DB_PATH', cls.root / 'test.db')
        cls.db_patch.start()
        cls.network_patch = patch.object(
            server.urllib.request, 'urlopen', side_effect=AssertionError('External call'))
        cls.network_patch.start()
        cls.square_patch = patch.object(server.SQUARE_OPENER, 'open', side_effect=AssertionError('External Square call'))
        cls.square_patch.start()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.network_patch.stop()
        cls.square_patch.stop()
        cls.db_patch.stop()
        cls.limiter_patch.stop()
        cls.root_patch.stop()
        cls.temp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        conn = http.client.HTTPConnection(*self.httpd.server_address, timeout=5)
        try:
            body = json.dumps(payload) if payload is not None else None
            request_headers = {'Content-Type': 'application/json', 'Origin': server.PUBLIC_ORIGIN}
            request_headers.update(headers or {})
            conn.request(method, path, raw if raw is not None else body, request_headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()


class PublicAssetTests(HTTPFixture):
    def test_dashboard_get_and_head(self):
        for path in ['/', '/index.html', '/?v=1', '/index.html?v=1']:
            with self.subTest(path=path):
                status, headers, body = self.request('GET', path)
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
                self.assertIn(self.dashboard.split('<script>')[0].encode('utf-8'), body)
                self.assertIn(b'<script nonce=', body)
                self.assertIn(b'<meta name="viewport"', body)
                self.assertEqual(int(headers['Content-Length']), len(body))
                head_status, head_headers, head_body = self.request('HEAD', path)
                self.assertEqual(head_status, status)
                self.assertEqual(head_headers['Content-Length'], headers['Content-Length'])
                self.assertEqual(head_body, b'')

    def test_private_files_and_directories_denied(self):
        paths = ['/' + p for p in self.private_paths]
        paths += ['/samples/', '/.git/', '/nested/', '/missing', '/favicon.ico']
        for method in ['GET', 'HEAD', 'POST']:
            for path in paths:
                for suffix in ['', '?download=1']:
                    with self.subTest(method=method, path=path + suffix):
                        status, _, body = self.request(method, path + suffix)
                        self.assertEqual(status, 404)
                        self.assertNotIn(self.marker, body)

    def test_directory_listing_never_exposes_repository_entries(self):
        for method in ['GET', 'HEAD']:
            for path in ['/', '/?list=1', '/samples/', '/private/', '/backups/', '/.git/']:
                with self.subTest(method=method, path=path):
                    status, _, body = self.request(method, path)
                    self.assertEqual(status, 200 if path.startswith('/?') or path == '/' else 404)
                    self.assertNotIn(b'Directory listing for', body)
                    self.assertNotIn(b'PRIVATE-FIXTURE', body)
                    self.assertNotIn(b'href=".env"', body)

    def test_encoding_and_traversal_denied(self):
        paths = [
            '/%2eenv', '/backhouse%2edb', '/%73erver.py', '/%2egit/config',
            '/samples%2forders.csv', '/%252eenv', '/backhouse%252edb',
            '/../.env', '/./.env', '/samples/../.env', '/%2e%2e/.env',
            '/%2E%2E%2F.env', '/%252e%252e%252f.env',
            '/..%5c.env', '/%5c.env', '/index.html/../.env',
            '/index.html%00.env', '/index.html/.env', '/index.html/',
            '/%69ndex.html', '/./index.html', '/samples/../index.html',
            '/%2findex.html', '/%ZZ', '/index.html;private',
            '//.env', '//samples/orders.csv', '/%2f.env',
            '/private%2ffinancial-report.pdf', '/backups/../.env',
            '/api/health/../.env', '/api/data/../../server.py',
        ]
        for method in ['GET', 'HEAD']:
            for path in paths:
                for suffix in ['', '?v=1']:
                    with self.subTest(method=method, path=path + suffix):
                        status, _, body = self.request(method, path + suffix)
                        self.assertEqual(status, 404)
                        self.assertNotIn(self.marker, body)

    def test_head_has_no_wire_body_even_for_errors(self):
        for path in ['/', '/.env', '/backhouse.db?download=1', '/api/health']:
            with self.subTest(path=path):
                with socket.create_connection(self.httpd.server_address, timeout=5) as conn:
                    conn.sendall(('HEAD ' + path + ' HTTP/1.0\r\nHost: localhost\r\n\r\n').encode())
                    chunks = []
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                self.assertEqual(b''.join(chunks).split(b'\r\n\r\n', 1)[1], b'')

    def test_allowlisted_symlink_and_missing_asset_denied(self):
        index = self.root / 'index.html'
        index.unlink()
        try:
            for method in ['GET', 'HEAD']:
                self.assertEqual(self.request(method, '/')[0], 404)
            index.symlink_to(self.root / '.env')
            for method in ['GET', 'HEAD']:
                self.assertEqual(self.request(method, '/index.html')[0], 404)
        finally:
            if index.is_symlink():
                index.unlink()
            index.write_text(self.dashboard, encoding='utf-8')

    def test_get_apis_preserved(self):
        data = {'orders': [], 'invoices': [], 'weeks': []}
        with patch.object(server, 'app_data', return_value=data) as app_data:
            for path in ['/api/data', '/api/data?v=1']:
                self.assertEqual(json.loads(self.request('GET', path)[2]), data)
            self.assertEqual(self.request('HEAD', '/api/data')[0], 200)
            self.assertEqual(app_data.call_count, 3)
        health = json.loads(self.request('GET', '/api/health?v=1')[2])
        self.assertTrue(health['ok'])
        self.assertTrue(health['backend'])
        for path in ['/api/database', '/api/health-private']:
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_post_api_dispatch_preserved_without_side_effects(self):
        routes = {
            '/api/import': 'import_csv', '/api/invoices': 'save_invoice',
            '/api/menu/apply': 'apply_menu', '/api/menu/preview': 'preview_menu',
            '/api/square/seed/preview': 'preview_seed', '/api/square/seed': 'confirm_seed', '/api/reset': 'reset',
            '/api/ask': 'ask', '/api/extract': 'extract',
            '/api/square/sync': 'square_sync',
        }
        with patch.object(server, 'KEY', 'dummy-test-key'), patch.object(
                server, 'SQUARE_TOKEN', 'dummy-test-token'):
            for route, function in routes.items():
                with self.subTest(route=route), patch.object(
                        server, function, return_value={'ok': True}) as mock:
                    status, _, body = self.request('POST', route, valid_payload(route))
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body), {'ok': True})
                    mock.assert_called_once()
        with patch.object(server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', ''):
            for path in ['/api/ask', '/api/extract', '/api/square/sync']:
                self.assertEqual(self.request('POST', path, valid_payload(path))[0], 400)


if __name__ == '__main__':
    unittest.main()
