# Final UI and owner-login verification

Task: finish the frontend against the latest backend, lighten green controls,
restore owner login, verify complete journeys, and publish to main as explicitly
requested in the session. Starting base: development@5733d75, with the same file
tree as main@c368461. No financial formulas or expected fixture amounts changed.

## Changes

- Ported the responsive workspace/overview redesign onto the current backend.
- Light sage primary buttons with dark text; measured CTA contrast above 4.5:1.
- Styled owner login with scrypt password hashes, stored session hashes, expiry,
  HttpOnly/SameSite cookies, secure HTTPS mode and session-specific CSRF checks.
- Anonymous financial reads/writes denied; bootstrap/recovery remain local CLI.
- Native help dialog, skip link, modal drawer focus containment/return, readable
  mobile layouts, reporting scope and honest data/connection/AI labels.
- Empty workspace prompts for data. Missing-cost records disable unsafe scenarios.
- Import errors preserve the loaded baseline and permit a corrected same-name file.
- Receipt save errors preserve review inputs; a successful retry refreshes books.
- Historical questions recover from failed calculations without a stuck spinner.
- Week changes refresh the backend recommendation, rather than showing a stale one.
- Conditional drafts download as text; prior-baseline drafts are labelled.
- Connected actions retain exact approval, stale/expiry/repeat protections and
  explicit simulated status. Historical Square writes retain separate review.
- Startup retains server instance/auth wiring and closes the server on shutdown.
- README, demo labels and user guide explain the two datasets and current login.

## Executed checks

| Command | Observed result |
|---|---|
| `python3 -m unittest discover -s tests -v` | 112 tests pass, including real CLI startup/login/golden backend/logout in disposable storage. |
| `node --test tests/js/*.test.cjs` | Seven tests pass; current inline JavaScript parses and consumes the server financial model. |
| `python3 tests/browser_prototype.py` | Connected golden journey, Guru, scenario/approval, conditional draft, no duplicate-payment claim, reload, dedupe, escaped untrusted text, reset and responsive checks pass. |
| `python3 tests/browser_owner_auth.py` | Anonymous login-only page, login, historical routes, CSRF invoice save, logout, session expiry and failed-live-load behavior pass. |
| `python3 tests/browser_input_security.py` | Stored order/evidence/receipt text escaping, sample receipt save, mocked Ask API schema, eight views, mobile and failed-live-load checks pass. |
| `python3 tests/browser_square_guard.py` | Fake Square only: review/phrase, once-only absolute-price writes, stale/repeated commands, test-order preview/create/readback and historical views pass. |
| `python3 tests/browser_final_ui.py` | Final login/error/import/receipt/retry/export/help/focus/contrast/empty/incomplete-data journey passes; all eight views at 390/768/1024/1440px have no page overflow; no page JavaScript errors. |
| `python3 -m py_compile server.py owner_auth.py financial_engine.py prototype.py` | Pass. |
| `PROFITSHIELD_PR_BASE=development python3 .github/scripts/check_pr_base.py` | Pass. |
| `git diff --check` | Pass. |

Browser scripts use real local HTTP/SQLite with temporary databases and synthetic
records. External Python transports are blocked or faked; browser requests outside
the local server are blocked. The new runtime test starts the actual server.py
entry point with blank provider keys and disposable auth/financial storage.
Screenshots were inspected for login, connected workspace, and mobile layouts.

## Boundaries

- Real OpenRouter/vision service and real Square credentials were not exercised.
  Their existing contracts are tested with mocks; no provider write was made.
- Connected Guru is deterministic; connected execution is simulated; drafts are
  never submitted. Contribution margin is not accounting profit. Historical
  estimates and normalized financial records remain separate datasets.
- Login is single-owner. Organization/location isolation, production deployment,
  payment reconciliation and complete cost collection remain incomplete.
- Keyboard/focus, labels/statuses, CTA contrast, reduced motion and responsive
  layouts were checked. This is not a full WCAG or screen-reader certification.
- Local verification uses Python 3.12 and Node 24. Python 3.9 compatibility is
  verified separately by the existing GitHub CI matrix when run.

## Migration and recovery

Existing restaurant tables and data are preserved. Owner authentication adds a
separate private owner-auth.sqlite3 file with local CLI bootstrap; no default
password is committed. If an owner already exists, its password remains valid.
Recovery intentionally revokes sessions. A demo reset does not reset the password.
For custom authentication storage, export the same path for CLI and server.
No production deployment or database reset is part of this change.
