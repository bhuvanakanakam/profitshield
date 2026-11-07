"""Exact reconciliation, connected workflow, persistence and security regression."""
import csv
import io
import json
import sqlite3
import time
from copy import deepcopy
from unittest.mock import patch
from financial_engine import normalize, calculate, findings, simulate, cents
import prototype
import server as http_server
from tests.support import BackendCase, server
from tests.test_http_security import HTTPFixture


def raw_golden():
    return list(csv.DictReader(io.StringIO((prototype.DATA / 'golden.csv').read_text())))


def golden():
    rows, rejected, duplicates = normalize(raw_golden())
    assert not rejected and not duplicates
    return rows


class FinancialGoldenTests(BackendCase):
    def test_exact_golden_reconciliation(self):
        b = calculate(golden())
        m = b['money_cents']
        self.assertEqual(m, dict(gross_sales=1000000, refunds=30000, restaurant_promotions=50000,
            platform_promotions=10000, net_sales=920000, commissions=150000, commission_overcharges=10000, other_platform_fees=0,
            food_costs=280000, other_variable_costs=20000, contribution_margin=470000, accounting_profit=None))
        self.assertEqual(m['gross_sales'] - m['refunds'] - m['restaurant_promotions'], m['net_sales'])
        self.assertEqual(m['net_sales'] - m['commissions'] - m['food_costs'] - m['other_variable_costs'], m['contribution_margin'])
        self.assertEqual(b['contribution_margin_pct_display'], '51.09')
        self.assertTrue(b['contribution_margin_pct'].startswith('51.0869565'))
        self.assertEqual(b['accounting_profit_status'], 'Unavailable — operating expenses incomplete')
        self.assertTrue(all(isinstance(x, int) for x in m.values() if x is not None))

    def test_signed_refunds_and_negative_adjustment_are_preserved(self):
        raw = raw_golden()
        adjustment = dict(raw[0], id='NEG', gross_sales='-100', refunds='-10', restaurant_promotions='0',
                          commissions='-20', food_costs='0', other_variable_costs='0')
        rows, rejected, _ = normalize([adjustment])
        self.assertFalse(rejected)
        self.assertEqual(rows[0]['gross_sales'], -10000)
        self.assertEqual(rows[0]['refunds'], -1000)
        self.assertEqual(calculate(rows)['money_cents']['contribution_margin'], -7000)

    def test_duplicates_never_inflate_and_conflicts_are_rejected(self):
        raw = raw_golden()
        rows, rejects, skipped = normalize(raw + [dict(raw[0], source='second-download:2')])
        self.assertEqual(skipped, 1)
        self.assertFalse(rejects)
        self.assertEqual(calculate(rows)['money_cents']['contribution_margin'], 470000)
        rows, rejects, skipped = normalize(raw + [dict(raw[0], gross_sales='9999')])
        self.assertEqual(len(rejects), 1)
        self.assertIn('Conflicting duplicate', rejects[0]['reason'])

    def test_platform_funding_is_not_deducted(self):
        rows = golden()
        expected = calculate(rows)['money_cents']['contribution_margin']
        rows[1]['platform_promotions'] = 999999
        self.assertEqual(calculate(rows)['money_cents']['contribution_margin'], expected)

    def test_contractual_basis_rate_and_no_double_counting(self):
        alerts = findings(golden())
        commission = [f for f in alerts if f['type'] == 'commission_overcharge']
        self.assertEqual(len(commission), 1)
        self.assertEqual(commission[0]['impact_cents'], 10000)
        self.assertEqual(calculate(golden())['money_cents']['contribution_margin'], 470000)
        rows = golden()
        rows[0]['contract_basis'] = 'net_sales'
        self.assertEqual(findings(rows)[0]['impact_cents'], 26000)
        rows[0]['contract_rate'] = None
        self.assertFalse(any(f['type'] == 'commission_overcharge' and f['platform'] == 'DoorDash' for f in findings(rows)))

    def test_invoice_documents_do_not_establish_payment(self):
        docs = json.loads((prototype.DATA / 'documents.json').read_text())
        alerts = findings(golden(), docs)
        duplicate = next(f for f in alerts if f['type'] == 'duplicate_invoice_document')
        self.assertIsNone(duplicate['impact_cents'])
        self.assertEqual(duplicate['status'], 'suspected')
        self.assertIn('NOT established', duplicate['reason'])
        self.assertEqual(next(f for f in alerts if f['type'] == 'invoice_price_increase')['status'], 'confirmed')
        self.assertEqual(len({a['id'] for a in alerts}), len(alerts))

    def test_missing_fields_and_zero_sales(self):
        rows = golden()
        rows[0]['food_costs'] = None
        result = calculate(rows)
        self.assertIsNone(result['money_cents']['contribution_margin'])
        self.assertFalse(result['complete'])
        self.assertIsNone(result['money_cents']['accounting_profit'])
        self.assertEqual(calculate([])['contribution_margin_pct'], None)
        zero = deepcopy(golden()[0])
        for field in prototype.calculate([zero])['money_cents']:
            if field in zero:
                zero[field] = 0
        self.assertIsNone(calculate([zero])['contribution_margin_pct'])
        self.assertEqual(calculate(golden(), operating_expenses='1000')['money_cents']['accounting_profit'], 370000)

    def test_malformed_nonfinite_precision_dates_channels_and_missing(self):
        for change in [dict(gross_sales='NaN'), dict(gross_sales='Infinity'), dict(gross_sales='1.001'),
                       dict(gross_sales='1e-10000000'), dict(gross_sales='1e100'), dict(date='2026-02-30'), dict(channel='Other'),
                       dict(contract_rate='1.1'), dict(contract_basis='payout'), dict(id=''),
                       dict(promotion_approved='maybe')]:
            with self.subTest(change=change):
                rows, rejected, _ = normalize([dict(raw_golden()[0], **change)])
                self.assertEqual(rows, [])
                self.assertEqual(len(rejected), 1)
        with self.assertRaises(ValueError):
            cents(True)

    def test_scenario_uses_same_engine_and_unchanged_demand(self):
        scenario = simulate(golden(), {'price_change_pct': 5})
        self.assertEqual(scenario['before'], calculate(golden()))
        self.assertEqual(scenario['after']['money_cents']['gross_sales'], 1050000)
        self.assertEqual(scenario['after']['money_cents']['commissions'], 157250)
        self.assertEqual(scenario['delta_cents'], 42750)
        self.assertIn('Unchanged demand and quantities', scenario['assumptions'])
        self.assertEqual(simulate(golden(), {'ingredient_cost_change_pct': 10})['delta_cents'], -28000)
        commission_scenario = simulate(golden(), {'commission_rate_change': .01})
        self.assertEqual(commission_scenario['delta_cents'], -10000)
        self.assertEqual(commission_scenario['after']['money_cents']['commission_overcharges'], 10000)
        for changes in [{'price_change_pct': 101}, {'demand': 100}, {'commission_rate_change': -1}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                simulate(golden(), changes)

    def test_isolated_edge_fixtures_and_repeated_errors(self):
        cases = json.loads((prototype.DATA / 'edge_cases.json').read_text())
        rows, rejected, skipped = normalize(cases['negative_adjustment'])
        self.assertEqual(calculate(rows)['money_cents']['contribution_margin'], -7000)
        rows, rejected, skipped = normalize(cases['duplicate_import'])
        self.assertEqual(skipped, 1)
        self.assertEqual(len(rows), 1)
        rows, rejected, skipped = normalize(cases['missing_field'])
        self.assertIsNone(calculate(rows)['money_cents']['contribution_margin'])
        rows, rejected, skipped = normalize(cases['zero_sales'])
        self.assertIsNone(calculate(rows)['contribution_margin_pct'])
        self.assertTrue(normalize(cases['malformed'])[1])
        rows, rejected, skipped = normalize(cases['repeated_error_charges'])
        alerts = [f for f in findings(rows) if 'error' in f['type'] or f['type'] == 'platform_fee']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['impact_cents'], 1200)

    def test_multiple_lines_are_not_duplicate_invoice_documents(self):
        docs = json.loads((prototype.DATA / 'documents.json').read_text())
        docs = [dict(docs[0], source='invoice.csv:2', document_source='One PDF'),
                dict(docs[0], ingredient='dough', source='invoice.csv:3', document_source='One PDF')]
        self.assertFalse(any(f['type'] == 'duplicate_invoice_document' for f in findings(golden(), docs)))

    def test_legacy_adapter_uses_same_engine_without_document_penalties(self):
        self.import_fixture('doordash_synthetic.csv')
        self.import_fixture('supplier_synthetic.csv')
        data = server.app_data()
        metric = data['financial_model']['metrics']['2026-10-26']
        self.assertEqual(metric['profit'] * 100, metric['baseline']['money_cents']['contribution_margin'])
        self.assertIsNone(metric['baseline']['money_cents']['accounting_profit'])
        self.assertTrue(metric['estimated'])

    def test_external_ai_is_blocked_without_privacy_approval(self):
        with patch.object(server, 'EXTERNAL_AI_APPROVED', False), patch.object(server, 'KEY', 'dummy'):
            with self.assertRaisesRegex(ValueError, 'privacy'):
                server.ask({'question':'Why?', 'facts':{}, 'week':'2026-10-26', 'prev_week':'2026-10-19', 'history':[]})
            with self.assertRaisesRegex(ValueError, 'privacy'):
                server.call_openrouter('fixture', [])
        self.network.assert_not_called()


