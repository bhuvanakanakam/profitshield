# Three-minute judge presentation

Prerequisite: run the credential-free command in README; open
http://localhost:8000/#demo. No internet, AI key or Square token is required.
Use only the repository's synthetic data.

## 0:00–0:30 — Ingest and establish trust

“Restaurant owners need a defensible number before an AI recommendation.”
Click **Load synthetic golden restaurant** (or upload `demo_data/golden.csv`).
Show **$10,000 gross**, **$9,200 net**, **$4,700 contribution margin**, **51.09%**.
“Refunds and merchant-funded promotions are represented once. The $100
platform-funded promotion does not reduce restaurant revenue. Accounting profit
is unavailable because operating expenses are incomplete.”

## 0:30–1:00 — Auditor and evidence

Open the **commission overcharge** finding's Evidence disclosure. Point to
`DoorDash:G-DD`: $4,000 gross, supplied 20% gross-sales commission contract,
$900 charged, **$100 conditional exposure**.
“The commission is already in margin. We do not deduct the $100 again.”
Show the duplicate invoice finding: “Two documents, no proof of two payments.”

## 1:00–1:25 — Ask Guru

Click **Explain finding** on the commission finding.
“Guru shares the exact same baseline and evidence. This run uses a clearly
labeled deterministic fallback, not a pretend AI answer. Optional external AI is
disabled pending privacy approval.”
Point to the $4,700 margin and $100 conditional exposure.

## 1:25–2:00 — Compare and approve

Use **5% menu price change**, **0% ingredient cost**, **0 commission points**.
Click **Calculate scenario**. Show **$5,127.50 after**, **+$427.50 conditional
weekly margin**, with unchanged demand and dollar refunds/promotions.
“These are assumptions, not promised demand or revenue.”
Click **Propose simulated execution**, review the requested scenario and type
**APPROVE SIMULATION ONLY**. Click **Approve simulation**.
Show `Proposed → Approved → Simulated` and the no-external-call result.
“Simulated is not externally Verified; no platform price has changed.”

## 2:00–2:30 — Prepare a defensible dispute

Click **Prepare draft** on the commission finding. Show the **$100 conditional
claim**, transaction, supplied contract assumption and owner-review status.
“We prepare evidence; the owner reviews and submits separately.”
Click **Prepare draft** on the duplicate-document finding. Show
**Not established — inquiry only**, with no claim amount.

## 2:30–3:00 — Persistence and repeatability

Reload. Show the same baseline, saved scenario, simulated history and last draft.
Optionally upload `demo_data/golden.csv` again: **4 duplicates skipped**, totals
unchanged. Click **Reset connected demo**, confirm, then **Load synthetic golden
restaurant** to repeat.

“Python cents and Decimal own the financial arithmetic. Source exports can be
incomplete, and we say so. This prototype demonstrates all six connected steps
without pretending that missing accounting data, external AI or production
execution are live.”

## If something fails

Show the visible error; do not claim the operation succeeded. Reload and use the
synthetic golden loader. Do not add keys, enable real integrations or substitute
customer documents during the demo. Offline Guru is intentionally rule-based.
