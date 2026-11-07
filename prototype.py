"""Connected local synthetic demonstration; no network/AI/external mutations."""
import csv
import io
import json
import re
import secrets
import time
from pathlib import Path
from financial_engine import normalize, calculate, findings, simulate, digest, MONEY, decimal, cents

DATA = Path(__file__).resolve().parent / 'demo_data'
CONFIRMATION = 'APPROVE SIMULATION ONLY'


def initialize(con):
    con.execute('CREATE TABLE IF NOT EXISTS prototype_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS prototype_scenarios (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS prototype_actions (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS prototype_disputes (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    con.execute('INSERT OR IGNORE INTO prototype_state VALUES(1, ?)', (json.dumps(
        {'rows': [], 'documents': [], 'rejected': [], 'duplicates_skipped': 0, 'label': 'No normalized data loaded'}),))


def read(con):
    return json.loads(con.execute('SELECT data FROM prototype_state WHERE id=1').fetchone()[0])


def write(con, state):
    con.execute('UPDATE prototype_state SET data=? WHERE id=1', (json.dumps(state),))


def output(con):
    state = read(con)
    baseline = calculate(state['rows'])
    alerts = findings(state['rows'], state['documents'])
    return {**state, 'baseline': baseline, 'findings': alerts,
            'briefing': f"Review {len(alerts)} evidence-backed findings. Suspected exposures are not guaranteed recoveries.",
            'scenarios': [json.loads(r[0]) for r in con.execute('SELECT data FROM prototype_scenarios ORDER BY rowid')],
            'actions': [json.loads(r[0]) for r in con.execute('SELECT data FROM prototype_actions ORDER BY rowid')],
            'disputes': [json.loads(r[0]) for r in con.execute('SELECT data FROM prototype_disputes ORDER BY rowid')],
            'mode': 'local synthetic demonstration', 'external_ai_used': False}