class ConnectedWorkflowTests(BackendCase):
    def op(self, operation, payload=None):
        with server.LOCK, server.db() as con:
            return prototype.operate(con, operation, payload or {})

    def test_six_agents_share_baseline_and_persist(self):
        loaded = self.op('load-golden')
        baseline = loaded['baseline']
        finding = next(f for f in loaded['findings'] if f['type'] == 'commission_overcharge')
        guru = self.op('guru', {'question': 'Why?', 'finding_id': finding['id']})
        self.assertEqual(guru['baseline'], baseline)
        self.assertIn('$4,700.00', guru['answer'])
        scenario = self.op('simulate', {'changes': {'price_change_pct': 5}})
        self.assertEqual(scenario['before'], baseline)
        action = self.op('propose', {'scenario_id': scenario['id']})
        self.assertEqual(action['baseline_id'], baseline['baseline_id'])
        with self.assertRaises(ValueError):
            self.op('approve', {'action_id': action['id'], 'confirmation': 'yes'})
        result = self.op('approve', {'action_id': action['id'], 'confirmation': prototype.CONFIRMATION})
        self.assertEqual(result['state'], 'Simulated')
        self.assertEqual(result['history'], ['Proposed', 'Approved', 'Simulated'])
        self.assertFalse(result['verified_external'])
        with self.assertRaises(ValueError):
            self.op('approve', {'action_id': action['id'], 'confirmation': prototype.CONFIRMATION})
        draft = self.op('dispute', {'finding_id': finding['id']})
        self.assertEqual(draft['baseline'], baseline)
        self.assertEqual(draft['claim_amount_cents'], 10000)
        with server.db() as con:
            persisted = prototype.output(con)
        self.assertEqual(persisted['baseline'], baseline)
        self.assertEqual(persisted['actions'][0]['state'], 'Simulated')
        self.assertEqual(len(persisted['scenarios']), 1)
        self.assertEqual(persisted['disputes'][0]['claim_amount_cents'], 10000)
        self.assertEqual(self.op('reset')['rows'], [])
        self.assertFalse(self.op('reset')['actions'])
        self.network.assert_not_called()

    def test_duplicate_document_draft_has_no_claim(self):
        data = self.op('load-golden')
        duplicate = next(f for f in data['findings'] if f['type'] == 'duplicate_invoice_document')
        draft = self.op('dispute', {'finding_id': duplicate['id']})
        self.assertIsNone(draft['claim_amount_cents'])
        self.assertEqual(draft['supporting_transactions'], [])

    def test_import_dedupe_rejections_and_persistence(self):
        data = self.op('load-golden')
        csv_text = (prototype.DATA / 'golden.csv').read_text()
        repeated = self.op('import', {'text': csv_text, 'filename': 'repeated.csv'})
        self.assertEqual(repeated['duplicates_skipped'], 4)
        self.assertEqual(repeated['baseline'], data['baseline'])
        bad = csv_text.replace('G-DD,', 'NEW,', 1).replace('4000,300', 'not-money,300', 1)
        result = self.op('import', {'text': bad, 'filename': 'bad.csv'})
        self.assertEqual(len(result['rejected']), 1)
        self.assertEqual(result['baseline'], data['baseline'])

    def test_stale_and_expired_proposals_fail_without_external_updates(self):
        self.op('load-golden')
        scenario = self.op('simulate', {'changes': {}})
        action = self.op('propose', {'scenario_id': scenario['id']})
        with patch.object(prototype.time, 'time', return_value=time.time() + 601):
            result = self.op('approve', {'action_id': action['id'], 'confirmation': prototype.CONFIRMATION})
        self.assertEqual(result['state'], 'Failed')
        action = self.op('propose', {'scenario_id': scenario['id']})
        with server.db() as con:
            state = prototype.read(con)
            state['rows'][0]['gross_sales'] += 100
            prototype.write(con, state)
        with self.assertRaises(ValueError):
            self.op('propose', {'scenario_id': scenario['id']})
        result = self.op('approve', {'action_id': action['id'], 'confirmation': prototype.CONFIRMATION})
        self.assertEqual(result['state'], 'Failed')

    def test_known_platform_and_supplier_exports_are_normalized_without_fabricated_costs(self):
        samples = [
            ('doordash_payouts_2026-10-05_to_2026-11-01.csv',781),
            ('ubereats_payments_2026-10-05_to_2026-11-01.csv',442),
            ('grubhub_transactions_2026-10-05_to_2026-11-01.csv',184),
            ('square_orders_2026-10-05_to_2026-11-01.csv',369),
            ('supplier_invoices_2026-10-05_to_2026-11-01.csv',0)]
        count = 0
        for filename, expected in samples:
            with server.db() as con:
                data = prototype.operate(con, 'import', {'filename': filename,
                    'text': (server.SAMPLES / filename).read_text(encoding='utf-8-sig')},
                    server.KNOWN_FORMATS, server.parse_date)
            count += expected
            self.assertEqual(len(data['rows']), count)
            self.assertEqual(data['rejected'], [])
            self.assertIsNone(data['baseline']['money_cents']['contribution_margin'])
        self.assertEqual(len(data['documents']),63)
        self.assertFalse(any(f['type']=='commission_overcharge' for f in data['findings']))
        # A second import has identical logical document lines even if renamed.
        with server.db() as con:
            repeated = prototype.operate(con, 'import', {'filename':'renamed.csv',
                'text': (server.SAMPLES / samples[-1][0]).read_text(encoding='utf-8-sig')},
                server.KNOWN_FORMATS, server.parse_date)
        self.assertEqual(repeated['duplicates_skipped'],63)
        self.assertEqual(len(repeated['documents']),63)
        self.network.assert_not_called()

    def test_known_import_preserves_signed_credits_and_rejected_row_numbers(self):
        text = ('DoorDash Order ID,Order Date,Order Time,Subtotal,Commission,Merchant Funded Promotion,Promotion Name,Error Charges,Error Reason,Error Item,Items\n'
                'CREDIT,2026-10-26,12:00,-100,-20,0,,0,,,1x Margherita pizza\n'
                'INVALID,2026-02-30,12:00,1,0,0,,0,,,1x Margherita pizza\n')
        with server.db() as con:
            data = prototype.operate(con, 'import', {'filename':'signed.csv','text':text},server.KNOWN_FORMATS,server.parse_date)
        self.assertEqual(data['rows'][0]['gross_sales'],-10000)
        self.assertEqual(data['rows'][0]['commissions'],-2000)
        self.assertEqual(data['rejected'][0]['row'],3)


