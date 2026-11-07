import io
import json
from unittest.mock import patch
from tests.support import BackendCase, server


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


class MockedIntegrationTests(BackendCase):
    def test_square_sync_uses_only_synthetic_sandbox_transport(self):
        def transport(request, timeout):
            self.assertTrue(request.full_url.startswith('https://connect.squareupsandbox.com/'))
            if request.full_url.endswith('/locations'):
                return response({'locations': [{'id': 'FIXTURE-LOCATION', 'name': 'Fixture sandbox'}]})
            self.assertTrue(request.full_url.endswith('/orders/search'))
            return response({'orders': [{'id': 'SYNTH001-ORDER', 'created_at': '2026-10-30T23:30:00Z',
                'line_items': [{'name': 'Margherita pizza', 'quantity': '2', 'base_price_money': {'amount': 1600}}]}]})
        with patch.object(server, 'SQUARE_TOKEN', 'synthetic-sandbox-token'), patch.object(
                getattr(server, 'SQUARE_OPENER', server.urllib.request),
                'open' if hasattr(server, 'SQUARE_OPENER') else 'urlopen', side_effect=transport) as mocked:
            self.assertEqual(server.square_sync()['new_rows'], 1)
            self.assertEqual(server.square_sync()['new_rows'], 0)
            self.assertEqual(mocked.call_count, 4)
        order = server.app_data()['orders'][0]
        self.assertEqual((order['subtotal'], order['date'], order['hour'], order['minute']), (32, '2026-10-30', 19, 30))

    def test_receipt_extraction_uses_synthetic_model_output(self):
        extracted = {'vendor': 'Fixture Foods', 'invoice_number': 'FIXTURE-PHOTO', 'date': '2026-10-30', 'total': 8,
                     'lines': [{'description': 'MOZZARELLA', 'qty': 2, 'unit': 'lb', 'unit_price': 4,
                                'ingredient': 'mozz', 'confidence': .9, 'page': 2}]}
        with patch.object(server, 'call_openrouter', return_value=json.dumps(extracted)) as vision:
            data = server.extract({'image': 'data:image/jpeg;base64,/9j/dGVzdA==', 'filename': 'fixture.jpg'})
            self.assertEqual(vision.call_count, 1)
        self.assertEqual((data['lines'][0]['ingredient'], data['lines'][0]['page'], data['lines'][0]['total']), ('mozz', 2, 8))
        self.network.assert_not_called()

    def test_agent_tool_call_is_grounded_without_real_ai(self):
        self.import_fixture('doordash_synthetic.csv')
        requests = []
        def model(request, timeout):
            self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
            body = json.loads(request.data)
            requests.append(body)
            if len(requests) == 1:
                return response({'choices': [{'message': {'content': '', 'tool_calls': [{
                    'id': 'fixture-call', 'type': 'function', 'function': {'name': 'check_commissions', 'arguments': '{}'}}]}}]})
            tool = json.loads(body['messages'][-1]['content'])
            self.assertEqual(tool['total_overcharge'], 1.6)
            return response({'choices': [{'message': {'content': 'Fixture overcharge is $1.60 [F1].'}}]})
        with patch.object(server, 'EXTERNAL_AI_APPROVED', True), patch.object(server.urllib.request, 'urlopen', side_effect=model):
            result = server.ask({'question': 'Check fixture commission', 'week': '2026-10-26', 'prev_week': '2026-10-19',
                                 'facts': {'findings': [{'id': 'F1', 'kind': 'comm'}]}, 'history': []})
        self.assertEqual((result['tools'], result['cites']), (['check_commissions'], ['F1']))
        self.assertEqual(len(requests), 2)
