"""Adapter for the original sample explorer, using the canonical cent engine.

Historical exports lack explicit refunds and consumed costs. This adapter is
labelled an ESTIMATE: refunds/other variable costs assumed zero, food estimated
from recipes and weighted supplier prices. It cannot establish accounting profit.
The normalized financial workspace does not make these assumptions.
"""
from decimal import Decimal
from financial_engine import calculate, rounded, decimal

RECIPES = {
 'marg': {'dough':1,'mozz':'.375','sauce':'.25','basil':'.2'},
 'pep': {'dough':1,'mozz':'.375','sauce':'.25','pepperoni':'.1875'},
 'knots': {'dough':'.5','gbutter':'.0625','parm':'.03'},
 'caesar': {'romaine':'.3125','parm':'.0625','dressing':'.125','croutons':'.0625'},
 'wings': {'wings':'1.1','buffalo':'.1875'}, 'soda': {'soda':1}, 'tira': {'tira':1}}
BASE = dict(dough='.55',mozz='4.10',sauce='1.20',pepperoni='5.40',basil='.45',parm='9.50',
            romaine='1.90',gbutter='3.80',dressing='3.20',croutons='2.80',wings='3.10',buffalo='2.40',soda='1.35',tira='2.60')
CONTRACT = {'DoorDash':'.20','Uber Eats':'.22','Grubhub':'.18','In-store':'0'}


def dollars(cents):
    return cents / 100


def model(data):
    price_table, metrics, menu_costs = {}, {}, {}
    last = {k: decimal(v) for k,v in BASE.items()}
    for week in data['weeks']:
        w = week['id']
        seen, acc = set(), {}
        for inv in data['invoices']:
            if inv['week'] != w or (inv['vendor'], inv['id']) in seen:
                continue
            seen.add((inv['vendor'], inv['id']))
            for line in inv['lines']:
                g = line['ing']
                if not g:
                    continue
                quantity = decimal(line['qty'])
                entry = acc.setdefault(g, [Decimal(0), Decimal(0)])
                entry[0] += quantity
                entry[1] += quantity * decimal(line['unitPrice'])
        for g, (qty, total) in acc.items():
            if qty:
                last[g] = total / qty
        price_table[w] = {**{g: float(v) for g,v in last.items()}, '__week': w}
        menu_costs[w] = {sku: dollars(rounded(sum((decimal(q)*last[g] for g,q in recipe.items()), Decimal(0))*100))
                         for sku,recipe in RECIPES.items()}
        orders = [o for o in data['orders'] if o['week'] == w]
        channels = {c: {'orders':0, 'gross':0} for c in CONTRACT}
        gross = comm = approved = unapproved = contractual = Decimal(0)
        quantities = {}
        for order in orders:
            sub = decimal(order['subtotal'])
            gross += sub
            comm += decimal(order['commission'])
            contractual += sub * decimal(CONTRACT[order['channel']])
            channels[order['channel']]['orders'] += 1
            channels[order['channel']]['gross'] += float(sub)
            promo = decimal(order['promoAmt'])
            if order['channel'] == 'DoorDash' and order['promo'] == 'Free garlic knots on $30+':
                approved += promo
            else:
                unapproved += promo
            for item in order['items']:
                for ingredient, quantity in RECIPES.get(item.get('sku'), {}).items():
                    quantities[ingredient] = quantities.get(ingredient, Decimal(0)) + decimal(quantity)*decimal(item['qty'])
        food = sum((q * last[g] for g,q in quantities.items()), Decimal(0))
        errors = [e for e in data['errors'] if e['week'] == w]
        error_cost = sum((decimal(e['amount']) for e in errors), Decimal(0))
        row = dict(id=w, source='Historical CSV recipe estimate', date=week['start'], channel='In-store',
                   gross_sales=rounded(gross*100), refunds=0, restaurant_promotions=rounded((approved+unapproved)*100),
                   platform_promotions=0, commissions=rounded(comm*100), other_platform_fees=rounded(error_cost*100),
                   food_costs=rounded(food*100), other_variable_costs=0, contract_rate=None, contract_basis='gross_sales',promotion_approved=False)
        result = calculate([row])
        money = result['money_cents']
        metrics[w] = dict(w=w, orders=len(orders), gross=dollars(money['gross_sales']),
            net=dollars(money['net_sales']), contractComm=dollars(rounded(contractual*100)),
            over=dollars(money['commissions']-rounded(contractual*100)), apprPromo=dollars(rounded(approved*100)),
            unappPromo=dollars(rounded(unapproved*100)), err=dollars(money['other_platform_fees']), errN=len(errors),dup=0,
            Q={g:float(q) for g,q in quantities.items()},food=dollars(money['food_costs']),
            profit=dollars(money['contribution_margin']), P=price_table[w], byCh=channels,
            baseline=result, estimated=True)
    return {'price_table':price_table,'metrics':metrics,'menu_costs':menu_costs,
            'assumptions':['Historical estimates only: unreported refunds and other variable costs assumed zero',
                           'Food consumption estimated from recipes; invoice purchases are not COGS',
                           'Invoice document copies never reduce margin or establish payment',
                           'Accounting profit unavailable; operating expenses incomplete']}
