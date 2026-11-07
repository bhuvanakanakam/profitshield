"""Bounded API inputs and single-process request limits; no external services."""
import base64
import binascii
import math
import re
import threading
import time
from urllib.parse import urlsplit

BODY_LIMIT = 16 * 1024 * 1024
CSV_LIMIT = 4 * 1024 * 1024
FILE_LIMIT = 8 * 1024 * 1024
ROW_LIMIT = 20000
CHANNELS = {'DoorDash', 'Uber Eats', 'Grubhub', 'In-store'}
UNITS = {'ea', 'lb', 'oz', 'kg', 'g', 'l', 'ml', 'case', 'pack', 'box', 'gal', 'qt', 'pt', 'ct', 'bottle'}
POST_ROUTES = {'/api/import', '/api/invoices', '/api/menu/apply', '/api/reset',
               '/api/square/sync', '/api/square/seed', '/api/ask', '/api/extract',
               '/api/menu/preview', '/api/square/seed/preview'}
EXPENSIVE_ROUTES = {'/api/ask', '/api/extract', '/api/import', '/api/square/sync',
                    '/api/square/seed', '/api/menu/apply', '/api/reset', '/api/invoices',
                    '/api/menu/preview', '/api/square/seed/preview'}


def text(value, name, maximum=255, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise ValueError(f'{name} must be a bounded string' + (' and cannot be blank' if required else ''))
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError(f'{name} must contain valid Unicode') from None
    return value


def number(value, name, minimum=0, maximum=1e12, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} must be a number')
    if not minimum <= value <= maximum or not math.isfinite(value) or (integer and value != int(value)):
        raise ValueError(f'{name} is outside the allowed numeric range')
    return value


def bounded_json(value, depth=0):
    if depth > 12:
        raise ValueError('JSON nesting is too deep')
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (float, int)):
        number(value, 'JSON number', minimum=-1e12)
    elif isinstance(value, str):
        text(value, 'JSON string', BODY_LIMIT)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError('JSON array is too long')
        for item in value:
            bounded_json(item, depth + 1)
    elif isinstance(value, dict):
        if len(value) > 100:
            raise ValueError('JSON object has too many fields')
        for key, item in value.items():
            text(key, 'JSON field name', 100, required=True)
            bounded_json(item, depth + 1)
    else:
        raise ValueError('Unsupported JSON value')


