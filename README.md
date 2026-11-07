# ProfitShield

Restaurant financial intelligence: import evidence, inspect suspected leakage,
explain the numbers, compare a scenario, explicitly approve a **simulated** action,
and prepare an owner-reviewed dispute draft. The connected hackathon workspace
uses one backend financial engine; it does not require credentials or an LLM.

This is a **local synthetic prototype**, not a hosted accounting or payments
system. It binds to `127.0.0.1`. Owner authentication and organization/location
isolation are not implemented on this development base: do not expose this branch
publicly, use production credentials, or load customer records.

## Launch the judge demo

Python **3.9+**, standard library only. No application dependencies to install.
From the repository root:

```bash
# Explicitly blank credentials override any local .env values.
OPENROUTER_API_KEY='' SQUARE_ACCESS_TOKEN='' PROFITSHIELD_AI_PRIVACY_APPROVED='' \
BACKHOUSE_DB=/tmp/profitshield-hackathon.db python3 server.py
```

Open **http://localhost:8000/#demo**. Choose **Load synthetic golden restaurant**.
The table must show $10,000 gross sales, $9,200 net sales, $4,700 contribution
margin and **51.09%**. Accounting profit is **Unavailable — operating expenses
incomplete**. Follow [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the three-minute journey
and [DEMO_VERIFICATION.md](DEMO_VERIFICATION.md) for actual tested status.

For a different port, set `PORT` and open `http://localhost:<port>`. If using
`127.0.0.1` in the browser instead of `localhost`, set
`PROFITSHIELD_ORIGIN=http://127.0.0.1:8000` to match exactly. Writes require the
configured Origin and JSON. No CORS permission is granted to other sites.

The existing first-run sample importer remains: 1,776 historical synthetic orders
and supplier documents load into the original tables. The connected demo uses
separate SQLite tables in that **same database**, so reset of the connected demo
does not discard the historical explorer's data.

## Repeat and reset

**Reset connected demo** asks for confirmation and clears only its normalized
transactions, documents, scenarios, simulated history and saved dispute drafts.
Then **Load synthetic golden restaurant** restores the deterministic reference.
Loading the golden restaurant also clears prior connected scenarios/history.
Reloading the page preserves data, scenarios, actions and generated dispute drafts.
Guru text is a transient explanation and can be generated again.

The original Connections page still supports the original `/api/reset`, which
resets the **whole database**, including both workspaces, and reimports the
historical samples. Use connected reset during the presentation.

## Six connected workflows

| Workflow | Implementation and limits |
|---|---|
| Ingest and normalize | Upload `demo_data/golden.csv` or known DoorDash, Uber Eats, Grubhub, Square or supplier CSV exports. Explicit rejection reports and logical transaction dedupe; renamed identical imports do not inflate totals. Missing expenses/refunds remain unknown. Known exports do not prove contract rates, consumed food costs or accounting profit. |
| Diagnostic Guru | Uses the same persisted rows, cent totals, findings and evidence as the dashboard. Clearly labeled **deterministic fallback**, not generated AI. Can explain an overcharge/ingredient finding, compare total commissions and summarize exposure. One-period data does not establish why margin changed. |
| Proactive Auditor | Supplied contractual basis/rate checks, unexpected restaurant-funded promotions, recorded/repeated fees, confirmed invoice unit-price changes and suspected duplicate documents. Stable finding IDs and evidence; no guaranteed recovery or payment claims. |
| Simulation | Uniform price, aggregate ingredient-cost and commission-rate scenarios; exact before/after cents, named comparisons and explicit unchanged-demand assumptions. Commission response follows supplied terms. Missing costs/terms prevent unsafe calculation. |
| Execution | Persisted `Proposed → Approved → Simulated` history. Exact phrase, ten-minute expiry, baseline freshness and repeat rejection. **No external API calls or menu changes** in this connected workflow; never marked externally Verified. Stale/expired proposals become Failed. |
| Disputes | Deterministic conditional claim amounts, supplied contract assumptions, supporting transactions, evidence references and owner-review status. Drafts persist. Never submitted; duplicate documents generate a reconciliation inquiry with **no monetary claim**. |

The sidebar preserves the original seven views. Their contribution estimates now
come from the same Python cent engine via `legacy_finance.py`; price simulations
use `/api/simulate`, not a separate browser formula. Those historical exports
still lack full refunds/variable expenses. Recipe consumption estimates and
zero assumptions are visibly disclosed; these are not complete accounting data.
The connected normalized workspace does **not** assume missing costs are zero.

## Financial definitions and signs

`financial_engine.py` is authoritative. Normalized amounts enter as dollar
strings with at most two decimal places, become integer cents, and remain cents
in JSON/SQLite. Inputs must be finite. Rates use `Decimal`; rates are fractions
(e.g. `0.20`), not percentages. Rounding is **half up**. Contractual commission is
rounded per transaction; historical recipe estimates round at the period total;
incremental item-price scenarios round once per channel and sum displayed cents.

| Metric | Definition |
|---|---|
| Gross sales | Sales before separately represented refunds and restaurant-funded promotions, excluding taxes/tips. Signed sales adjustments retain their original sign. Never supply an already-netted amount as gross. |
| Refunds / credits | Positive refunded amounts reduce revenue; negative values reverse/refund a deduction. |
| Restaurant-funded promotions | Positive merchant-funded deductions; never include the platform-funded portion. Approval evidence is separate. |
| Platform-funded promotions | Informational; does not reduce restaurant revenue under this schema. |
| Net sales | Gross − refunds − restaurant-funded promotions. |
| Platform commissions | Actual commission costs, including any suspected overcharge. Negative commission credits remain negative. |
| Commission overcharges | Informational subset: actual commission minus rounded contractual commission, floored at zero for positive sales. Supplied `gross_sales` or `net_sales` contractual basis. Unknown terms produce unavailable exposure, not an invented rate. **Never subtracted a second time.** |
| Other platform fees | Separate costs only; do not also encode the same charge as a refund or netted sales. Repetition does not prove an erroneous fee. |
| Food costs | Consumed food cost supplied in normalized rows. Invoice purchases alone do not establish consumption. |
| Other variable costs | Separate variable costs, not already included in food, fees or commissions. |
| Contribution margin | Net sales − commissions − other platform fees − food costs − other variable costs. Missing required fields make it unavailable. |
| Contribution margin % | Contribution margin / net sales × 100. Zero net sales: unavailable. Display rounded to two decimal places. |
| Accounting profit | Contribution margin − complete operating expenses. The reference dataset lacks operating expenses: unavailable. The Python calculation API accepts an explicitly complete expense total; the demo UI does not collect one. |

Costs/refunds are **positive deductions**; negative costs are credits. The engine
cannot detect an owner incorrectly labeling already-netted source data: review
source definitions. The normalized CSV schema makes each deduction explicit.
Duplicate invoice documents never establish duplicate payments and never reduce
margin. Suspected exposures may overlap and must not be summed as guaranteed loss.

## Data and architecture

```text
Known/sample CSV or normalized CSV → validation + dedupe → SQLite prototype_state
                                                              ↓
                                  financial_engine.py (Decimal / integer cents)
                                       ↓              ↓              ↓
                              baseline/findings    scenarios     Guru / disputes
                                                      ↓
                                            explicit approval
                                                      ↓
                                      persistent simulated action history
```

- `server.py`: original HTTP server, importers, SQLite, optional integrations and
  exact public-asset allowlist. No repository directory is public.
- `financial_engine.py`: normalization, totals, contractual checks and scenarios.
- `prototype.py`: connected workflow and SQLite persistence. Known-format column
  definitions/date parsing are reused from the existing server.
- `legacy_finance.py`: adapter for the historical recipe-based estimates.
- `index.html`: existing dashboard plus accessible connected workspace. Financial
  baseline/price calculations are requested from the backend. New untrusted
  values use DOM `textContent`.
- `demo_data/`: deterministic `golden.csv`, invoice evidence in `documents.json`,
  and **isolated** `edge_cases.json`. None is served as a public HTTP file.
- `square_actions.py`: existing sandbox review, freshness, one-use and verification
  guards, preserved and regression-tested with a fake transport.

Schema migration is additive and automatic: `prototype_state`,
`prototype_scenarios`, `prototype_actions`, `prototype_disputes`. Existing order,
invoice and Square action tables are preserved. This single-instance demo stores
its normalized dataset as bounded JSON; it is not an enterprise ledger.

## Optional integrations and privacy

The connected demo **never calls an external LLM or a platform API**. AI is not
needed for the judge flow. Optional pre-existing OpenRouter mapping, Q&A and
receipt/PDF extraction remain behind an explicit privacy gate:

- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `OPENROUTER_VISION_MODEL` select the
  optional service. Even with a key, calls are blocked unless
  `PROFITSHIELD_AI_PRIVACY_APPROVED=true` after owner-approved privacy policy.
- PII redaction is best effort, **not** a guarantee that financial documents are
  safe to transmit. Do not enable external AI for real financial records before
  privacy approval. Receipt extraction still requires line review before save.
- `SQUARE_ACCESS_TOKEN` must be a sandbox token. Server transport is pinned to
  `https://connect.squareupsandbox.com` regardless of `SQUARE_ENV`; redirects
  cannot change destination. Do not supply production credentials.
- Existing Connections **Review 3 sandbox test orders** and price review controls
  require a separate review and exact phrase. No real Square call was made during
  this sprint; automated tests mock every transport, including read-back checks.
- Delivery-app menu writes and dispute submission are not implemented.

`BACKHOUSE_DB`, `STORE_TZ` (default America/New_York), `PORT`, and exact
`PROFITSHIELD_ORIGIN` configure local execution. `.env` is optional and private;
real environment variables take precedence. Never commit credentials or databases.

## Checks

```bash
python3 -m unittest discover -s tests -v
node --test tests/js/*.test.cjs
# Optional, with Playwright and Chromium already installed:
python3 tests/browser_prototype.py
python3 tests/browser_input_security.py
python3 tests/browser_square_guard.py
```

Tests use temporary databases, synthetic records and mocked/blocked outbound
HTTP; no credentials are needed. The existing GitHub Actions workflow discovers
the Python and JavaScript tests for PRs. Browser tests are optional local checks;
they are not currently installed/run in CI. See [tests/README.md](tests/README.md).

Development PRs target **development**, never main. No automatic merge/deployment.
See [DEMO_VERIFICATION.md](DEMO_VERIFICATION.md) for limits and migration notes.
