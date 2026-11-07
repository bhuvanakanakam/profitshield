"""Owner authentication tests exercise real local HTTP; integrations stay mocked."""
import json
import re
import os
import time
from unittest.mock import patch

from owner_auth import OwnerAuth
import server
from test_http_security import HTTPFixture, valid_payload


class OwnerAuthTests(HTTPFixture):
    def setUp(self):
        with self.auth.connection() as con:
            con.execute('UPDATE attempts SET failures=0,blocked_until=0')
            con.execute('DELETE FROM sessions')
        self.token, status = self.auth.login(self.password)
        self.assertEqual(status, 200)

    def login(self, password=None, **kwargs):
        return self.request('POST', '/api/auth/login',
                            {'password': self.password if password is None else password},
                            authenticated=False, **kwargs)

    def test_anonymous_cannot_call_sensitive_endpoints(self):
        for method, paths in [('GET', ['/api/data', '/api/auth/session']),
                              ('HEAD', ['/api/data', '/api/auth/session']),
                              ('POST', ['/api/data', '/api/reset', '/api/import', '/api/invoices',
                                        '/api/menu/apply', '/api/menu/preview', '/api/square/seed/preview', '/api/ask', '/api/extract',
                                        '/api/square/sync', '/api/square/seed', '/api/auth/logout'])]:
            for path in paths:
                with self.subTest(method=method, path=path), patch.object(server, 'app_data') as data:
                    status, _, body = self.request(method, path, {} if method == 'POST' else None,
                                                   authenticated=False)
                    self.assertEqual(status, 401)
                    data.assert_not_called()
                    self.assertNotIn(b'orders', body)
        status, _, body = self.request('GET', '/', authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn(b'Owner password', body)
        self.assertNotIn(b'genData', body)
        health = json.loads(self.request('GET', '/api/health', authenticated=False)[2])
        self.assertEqual(health, {'ok': True, 'backend': True})

    def test_every_review_endpoint_requires_session_csrf(self):
        routes = {'/api/menu/preview': 'preview_menu', '/api/menu/apply': 'apply_menu',
                  '/api/square/seed/preview': 'preview_seed', '/api/square/seed': 'confirm_seed'}
        for route, handler in routes.items():
            with self.subTest(route=route), patch.object(server, handler) as dispatch:
                self.assertEqual(self.request('POST', route, valid_payload(route),
                                              headers={'X-CSRF-Token': ''})[0], 403)
                dispatch.assert_not_called()

    def test_login_and_dashboard_scripts_share_response_nonce(self):
        for authenticated in [False, True]:
            status, headers, body = self.request('GET', '/', authenticated=authenticated)
            self.assertEqual(status, 200)
            nonce = re.search(r"script-src 'nonce-([^']+)'", headers['Content-Security-Policy'])[1]
            scripts = re.findall(rb'<script([^>]*)>', body)
            self.assertTrue(scripts)
            for attributes in scripts:
                self.assertIn(('nonce="'+nonce+'"').encode(), attributes)
            self.assertNotIn(self.token.encode(), body)

    def test_login_password_hash_and_cookie_security(self):
        self.assertEqual(self.login('incorrect password')[0], 401)
        status, headers, body = self.login()
        self.assertEqual(status, 200)
        token = self.auth.token_from_cookie(headers['Set-Cookie'])
        self.assertTrue(self.auth.authorized(token))
        for attribute in ['__Host-profitshield=', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/', 'Max-Age=43200']:
            self.assertIn(attribute, headers['Set-Cookie'])
        self.assertNotIn('Domain=', headers['Set-Cookie'])
        self.assertEqual(json.loads(body)['csrf'], self.auth.csrf(token))
        with self.auth.connection() as con:
            salt, digest = con.execute('SELECT salt,digest FROM owner').fetchone()
            self.assertEqual(len(salt), 32)
            self.assertEqual(len(digest), 64)
            self.assertNotIn(token, str(con.execute('SELECT * FROM sessions').fetchall()))
        self.assertEqual(os.stat(self.auth.path).st_mode & 0o777, 0o600)

    def test_authorized_read_and_csrf_protected_mutation(self):
        data = {'orders': [{'id': 'test'}]}
        with patch.object(server, 'app_data', return_value=data):
            self.assertEqual(json.loads(self.request('GET', '/api/data')[2]), data)
        csrf = json.loads(self.request('GET', '/api/auth/session')[2])['csrf']
        self.assertEqual(csrf, self.auth.csrf(self.token))
        for headers in [{'X-CSRF-Token': ''}, {'X-CSRF-Token': 'bad'},
                        {'Origin': 'https://attacker.test'}, {'Origin': ''}]:
            with self.subTest(headers=headers), patch.object(server, 'reset') as reset:
                self.assertEqual(self.request('POST', '/api/reset', {}, headers=headers)[0], 403)
                reset.assert_not_called()
        with patch.object(server, 'reset') as reset:
            self.assertEqual(self.request('POST', '/api/reset', {})[0], 200)
            reset.assert_called_once()
        # One session's CSRF token cannot authorize a different session.
        other, _ = self.auth.login(self.password)
        self.assertEqual(self.request('POST', '/api/reset', {}, headers={
            'X-CSRF-Token': self.auth.csrf(other)})[0], 403)

    def test_login_csrf_and_content_type_enforced(self):
        for origin in ['', 'https://attacker.test', 'null']:
            self.assertEqual(self.login(headers={'Origin': origin})[0], 403)
        self.assertEqual(self.login(headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/import', {}, headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/auth/login', [], authenticated=False)[0], 400)

    def test_logout_revokes_session_and_clears_cookie(self):
        status, headers, _ = self.request('POST', '/api/auth/logout', {})
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertFalse(self.auth.authorized(self.token))
        self.assertEqual(self.request('GET', '/api/data')[0], 401)
        self.assertEqual(self.request('POST', '/api/reset', {})[0], 401)

    def test_idle_and_absolute_expiry(self):
        for column, seconds in [('touched', self.auth.idle_seconds), ('created', self.auth.absolute_seconds)]:
            with self.subTest(column=column):
                self.token, _ = self.auth.login(self.password)
                with self.auth.connection() as con:
                    con.execute(f'UPDATE sessions SET {column}=?', (time.time()-seconds-1,))
                self.assertEqual(self.request('GET', '/api/data')[0], 401)
                self.assertEqual(self.request('POST', '/api/reset', {})[0], 401)
                with self.auth.connection() as con:
                    self.assertFalse(con.execute('SELECT 1 FROM sessions WHERE token_hash=?',
                                                 (self.auth.token_hash(self.token),)).fetchone())

    def test_forged_cookies_and_login_rotation(self):
        for value in ['x'*43, '../../.env', '']:
            self.assertEqual(self.request('GET', '/api/data', headers={
                'Cookie': self.auth.cookie_name+'='+value})[0], 401)
        status, headers, _ = self.request('POST', '/api/auth/login', {'password': self.password})
        self.assertEqual(status, 200)
        self.assertFalse(self.auth.authorized(self.token))
        self.assertTrue(self.auth.authorized(self.auth.token_from_cookie(headers['Set-Cookie'])))

    def test_persistent_lockout_and_expiry(self):
        for _ in range(self.auth.max_failures):
            self.assertEqual(self.login('wrong')[0], 401)
        reopened = OwnerAuth(self.auth.path, self.auth.origin)
        self.assertEqual(reopened.login(self.password)[1], 429)
        self.assertEqual(self.login()[0], 429)
        with self.auth.connection() as con:
            con.execute('UPDATE attempts SET blocked_until=?', (time.time()-1,))
        self.assertEqual(self.login()[0], 200)

    def test_bootstrap_recovery_and_no_http_registration(self):
        with self.assertRaises(ValueError):
            self.auth.set_password('another-long-password')
        for weak in ['', 'short', 'x'*1025]:
            with self.assertRaises(ValueError):
                self.auth.set_password(weak, recovery=True)
        replacement = 'replacement-owner-password'
        self.auth.set_password(replacement, recovery=True)
        try:
            self.assertFalse(self.auth.authorized(self.token))
            self.assertEqual(self.login()[0], 401)
            self.assertEqual(self.login(replacement)[0], 200)
            self.assertEqual(self.request('POST', '/api/auth/bootstrap', {}, authenticated=False)[0], 401)
        finally:
            self.auth.set_password(self.password, recovery=True)

    def test_persistence_and_auth_database_isolation(self):
        reopened = OwnerAuth(self.auth.path, self.auth.origin)
        self.assertTrue(reopened.authorized(self.token))
        for path in ['/owner-auth.sqlite3', '/owner_auth.py', '/owner-auth.sqlite3?download=1']:
            self.assertEqual(self.request('GET', path)[0], 404)
            self.assertEqual(self.request('HEAD', path)[0], 404)
        # Database reset dispatch cannot reset authentication; storage is separate.
        with patch.object(server, 'reset'):
            self.assertEqual(self.request('POST', '/api/reset', {})[0], 200)
        self.assertTrue(reopened.authorized(self.token))

    def test_console_bootstrap_and_recovery(self):
        from owner_auth import main
        target = self.root / 'console.sqlite3'
        with patch('sys.argv', ['owner_auth.py', 'bootstrap', '--db', str(target)]), patch(
                'owner_auth.getpass.getpass', side_effect=[self.password, self.password]):
            main()
        auth = OwnerAuth(target, self.auth.origin)
        token, status = auth.login(self.password)
        self.assertEqual(status, 200)
        replacement = 'new-console-owner-passphrase'
        with patch('sys.argv', ['owner_auth.py', 'recover', '--db', str(target)]), patch(
                'owner_auth.getpass.getpass', side_effect=[replacement, replacement]):
            main()
        self.assertFalse(auth.authorized(token))
        self.assertEqual(auth.login(replacement)[1], 200)

    def test_unconfigured_owner_fails_closed(self):
        empty = OwnerAuth(self.root / 'unconfigured.sqlite3', self.auth.origin)
        with patch.object(self.httpd, 'owner_auth', empty):
            self.assertEqual(self.login()[0], 401)
            self.assertEqual(self.request('GET', '/api/data')[0], 401)

    def test_login_body_limits_and_invalid_password_types(self):
        self.assertEqual(self.login('x'*20000)[0], 413)
        for value in [None, 42, ['password'], {'password': 'password'}]:
            self.assertEqual(self.request('POST', '/api/auth/login', {'password': value}, authenticated=False)[0], 401)

    def test_auth_storage_rejects_directories_and_symlinks(self):
        mode = os.stat(self.root).st_mode
        with self.assertRaisesRegex(ValueError, 'regular file'):
            OwnerAuth(self.root, self.auth.origin)
        self.assertEqual(os.stat(self.root).st_mode, mode)
        link = self.root / 'linked-auth.sqlite3'
        link.symlink_to(self.auth.path)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            OwnerAuth(link, self.auth.origin)

    def test_shared_auth_and_restaurant_path_rejected(self):
        with patch.object(server, 'AUTH_PATH', self.auth.path), patch.object(server, 'DB_PATH', self.auth.path):
            with self.assertRaisesRegex(ValueError, 'separate paths'):
                server.configured_auth()

    def test_https_configuration_and_explicit_loopback_mode(self):
        for origin in ['', 'http://profitshield.test', 'https://host/path', 'https://user:pass@host']:
            with self.assertRaises(ValueError):
                OwnerAuth(self.root / 'bad.sqlite3', origin)
        with self.assertRaises(ValueError):
            OwnerAuth(self.root / 'bad.sqlite3', 'http://remote.test', local_http=True)
        local = OwnerAuth(self.root / 'local.sqlite3', 'http://127.0.0.1:8000', local_http=True)
        self.assertNotIn('Secure', local.cookie('dummy'))
        self.assertNotIn('__Host-', local.cookie_name)
