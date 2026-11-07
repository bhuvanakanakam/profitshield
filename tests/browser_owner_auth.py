"""Optional Chromium auth/UI regression: python3 tests/browser_owner_auth.py.

Requires Playwright and Chromium already installed; no external API calls.
"""
import tempfile
import threading
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from owner_auth import OwnerAuth
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_PATH', Path(tmp)/'data.db'), patch.object(server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', ''), patch.object(server.urllib.request, 'urlopen', side_effect=AssertionError('External integration forbidden')):
    server.init_db()
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    origin = 'http://127.0.0.1:' + str(httpd.server_port)
    auth = OwnerAuth(Path(tmp)/'auth.sqlite3', origin, local_http=True)
    password = 'browser-test-owner-password'
    auth.set_password(password)
    httpd.owner_auth = auth
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=__import__('shutil').which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(origin) else r.abort())
            page.goto(origin)
            assert page.locator('#password').is_visible()
            assert page.locator('#kpis').count() == 0
            print('Anonymous page contains login only: PASS')
            def login():
                page.locator('#password').fill(password)
                page.locator('button').click()
                page.locator('#dataText').filter(has_text='Live database').wait_for()
            login()
            assert '1,776' in page.locator('#dataText').inner_text()
            for view in ['overview','leaks','agents','payouts','invoices','margins','sources']:
                page.locator('nav [data-go="'+view+'"]').click()
                page.locator('#v-'+view).wait_for(state='visible')
            print('Owner login and all seven live views: PASS')
            status = page.evaluate("async()=>{const r=await apiFetch('/api/invoices',{method:'POST',body:JSON.stringify({vendor:'Test',invoice_number:'TEST-AUTH',date:'2026-10-30',lines:[{description:'Fixture supplies',qty:1,unit:'ea',unit_price:2}]})});return r.status}")
            assert status == 200
            print('Session/CSRF wrapper permits local invoice save: PASS')
            page.locator('#logout').click()
            page.locator('#password').wait_for(state='visible')
            assert page.request.get(origin+'/api/data').status == 401
            print('Logout revokes access: PASS')
            login()
            with auth.connection() as con:
                con.execute('UPDATE sessions SET touched=0')
            page.evaluate("apiFetch('/api/data').catch(()=>{})")
            page.locator('#password').wait_for(state='visible')
            print('Expired session returns to login: PASS')
            login()
            page.route('**/api/data', lambda r: r.fulfill(status=503, body='{"error":"unavailable"}', content_type='application/json'))
            page.reload()
            page.locator('main').filter(has_text='Unable to load your live database').wait_for()
            assert page.locator('#kpis').count() == 0
            print('Failed live load does not fall back to demo: PASS')
            assert not errors, errors
            print('JavaScript page errors: NONE')
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