class PrototypeHTTPTests(HTTPFixture):
    def setUp(self):
        with patch.object(server, 'seed'):
            http_server.init_db()
        with http_server.db() as con:
            prototype.operate(con, 'reset', {})

    def post(self, operation, payload):
        status, headers, body = self.request('POST', '/api/prototype/' + operation, payload)
        return status, json.loads(body)

    def test_http_connected_baselines_and_head(self):
        status, data = self.post('load-golden', {})
        self.assertEqual(status, 200)
        baseline = data['baseline']
        status, scenario = self.post('simulate', {'changes': {'price_change_pct': 5}})
        self.assertEqual(status, 200)
        self.assertEqual(scenario['before'], baseline)
        self.assertEqual(json.loads(self.request('GET', '/api/prototype?cache=1')[2])['baseline'], baseline)
        self.assertEqual(self.request('HEAD', '/api/prototype')[2], b'')

    def test_cross_origin_malformed_oversized_and_encoded_security(self):
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', {}, {'Origin': 'https://evil.test'})[0], 403)
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', {}, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', raw='[]')[0], 400)
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', raw='{"x":NaN}')[0], 400)
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', raw='{"x":1,"x":2}')[0], 400)
        self.assertEqual(self.request('POST', '/api/prototype/load-golden', raw='x', headers={'Content-Length': '16777217'})[0], 413)
        self.assertEqual(self.request('GET', '/financial_engine.py')[0], 404)
        self.assertEqual(self.request('GET', '/demo_data/golden.csv')[0], 404)
        self.assertEqual(self.request('GET', '/%61pi/prototype')[0], 404)
        self.assertEqual(self.request('POST', '/api/prototype/approve?bypass=1', {})[0], 404)

    def test_malformed_identifiers_and_nonfinite_scenarios_are_client_errors(self):
        self.post('load-golden', {})
        for operation, payload in [('propose', {'scenario_id': []}), ('approve', {'action_id': {}, 'confirmation':'x'}),
                                   ('dispute', {'finding_id': 123}), ('guru', {'question':'Why?', 'finding_id': []}),
                                   ('simulate', {'changes': {'price_change_pct': 'NaN'}})]:
            with self.subTest(operation=operation):
                self.assertEqual(self.post(operation, payload)[0], 400)
        self.assertEqual(self.request('POST','/api/simulate',{'week':'2026-10-26','items':['marg'],'increase':float('inf')})[0],400)

    def test_stored_xss_remains_data(self):
        malicious = (prototype.DATA / 'golden.csv').read_text().replace('G-DD', '<img src=x onerror=alert(1)>')
        status, data = self.post('import', {'text': malicious, 'filename': 'xss.csv'})
        self.assertEqual(status, 200)
        self.assertEqual(data['rows'][0]['id'], '<img src=x onerror=alert(1)>')
        self.assertIn("script-src 'nonce-", self.request('GET', '/')[1]['Content-Security-Policy'])
