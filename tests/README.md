# ProfitShield test foundation

Run from the repository root with Python 3.9+ and Node.js 24. No pip/npm installs,
API keys, browser binaries or running application are needed.

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/*.test.cjs
# If your Python executable is named differently:
PYTHON=python node --test tests/js/*.test.cjs
```

The application source is unchanged. The test seam in `js/load-engine.cjs` reads
the actual inline engine, simulation and recipe-cost sections of `index.html`
using existing section markers, then runs them in an isolated Node VM. It does
not copy the formulas, execute DOM rendering/boot, or expose fetch/require/process.
It also parses the full script. If calculation sections move, update the named
boundaries explicitly; a missing boundary fails instead of silently using a copy.
This seam is intentionally temporary until a separately authorized engine
extraction. Node VM is test isolation, not a security sandbox for untrusted code.

## Layout

- `support.py`: load real backend definitions without developer `.env`, blank
  integration credentials, force sandbox configuration, deny outbound transport,
  and give every test a fresh temporary SQLite database.
- `test_imports.py`: formats, aliases, dedupe, transaction rollback, redaction,
  data transformation and audited sample counts.
- `test_financial_tools.py`: independent synthetic expectations and real sample
  baselines for commissions, promos, document grouping, ingredient prices,
  error patterns and price simulations.
- `test_integrations.py`: synthetic Square sync, vision extraction and agent
  tool calling. Requests are intercepted; no provider is contacted.
- `fixtures/`: fictional CSV/JSON transactions and explicit audit expectations.
- `export_sample_data.py`: import samples/synthetic CSV through the actual Python
  backend and emit API data as JSON to stdout. All writes stay in temporary
  storage; the Node tests use this to test the actual frontend/backend boundary.
- `js/financial.test.cjs`: audited generated/imported estimates, bridge totals,
  recipes, fees, channel-specific simulations, weighted prices and carry-forward.

No tests start the normal server, reset the user's database, send real documents
to AI, or mutate any external system. Do not replace fake transport with live
API calls. Add a new synthetic response and assert the request/response contract.
Python helpers also deny a dedicated Square opener when present, to support the
separate sandbox-guard work without allowing network by accident.

## Audit baselines

`fixtures/audit_baselines.json` records the audited source commit and keeps
**CSV-backed data and generated preview separate**:

| Metric | Imported samples | Generated preview |
|---|---:|---:|
| Orders | 1,776 | 1,776 |
| Estimated week-over-week change | -573.766875 | -578.106875 |
| Commission overcharge | 135.96 | 135.80 |
| Error-charge change | -52.50 | -57.00 |

Imported samples also contain 16 error charges, nine invoice records and four
weeks. The unusual Grubhub file still adds zero orders and removes 100 personal
fields. Expected values come from the audit and independently worked synthetic
transactions, not values calculated by the function under test and copied back.

The application's `profit` property is its existing estimated restaurant measure
before labor/rent, not accounting profit. These compatibility tests do not endorse
the current duplicate-document penalty as evidence of duplicate payment. They
characterize the prototype until the financial model is corrected. Two copies
of an invoice prove only duplicate documents; the fixtures contain no payment
ledger. An approved correctness change should update its baseline and explanation
in the same PR, rather than retaining an inaccurate rule just to keep tests green.

The synthetic four-order fixture has independently calculated totals: previous
sales 20.50, current sales 41.00; previous recipe cost 3.8275, current recipe cost
8.78; existing estimates 13.4725 and 21.22; change 7.7475. A 0.75 pizza increase
on two DoorDash units yields 1.20 after the assumed 20% contract commission,
with no demand change. These are model estimates, not accounting/recovery claims.

## CI and scope

`.github/workflows/tests.yml` runs on pull requests to any branch and can be
started manually. Backend jobs use Python 3.9 and 3.12; the frontend/data-bridge
job uses Node 24 and Python 3.12. Jobs have ten-minute timeouts, read-only repository
permissions, no secrets, no persisted checkout credentials, and pinned official
GitHub Actions revisions. There is no deployment, npm install or pip install.

Local validation in the development environment uses Python 3.12 and Node 24;
the Python 3.9 matrix job is verified when GitHub runs it. These tests are not a
full UI/end-to-end, load, security or coverage certification. Existing audit bugs
such as tiny bridge adjustments, unknown-item costs and invoice/payment semantics
remain separate work. Other security PRs bring their own regression suites;
retarget/reconcile API contracts and run all tests when integrating them.

## Connected hackathon financial workspace

`python3 -m unittest tests.test_prototype -v` covers the exact golden dataset,
signed credits, missing values, promotion funding, supplied contractual bases,
no duplicate-payment inference, connected persistence and HTTP security.
`python3 tests/browser_prototype.py` executes the complete judge journey with
all external transport blocked (requires preinstalled Playwright/Chromium).

The new reference is `demo_data/golden.csv`, with isolated controls in
`demo_data/edge_cases.json` and invoice observations in `demo_data/documents.json`.
See `DEMO_VERIFICATION.md` for the executed counts, limits and the documented
historical correction: duplicate invoice documents no longer reduce margin.
The original audit fixture is retained as a historical record; the corrected
CSV-backed weekly change is +$164.03 estimated contribution margin. JavaScript
checks now consume the backend financial model rather than recalculate profit.
