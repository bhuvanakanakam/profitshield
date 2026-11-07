# Development workflow (M0)

`bhuvanakanakam/profitshield` is the upstream repository. `development` is the
integration branch. Each task uses a dedicated branch and a review PR targeting
`development`; `main` is not a task branch or integration target. This document
establishes conventions, not a release pipeline or a claim that all product
constraints are already implemented.

## Start a task

Inspect the repository, applicable `AGENTS.md` files, related PRs and tests first.
Use a clean working tree, preserving anyone else's uncommitted work.

```bash
git fetch origin
git switch development
git pull --ff-only origin development
git switch -c chore/01-development-workflow  # use the assigned task branch
```

Confirm `origin` points to upstream before pushing. If using a fork, fetch
upstream `development` explicitly and open the cross-repository PR against
upstream `development`. Do not automatically rewrite remotes in other developers'
checkouts. Never force-push or discard shared work without specific approval.

If upstream `development` does not exist, stop and report the blocker. A maintainer
must approve its initial source commit before anyone creates it. For this M0 task,
creation from `main@f5489e7c20225cca96278e587f7190fbd2f160c5` was explicitly approved.
That is a one-time bootstrap decision, not standing permission to recreate or
reset the integration branch. Creation copies a pointer; it makes no `main`
commit. If permissions deny creation, prepare only the authorized local work and
report the publishing blocker; do not create a PR against `main` instead.

## Make a reviewable change

Use `feat/<task>`, `fix/<task>` or `chore/<task>` as appropriate; honor the assigned
branch name. Keep each PR within its acceptance criteria. Inspect existing tests
and record baseline failures before editing. Avoid unrelated refactors and do
not bundle other pending PRs into a workflow change.

Use the PR template for the problem, outcome, scope, verification, risks and
migration/recovery notes. State the milestone (for example M0) and task identifier.
“PR #01 — Development workflow” is a roadmap identifier; GitHub assigns the actual
pull-request number. Select **base: development** in GitHub before submitting.

The PR should include relevant unit/integration/security/accessibility evidence,
not invented “pass” statuses. Mark irrelevant checks N/A with a reason. Keep
secrets and real customer/financial records out of code, fixtures, logs and PRs.
Do not silently alter financial baselines or weaken failing checks.

A human reviewer decides whether to integrate. Do not automatically merge or
deploy. This workflow does not authorize a `development` → `main` promotion;
release policy is separate work requiring explicit approval.

## Product and data boundaries

Follow `AGENTS.md`. The target is one restaurant organization with isolated
locations, not a single global location. Server authorization and location scope
must cover all relevant operations; test cross-location denial when changing
those paths. Contribution margin and accounting profit must remain distinct and
state completeness, assumptions and evidence. Canonical calculations belong on
the server. Document copies alone do not establish duplicate payment.

Use Square sandbox only and explicit review/approval for external writes. Mock
integrations and use synthetic data in tests. Do not transmit real financial
documents to AI before privacy approval. UI work should target WCAG 2.2 AA,
including keyboard/focus, semantics, errors, contrast and calm responsive behavior.
These constraints guide review; M0 does not implement location isolation,
authorization, accounting models, privacy policy or accessibility features.

## CI conventions

`.github/workflows/tests.yml` keeps the existing Python 3.9/3.12 and Node 24 jobs.
The **Development branch policy** job rejects PRs whose base is not exactly
`development`. PR `edited` events are included so changing the base reruns policy.
Do not filter the workflow to only development: that would hide wrong-base PRs.
The policy job is skipped for manual workflow runs, which have no PR base.

CI uses pinned official actions, read-only `contents` permission, no secrets,
non-persistent checkout credentials, sandbox settings, blank integration tokens,
timeouts and cancellation of superseded runs. Use `pull_request`, not
`pull_request_target`, to test untrusted changes. Never run production integrations
or deploy from test workflows. No new package installation is required for M0.

Run from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_development_workflow.py' -v
PROFITSHIELD_PR_BASE=development python3 .github/scripts/check_pr_base.py
python3 -m unittest discover -s tests -v
node --test tests/js/*.test.cjs
git diff --check
```

Read [tests/README.md](../tests/README.md) for synthetic fixtures and optional
browser regression commands. On systems where Python is named differently,
set the existing `PYTHON` override for the Node data bridge.

CI is a review signal, not repository-level protection. A maintainer should
configure the following after the workflow is available on the target branch:

- Require reviewed PRs and the policy, Python and frontend checks on `development`.
- Restrict direct writes, force pushes and deletion of integration/release branches.
- Restrict `main` to a separately approved release process; do not enable automatic
  task-PR merges or deployment as part of M0.
- Retarget/rebase existing task PRs to `development` and rerun tests, reviewing any
  changed diff. Existing PRs #4 and #5 targeted `main` at inspection; M0 does not
  silently edit their bases or resolve their application changes.

These settings need repository administration access and are not configured by
this documentation or by a required check inside a mutable PR workflow. No
organization-wide settings, default branch, branch protection, merge settings,
release workflow or existing PR metadata are changed here.

## Baseline, migration and rollback notes

M0 changes contributor docs, a PR template and workflow policy only. It has no
application code, database/schema migration, customer-data migration or deployment.
Removing the policy job/script/docs reverts M0 behavior; never delete or reset
`development` as a rollback shortcut because it may contain other reviewed work.

At the approved `f5489e7` baseline, Python tests already fail (one failure and 34
errors), including `NameError: validate_payload is not defined` in `server.py`.
The Node financial suite cannot complete because its Python sample importer
hits the same error. Preserve and report those results; repair application
integration in a separate scoped PR. Existing CI remains enabled and failures
remain visible. The new policy has independent regression tests and must not
be presented as proof that the application/security suite is green.
