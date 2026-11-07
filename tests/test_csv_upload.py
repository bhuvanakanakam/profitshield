"""CSV upload regressions: common spreadsheet encodings/dialects and safe errors."""
import csv
import io
import json
from pathlib import Path
from csv_import import CSVImportError, csv_reader, csv_rows
import prototype
import server as http_server
from tests.support import BackendCase, server
from tests.test_http_security import HTTPFixture


def recode(source, delimiter=',', headers=None):
    records=list(csv.reader(io.StringIO(source)))
    if headers:records[0]=headers(records[0])
    out=io.StringIO();writer=csv.writer(out,delimiter=delimiter)
    writer.writerows(records)
    return out.getvalue()


class CSVUploadTests(BackendCase):
    def ingest(self, source, filename='financial.csv'):
        with server.db() as con:
            return prototype.ingest(con,source,filename,server.KNOWN_FORMATS,server.parse_date)

    def test_normalized_dialects_and_header_spaces_preserve_cents_and_dedupe(self):
        golden=(prototype.DATA/'golden.csv').read_text()
        variants=[golden,recode(golden,headers=lambda headers:[' '+name.upper()+' ' for name in headers]),
                  recode(golden,';'),recode(golden,'\t'),'sep=;\r\n'+recode(golden,';'),'sep=,\n'+golden,'sep=\t\n'+recode(golden,'\t'),
                  '\ufeff\n'+recode(golden,headers=lambda headers:[' '+name+' ' for name in headers])]
        for index,source in enumerate(variants):
            with self.subTest(index=index):
                data=self.ingest(source,'variant-'+str(index)+'.csv')
                self.assertEqual(data['baseline']['money_cents']['contribution_margin'],470000)
                self.assertEqual(len(data['rows']),4)
                self.assertEqual(data['rejected'],[])
                self.assertEqual(data['last_import']['transactions_added'],4 if index==0 else 0)
                self.assertEqual(data['duplicates_skipped'],0 if index==0 else 4)

    def test_historical_platform_export_accepts_semicolon_and_spaces(self):
        source=(server.SAMPLES/'doordash_payouts_2026-10-05_to_2026-11-01.csv').read_text()
        changed='sep=;\r\n'+recode(source,';',lambda headers:[' '+name+' ' for name in headers])
        result=server.import_csv(changed,'doordash.csv',allow_ai=False)
        self.assertEqual(result['new_rows'],781)
        self.assertEqual(server.import_csv(source,'original.csv',allow_ai=False)['new_rows'],0)

    def test_empty_binary_and_duplicate_headers_are_clear_errors(self):
        golden=(prototype.DATA/'golden.csv').read_text()
        for source,expected in [('', 'empty'),('id,source\n','not supported'),
                                (golden.splitlines()[0]+'\n','no data rows'),
                                (golden.replace('id,source',' id ,id',1),'distinct'),
                                ('\x00data','encoding')]:
            with self.subTest(expected=expected),self.assertRaisesRegex(CSVImportError,expected):
                self.ingest(source)
        with self.assertRaisesRegex(CSVImportError,'no data rows'):
            server.import_csv('Order,Date,Time,Gross Sales,Itemization\n','square.csv',allow_ai=False)

    def test_rejected_rows_are_reported_without_replacing_prior_records(self):
        golden=(prototype.DATA/'golden.csv').read_text()
        before=self.ingest(golden)
        invalid=golden.replace('G-DD,','NEW-DD,',1).replace('4000,300','not-money,300',1)
        after=self.ingest('sep=;\n'+recode(invalid,';'))
        self.assertEqual(after['baseline'],before['baseline'])
        self.assertEqual(after['last_import']['rejected_rows'],1)
        self.assertEqual(after['last_import']['transactions_added'],0)
        self.assertEqual(after['rejected'][0]['row'],3)

    def test_excel_quoted_values_are_not_split_on_the_wrong_separator(self):
        source=(prototype.DATA/'golden.csv').read_text().replace('demo_data/golden.csv:2','"synthetic;source,with commas"')
        data=self.ingest(source)
        self.assertEqual(data['rows'][0]['source'],'synthetic;source,with commas')
        self.assertEqual(data['baseline']['money_cents']['contribution_margin'],470000)

    def test_parser_failure_does_not_write_partial_state(self):
        before=self.ingest((prototype.DATA/'golden.csv').read_text())
        malformed=(prototype.DATA/'golden.csv').read_text()+'"unterminated\n'
        with self.assertRaises(CSVImportError):self.ingest(malformed)
        with server.db() as con:
            self.assertEqual(prototype.output(con)['baseline'],before['baseline'])


class CSVUploadHTTPTests(HTTPFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        http_server.init_db()

    def test_authenticated_api_returns_actionable_csv_error(self):
        for route in ['/api/prototype/import','/api/import']:
            with self.subTest(route=route):
                status,_,body=self.request('POST',route,{'text':'','filename':'empty.csv'})
                self.assertEqual(status,400)
                result=json.loads(body)
                self.assertEqual(result['code'],'csv_import')
                self.assertIn('empty',result['error'])
        self.assertEqual(self.request('POST','/api/prototype/import',{'text':'','filename':'empty.csv'},authenticated=False)[0],401)

    def test_spreadsheet_dialect_works_through_http(self):
        source=(prototype.DATA/'golden.csv').read_text()
        text='sep=;\n'+recode(source,';',lambda headers:[' '+name+' ' for name in headers])
        status,_,body=self.request('POST','/api/prototype/import',{'text':text,'filename':'excel.csv'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['baseline']['money_cents']['contribution_margin'],470000)