def require_fields(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - set(allowed):
        raise ValueError('Unknown request fields')


def identifier(payload, field, length=32):
    value = payload.get(field)
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{' + str(length) + '}', value):
        raise ValueError('Invalid ' + field)
    return value


def ingest(con, text, filename, known_formats=(), date_parser=None):
    if not isinstance(text, str) or len(text.encode()) > 4 * 1024 * 1024:
        raise ValueError('CSV must be at most 4 MiB')
    if not isinstance(filename, str) or not filename or len(filename) > 200:
        raise ValueError('Filename required (200 characters maximum)')
    reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
    headers = reader.fieldnames or []
    if not headers or len(headers) > 30 or len(set(headers)) != len(headers):
        raise ValueError('Distinct normalized CSV headers required')
    known = next((fmt for fmt in known_formats if set(fmt['map'].values()).issubset(headers)), None)
    if not known and not {'id','source','date','channel',*MONEY}.issubset(headers):
        raise ValueError('Use normalized financial CSV or a supported known platform/supplier export')
    raw, malformed, documents = [], [], []
    for i, row in enumerate(reader, 2):
        if i > 20001:
            raise ValueError('CSV exceeds 20000 rows')
        if None in row or any(v is None or len(v) > 4096 for v in row.values()):
            malformed.append({'row': i, 'reason': 'CSV cell count or size invalid'})
            continue
        try:
            if known:
                mapping = known['map']
                get = lambda field: row.get(mapping.get(field), '')
                parsed_date = date_parser(get('date'))
                if not parsed_date:
                    raise ValueError('Valid transaction or invoice date required')
                if known['kind'] == 'invoice':
                    vendor, invoice = get('vendor').strip(), get('invoice_number').strip()
                    if not vendor or not invoice or not get('description') or not get('unit'):
                        raise ValueError('Invoice vendor, number, description and unit required')
                    if decimal(get('qty')) <= 0 or cents(get('unit_price')) < 0:
                        raise ValueError('Positive invoice quantity and nonnegative unit price required')
                    documents.append({'vendor': vendor, 'invoice': invoice, 'date': parsed_date,
                                      'ingredient': get('description').strip().casefold(), 'unit': get('unit').strip().casefold(),
                                      'unit_price': get('unit_price'), 'source': filename + ':' + str(i),
                                      'document_source': get('source').strip(), 'line': get('line')})
                    continue
                # Known Uber exports express fee deductions as negative values.
                # Reverse that documented convention; never abs() credits/refunds.
                sign = -1 if known['channel'] == 'Uber Eats' else 1
                fee = lambda field: str(decimal(get(field)) * sign) if get(field) != '' else None
                row = dict(id=get('order_id'), source=filename + ':' + str(i), date=parsed_date,
                           channel=known['channel'], gross_sales=get('subtotal'),
                           commissions=fee('commission') if known['kind'] == 'payout' else '0',
                           restaurant_promotions=fee('promo_amount') if known['kind'] == 'payout' else '0',
                           other_platform_fees=fee('error_amount') if known['kind'] == 'payout' else '0',
                           platform_promotions=None, refunds=None, food_costs=None, other_variable_costs=None,
                           contract_rate='0' if known['channel'] == 'In-store' else None,
                           contract_basis='gross_sales', promotion_approved=False, fee_reason=get('error_reason'))
            raw.append((i, row))
        except ValueError as error:
            malformed.append({'row': i, 'reason': str(error)})
    accepted, rejected, duplicates = normalize([r for _,r in raw])
    for rejection in rejected:
        rejection['row'] = raw[rejection['row'] - 2][0]
    state = read(con)
    existing = {(r['channel'], r['id']): r for r in state['rows']}
    for row in accepted:
        key = (row['channel'], row['id'])
        if key in existing:
            compare = lambda r: {k: v for k, v in r.items() if k != 'source'}
            if compare(existing[key]) != compare(row):
                rejected.append({'id': row['id'], 'reason': 'Conflicting stored transaction; not overwritten'})
                continue
            duplicates += 1
        else:
            existing[key] = row
    document_key = lambda doc: digest({k:v for k,v in doc.items() if k != 'source'})
    existing_docs = {document_key(doc): doc for doc in state['documents']}
    for doc in documents:
        key = document_key(doc)
        if key in existing_docs:
            duplicates += 1
        else:
            existing_docs[key] = doc
    if len(existing) > 20000 or len(existing_docs) > 20000:
        raise ValueError('Connected dataset exceeds 20000 records; reset before importing more')
    state.update(rows=list(existing.values()), documents=list(existing_docs.values()), rejected=rejected + malformed,
                 duplicates_skipped=duplicates, label='Imported ' + (known['name'] if known else 'normalized financial CSV'))
    write(con, state)
    return output(con)


def operate(con, operation, payload, known_formats=(), date_parser=None):
    if operation in ('load-golden', 'reset'):
        require_fields(payload, [])
        con.execute('DELETE FROM prototype_scenarios')
        con.execute('DELETE FROM prototype_actions')
        con.execute('DELETE FROM prototype_disputes')
        write(con, {'rows': [], 'documents': [], 'rejected': [], 'duplicates_skipped': 0,
                    'label': 'Synthetic golden restaurant' if operation == 'load-golden' else 'No normalized data loaded'})
        if operation == 'load-golden':
            ingest(con, (DATA / 'golden.csv').read_text(), 'golden.csv')
            state = read(con)
            state.update(documents=json.loads((DATA / 'documents.json').read_text()), label='Synthetic golden restaurant')
            write(con, state)
        return output(con)
    if operation == 'import':
        require_fields(payload, ['text', 'filename'])
        return ingest(con, payload.get('text'), payload.get('filename'), known_formats, date_parser)
    state = read(con)
    if not state['rows']:
        raise ValueError('Load or import financial rows first')
    baseline = calculate(state['rows'])
    if operation == 'guru':
        require_fields(payload, ['question', 'finding_id'])
        if 'finding_id' in payload:
            identifier(payload, 'finding_id', 16)
        q = payload.get('question', '')
        if not isinstance(q, str) or not 1 <= len(q.strip()) <= 2000:
            raise ValueError('Question must contain 1–2000 characters')
        alerts = findings(state['rows'], state['documents'])
        chosen = [f for f in alerts if f['id'] == payload.get('finding_id')] if payload.get('finding_id') else alerts
        if payload.get('finding_id') and not chosen:
            raise ValueError('Finding no longer exists')
        # No probabilistic generator or browser-supplied numbers enter this answer.
        amount = baseline['money_cents']['contribution_margin']
        summary = 'Contribution margin unavailable: required variable financial fields are missing.' if amount is None else (
            f"Contribution margin is ${amount / 100:,.2f}; net sales are ${baseline['money_cents']['net_sales'] / 100:,.2f}.")
        lower = q.casefold()
        if not payload.get('finding_id'):
            kind = ('commission_overcharge' if any(word in lower for word in ('overcharg', 'commission')) else
                    'invoice_price_increase' if any(word in lower for word in ('ingredient', 'expensive', 'supplier', 'price')) else
                    'duplicate_invoice_document' if any(word in lower for word in ('duplicate', 'twice', 'payment')) else None)
            if kind:
                chosen = [f for f in alerts if f['type'] == kind]
        explanations = []
        for finding in chosen:
            impact = finding['impact_cents']
            explanations.append(finding['reason'] + (f"; conditional exposure ${impact / 100:,.2f}" if impact is not None else
                                '; monetary claim not established') + ' [' + finding['id'] + '].')
        if 'highest' in lower and 'commission' in lower:
            totals = {}
            for row in state['rows']:
                if row['commissions'] is not None:
                    totals[row['channel']] = totals.get(row['channel'], 0) + row['commissions']
            if totals:
                channel = max(totals, key=totals.get)
                explanations.insert(0, f"{channel} has the largest total commission amount: ${totals[channel] / 100:,.2f}; this compares amounts, not effective rates.")
        if any(word in lower for word in ('decrease', 'drop', 'why')):
            explanations.append('A period-over-period cause cannot be established from this single baseline; compare a prior period before attributing a decline.')
        return {'mode': 'Deterministic fallback — external AI not used', 'baseline': baseline,
                'answer': summary + ' ' + ' '.join(explanations),
                'evidence': chosen, 'limitations': 'Rule-based summary, not open-ended AI reasoning; no demand forecast or causal proof.'}
    if operation == 'simulate':
        require_fields(payload, ['changes', 'name'])
        name = payload.get('name', 'Scenario')
        if not isinstance(name, str) or not 1 <= len(name) <= 100:
            raise ValueError('Scenario name must contain 1–100 characters')
        scenario = {**simulate(state['rows'], payload.get('changes', {})), 'id': secrets.token_urlsafe(24),
                    'name': name, 'baseline_id': baseline['baseline_id'], 'created': time.time()}
        con.execute('INSERT INTO prototype_scenarios VALUES(?,?)', (scenario['id'], json.dumps(scenario)))
        return scenario
    if operation == 'propose':
        require_fields(payload, ['scenario_id'])
        row = con.execute('SELECT data FROM prototype_scenarios WHERE id=?', (identifier(payload, 'scenario_id'),)).fetchone()
        if not row:
            raise ValueError('Scenario not found')
        scenario = json.loads(row[0])
        if scenario['baseline_id'] != baseline['baseline_id']:
            raise ValueError('Scenario stale; recalculate before proposing')
        action = {'id': secrets.token_urlsafe(24), 'scenario': scenario, 'state': 'Proposed',
                  'baseline_id': baseline['baseline_id'], 'created': time.time(),
                  'history': ['Proposed'], 'mode': 'Simulated external action — no platform changes',
                  'confirmation': CONFIRMATION}
        con.execute('INSERT INTO prototype_actions VALUES(?,?)', (action['id'], json.dumps(action)))
        return action
    if operation == 'approve':
        require_fields(payload, ['action_id', 'confirmation'])
        row = con.execute('SELECT data FROM prototype_actions WHERE id=?', (identifier(payload, 'action_id'),)).fetchone()
        if not row:
            raise ValueError('Action not found')
        action = json.loads(row[0])
        if action['state'] != 'Proposed':
            raise ValueError('Action already consumed; execution is never repeated')
        if payload.get('confirmation') != CONFIRMATION:
            raise ValueError('Explicit exact approval phrase required')
        if action['baseline_id'] != baseline['baseline_id'] or time.time() - action['created'] > 600:
            action.update(state='Failed', error='Financial baseline changed or proposal expired')
            action['history'].append('Failed')
        else:
            action['history'].append('Approved')
            action.update(state='Simulated', verified_external=False,
                          result='Simulation recorded; NO external API was called and no menu was updated.')
            action['history'].append('Simulated')
        con.execute('UPDATE prototype_actions SET data=? WHERE id=?', (json.dumps(action), action['id']))
        return action
    if operation == 'dispute':
        require_fields(payload, ['finding_id'])
        identifier(payload, 'finding_id', 16)
        finding = next((f for f in findings(state['rows'], state['documents']) if f['id'] == payload.get('finding_id')), None)
        if not finding:
            raise ValueError('Finding not found')
        claimable = finding['type'] in ('commission_overcharge', 'unapproved_promotion')
        draft = {'platform_or_supplier': finding['platform'], 'reason': finding['reason'],
                 'claim_amount_cents': finding['impact_cents'] if claimable else None,
                 'supporting_transactions': [r for r in state['rows'] if r['channel'] + ':' + r['id'] in finding['evidence']],
                 'contractual_assumptions': finding['assumption'], 'evidence': finding['evidence'],
                 'owner_review_status': 'Draft — owner review required; never submitted',
                 'baseline': baseline, 'finding': finding,
                 'note': 'Claim is conditional on supporting contract/approval evidence.' if claimable else
                         'Evidence inquiry only. No payment evidence or supported monetary claim established.'}
        con.execute('INSERT OR REPLACE INTO prototype_disputes VALUES(?,?)',
                    (digest([baseline['baseline_id'], finding['id']]), json.dumps(draft)))
        return draft
    raise ValueError('Unknown prototype operation')
