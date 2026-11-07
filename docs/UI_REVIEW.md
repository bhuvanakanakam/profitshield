# Frontend experience review

Base: upstream `development` at `3aacf07`. Task: restaurant frontend / hackathon demo.
Review target: `development`. No deployment, merge, or external platform write.

## Outcome

The connected workspace now leads with server-provided net sales, contribution
margin, margin percentage, and accounting-profit availability. The complete
financial baseline remains in an expandable table with caption and scoped headers.
The UI organizes import, explanation, evidence, simulation, approval and dispute
preparation into responsive cards with labelled navigation and clear empty states.
The historical explorer shares the forest/ivory design and adds a weekly chart
using the server-provided reporting metrics. No financial formula changed.

## Integration repairs

The fetched base combined partial owner-auth code with an unauthenticated demo:
`_json` referenced an undefined cookie, startup did not attach its auth instance,
and session/login/logout/CSRF handling had been removed. The existing main-branch
authentication checks were restored around both historical and new demo APIs.
The connected demo now uses the existing `apiFetch` session/CSRF wrapper.
HTTP test setup also lacked its OwnerAuth import and authenticated argument.
These were repaired rather than removing authentication to make UI tests pass.

The unmodified base ran 44 Python tests with five setup errors. The final suite
runs 108 tests successfully, including denied anonymous demo requests and denied
demo writes without CSRF. Financial fixture expectations remain unchanged.

## Verification

- `python3 -m unittest discover -s tests -v`: 108 tests pass.
- `node --test tests/js/*.test.cjs`: seven tests pass.
- `python3 tests/browser_prototype.py`: golden baseline, explanation, conditional
  dispute, duplicate-document inquiry, CSV dedupe, untrusted text rendering,
  scenario/approval, persistence/reset, keyboard and 390/768/1024/1440px checks pass.
- `python3 tests/browser_owner_auth.py`: login/logout, all historical views,
  expired session, CSRF-protected invoice save, and live-load error handling pass.
- `python3 tests/browser_square_guard.py`: fake transport only; explicit review,
  exact phrase, repeat prevention, stale scenario and sandbox order flow pass.
- `PROFITSHIELD_PR_BASE=development python3 .github/scripts/check_pr_base.py`.
- `git diff --check`.

Optional screenshot capture:
`PROFITSHIELD_UI_SCREENSHOTS=/tmp/profitshield-ui python3 tests/browser_prototype.py`.
Browser tests block outbound services and use disposable auth/restaurant databases.

## Boundaries and recovery

All demo amounts remain canonical backend cents; missing expenses remain unknown,
duplicate documents remain inquiries, and execution remains explicitly simulated.
Organization/location isolation is still incomplete: use synthetic local data.
No external AI document processing or real Square calls were tested or performed.
Keyboard, table semantics, focus, reduced-motion and responsive checks are useful
partial evidence; no full screen-reader or WCAG conformance audit is claimed.
The new light palette is deliberate; a separate dark theme is not provided.

No schema or data migration is required. Revert the UI commit to remove the
redesign; keep the separate authentication integration repair. Reversing that
repair restores known runtime and access-control defects and is not recommended.
The GitHub connector account mismatch remains unresolved, so changes are local.
