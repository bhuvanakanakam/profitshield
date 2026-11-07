"""Single-owner authentication. No external dependencies or integration calls."""
import argparse
from contextlib import contextmanager
import getpass
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

AUTH_PATH = Path(os.environ.get('PROFITSHIELD_AUTH_DB', Path(__file__).parent / 'owner-auth.sqlite3'))


class OwnerAuth:
    absolute_seconds = 12 * 60 * 60
    idle_seconds = 30 * 60
    max_failures = 5
    lock_seconds = 60

    def __init__(self, path, origin, local_http=False):
        parsed = urlsplit(origin)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username or parsed.password or parsed.query or parsed.fragment or
                parsed.path not in ('', '/') or origin.rstrip('/') != f'{parsed.scheme}://{parsed.netloc}'):
            raise ValueError('Set PROFITSHIELD_ORIGIN to the exact browser origin, without a path')
        if local_http:
            if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1'):
                raise ValueError('Local HTTP requires a localhost or 127.0.0.1 origin')
        elif parsed.scheme != 'https':
            raise ValueError('Hosted authentication requires an HTTPS origin')
        self.origin = origin.rstrip('/')
        self.secure = not local_http
        self.cookie_name = '__Host-profitshield' if self.secure else 'profitshield-local'
        self.path = Path(path)
        self.lock = threading.Lock()
        if self.path.is_symlink():
            raise ValueError('Authentication database must not be a symlink')
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        if not self.path.is_file():
            raise ValueError('Authentication database must be a regular file')
        os.chmod(self.path, 0o600)
        with self.connection() as con:
            con.executescript('''
                CREATE TABLE IF NOT EXISTS owner(id INTEGER PRIMARY KEY CHECK(id=1), salt BLOB, digest BLOB);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, created REAL, touched REAL);
                CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER, blocked_until REAL);
                INSERT OR IGNORE INTO attempts VALUES(1,0,0);
            ''')

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        try:
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def password_hash(password, salt):
        return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=131072, r=8, p=1, dklen=64, maxmem=256*1024*1024)

    def set_password(self, password, recovery=False):
        if not isinstance(password, str) or not 14 <= len(password) <= 1024:
            raise ValueError('Use a password of 14–1024 characters')
        salt = secrets.token_bytes(32)
        digest = self.password_hash(password, salt)
        with self.lock, self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM owner').fetchone() and not recovery:
                raise ValueError('Owner already exists; use the local recover command')
            con.execute('INSERT OR REPLACE INTO owner VALUES(1,?,?)', (salt, digest))
            con.execute('DELETE FROM sessions')
            con.execute('UPDATE attempts SET failures=0, blocked_until=0')

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def csrf(token):
        return hmac.new(token.encode(), b'profitshield-csrf-v1', hashlib.sha256).hexdigest()

    def login(self, password, previous=None):
        now = time.time()
        with self.lock, self.connection() as con:
            # A database transaction also serializes attempts across processes.
            con.execute('BEGIN IMMEDIATE')
            failures, blocked = con.execute('SELECT failures, blocked_until FROM attempts').fetchone()
            if blocked > now:
                return None, 429
            if blocked:
                failures = 0
            row = con.execute('SELECT salt,digest FROM owner WHERE id=1').fetchone()
            valid_input = isinstance(password, str) and len(password) <= 1024
            supplied = password if valid_input else ''
            # Do the same expensive hash even before bootstrap or on malformed input.
            salt, expected = row if row else (bytes(32), bytes(64))
            try:
                candidate = self.password_hash(supplied, salt)
            except UnicodeEncodeError:
                valid_input = False
                candidate = self.password_hash('', salt)
            if not row or not valid_input or not hmac.compare_digest(candidate, expected):
                failures += 1
                con.execute('UPDATE attempts SET failures=?, blocked_until=?',
                            (failures, now + self.lock_seconds if failures >= self.max_failures else 0))
                return None, 401
            con.execute('UPDATE attempts SET failures=0, blocked_until=0')
            con.execute('DELETE FROM sessions WHERE created<=? OR touched<=?',
                        (now-self.absolute_seconds, now-self.idle_seconds))
            if previous:
                con.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(previous),))
            token = secrets.token_urlsafe(32)
            con.execute('INSERT INTO sessions VALUES(?,?,?)', (self.token_hash(token), now, now))
            return token, 200

    def token_from_cookie(self, header):
        try:
            cookie = SimpleCookie()
            cookie.load(header or '')
            value = cookie[self.cookie_name].value if self.cookie_name in cookie else ''
            return value if len(value) == 43 and all(c.isalnum() or c in '-_' for c in value) else None
        except CookieError:
            return None

    def authorized(self, token):
        if not token:
            return False
        now = time.time()
        with self.lock, self.connection() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT created,touched FROM sessions WHERE token_hash=?', (self.token_hash(token),)).fetchone()
            if not row:
                return False
            if row[0] + self.absolute_seconds <= now or row[1] + self.idle_seconds <= now:
                con.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(token),))
                return False
            con.execute('UPDATE sessions SET touched=? WHERE token_hash=?', (now, self.token_hash(token)))
            return True

    def logout(self, token):
        with self.lock, self.connection() as con:
            con.execute('DELETE FROM sessions WHERE token_hash=?', (self.token_hash(token),))

    def cookie(self, token='', clear=False):
        cookie = SimpleCookie()
        cookie[self.cookie_name] = token
        morsel = cookie[self.cookie_name]
        morsel['path'] = '/'
        morsel['httponly'] = True
        morsel['samesite'] = 'Strict'
        morsel['max-age'] = 0 if clear else self.absolute_seconds
        if self.secure:
            morsel['secure'] = True
        return morsel.OutputString()


LOGIN_PAGE = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sign in · ProfitShield</title>
<style>body{font:18px system-ui;max-width:28rem;margin:12vh auto;padding:24px;background:#f7f8fa;color:#17212b}
label,input,button{display:block;margin:16px 0}input,button{font:inherit;padding:12px;box-sizing:border-box;width:100%}</style>
<h1>ProfitShield</h1><p>Sign in to your restaurant’s financial dashboard.</p>
<form id="login"><label for="password">Owner password</label>
<input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024">
<button>Sign in</button><p id="error" role="alert"></p></form>
<script>document.querySelector('#login').onsubmit=async e=>{e.preventDefault();
const button=document.querySelector('button');button.disabled=true;
try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({password:document.querySelector('#password').value})});
if(r.ok){location.replace('/');return}document.querySelector('#error').textContent=r.status===429?
'Too many attempts. Wait a minute and try again.':'Sign in failed. Check your password or contact the server administrator.';
}catch(e){document.querySelector('#error').textContent='Unable to connect. Please try again.'}
finally{document.querySelector('#password').value='';button.disabled=false}};</script></html>'''


def main():
    parser = argparse.ArgumentParser(description='Local-only ProfitShield owner setup/recovery')
    parser.add_argument('command', choices=['bootstrap', 'recover'])
    parser.add_argument('--db', type=Path, default=AUTH_PATH)
    args = parser.parse_args()
    password = getpass.getpass('New owner password (14+ characters): ')
    if password != getpass.getpass('Repeat password: '):
        parser.error('Passwords do not match')
    try:
        auth = OwnerAuth(args.db, 'https://localhost')
        auth.set_password(password, recovery=args.command == 'recover')
    except ValueError as exc:
        parser.error(str(exc))
    print('Owner password saved. All existing sessions have been revoked.')


if __name__ == '__main__':
    main()
