"""Optional UI regression: python3 tests/browser_square_guard.py.

Requires preinstalled Playwright and Chromium. All Square transport is fake.
"""
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from owner_auth import OwnerAuth
from square_fakes import FakeSquare
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_PATH', Path(tmp)/'data.db'), patch.object(
        server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', 'dummy-sandbox-token'), patch.object(
        server.urllib.request, 'urlopen', side_effect=AssertionError('External integration forbidden')):
    fake = FakeSquare()
    fake.add_item('Margherita pizza', 1600)
    fake.add_item('Pepperoni pizza', 1800)
    server.init_db()
    httpd = server.ThreadingHTTPServer(('127.0.0.1',0), server.Handler)
    origin = 'http://127.0.0.1:'+str(httpd.server_port)
    auth = OwnerAuth(Path(tmp)/'browser-auth.sqlite3', origin, local_http=True)
    auth.set_password('browser-owner-test-password')
    httpd.owner_auth = auth
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(server, 'PUBLIC_ORIGIN', origin), patch.object(
                server.SQUARE_OPENER, 'open', side_effect=fake.open), sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin) else r.abort())
            page.goto(origin)
            page.locator('#password').fill('browser-owner-test-password')
            page.locator('button').click()
            page.locator('#dataText').filter(has_text='Live database').wait_for()
            for view in ['overview','leaks','agents','payouts','invoices','margins','sources']:
                page.locator('nav [data-go="'+view+'"]').click()
                page.locator('#v-'+view).wait_for(state='visible')
            page.evaluate("openAssist()")
            for command in ['execute','execute','apply','do it']:
                page.evaluate('(q)=>ask(q)', command)
            assert not fake.writes
            print('Repeated chat commands never write to Square: PASS')
            page.evaluate("ask('What if I raise pizza prices by $0.75?')")
            button = page.locator('#msgs [data-review-sim]').last
            button.wait_for()
            button.click()
            page.locator('#actionPhrase').wait_for(state='visible')
            assert not fake.writes
            assert page.locator('#confirmAction').is_disabled()
            page.locator('#actionPhrase').fill('yes')
            assert page.locator('#confirmAction').is_disabled()
            print('Simulation review is read-only; exact phrase required: PASS')
            page.locator('#actionPhrase').fill('APPLY SANDBOX PRICES')
            page.locator('#confirmAction').click()
            page.locator('#actionResult').filter(has_text='Review plan completed').wait_for()
            assert len(fake.writes) == 2
            assert fake.catalog['Margherita pizza']['item_variation_data']['price_money']['amount'] == 1675
            assert fake.catalog['Pepperoni pizza']['item_variation_data']['price_money']['amount'] == 1875
            assert page.locator('#confirmAction').is_disabled()
            page.evaluate("document.querySelector('#confirmAction').onclick()")
            page.evaluate("ask('execute')")
            assert len(fake.writes) == 2
            print('Confirmed absolute prices applied once; duplicate click/chat cannot reapply: PASS')
            # A second rendered chart is bound to its own week; changing weeks invalidates it.
            page.evaluate("closeDrawer();ask('What if I raise pizza prices by $0.75?')")
            next_button = page.locator('#msgs [data-review-sim]').last
            next_button.wait_for()
            page.evaluate("CUR=WEEKS[1].id")
            next_button.click()
            page.locator('#toast').filter(has_text='Simulation is stale').wait_for()
            assert len(fake.writes) == 2
            print('Stale simulation cannot open an execution review: PASS')
            page.evaluate("CUR=WEEKS.at(-1).id;closeAssist();go('sources')")
            page.locator('#sqSeed').click()
            page.locator('#actionPhrase').wait_for(state='visible')
            assert len(fake.orders) == 0
            page.locator('#actionPhrase').fill('CREATE 3 SANDBOX ORDERS')
            page.locator('#confirmAction').click()
            page.locator('#actionResult').filter(has_text='Review plan completed').wait_for()
            assert len(fake.orders) == 3
            page.evaluate("document.querySelector('#confirmAction').onclick()")
            assert len(fake.orders) == 3
            print('Seed preview is read-only; confirmation creates exactly three sandbox orders once: PASS')
            assert not errors, errors
            print('All seven views and review flow: PASS; no JavaScript errors')
            browser.close()
    finally:
        httpd.shutdown();httpd.server_close();thread.join()
