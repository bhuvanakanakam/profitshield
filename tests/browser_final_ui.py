"""Full final-UI regression: real local backend, disposable auth/data, no external calls.

Run: python3 tests/browser_final_ui.py (preinstalled Playwright + Chromium).
Optional screenshots: PROFITSHIELD_UI_SCREENSHOTS=/tmp/profitshield-ui.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from owner_auth import OwnerAuth
from input_security import RequestLimiter
from playwright.sync_api import sync_playwright, expect

with tempfile.TemporaryDirectory() as temporary, patch.object(server, 'DB_PATH', Path(temporary)/'data.db'), patch.object(
        server, 'KEY', ''), patch.object(server, 'SQUARE_TOKEN', ''), patch.object(
        server, 'REQUEST_LIMITER', RequestLimiter(10000, 20000, 10000)), patch.object(
        server.urllib.request, 'urlopen', side_effect=AssertionError('No external integrations')), patch.object(
        server.SQUARE_OPENER, 'open', side_effect=AssertionError('No Square calls')):
    server.init_db()
    httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    origin='http://127.0.0.1:'+str(httpd.server_port)
    auth=OwnerAuth(Path(temporary)/'auth.db',origin,local_http=True)
    password='final-ui-fixture-password'
    auth.set_password(password)
    httpd.owner_auth=auth
    thread=threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    try:
        with patch.object(server,'PUBLIC_ORIGIN',origin),sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce',accept_downloads=True)
            errors=[];requests=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda request:requests.append((request.method,request.url)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) or route.request.url.startswith('blob:') else route.abort())
            def screenshot(name):
                directory=os.environ.get('PROFITSHIELD_UI_SCREENSHOTS')
                if directory:
                    Path(directory).mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(Path(directory)/(name+'.png')),full_page=True)
            page.goto(origin)
            expect(page.locator('#password')).to_be_visible()
            assert page.request.get(origin+'/api/prototype').status==401
            assert page.request.get(origin+'/api/data').status==401
            screenshot('login-desktop')
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            screenshot('login-mobile')
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('#password').fill('incorrect-owner-password')
            page.locator('#login button').click()
            expect(page.locator('#error')).to_contain_text('Sign in failed')
            expect(page.locator('#password')).to_have_attribute('aria-invalid','true')
            page.locator('#password').fill(password)
            page.locator('#login button').click()
            expect(page.locator('.workspace-empty')).to_be_visible()
            expect(page.locator('#demoSimForm button')).to_be_disabled()
            expect(page.locator('#demoGuruForm button')).to_be_disabled()
            print('Login, mobile login, denied anonymous records, and honest empty state: PASS')
            page.locator('#helpOpen').click()
            expect(page.locator('#helpDialog')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#helpOpen')).to_be_focused()
            # Two immediate clicks can issue only one mutation while controls are locked.
            page.evaluate("document.querySelector('#demoLoad').click();document.querySelector('#demoLoad').click()")
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            assert sum(method=='POST' and url.endswith('/api/prototype/load-golden') for method,url in requests)==1
            expect(page.locator('#demoSimForm button')).to_be_enabled()
            print('Help focus return and double-click protection: PASS')
            # Verify actual primary color contrast, and that its background is light.
            colors=page.locator('#demoLoad').evaluate('(node)=>{const s=getComputedStyle(node);return [s.color,s.backgroundColor]}')
            def luminance(rgb):
                values=[int(x.strip())/255 for x in rgb[rgb.index('(')+1:rgb.index(')')].split(',')[:3]]
                linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
                return sum(a*b for a,b in zip(linear,[.2126,.7152,.0722]))
            ink,background=map(luminance,colors)
            assert background>.5 and (background+.05)/(ink+.05)>=4.5,colors
            print('Light sage CTA with at least 4.5:1 text contrast: PASS')
            # Unreadable/import-error responses must keep last successfully loaded records.
            page.locator('#demoFile').set_input_files({'name':'bad.csv','mimeType':'text/csv','buffer':b'not,a,financial,file\nx,y,z,q'})
            expect(page.locator('#demoError')).not_to_be_empty()
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            page.locator('#demoFile').set_input_files({'name':'bad.csv','mimeType':'text/csv','buffer':(server.prototype.DATA/'golden.csv').read_bytes()})
            expect(page.locator('#demoImportReport')).to_contain_text('4 duplicate rows skipped')
            expect(page.locator('#demoError')).to_be_empty()
            print('Import rejection preserves financial data; same-name corrected file can retry: PASS')
            commission=page.locator('#demoFindings article').filter(has_text='commission overcharge')
            commission.get_by_role('button',name='Prepare draft').click()
            expect(page.locator('#demoDispute')).to_contain_text('Claim: $100.00')
            with page.expect_download() as capture:
                page.locator('#demoDispute button').click()
            download=capture.value
            assert download.suggested_filename.endswith('.txt')
            assert 'Claim: $100.00' in Path(download.path()).read_text()
            print('Evidence-backed dispute download: PASS')
            # A backend outage is visible, does not zero the baseline, and is retryable.
            page.route('**/api/prototype',lambda route:route.fulfill(status=503,body='{"error":"Temporary backend outage"}',content_type='application/json'))
            page.locator('#demoRefresh').click()
            expect(page.locator('#demoError')).to_contain_text('Temporary backend outage')
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            page.unroute('**/api/prototype')
            page.locator('#demoRefresh').click()
            expect(page.locator('#demoError')).to_be_empty()
            print('Backend outage retains last baseline and recovers on refresh: PASS')
            page.locator('nav [data-go="sources"]').click()
            page.locator('#csvFile').set_input_files(str(server.SAMPLES/'grubhub_export_unusual_columns.csv'))
            expect(page.locator('#csvResult')).to_contain_text('0 new rows')
            expect(page.locator('#csvResult')).to_contain_text('100 personal fields removed')
            print('Historical CSV mapping, dedupe and redaction through UI/backend: PASS')
            # A receipt is still editable after a rejected save.
            page.locator('#sampleBtn').click()
            page.route('**/api/invoices',lambda route:route.fulfill(status=400,body='{"error":"Fixture validation rejection"}',content_type='application/json'))
            page.locator('#addBooks').click()
            expect(page.locator('#toast')).to_contain_text("Couldn't save")
            expect(page.locator('#addBooks')).to_be_enabled()
            expect(page.locator('#review')).to_contain_text('Restaurant Depot')
            page.unroute('**/api/invoices')
            page.locator('#addBooks').click()
            expect(page.locator('#toast')).to_contain_text('Saved to your books')
            expect(page.locator('#review')).to_be_empty()
            print('Receipt rejection preserves review; retry saves to historical backend: PASS')
            page.locator('nav [data-go="overview"]').click()
            week=page.locator('#weekSel')
            week.select_option(index=0)
            expect(page.locator('#pageSub')).to_contain_text('Oct 12')
            expect(page.locator('#weekSel')).to_be_enabled()
            assert any(method=='POST' and url.endswith('/api/simulate') for method,url in requests)
            week.select_option(index=2)
            expect(page.locator('#pageSub')).to_contain_text('Oct 26')
            expect(page.locator('#feed')).to_contain_text('Suggested fix')
            page.locator('#topLeaks .leak').first.click()
            expect(page.locator('#drawer')).to_be_visible()
            assert page.locator('.app').evaluate('(node)=>node.inert')
            page.keyboard.press('Shift+Tab')
            assert page.locator('#drawer').evaluate('(node)=>node.contains(document.activeElement)')
            page.keyboard.press('Escape')
            expect(page.locator('#drawer')).to_be_hidden()
            assert not page.locator('.app').evaluate('(node)=>node.inert')
            print('Week recalculation and evidence drawer keyboard containment: PASS')
            page.evaluate("openAssist();ask('How did estimated margin change this week?')")
            expect(page.locator('#msgs')).to_contain_text('Estimated contribution margin went from')
            page.route('**/api/simulate',lambda route:route.fulfill(status=503,body='{"error":"Simulation is temporarily unavailable"}',content_type='application/json'))
            page.evaluate("openAssist();ask('What if I raise pizza prices by $0.75?')")
            expect(page.locator('#msgs')).to_contain_text('Unable to finish that request')
            expect(page.locator('#msgs .typing')).to_have_count(0)
            expect(page.locator('#askForm button')).to_be_enabled()
            page.unroute('**/api/simulate')
            page.evaluate('closeAssist()')
            print('Historical assistant handles calculation outage without a stuck typing state: PASS')
            # Missing financial inputs remain unknown and prevent unsafe simulation.
            page.locator('nav [data-go="demo"]').click()
            page.on('dialog',lambda dialog:dialog.accept())
            page.locator('#demoReset').click()
            expect(page.locator('.workspace-empty')).to_be_visible()
            incomplete=(server.prototype.DATA/'golden.csv').read_text().replace(',1000,80,',',,80,')
            page.locator('#demoFile').set_input_files({'name':'incomplete.csv','mimeType':'text/csv','buffer':incomplete.encode()})
            expect(page.locator('#demoSimForm button')).to_be_disabled()
            expect(page.locator('#scenarioRequirements')).to_contain_text('missing variable costs')
            expect(page.locator('.baseline-metric').nth(1)).to_contain_text('Unavailable')
            print('Missing costs remain unavailable; scenario controls prevent unsafe calculation: PASS')
            # Show the final normal state for screenshots and check every route at mobile sizes.
            page.locator('#demoLoad').click()
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            screenshot('workspace-desktop')
            for width in [390,768,1024,1440]:
                page.set_viewport_size({'width':width,'height':900})
                for view in ['demo','overview','leaks','agents','payouts','invoices','margins','sources']:
                    page.locator('nav [data-go="'+view+'"]').click()
                    expect(page.locator('#v-'+view)).to_be_visible()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,view)
                page.locator('nav [data-go="demo"]').click()
                if width==390:screenshot('workspace-mobile')
            print('All eight views at 390/768/1024/1440px without page overflow: PASS')
            page.locator('#logout').click()
            expect(page.locator('#password')).to_be_visible()
            assert page.request.get(origin+'/api/prototype').status==401
            assert not errors,errors
            print('Logout revokes financial API access; no browser JavaScript errors: PASS')
            browser.close()
    finally:
        httpd.shutdown();httpd.server_close();thread.join()
