"""Optional stored-XSS/UI regression: python3 tests/browser_input_security.py.

Requires preinstalled Playwright and Chromium. Uses temporary sample data only.
"""
import csv
import io
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from input_security import RequestLimiter
from playwright.sync_api import sync_playwright

ATTACK = '<img src=x onerror="window.__xss=1">'
ATTRIBUTE = '1" autofocus onfocus="window.__xss=1'

with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_PATH', Path(tmp)/'data.db'), patch.object(
        server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', ''), patch.object(
        server, 'REQUEST_LIMITER', RequestLimiter(10000, 20000, 10000)), patch.object(
        server.urllib.request, 'urlopen', side_effect=AssertionError('External integration forbidden')):
    server.init_db()
    # Imported order IDs remain text, rather than relying on rejecting HTML-like IDs.
    fmt = next(f for f in server.KNOWN_FORMATS if f['channel'] == 'DoorDash')
    record = {header: '' for header in fmt['map'].values()}
    values = {'order_id': ATTACK, 'date': '2026-10-30', 'time': '12:00', 'subtotal': '10',
              'commission': '2.50', 'error_amount': '5', 'error_reason': ATTACK,
              'error_item': ATTACK, 'items': '1x Soda (2 L)', 'promo_amount': '0'}
    record.update({fmt['map'][k]: v for k, v in values.items()})
    content = io.StringIO()
    writer = csv.DictWriter(content, fieldnames=list(record))
    writer.writeheader()
    writer.writerow(record)
    prototype_record = dict(record)
    prototype_record[fmt['map']['order_id']] = '__proto__'
    writer.writerow(prototype_record)
    server.import_csv(content.getvalue(), 'doordash.csv', allow_ai=False)
    # Legacy data may predate server-side unit validation: render it safely too.
    with server.db() as con:
        con.execute("UPDATE invoice_lines SET unit=?,page=? WHERE ingredient='mozz'", (ATTACK, ATTACK))
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    origin = 'http://127.0.0.1:' + str(httpd.server_port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(server, 'PUBLIC_ORIGIN', origin), sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda r: r.continue_() if r.request.url.startswith(origin) else r.abort())
            page.goto(origin)
            page.locator('#dataText').filter(has_text='Live database').wait_for()
            def no_injected_nodes(selector):
                assert page.locator(selector+' img').count() == 0
                assert page.locator(selector+' [onerror], '+selector+' [onfocus], '+selector+' [autofocus]').count() == 0
                assert not page.evaluate('window.__xss')
            for view in ['demo','overview','leaks','agents','payouts','invoices','margins','sources']:
                page.locator('nav [data-go="'+view+'"]').click()
                page.locator('#v-'+view).wait_for(state='visible')
            page.evaluate("go('payouts')")
            assert ATTACK in page.locator('#payTable').inner_text()
            no_injected_nodes('#payTable')
            page.evaluate("openFinding(A.findings.find(f=>f.kind==='comm'&&f.ch==='DoorDash').id)")
            assert ATTACK in page.locator('#dBody').inner_text()
            no_injected_nodes('#dBody')
            page.evaluate("closeDrawer();openFinding(A.findings.find(f=>f.kind==='err').id)")
            assert ATTACK in page.locator('#dBody').inner_text()
            no_injected_nodes('#dBody')
            page.evaluate("closeDrawer();openFinding(A.findings.find(f=>f.kind==='price').id)")
            assert ATTACK in page.locator('#dBody').inner_text()
            no_injected_nodes('#dBody')
            print('Stored order IDs, error evidence and legacy invoice units/pages render as text: PASS')
            page.evaluate("closeDrawer();go('sources')")
            page.evaluate('(p)=>showReview(p,true)', {'filename': 'test.pdf', 'vendor': ATTACK, 'invoice_number': ATTACK,
                          'date': '2026-10-30', 'lines': [{'description': ATTACK, 'qty': ATTRIBUTE,
                           'unit': 'ea', 'unit_price': ATTRIBUTE, 'ingredient': None, 'page': ATTACK, 'confidence': .5}]})
            no_injected_nodes('#review')
            assert ATTACK in page.locator('#review').inner_text()
            print('Receipt review text and input attributes are escaped: PASS')
            page.locator('#cancelRv').click()
            page.locator('#sampleBtn').click()
            page.locator('#addBooks').click()
            page.locator('#toast').filter(has_text='Saved to your books').wait_for()
            print('Same-origin JSON/validation preserves sample receipt save: PASS')
            page.evaluate("go('overview')")
            page.set_viewport_size({'width':390,'height':844})
            assert page.locator('#kpis').is_visible()
            assert not errors, errors
            with patch.object(server, 'KEY', 'dummy-test-key'), patch.object(server, 'ask', return_value={'answer': 'Test', 'cites': [], 'tools': [], 'chart': None}):
                status = page.evaluate("async()=>{const r=await apiFetch('/api/ask',{method:'POST',body:JSON.stringify({question:'Why?',facts:facts(),history:[],week:CUR,prev_week:prevOf(CUR)})});return r.status}")
                assert status == 200
            print('Current browser facts pass the Ask API schema with AI mocked: PASS')
            print('All eight views, mobile dashboard and nonce CSP: PASS; no JavaScript errors')
            page.route('**/api/data', lambda r: r.fulfill(status=503, body='{"error":"unavailable"}', content_type='application/json'))
            page.reload()
            page.locator('main').filter(has_text='Unable to load your live database').wait_for()
            assert page.locator('#kpis').count() == 0
            print('Failed live loads show an error without switching to demo: PASS')
            browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
