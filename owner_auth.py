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


LOGIN_PAGE = '''<title>Sign in · ProfitShield</title>
<meta name="theme-color" content="#193f32">
<style>
*{box-sizing:border-box}body{margin:0;font:15px/1.6 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f5f7f0;color:#243d2b}.login-layout{min-height:100vh;display:grid;grid-template-columns:1.05fr 1fr}.login-story{background:#193f32;color:#e1edd5;padding:55px clamp(30px,6vw,90px);display:flex;flex-direction:column;justify-content:center;position:relative;overflow:hidden}.login-story:before{content:'';position:absolute;right:-200px;top:-130px;width:620px;height:620px;border:1px solid #70946033;border-radius:50%;box-shadow:0 0 0 70px #70946009,0 0 0 140px #70946008;pointer-events:none}.login-brand{font-size:23px;font-weight:750;letter-spacing:-1px;margin-bottom:80px;display:flex;align-items:center;gap:11px}.login-mark{display:grid;place-items:center;width:35px;height:39px;border:1px solid #8fa777;border-radius:9px 9px 15px 15px;font-size:16px;color:#d7e9b7}.login-story h1{font-size:clamp(35px,4.4vw,62px);line-height:1.12;letter-spacing:-2px;font-weight:550;position:relative;margin:0 0 25px}.login-story h1 em{font-style:normal;color:#d7e9b7}.login-story p{font-size:14px;color:#c1d1bf;max-width:42ch;margin:0;position:relative}.login-meta{margin-top:auto;padding-top:65px;font-size:11px;color:#b8ccaf}.login-panel{display:grid;place-items:center;padding:50px 30px}.login-card{width:min(100%,390px)}.login-eyebrow{font-size:10px;font-weight:650;letter-spacing:2px;color:#6d805f}.login-card h2{font-size:33px;font-weight:600;letter-spacing:-1px;margin:12px 0 8px;line-height:1.2}.login-card>p{font-size:13px;color:#64745b;margin-bottom:30px}.login-card label{display:block;font-size:12px;font-weight:650;margin-bottom:8px}.login-card input{width:100%;border:1px solid #cbd8c0;background:#fff;border-radius:10px;padding:14px;font:inherit;color:#263d25}.login-card button{display:block;width:100%;background:#cce2ad;color:#263d25;border:1px solid #b3cd91;border-radius:10px;font:600 14px system-ui;padding:14px;margin-top:19px;cursor:pointer}.login-card button:hover{background:#bed898}.login-card button:disabled{opacity:.6;cursor:wait}:focus-visible{outline:3px solid #4c723b;outline-offset:3px}#error{font-size:12px;color:#9d3829;min-height:24px;line-height:1.7}.login-note{border-top:1px solid #dce4d3;margin-top:24px;padding-top:19px;font-size:11px;color:#63745b}.login-note code{font-size:10px;background:#eaf0e0;padding:3px 5px;border-radius:4px;overflow-wrap:anywhere}@media(max-width:760px){.login-layout{grid-template-columns:1fr}.login-story{padding:27px 25px 33px}.login-brand{margin-bottom:30px;font-size:20px}.login-story h1{font-size:36px;letter-spacing:-1.2px}.login-story p{font-size:12px}.login-meta{display:none}.login-panel{padding:38px 24px 50px}.login-card h2{font-size:29px}}@media(prefers-reduced-motion:no-preference){.login-card{animation:enter .25s ease-out}@keyframes enter{from{opacity:.4;transform:translateY(8px)}to{opacity:1;transform:none}}}
</style>
<main class="login-layout"><section class="login-story" aria-label="ProfitShield"><div class="login-brand"><span class="login-mark" aria-hidden="true">✓</span>ProfitShield</div><h1>Good food.<br><em>Stronger margins.</em></h1><p>Your payouts, invoices and next best move. A clearer picture of your restaurant, with evidence behind the numbers.</p><div class="login-meta">YOUR RECORDS. YOUR REVIEW. YOUR DECISION.</div></section>
<section class="login-panel"><div class="login-card"><span class="login-eyebrow">YOUR RESTAURANT WORKSPACE</span><h2>Welcome back.</h2><p>Sign in to review your restaurant’s financial records.</p>
<form id="login"><label for="password">Owner password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024" aria-describedby="error"><button type="submit">Sign in to ProfitShield</button><p id="error" role="alert" aria-live="polite"></p></form>
<div class="login-note">First time running this app? Set your owner password in the server terminal with <code>python3 owner_auth.py bootstrap</code>. Password recovery is also local to the server.</div></div></section></main>
<script>document.querySelector('#login').onsubmit=async event=>{event.preventDefault();const button=document.querySelector('button'),password=document.querySelector('#password'),error=document.querySelector('#error');button.disabled=true;button.textContent='Signing in…';error.textContent='';password.removeAttribute('aria-invalid');
try{const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:password.value})});if(response.ok){location.replace('/');return}password.setAttribute('aria-invalid','true');error.textContent=response.status===429?'Too many attempts. Wait a minute and try again.':'Sign in failed. Check your password or contact the server administrator.';password.focus();}
catch(error){document.querySelector('#error').textContent='Unable to connect. Please try again.'}
finally{password.value='';button.disabled=false;button.textContent='Sign in to ProfitShield'}};</script>'''



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
