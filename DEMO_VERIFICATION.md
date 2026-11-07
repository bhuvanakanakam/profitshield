# Demo verification

Verified locally on 2026-11-07. Branch: `feat/hackathon-complete-prototype`,
created from `origin/development` at `f5489e7` (not from main). Inspected the base
implementation/tests and historical security PR #1 before editing. No deployment,
merge, production credentials, real financial documents, real AI or platform API
calls were used.

## Classification

- **Verified working:** implemented and tested successfully.
- **Working with limitations:** functional with the restrictions below.
- **Simulated:** explicit fake/sandbox behavior; not a real external change.
- **Incomplete:** not working end to end.
- **Not tested:** implemented but not verified.

## Golden reconciliation — verified working

Exact input: `demo_data/golden.csv` (four source-level synthetic transactions).
Invoice evidence: `demo_data/documents.json` (three synthetic observations).
Isolated negative adjustment, duplicate, missing, zero-sales, malformed and
repeated-charge cases: `demo_data/edge_cases.json`. Edge cases do not alter golden
totals. The golden itself includes a refund, both promotion funding types and an
explicit contractual commission discrepancy.

| Metric | Expected and observed |
|---|---:|
| Gross sales | $10,000.00 |
| Refund deduction | $300.00 |
| Restaurant-funded promotion deduction | $500.00 |
| Platform-funded promotion, informational only | $100.00 |
| Net sales | $9,200.00 |
| Commission deduction | $1,500.00 |
| Commission overcharge subset, **already in commission** | $100.00 |
| Other platform fees | $0.00 |
| Food costs | $2,800.00 |
| Other variable costs | $200.00 |
| Contribution margin | **$4,700.00** |
| Contribution margin percentage | **51.09%** (unrounded 51.0869565…%) |
| Accounting profit | **Unavailable — operating expenses incomplete** |

Costs are positive deductions; negative costs are credits. Net sales = gross −
refunds − merchant promotions. Margin = net sales − commissions − other platform
fees − food − other variable costs. See README for all definitions and rounding.

Evidence: `FinancialGoldenTests.test_exact_golden_reconciliation`; browser output
`Launch / golden baseline: $9,200 net, $4,700 margin, 51.09%, no accounting profit: PASS`.

## Six workflow verification

| Workflow | Exact test input | Expected | Actual observed | Status | Evidence | Known limitations |
|---|---|---|---|---|---|---|
| 1. Ingest/normalize | Golden CSV; re-upload; all five known-format historical sample CSVs in `samples/`; isolated malformed and signed-credit rows | Exact golden totals, 4 duplicate rows skipped; all known sample rows accounted for; invalid rows reported; unknown costs stay unknown | Golden and dedupe reconcile; known formats accept 781 DoorDash, 442 Uber Eats, 184 Grubhub, 369 Square rows and 63 supplier observations; malformed rows reported; signed credits preserved | **Working with limitations** | `test_import_dedupe_rejections_and_persistence`, `test_known_platform_and_supplier_exports_are_normalized_without_fabricated_costs`, `test_known_import_preserves_signed_credits_and_rejected_row_numbers`; browser upload check | Known exports lack refunds/consumption/complete variable costs and contract terms, so connected margin stays unavailable. Unknown formats require normalized CSV; no external AI mapping in connected ingestion. Unit/vendor normalization is basic; no inventory matching. |
| 2. Diagnostic Guru | Explain `DoorDash:G-DD` finding; question “Am I being overcharged?” | Same $4,700 baseline; $100 conditional exposure; explicit fallback and evidence | Matching baseline/evidence, deterministic fallback label, contract discrepancy explanation | **Working with limitations** | `test_six_agents_share_baseline_and_persist`; browser `Auditor evidence → Guru shares baseline: PASS` | Rule-based summary, not general LLM reasoning; no causal explanation of period-over-period change without a prior baseline. Optional legacy LLM is privacy-gated and only mocked in tests. |
| 3. Proactive Auditor | Golden rows, document observations and repeated-fee edge fixture | $100 commission discrepancy on supplied 20% gross basis; merchant promotion review; document-copy inquiry; confirmed $1/lb invoice price change; grouped repeated charges | Four golden findings with stable IDs/evidence; duplicate invoice has no payment claim; repeated charge group avoids duplicate fee alerts | **Working with limitations** | `test_contractual_basis_rate_and_no_double_counting`, `test_invoice_documents_do_not_establish_payment`, `test_isolated_edge_fixtures_and_repeated_errors`, `test_multiple_lines_are_not_duplicate_invoice_documents`; browser finding checks | Contract/authenticity supplied by owner, not independently verified. Price observations confirm a document change, not a guaranteed overcharge. Briefing is generated on load; no scheduler/background job. |
| 4. Predictive Simulation | 5% price, 0% food, 0 commission points; separate 10% food and 1 commission point unit tests | Before $4,700, after $5,127.50, delta $427.50; food scenario −$280; commission scenario −$100 | Exact expected cents; persisted named scenarios use same baseline | **Verified working** | `test_scenario_uses_same_engine_and_unchanged_demand`; browser `Exact scenario → explicit approval → honestly simulated execution: PASS` | Aggregate uniform scenario; unchanged demand; refunds/promotions fixed in dollars; existing fee discrepancies retained. Does not forecast demand. Original item-specific simulations remain backend-calculated in historical explorer. |
| 5. Execution | Propose saved scenario; incorrect then exact `APPROVE SIMULATION ONLY`; retry, expiry and changed-baseline cases | Exact approval required; persisted Proposed → Approved → Simulated; reject repeat; stale/expired proposal Failed | Expected state/history; no external API call; never externally Verified | **Simulated** | `test_six_agents_share_baseline_and_persist`, `test_stale_and_expired_proposals_fail_without_external_updates`; browser approval and persistence checks | No production/sandbox write is made by connected workflow. “Verified” requires actual external verification and is deliberately never claimed here. Original sandbox integration is tested separately with a fake transport. |
| 6. Disputes | Commission finding and duplicate-document finding from same baseline | Conditional $100 commission claim with actual row/terms; duplicate inquiry without amount; owner review; never submitted | Expected claims, evidence and review statuses; drafts persist | **Verified working** | `test_duplicate_document_draft_has_no_claim`, `test_six_agents_share_baseline_and_persist`; browser `Evidence-backed $100 conditional dispute; duplicate document has no payment claim: PASS` | Draft generation only. No filing API, attachments export, payment ledger or guaranteed recovery. |

