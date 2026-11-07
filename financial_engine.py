"""Canonical, dependency-free financial arithmetic. Money is integer cents.

Positive costs are deductions; negative costs are credits. Gross includes signed
sales adjustments, never tax/tips. Refunds and restaurant promotions are separate
from gross (never already netted). Platform-funded promotions are informational.
Commission overcharges are a subset of commissions, never an extra deduction.
Invoice documents are evidence of purchases, not evidence of payments or COGS.
"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import date
import hashlib
import json

MONEY = ('gross_sales', 'refunds', 'restaurant_promotions', 'platform_promotions',
         'commissions', 'other_platform_fees', 'food_costs', 'other_variable_costs')
CHANNELS = ('DoorDash', 'Uber Eats', 'Grubhub', 'In-store')


def decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('Finite numeric value required')
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Finite numeric value required') from None
    if not out.is_finite() or abs(out) > Decimal('1000000000'):
        raise ValueError('Numeric value outside supported range')
    return out


def cents(value):
    amount = decimal(value)
    # Check the original value before multiplication can underflow to zero.
    if amount != amount.quantize(Decimal('.01')):
        raise ValueError('Money must have at most two decimal places')
    v = amount * 100
    if v != v.to_integral_value():
        raise ValueError('Money must have at most two decimal places')
    return int(v)


def rounded(value):
    return int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def normalize(rows):
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError('At most 20000 transaction rows required')
    accepted, rejected, seen, duplicates = [], [], {}, 0
    for position, raw in enumerate(rows, 2):
        try:
            if not isinstance(raw, dict):
                raise ValueError('Row must be an object')
            row = dict(raw)
            for field in ('id', 'source', 'date', 'channel'):
                if not isinstance(row.get(field), str) or not row[field].strip() or len(row[field]) > 255:
                    raise ValueError('Missing or invalid ' + field)
                row[field] = row[field].strip()
            if date.fromisoformat(row['date']).isoformat() != row['date']:
                raise ValueError('Date must be ISO YYYY-MM-DD')
            if row['channel'] not in CHANNELS:
                raise ValueError('Unsupported channel')
            for field in MONEY:
                value = row.get(field)
                row[field] = None if value is None or value == '' else cents(value)
            rate = row.get('contract_rate')
            row['contract_rate'] = None if rate is None or rate == '' else str(decimal(rate))
            if row['contract_rate'] is not None and not 0 <= decimal(row['contract_rate']) <= 1:
                raise ValueError('Contract rate must be 0 to 1')
            basis = row.get('contract_basis', 'gross_sales')
            if basis not in ('gross_sales', 'net_sales'):
                raise ValueError('Unsupported contract basis')
            row['contract_basis'] = basis
            fee_reason = row.get('fee_reason', '')
            if not isinstance(fee_reason, str) or len(fee_reason) > 255:
                raise ValueError('Invalid fee reason')
            row['fee_reason'] = fee_reason.strip()
            approved = row.get('promotion_approved', False)
            if approved in ('true', 'false'):
                approved = approved == 'true'
            if not isinstance(approved, bool):
                raise ValueError('promotion_approved must be boolean')
            row['promotion_approved'] = approved
            key = (row['channel'], row['id'])
            # Provenance may differ for an identical transaction downloaded twice.
            signature = {k: row[k] for k in (*MONEY, 'date', 'contract_rate', 'contract_basis', 'promotion_approved', 'fee_reason')}
            if key in seen:
                if seen[key] != signature:
                    raise ValueError('Conflicting duplicate transaction; review before replacing')
                duplicates += 1
                continue
            seen[key] = signature
            accepted.append({k: row[k] for k in (*MONEY, 'id', 'source', 'date', 'channel',
                                                'contract_rate', 'contract_basis', 'promotion_approved', 'fee_reason')})
        except (ValueError, TypeError, InvalidOperation) as exc:
            rejected.append({'row': position, 'reason': str(exc)})
    return accepted, rejected, duplicates


def commission_overcharge(row):
    rate, basis = row['contract_rate'], row['gross_sales']
    if row['contract_basis'] == 'net_sales':
        basis = None if any(row[f] is None for f in ('gross_sales','refunds','restaurant_promotions')) else (
            row['gross_sales'] - row['refunds'] - row['restaurant_promotions'])
    if rate is None or basis is None or row['commissions'] is None:
        return None
    if basis <= 0:
        return 0
    return max(0, row['commissions'] - rounded(Decimal(basis) * decimal(rate)))


def calculate(rows, operating_expenses=None):
    """Consume normalized cent rows; never replace a missing required field with 0."""
    missing = [{'id': r['id'], 'field': f} for r in rows for f in MONEY if r[f] is None]
    totals = {f: None if any(r[f] is None for r in rows) else sum(r[f] for r in rows) for f in MONEY}
    core = [totals[f] for f in MONEY if f != 'platform_promotions']
    complete = all(v is not None for v in core)
    net = None if any(totals[f] is None for f in ('gross_sales', 'refunds', 'restaurant_promotions')) else (
        totals['gross_sales'] - totals['refunds'] - totals['restaurant_promotions'])
    margin = None if not complete else net - sum(totals[f] for f in (
        'commissions', 'other_platform_fees', 'food_costs', 'other_variable_costs'))
    pct = None if margin is None or not net else str(Decimal(margin) * 100 / Decimal(net))
    overcharges = [commission_overcharge(row) for row in rows]
    totals.update(commission_overcharges=None if any(v is None for v in overcharges) else sum(overcharges),
                  net_sales=net, contribution_margin=margin,
                  accounting_profit=None if margin is None or operating_expenses is None else margin - cents(operating_expenses))
    return {'money_cents': totals, 'contribution_margin_pct': pct,
            'contribution_margin_pct_display': None if pct is None else str(Decimal(pct).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
            'accounting_profit_status': 'Unavailable — operating expenses incomplete' if operating_expenses is None else (
                'Available' if margin is not None else 'Unavailable — variable costs incomplete'),
            'complete': complete, 'missing_fields': missing, 'transactions': len(rows), 'baseline_id': digest(rows)}


def findings(rows, documents=()):
    out = []
    def add(kind, status, amount, evidence, reason, next_action, platform, assumption=''):
        key = digest([kind, evidence])[:16]
        out.append(dict(id=key, type=kind, status=status, impact_cents=amount, evidence=evidence,
                        reason=reason, next_action=next_action, platform=platform, assumption=assumption))
    fee_groups = {}
    for row in rows:
        if row.get('fee_reason') and row['other_platform_fees'] is not None and row['other_platform_fees'] > 0:
            fee_groups.setdefault((row['channel'], row['fee_reason']), []).append(row)
    for row in rows:
        evidence = [row['channel'] + ':' + row['id']]
        over = commission_overcharge(row)
        if over is not None and over > 0:
            add('commission_overcharge', 'suspected', over, evidence, 'Commission exceeds supplied contractual terms',
                'Review contract and payout, then prepare a dispute', row['channel'],
                'Rate ' + row['contract_rate'] + ' on ' + row['contract_basis'] + '; rounded per transaction')
        if row['restaurant_promotions'] is not None and row['restaurant_promotions'] > 0 and not row['promotion_approved']:
            add('unapproved_promotion', 'suspected', row['restaurant_promotions'], evidence,
                'Restaurant-funded promotion requires approval evidence', 'Check promotion authorization', row['channel'])
        if row['other_platform_fees'] and row['other_platform_fees'] > 0 and len(fee_groups.get((row['channel'], row.get('fee_reason')), [])) < 2:
            add('platform_fee', 'suspected', row['other_platform_fees'], evidence,
                'Platform fee recorded; not automatically an error', 'Review fee evidence before claiming', row['channel'])
    for (channel, reason), group in fee_groups.items():
        if len(group) > 1:
            add('repeated_error_charges', 'suspected', sum(r['other_platform_fees'] for r in group),
                [channel + ':' + r['id'] for r in group],
                'Repeated recorded error charges: ' + reason, 'Review transaction evidence; repetition does not establish platform fault', channel)
    grouped, prices = {}, {}
    for doc in documents:
        key = (doc['vendor'], doc['invoice'])
        grouped.setdefault(key, []).append(doc)
        prices.setdefault((doc['vendor'], doc['ingredient'], doc['unit']), []).append(doc)
    for (vendor, invoice), copies in grouped.items():
        sources = {d.get('document_source') or d['source'] for d in copies}
        if len(sources) > 1:
            add('duplicate_invoice_document', 'suspected', None, sorted({d['source'] for d in copies}),
                'Duplicate invoice document; duplicate payment is NOT established',
                'Reconcile bank/payment evidence before claiming any credit', vendor)
    for (vendor, ingredient, unit), docs in prices.items():
        unique = {d['invoice']: d for d in docs}
        ordered = sorted(unique.values(), key=lambda d: d['date'])
        if len(ordered) > 1:
            first, last = ordered[-2:]
            change = cents(last['unit_price']) - cents(first['unit_price'])
            if change > 0:
                add('invoice_price_increase', 'confirmed', None, [first['source'], last['source']],
                    ingredient + ' increased ' + str(change) + ' cents per ' + unit,
                    'Confirm contractual pricing; purchase quantity is not consumption', vendor)
    return out


def simulate(rows, changes):
    allowed = {'price_change_pct', 'ingredient_cost_change_pct', 'commission_rate_change'}
    if not isinstance(changes, dict) or set(changes) - allowed:
        raise ValueError('Unknown scenario field')
    price = decimal(changes.get('price_change_pct', 0))
    food = decimal(changes.get('ingredient_cost_change_pct', 0))
    rate = decimal(changes.get('commission_rate_change', 0))
    if not -100 <= price <= 100 or not -100 <= food <= 100 or not -1 <= rate <= 1:
        raise ValueError('Scenario outside supported range')
    after = []
    for row in rows:
        item = dict(row)
        if row['gross_sales'] is not None:
            item['gross_sales'] = rounded(Decimal(row['gross_sales']) * (1 + price / 100))
        if row['food_costs'] is not None:
            item['food_costs'] = rounded(Decimal(row['food_costs']) * (1 + food / 100))
        if row['commissions'] is not None and row['gross_sales'] is not None:
            # Incremental commission uses supplied terms, never an assumed industry rate.
            if (price or rate) and row['contract_rate'] is None and row['channel'] != 'In-store':
                raise ValueError('Contract rate required for commission-sensitive scenario')
            current_rate = decimal(row['contract_rate'] or 0)
            if not 0 <= current_rate + rate <= 1:
                raise ValueError('Resulting commission rate outside 0 to 1')
            old_basis = row['gross_sales']
            new_basis = item['gross_sales']
            if row['contract_basis'] == 'net_sales':
                if row['refunds'] is None or row['restaurant_promotions'] is None:
                    raise ValueError('Net commission basis incomplete')
                old_basis -= row['refunds'] + row['restaurant_promotions']
                new_basis -= row['refunds'] + row['restaurant_promotions']
            item['commissions'] += rounded(Decimal(new_basis - old_basis) * current_rate + Decimal(new_basis) * rate)
            if rate:
                item['contract_rate'] = str(current_rate + rate)
        after.append(item)
    before, result = calculate(rows), calculate(after)
    if not before['complete']:
        raise ValueError('Cannot simulate incomplete financial records')
    return {'before': before, 'after': result, 'changes': changes,
            'delta_cents': result['money_cents']['contribution_margin'] - before['money_cents']['contribution_margin'],
            'assumptions': ['Unchanged demand and quantities', 'Refunds and promotions unchanged in dollars',
                            'Incremental commissions follow supplied contract terms; existing discrepancies remain', 'No external price or contract changes performed']}


def price_increment(units, increase, rates):
    """Fixed-demand incremental margin; costs unchanged, commissions contractual.

    Round each channel once to cents, then sum those displayed channel amounts.
    """
    inc = decimal(increase)
    if not 0 <= inc <= 100:
        raise ValueError('Price increase must be between 0 and 100 dollars')
    return {ch: rounded(decimal(quantity) * inc * (1 - decimal(rates.get(ch, 0))) * 100)
            for ch, quantity in units.items()}