def fields(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError('Invalid or missing JSON fields')


def iso_date(value, name='date'):
    from datetime import date
    text(value, name, 10, required=True)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError(f'{name} must be YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError(f'{name} is not a valid date') from None


def choices(value, allowed, name, maximum=100):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f'{name} must be a nonempty bounded list')
    if any(not isinstance(v, str) or v not in allowed for v in value) or len(set(value)) != len(value):
        raise ValueError(f'{name} contains invalid or repeated values')


def invoice_line(line, ingredients):
    fields(line, {'description', 'ingredient', 'qty', 'unit', 'unit_price', 'total', 'confidence', 'page'},
           {'description', 'qty', 'unit', 'unit_price'})
    text(line['description'], 'description', 120, required=True)
    ingredient = line.get('ingredient')
    if ingredient is not None and (not isinstance(ingredient, str) or ingredient not in ingredients):
        raise ValueError('Unknown ingredient')
    if not isinstance(line['unit'], str) or line['unit'] not in UNITS:
        raise ValueError('Unknown invoice unit')
    if ingredient and line['unit'] != ingredients[ingredient][1]:
        raise ValueError('Convert the line to the ingredient unit before saving')
    number(line['qty'], 'qty', minimum=0.000001, maximum=1e6)
    number(line['unit_price'], 'unit_price', maximum=1e6)
    number(line['qty'] * line['unit_price'], 'line total')
    if 'total' in line:
        number(line['total'], 'total')
    if 'confidence' in line:
        number(line['confidence'], 'confidence', maximum=1)
    if line.get('page') is not None:
        number(line['page'], 'page', minimum=1, maximum=1000, integer=True)


def upload(value):
    text(value, 'image', BODY_LIMIT, required=True)
    match = re.fullmatch(r'data:(image/(?:jpeg|png|webp)|application/pdf);base64,([A-Za-z0-9+/=]+)', value)
    if not match or len(match[2]) > ((FILE_LIMIT + 2)//3)*4:
        raise ValueError('Use a base64 JPEG, PNG, WebP or PDF up to 8 MiB')
    try:
        data = base64.b64decode(match[2], validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('Invalid base64 file') from None
    if not data or len(data) > FILE_LIMIT:
        raise ValueError('File is empty or too large')
    signatures = {'image/jpeg': data.startswith(b'\xff\xd8\xff'),
                  'image/png': data.startswith(b'\x89PNG\r\n\x1a\n'),
                  'image/webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP',
                  'application/pdf': data.startswith(b'%PDF-')}
    if not signatures[match[1]]:
        raise ValueError('File signature does not match its media type')


def validate_payload(route, p, menu, ingredients):
    bounded_json(p)
    if route == '/api/import':
        fields(p, {'text', 'filename', 'channel'}, {'text'})
        text(p['text'], 'CSV', CSV_LIMIT, required=True)
        if len(p['text'].encode('utf-8')) > CSV_LIMIT:
            raise ValueError('CSV exceeds 4 MiB')
        name = text(p.get('filename', 'upload.csv'), 'filename', required=True)
        if not name.lower().endswith('.csv'):
            raise ValueError('CSV filename required')
        if p.get('channel') is not None and (not isinstance(p['channel'], str) or p['channel'] not in CHANNELS):
            raise ValueError('Unknown channel')
    elif route == '/api/invoices':
        fields(p, {'vendor', 'invoice_number', 'date', 'filename', 'lines'}, {'date', 'lines'})
        iso_date(p['date'])
        for key in ('vendor', 'invoice_number', 'filename'):
            if p.get(key) is not None:
                text(p[key], key, 200)
        if not isinstance(p['lines'], list) or not 1 <= len(p['lines']) <= 500:
            raise ValueError('Invoice requires 1–500 lines')
        for line in p['lines']:
            invoice_line(line, ingredients)
    elif route == '/api/menu/preview':
        fields(p, {'changes', 'channels', 'week'}, {'changes', 'channels', 'week'})
        iso_date(p['week'], 'week')
        if not isinstance(p['changes'], list) or not 1 <= len(p['changes']) <= len(menu):
            raise ValueError('Invalid menu changes')
        seen = set()
        for change in p['changes']:
            fields(change, {'sku', 'price', 'old_price'}, {'sku', 'price', 'old_price'})
            sku = change['sku']
            if not isinstance(sku, str) or sku not in menu or sku in seen:
                raise ValueError('Unknown or repeated menu item')
            seen.add(sku)
            number(change['price'], 'price', minimum=0.01, maximum=1e6)
            number(change['old_price'], 'old_price', minimum=0.01, maximum=1e6)
        if 'channels' in p:
            choices(p['channels'], CHANNELS, 'channels', 4)
    elif route in ('/api/menu/apply', '/api/square/seed'):
        required = {'plan_id', 'confirmation'} | ({'week'} if route == '/api/menu/apply' else set())
        fields(p, required, required)
        text(p['plan_id'], 'plan id', 43, required=True)
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', p['plan_id']):
            raise ValueError('Invalid plan id')
        text(p['confirmation'], 'confirmation', 100, required=True)
        if 'week' in p:
            iso_date(p['week'], 'week')
    elif route == '/api/extract':
        fields(p, {'image', 'filename', 'ingredients'}, {'image'})
        upload(p['image'])
        if p.get('filename') is not None:
            text(p['filename'], 'filename', required=True)
        if 'ingredients' in p and (not isinstance(p['ingredients'], list) or len(p['ingredients']) > 100):
            raise ValueError('Invalid ingredients list')
    elif route == '/api/ask':
        fields(p, {'question', 'facts', 'history', 'week', 'prev_week'}, {'question', 'facts', 'week', 'prev_week'})
        text(p['question'], 'question', 2000, required=True)
        iso_date(p['week'], 'week')
        iso_date(p['prev_week'], 'prev_week')
        facts = p['facts']
        fields(facts, {'store', 'week_start', 'this_week', 'last_week', 'summary', 'profit_bridge', 'findings',
                       'menu_margins', 'channels', 'approved_promotions'})
        for key in ('store', 'this_week', 'last_week'):
            if key in facts:
                text(facts[key], key, 255)
        if 'week_start' in facts:
            iso_date(facts['week_start'], 'week_start')
            if facts['week_start'] != p['week']:
                raise ValueError('Conflicting selected weeks')
        if 'summary' in facts:
            summary = facts['summary']
            fields(summary, {'gross_sales_this', 'gross_sales_last', 'profit_this', 'profit_last',
                             'orders_this', 'orders_last', 'food_cost_this', 'food_cost_last', 'note'})
            for key, value in summary.items():
                if key == 'note':
                    text(value, key, 1000)
                else:
                    number(value, key, minimum=-1e12)
        for key in ('findings', 'profit_bridge', 'menu_margins', 'channels', 'approved_promotions'):
            if key in facts and (not isinstance(facts[key], list) or len(facts[key]) > 100 or
                                 any(not isinstance(x, dict) for x in facts[key])):
                raise ValueError('Invalid facts list')
        for finding in facts.get('findings', []):
            fields(finding, {'id', 'title', 'weekly_impact', 'yearly_if_unchanged', 'detail', 'kind', 'suggested_action'}, {'id'})
            text(finding['id'], 'finding id', 10, required=True)
            if not re.fullmatch(r'F[0-9]+', finding['id']):
                raise ValueError('Invalid finding id')
            for key in ('title', 'detail', 'suggested_action'):
                if key in finding:
                    text(finding[key], key, 2000)
            if 'kind' in finding and finding['kind'] not in ('price', 'comm', 'promo', 'err', 'dup'):
                raise ValueError('Invalid finding kind')
            for key in ('weekly_impact', 'yearly_if_unchanged'):
                if key in finding:
                    number(finding[key], key, minimum=-1e12)
        for step in facts.get('profit_bridge', []):
            fields(step, {'step', 'amount', 'finding'}, {'step', 'amount'})
            text(step['step'], 'bridge step', 255)
            number(step['amount'], 'bridge amount', minimum=-1e12)
            if step.get('finding') is not None and (not isinstance(step['finding'], str) or
                                                    not re.fullmatch(r'F[0-9]+', step['finding'])):
                raise ValueError('Invalid bridge citation')
        for margin in facts.get('menu_margins', []):
            fields(margin, {'item', 'price', 'food_cost_last', 'food_cost_this', 'margin_this_pct'}, {'item'})
            text(margin['item'], 'menu item', 255)
            for key, value in margin.items():
                if key != 'item':
                    number(value, key, minimum=-1e12)
        for channel in facts.get('channels', []):
            fields(channel, {'channel', 'orders', 'gross', 'contract_rate'}, {'channel'})
            if not isinstance(channel['channel'], str) or channel['channel'] not in CHANNELS:
                raise ValueError('Unknown channel')
            for key, value in channel.items():
                if key != 'channel':
                    number(value, key, maximum=1 if key == 'contract_rate' else 1e12)
        for promotion in facts.get('approved_promotions', []):
            fields(promotion, {'channel', 'name'}, {'channel', 'name'})
            if not isinstance(promotion['channel'], str) or promotion['channel'] not in CHANNELS:
                raise ValueError('Unknown channel')
            text(promotion['name'], 'promotion name', 255)
        history = p.get('history', [])
        if not isinstance(history, list) or len(history) > 6:
            raise ValueError('Invalid history')
        for h in history:
            fields(h, {'role', 'content'}, {'role', 'content'})
            if h['role'] not in ('user', 'assistant'):
                raise ValueError('Invalid history role')
            text(h['content'], 'history content', 2000)
    else:
        fields(p, set())


def validate_origin(origin):
    parsed = urlsplit(origin)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.path or origin != f'{parsed.scheme}://{parsed.netloc}'):
        raise ValueError('PROFITSHIELD_ORIGIN must be an exact HTTP(S) origin without a path')
    if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '[::1]', '::1'):
        raise ValueError('Hosted origins must use HTTPS')
    return origin


class RequestLimiter:
    """Bounded sliding windows. Peer IP only; never trust client forwarding headers."""
    def __init__(self, per_peer=120, global_limit=300, expensive=10, peers=1024):
        self.per_peer, self.global_limit, self.expensive, self.peers = per_peer, global_limit, expensive, peers
        self.lock = threading.Lock()
        self.windows = {}
        self.global_hits = []
        self.expensive_hits = []

    def allow(self, peer, costly=False, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            self.windows = {key: [t for t in values if t > now-60]
                            for key, values in self.windows.items() if values and values[-1] > now-60}
            self.global_hits = [t for t in self.global_hits if t > now-60]
            self.expensive_hits = [t for t in self.expensive_hits if t > now-60]
            if peer not in self.windows and len(self.windows) >= self.peers:
                return False
            peer_requests = self.windows.get(peer, [])
            if (len(self.global_hits) >= self.global_limit or len(peer_requests) >= self.per_peer or
                    (costly and len(self.expensive_hits) >= self.expensive)):
                return False
            self.windows.setdefault(peer, []).append(now)
            self.global_hits.append(now)
            if costly:
                self.expensive_hits.append(now)
            return True


def validate_tool_args(name, args, menu):
    bounded_json(args)
    allowed = {'check_commissions': {'channel'}, 'find_price_changes': {'min_pct'},
               'simulate_price_change': {'items', 'increase', 'channels'}}.get(name, set())
    fields(args, allowed)
    if 'channel' in args and (not isinstance(args['channel'], str) or args['channel'] not in CHANNELS):
        raise ValueError('Unknown channel')
    if 'min_pct' in args:
        number(args['min_pct'], 'min_pct', maximum=1000)
    if 'increase' in args:
        number(args['increase'], 'increase', minimum=-1000, maximum=1e6)
    if 'channels' in args:
        choices(args['channels'], CHANNELS, 'channels', 4)
    if 'items' in args:
        choices(args['items'], set(menu) | {v[0] for v in menu.values()}, 'items', len(menu))

# Local connected demonstration: same bounded JSON and same-origin transport.
POST_ROUTES.update('/api/prototype/' + operation for operation in (
    'load-golden', 'reset', 'import', 'guru', 'simulate', 'propose', 'approve', 'dispute'))

POST_ROUTES.add('/api/simulate')