## End-to-end judge journey

Executed by `python3 tests/browser_prototype.py` with headless Chromium and all
non-local requests blocked. The test uses a temporary SQLite file, a loopback
HTTP server, blank credentials and denied Python outbound transports.

| Step | Observed result |
|---|---|
| 1. Launch | Real HTTP server starts, default connected view appears. Header Ask focuses this workspace's Guru, not the historical sample assistant. |
| 2. Load/import | Golden loader works; CSV upload works; repeated upload skips 4 rows. |
| 3. Financial totals | $9,200 net, $4,700 margin, 51.09%; accounting profit unavailable. |
| 4. Auditor evidence | Commission finding exposes `DoorDash:G-DD` and its normalized row. |
| 5. Guru explanation | Finding-specific deterministic answer appears. |
| 6. Guru agreement | $4,700 baseline matches dashboard; no invented payment conclusion. |
| 7. Simulation | Before/after and +$427.50 match exact engine results. |
| 8. Approval | Wrong phrase keeps approval disabled; exact phrase permits only the simulation. |
| 9. Execution result | Proposed → Approved → Simulated; explicit no external API result. |
| 10. Dispute | $100 conditional claim; duplicate document has no established claim. |
| 11. Reload | Baseline, scenarios, action history and saved draft persist. |
| 12. Reset | Connected records/history clear; original seeded data remains available. |

## Executed checks

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -q` | **84 tests passed**: financial, ingestion, persistence, HTTP security, original integrations and Square guards. |
| `node --test tests/js/*.test.cjs` | **7 tests passed**: production inline JS parsing, consumption of backend financial models, corrected sample/synthetic baselines, recipe-cost display and backend/frontend transaction bridge. |
| `python3 tests/browser_prototype.py` | **Passed**: twelve-step connected journey, repeat upload, XSS text rendering, keyboard/table semantics, 390px layout and reset. |
| `python3 tests/browser_input_security.py` | **Passed**: all eight views, hostile stored data/receipt fields, sample invoice save, mocked Ask schema, mobile layout, CSP and failed-live-load behavior. |
| `python3 tests/browser_square_guard.py` | **Passed**: original seven views, explicit sandbox review, fake absolute-price writes once, stale/repeated command rejection, fake seed and read-back. |
| `python3 -m py_compile server.py financial_engine.py legacy_finance.py prototype.py` | **Passed**. |
| `git diff --check` | **Passed**. |

Security checks cover `.env`/database/Python/sample/private-file isolation,
encoded traversal/query strings/HEAD, same-origin JSON writes, finite numbers,
malformed/duplicate JSON fields, invalid identifiers, body limits, throttling,
CSP nonces, stored XSS, exact confirmation and action freshness. Optional AI
transport is blocked without privacy approval even if a key is present.

Accessibility verification is **limited**: keyboard focus, visible focus styles,
labels, status/alert regions, disclosure controls, table captions/scoped headers
and mobile overflow. **Not tested:** full WCAG 2.2 AA audit, assistive-technology
compatibility, color contrast across every theme/browser. No claim of conformance.

## Historical correction and migrations

The base branch had references to input-validation helpers without their imports
and had lost common HTTP security dispatch. This PR restores the necessary
validation/nonce/transport prerequisites, using the repaired security PR behavior,
then retains and tests the sandbox review routes.

A duplicate supplier document was previously deducted as a second payment. The
historical CSV-backed change was −$573.766875. After removing the unsupported
$737.80 deduction and applying backend period-cent rounding, the comparison is
**+$164.03 estimated contribution margin**, not a $574 profit decline. Original
audit fixture values remain recorded for history; regression expectations now
assert the corrected behavior. Invoice purchases still do not prove consumed
food costs or payments.

The browser no longer owns the historical margin/simulation formula. Historical
recipe costs are supplied by the backend with explicit zero/missing-data
assumptions. The new normalized workspace uses no such missing-cost substitutions.
Existing SQLite tables are unchanged; four `prototype_*` tables are created
idempotently. Existing Square transport and review protections remain intact.

## Remaining risks / deliberately incomplete

- **Local only:** owner authentication and location isolation are not on this
  development base. This is not suitable for a public hosted deployment.
- The normalized schema relies on reviewed source semantics; it cannot infer
  that gross sales were already netted. Payment reconciliation, tax, inventory,
  labor and full operating expense accounting are incomplete.
- No external AI or real Square integration was tested. All external operations
  were blocked or mocked; no disputes were submitted. Multilingual free-text
  reasoning is not implemented.
- Scenarios are conditional arithmetic, not causal forecasts. Price changes,
  contract changes and food costs are aggregate assumptions in the connected UI.
- Synthetic invoice evidence is structured fixture data, not a newly executed
  OCR/PDF extraction. Existing extraction/review is covered with mocked output.
- SQLite JSON state and in-process locking are intended for one local instance,
  not horizontally scaled hosted financial storage.
- Browser regression scripts are not currently run in GitHub Actions. Hosted CI
  status is separate from the local results above.
