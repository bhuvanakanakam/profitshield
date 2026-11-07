# ProfitShield contributor instructions

## Scope and repository workflow

- The upstream repository is `bhuvanakanakam/profitshield`.
- `development` is the integration branch. Start each task from its latest state,
  use a dedicated working branch and target `development` in every task PR.
- Inspect existing implementation, relevant PRs, nested instructions and tests
  before editing. Keep the change within the task's acceptance criteria.
- Never commit directly to `main`, push work to `main`, or automatically merge or
  deploy. Release/promotion policy requires a separately approved process.
- If upstream `development` is missing, report the blocker. Do not create or
  repoint branches without explicit approval. Do not substitute `main` as a PR base.
- Fetch before starting. Use fast-forward-only updates; do not discard another
  person's changes or rewrite shared branch history without explicit approval.
- A GitHub permission failure is a blocker, not authorization to change targets.
  Fork heads may be used for review, but the upstream PR must still target
  `development`. Explain any publication/access blocker clearly.
- Read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for commands, CI and handoff rules.

## Product constraints

These are requirements for development, not claims that the current prototype
already implements them. Do not add unrelated product changes to a workflow PR.

- One restaurant organization can have multiple isolated locations. Enforce
  authorization and location scope on APIs, queries, imports, evidence, files,
  caches, background work, AI context and external actions. Derive authorized
  scope on the server; do not trust a client-supplied location alone. Add negative
  tests for anonymous/unauthorized access and cross-location leakage when relevant.
- Keep contribution margin distinct from accounting profit. Show completeness,
  assumptions, missing inputs, time period, location and evidence. Ground outputs
  in canonical server-side calculations. Do not make the browser authoritative
  for financial truth or present incomplete estimates as accounting profit.
- Duplicate invoice documents are not proof of duplicate payment. Require
  payment/reconciliation evidence before asserting recovery or payment duplication.
- Square is sandbox only, regardless of environment settings. Never use production
  credentials or endpoints. Every external write requires explicit review and
  approval; retries and stale/repeated commands must not duplicate side effects.
- Do not send real financial documents or customer data to external AI before
  privacy approval. Use synthetic fixtures and mock external integrations in tests.
- Never expose secrets, database files, source or private documents through HTTP.
  Validate untrusted structures, allowed values, dates and numeric finiteness.
- Do not silently replace live-data errors with demo data. Preserve existing
  sample baselines unless an explicitly documented correctness fix requires change.
- Aim for WCAG 2.2 AA: semantic controls and names, full keyboard operation,
  visible/unobscured focus, useful programmatic errors, contrast, responsive zoom
  and reduced-motion support. Prefer calm interfaces and clear approval states.

## Verification and handoff

- Add meaningful regression tests for bugs and financial rules. Use temporary
  databases/files, synthetic data and mocked/blocked network integrations.
- Run the relevant tests and existing regression suites. Record inherited failures
  separately; do not disable checks, hide failures or change expected values just
  to make CI green. Baseline corrections require an explanation and review.
- For UI changes, check keyboard/focus, screen-reader semantics, contrast and
  responsive behavior as applicable. Explain N/A and untested cases; do not claim
  accessibility conformance from a calculation-only test.
- Reports/PRs must state the problem/outcome, changed files, commands and results,
  tests not run, assumptions, location/security/financial/accessibility impact,
  remaining risks and migration/recovery notes. Never include actual credentials
  or private records. Leave the PR for human review; do not merge or deploy.
