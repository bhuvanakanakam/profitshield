"""Repeatable synthetic judge journey, safe rendering, keyboard/mobile checks.

Run python3 tests/browser_prototype.py with preinstalled Playwright/Chromium.
No credentials, real data, remote AI, or external writes.
"""
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from input_security import RequestLimiter
from playwright.sync_api import sync_playwright, expect

with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'DB_PATH', Path(tmp)/'demo.db'), patch.object(
        server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', ''), patch.object(
        server, 'REQUEST_LIMITER', RequestLimiter(10000, 20000, 10000)), patch.object(
        server.urllib.request, 'urlopen', side_effect=AssertionError('No external calls allowed')), patch.object(
        server.SQUARE_OPENER, 'open', side_effect=AssertionError('No Square writes allowed')):
    server.init_db()
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    origin = 'http://127.0.0.1:'+str(httpd.server_port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.object(server, 'PUBLIC_ORIGIN', origin), sync_playwright() as p:
            browser = p.chromium.launch(executable_path=shutil.which('chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page()
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/*',lambda r:r.continue_() if r.request.url.startswith(origin) else r.abort())
            page.goto(origin)
            page.locator('#v-demo.active').wait_for()
            page.locator('.actions [data-go="ask"]').click()
            expect(page.locator('#demoQuestion')).to_be_focused()
            expect(page.locator('#assist')).to_be_hidden()
            page.locator('#demoLoad').click()
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            expect(page.locator('#demoBaseline')).to_contain_text('51.09%')
            expect(page.locator('#demoBaseline')).to_contain_text('Unavailable — operating expenses incomplete')
            print('Launch / golden baseline: $9,200 net, $4,700 margin, 51.09%, no accounting profit: PASS')
            first = page.locator('#demoFindings article').filter(has_text='commission overcharge')
            first.locator('summary').click()
            expect(first).to_contain_text('DoorDash:G-DD')
            first.get_by_role('button',name='Explain finding').click()
            expect(page.locator('#demoGuru')).to_contain_text('$4,700.00')
            expect(page.locator('#demoGuru')).to_contain_text('Deterministic fallback')
            print('Auditor evidence → Guru shares baseline: PASS')
            page.locator('#demoSimForm button').click()
            expect(page.locator('#demoScenarios')).to_contain_text('$5,127.50')
            expect(page.locator('#demoScenarios')).to_contain_text('$427.50')
            page.locator('#demoScenarios button').click()
            expect(page.locator('#demoActions')).to_contain_text('Proposed')
            approve=page.get_by_role('button',name='Approve simulation',exact=True)
            expect(approve).to_be_disabled()
            phrase=page.locator('#demoActions input')
            phrase.fill('yes')
            expect(approve).to_be_disabled()
            phrase.fill('APPROVE SIMULATION ONLY')
            approve.click()
            expect(page.locator('#demoActions')).to_contain_text('Proposed → Approved → Simulated')
            expect(page.locator('#demoActions')).to_contain_text('NO external API was called')
            print('Exact scenario → explicit approval → honestly simulated execution: PASS')
            first.get_by_role('button',name='Prepare draft').click()
            expect(page.locator('#demoDispute')).to_contain_text('Claim: $100.00')
            duplicate=page.locator('#demoFindings article').filter(has_text='duplicate invoice document')
            duplicate.get_by_role('button',name='Prepare draft').click()
            expect(page.locator('#demoDispute')).to_contain_text('Not established — inquiry only')
            print('Evidence-backed $100 conditional dispute; duplicate document has no payment claim: PASS')
            page.reload()
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            expect(page.locator('#demoActions')).to_contain_text('Simulated')
            expect(page.locator('#demoDispute')).to_contain_text('Not established — inquiry only')
            print('SQLite persistence after reload: PASS')
            # Upload the golden CSV again and prove no duplicate inflation.
            page.locator('#demoFile').set_input_files(str(server.prototype.DATA/'golden.csv'))
            expect(page.locator('#demoImportReport')).to_contain_text('4 duplicate rows skipped')
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            print('Upload normalized CSV / duplicate import does not inflate totals: PASS')
            # Browser tests include malicious order IDs and provenance, not just API serialization.
            malicious=(server.prototype.DATA/'golden.csv').read_text().replace('G-DD','<img src=x onerror=alert(1)>').replace('demo_data/golden.csv:2','<svg onload=alert(1)>')
            page.locator('#demoFile').set_input_files({'name':'xss.csv','mimeType':'text/csv','buffer':malicious.encode()})
            expect(page.locator('#demoImportReport')).to_contain_text('5 normalized transactions')
            page.locator('#demoFindings article').filter(has_text='<img src=x').first.locator('summary').click()
            assert page.locator('#v-demo img,#v-demo svg').count()==0
            print('Untrusted order IDs and source evidence render as text, no injected elements: PASS')
            # Keyboard focus and table semantics, not a claim of full WCAG conformance.
            page.locator('#demoLoad').focus()
            expect(page.locator('#demoLoad')).to_be_focused()
            assert page.locator('#demoBaseline table caption').count()==1
            assert page.locator('#demoBaseline th[scope="col"]').count()==2
            page.keyboard.press('Tab')
            expect(page.locator('#demoReset')).to_be_focused()
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#demoHeading').scroll_into_view_if_needed()
            assert page.locator('#demoHeading').bounding_box()['width'] < 390
            overflow = page.evaluate("[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>window.innerWidth+1).map(e=>({tag:e.tagName,id:e.id,cl:e.className,right:e.getBoundingClientRect().right})).slice(0,15)")
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), overflow
            print('Keyboard navigation / scoped table headers / mobile overflow checks: PASS (not a full WCAG audit)')
            page.on('dialog',lambda d:d.accept())
            page.locator('#demoReset').click()
            expect(page.locator('#demoImportReport')).to_contain_text('0 normalized transactions')
            expect(page.locator('#demoActions')).to_be_empty()
            with server.db() as con:
                assert con.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1776
            assert not errors, errors
            print('Reset demo with original samples preserved; no browser JavaScript errors: PASS')
            browser.close()
    finally:
        httpd.shutdown();httpd.server_close();thread.join()
