"""CSV picker/drop/template/encoding regressions against authenticated local HTTP."""
from pathlib import Path
import csv
import io
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server
from owner_auth import OwnerAuth
from input_security import RequestLimiter
from playwright.sync_api import sync_playwright,expect


def dialect(source,delimiter):
    out=io.StringIO();csv.writer(out,delimiter=delimiter).writerows(csv.reader(io.StringIO(source)))
    return out.getvalue()


with tempfile.TemporaryDirectory() as tmp,patch.object(server,'DB_PATH',Path(tmp)/'data.db'),patch.object(
        server,'KEY',''),patch.object(server,'SQUARE_TOKEN',''),patch.object(
        server,'REQUEST_LIMITER',RequestLimiter(10000,20000,10000)),patch.object(
        server.urllib.request,'urlopen',side_effect=AssertionError('No external calls')),patch.object(
        server.SQUARE_OPENER,'open',side_effect=AssertionError('No Square calls')):
    server.init_db()
    httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    origin='http://127.0.0.1:'+str(httpd.server_port)
    auth=OwnerAuth(Path(tmp)/'auth.db',origin,local_http=True);auth.set_password('csv-browser-fixture-password');httpd.owner_auth=auth
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        with patch.object(server,'PUBLIC_ORIGIN',origin),sync_playwright() as p:
            browser=p.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(accept_downloads=True);errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) or route.request.url.startswith('blob:') else route.abort())
            page.goto(origin);page.locator('#password').fill('csv-browser-fixture-password');page.locator('#login button').click()
            expect(page.locator('.workspace-empty')).to_be_visible()
            golden=(server.prototype.DATA/'golden.csv').read_text()
            semicolon='sep=;\r\n'+dialect(golden,';')
            # Native file picker path: UTF-16 little endian BOM + Excel preamble.
            with page.expect_file_chooser() as picker:
                page.locator('#demoUpload').click()
            picker.value.set_files({'name':'excel.csv','mimeType':'text/csv','buffer':semicolon.encode('utf-16')})
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            expect(page.locator('#demoStatus')).to_contain_text('4 transactions added')
            expect(page.locator('#demoError')).to_be_empty()
            print('Excel semicolon/preamble/UTF-16 upload through picker: PASS')
            # Reupload the same logical data with padded/capitalized normalized headers.
            out=io.StringIO();records=list(csv.reader(io.StringIO(golden)));records[0]=[' '+name.upper()+' ' for name in records[0]];csv.writer(out).writerows(records)
            page.locator('#demoFile').set_input_files({'name':'same-data.csv','mimeType':'text/csv','buffer':out.getvalue().encode()})
            expect(page.locator('#demoStatus')).to_contain_text('4 duplicates skipped')
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            print('Padded/capitalized headers normalize without duplicate inflation: PASS')
            # Real browser drag/drop path, with UTF-16 big endian and tab-separated data.
            tab=dialect(golden,'\t');payload=b'\xfe\xff'+tab.encode('utf-16-be')
            transfer=page.evaluate_handle('bytes=>{const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(bytes)],"tabbed.csv",{type:"text/csv"}));return transfer}',list(payload))
            page.locator('#demoUpload').dispatch_event('drop',{'dataTransfer':transfer})
            expect(page.locator('#demoStatus')).to_contain_text('4 duplicates skipped')
            expect(page.locator('#demoImportReport')).to_contain_text('tabbed.csv')
            print('Tab-separated UTF-16 BE file works through drag/drop: PASS')
            # All-row rejection is no longer announced as an unqualified successful import.
            invalid=golden.replace('G-DD','NEW-DD').replace('4000,300','not-money,300')
            page.locator('#demoFile').set_input_files({'name':'invalid-row.csv','mimeType':'text/csv','buffer':invalid.encode()})
            expect(page.locator('#demoStatus')).to_contain_text('No records added.')
            expect(page.locator('#demoImportReport')).to_contain_text('Rejected row 2')
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            # Unsupported layout points to the correct import path without changing datasets.
            page.locator('#demoFile').set_input_files({'name':'unknown.csv','mimeType':'text/csv','buffer':b'foo,bar\n1,2'})
            expect(page.locator('#demoError')).to_contain_text('Use the normalized CSV template')
            expect(page.locator('#demoError')).to_contain_text('Connections')
            expect(page.locator('#demoBaseline')).to_contain_text('$4,700.00')
            print('Rejected/unsupported files show useful messages and preserve prior data: PASS')
            with page.expect_download() as captured:page.locator('#demoTemplate').click()
            source=Path(captured.value.path()).read_bytes()
            page.locator('#demoFile').set_input_files({'name':'template.csv','mimeType':'text/csv','buffer':source})
            expect(page.locator('#demoStatus')).to_contain_text('1 transactions added')
            expect(page.locator('#demoError')).to_be_empty()
            print('Downloadable synthetic template can be uploaded: PASS')
            # The historical upload shares the robust parser and decoder.
            page.locator('nav [data-go="sources"]').click()
            original=(server.SAMPLES/'doordash_payouts_2026-10-05_to_2026-11-01.csv').read_text()
            source=('sep=;\n'+dialect(original,';')).encode('utf-16')
            with page.expect_file_chooser() as picker:
                page.locator('#csvDrop').click()
            picker.value.set_files({'name':'doordash-excel.csv','mimeType':'text/csv','buffer':source})
            expect(page.locator('#csvResult')).to_contain_text('0 new rows')
            expect(page.locator('#csvResult')).to_contain_text('781 already in your books')
            print('Connections accepts Excel CSV without duplicating seeded orders: PASS')
            assert not errors,errors
            print('No browser JavaScript errors: PASS')
            browser.close()
    finally:httpd.shutdown();httpd.server_close();thread.join()
