# ProfitShield: what each part does

## Start and sign in

Python 3.9+ is enough for the application. There is no npm build or application
package installation. In your repository directory, run once:

```bash
python3 owner_auth.py bootstrap
```

Choose your own password of at least 14 characters. Then start the server:

```bash
python3 server.py
```

Open **http://localhost:8000/#demo** and sign in. Keep external API credentials
blank for the synthetic hackathon run; see README for blank-credential launch
commands. If an owner already exists, use the existing password. Local recovery
is `python3 owner_auth.py recover`; recovery revokes all previous sessions.

## Financial workspace

This is the default page and the main connected workflow. Its records are separate
from the historical books. Load **example restaurant** or choose/drop a synthetic
normalized CSV. Loading the example replaces the connected dataset, scenarios,
actions and drafts after confirmation. It does not replace historical imports.

The empty state asks for records rather than presenting zeroes as business results.
The summary shows the imported date range and channels. Net sales, contribution
margin, margin rate and accounting-profit availability come from backend cents.
Open **Inspect the financial baseline** for all components and missing inputs.

The example reconciles to $10,000 gross, $9,200 net, $4,700 contribution margin,
and 51.09%. Accounting profit is unavailable because operating expenses are not
complete. These are synthetic reference amounts, not a customer outcome.

### Import

Upload a normalized CSV or known DoorDash, Uber Eats, Grubhub, Square or supplier
export. Comma, semicolon and tab separators, Excel separator preambles, UTF-8
and BOM-marked UTF-16 are supported. Header spaces are trimmed; normalized names
are case-insensitive. Download a synthetic CSV template beside the upload control. A corrected file can be selected again after an error. Accepted data is
stored; identical transactions are skipped, and conflicting records are reported
rather than silently overwritten. Supplier files can provide invoice observations
without proving payments. Missing variable costs remain unknown. Known platform
exports alone may therefore be insufficient for a complete margin or scenario.

Historical import uploads in Connections update a different set of books. Do not
expect an import in one experience to populate the other.

### Findings and Guru

The Auditor checks supplied contract terms, promotion approvals, recorded fees,
invoice prices and document copies. Open the evidence disclosure for source rows.
The $100 example commission exposure compares $900 charged against a supplied
20% contract on $4,000 gross. It is conditional, not recovered money.

**Explain finding** opens a rule-based Guru explanation tied to that baseline.
The connected Guru is explicitly deterministic; it makes no external AI request.
A duplicate invoice produces a reconciliation inquiry, not a duplicate-payment claim.

### Scenarios and approval

Change uniform menu-price %, ingredient-cost %, or commission percentage points,
then calculate. The example 5% price scenario gives $5,127.50 margin, +$427.50
on the reference dataset. Demand and quantities remain fixed; refunds/promotions
remain fixed in dollars. This is conditional arithmetic, not a demand forecast.
Missing required costs prevent a scenario. An old baseline also makes saved
scenarios/proposals stale; recalculate instead of approving stale numbers.

Propose a scenario, review it, then type **APPROVE SIMULATION ONLY**. The backend
checks the phrase, baseline and ten-minute expiry and blocks repeat approval.
History becomes **Proposed → Approved → Simulated**. It does not change a platform
menu or call Square. Simulated results are never presented as externally verified.

### Dispute drafts

**Prepare draft** uses the finding, evidence and supplied contract assumptions.
Review it and use **Download draft** for a text copy. Submit it yourself through
the platform or supplier's process. No dispute is filed automatically. Drafts
from an earlier baseline are labelled; regenerate before using them.

**Refresh** reloads persisted data. A failed refresh preserves the last loaded
baseline and shows an error. **Reset demo** clears connected data and histories
after confirmation, preserving historical books and the owner password.

## Historical pages

| Page | What it does |
|---|---|
| Overview | Weekly historical sales, estimated contribution margin, a waterfall/evidence chart, findings, channel breakdown and reporting-week trend. Change the week to recalculate the recommendation. |
| Money leaks | Review historical findings and manually track open/disputed/handled status. Status is owner-entered, not externally verified recovery. |
| Workflow assistants | Explains ingestion, auditing, Guru, simulation, execution review and dispute preparation for historical books. These are workflows, not six independent autonomous LLM processes. |
| Payouts | Filter imported delivery transactions by channel, contract discrepancy, promotion or error charge; inspect line items and net payout. |
| Invoices | Inspect historical supplier invoice records and ingredient-price observations. Duplicate document flags do not establish payment duplication. |
| Menu & margins | Recipe-based food-cost estimates and in-store/delivery margin estimates. These exclude missing operating expenses. |
| Connections | Historical CSV imports, import/dedupe/redaction reports, optional Square sandbox sync, and owner-reviewed receipt lines. |

Historical estimates assume unreported refunds and other variable costs are zero
and estimate food from recipes. The normalized workspace does not fill missing
costs this way. Neither experience claims full accounting profit without inputs.

## Connections, receipts and optional integrations

- Historical CSV reports import directly. Unfamiliar layouts use rules or optional
  AI mapping. Delivery platforms are not live-connected or scheduled background syncs.
- **Use sample receipt** loads pre-read synthetic lines with no AI. Review quantity,
  price and ingredient matching, then **Add to books**. If save fails, the review
  stays editable. Saving refreshes historical prices and estimates.
- Receipt/PDF extraction needs an OpenRouter key and privacy approval. Errors are
  visible; the sample can demonstrate line review without an external call.
- **Ask ProfitShield** uses the historical assistant outside the Financial workspace.
  It supports rule-based answers and backend item-price scenarios; optional AI is
  privacy-gated. In the Financial workspace, Ask focuses its own Guru.
- Square is sandbox-only. Sync imports test orders. Test-order creation and menu
  changes use a separate exact review/confirmation plan; they are not the connected
  simulated action. Production writes are blocked, and delivery menus require
  manual edits. Browser tests use fake Square transport, not a real account.

## How the backend fits together

| Component | Responsibility |
|---|---|
| `index.html` | Responsive UI, form state, visualizations, evidence/review controls, request/error handling, help dialog. |
| `server.py` | HTTP APIs, session/CSRF enforcement, imports, historical storage, optional integrations and serving the UI. |
| `owner_auth.py` | Password bootstrap/recovery, scrypt hashing, stored session hashes, cookie/session expiration and login throttling. |
| `financial_engine.py` | Canonical Decimal/integer-cent financial normalization, calculations, findings and scenarios. |
| `prototype.py` | Connected dataset, scenarios, proposals, simulated action history and dispute drafts in SQLite. |
| `legacy_finance.py` | Historical estimates and explicit recipe/missing-input assumptions. |
| `input_security.py` | Bounded requests, allowed fields/values, dates, finite numbers, routing and request limits. |
| `square_actions.py` | Expiring one-use review plans around separate Square sandbox mutations. |

## Security and limits

Financial APIs require an owner session. Mutations also require an exact allowed
origin, JSON and a session-specific CSRF token. Cookies are HttpOnly/SameSite;
HTTPS uses Secure cookies. Plain HTTP authentication is accepted only for
localhost/127.0.0.1. The server binds to loopback. Sessions expire after 30 minutes
idle or 12 hours total; sign out revokes the session. Password storage remains
separate from restaurant records and is not served by HTTP or committed to Git.

This is single-owner authentication, not completed multi-user organization/location
isolation or a production hosting setup. Use synthetic records. No real documents
or provider credentials are necessary for the tested demo. External AI still needs
privacy approval even if its key is configured.
