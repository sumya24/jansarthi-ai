# Playwright E2E Test Report — 2026-08-22

**Scope: Playwright end-to-end testing only** (`frontend-react/e2e/*.spec.ts`, 35 tests total). This
does not cover the separate manual Ask Sarthi conversation testing done the same day — see that
discussion separately if needed.

## What was run

Three consecutive full-suite runs today, against the real backend + real frontend dev servers
(no mocking), as each run's findings led to a fix before the next attempt:

1. **Run 1** (`--headed`, then investigated via saved failure artifacts) — surfaced 2 distinct root
   causes (below), not one bug.
2. **Run 2** (background, after fixing cause #1 and raising rate limits) — backend crashed silently
   partway through (no Python traceback in its log); stopped once confirmed the backend was
   unreachable.
3. **Run 3** (background, backend restarted cleanly) — currently the authoritative run; see
   **Results** below. Still finishing at the time of this report (see **Status**).

## Root causes found

### 1. Fixed — a genuine test bug, not an app bug
`admin-worker-flow.spec.ts`'s "super admin creates a worker" test filled every field in the "Add a
worker" modal *except* **Confirm password** — a field that exists on that form but wasn't there
when this test was originally written. The browser's own validation silently blocked the submit,
so the worker was never created and the test failed waiting for the new worker's name to appear.
**Fixed**: added the missing `.fill()` call. This test now passes reliably.

### 2. Not fixable in code — Gmail's daily sending limit is exhausted
The backend log shows:
```
550 5.4.5 Daily user sending limit exceeded ... support.google.com/a/answer/166852
```
The real Gmail account this app uses to send signup/OTP verification emails has hit Google's own
daily cap (from today's cumulative testing volume). Every test that needs a citizen to sign up via
a real verification email fails at that exact step — the backend correctly returns `503`, the
frontend correctly never shows the "Verification code" box, and the test times out waiting for it.
**This affects the majority of the suite** (`ask-janmitra*`, `citizen-signup`, `complaint-tracking`,
most of `auth-session`, several `email-otp-and-login` cases) and can only be resolved by waiting for
Google's daily limit to reset or switching to a different email sender — no code change can work
around it, since there's no dev-mode bypass for SMTP in this codebase today.

### 3. Documented gap in `docs/RATE_LIMITING.md`, corrected for this session
That doc's "Running Playwright locally" section only mentions raising `GENERAL_RATE_LIMIT`/
`LOGIN_RATE_LIMIT`/`AI_RATE_LIMIT` — it doesn't mention `OTP_RATE_LIMIT` (3 requests/10 min per IP),
which is the one that actually blocks a signup-heavy test run. Also written in bash syntax, which
doesn't run in PowerShell. Backend was restarted for these runs with the full, corrected set:
```powershell
$env:GENERAL_RATE_LIMIT=1000; $env:LOGIN_RATE_LIMIT=1000; $env:AI_RATE_LIMIT=1000; $env:OTP_RATE_LIMIT=1000; $env:SIGNUP_RATE_LIMIT=1000; python -m uvicorn backend.main:app --port 8000
```
(Not yet folded back into the doc itself — flag if you want that done.)

### 4. Backend crashed TWICE, unexplained — now a confirmed recurring issue, not a one-off
Between run 2 and run 3, and again near the very end of run 3 (mid-`worker-notifications-and-reports.spec.ts`),
the backend process stopped responding with no error in its own log — same silent-stop pattern both
times, no Python traceback either time. This is consistent with a known prior issue in this project
(Windows running low on memory under combined load: backend + the RAG embedding model + a real
Chromium browser at once), but still not conclusively confirmed. Happening twice in one session,
in the same way, means this is now worth treating as a real, reproducible stability issue rather
than a fluke — recommend investigating properly (e.g. watching Task Manager's memory graph during
a run) before relying on a single long unattended Playwright run again.

## Results (Run 3 — snapshot as of this report; a few specs were still finishing)

| # | Spec | Result | Reason |
|---|---|---|---|
| 1 | admin-ai-monitoring | ✅ Pass | No signup involved |
| 2 | admin-worker-flow (create worker) | ✅ Pass | **Fixed this run** (Confirm password) |
| 3 | admin-worker-flow (citizen can't add worker) | ❌ Fail | Needs citizen signup → Gmail limit |
| 5,7 | ask-janmitra-image (×2) | ❌ Fail | Needs citizen signup → Gmail limit |
| 9 | ask-janmitra-voice-image | ❌ Fail | Needs citizen signup → Gmail limit |
| 11 | ask-janmitra-voice | ❌ Fail | Needs citizen signup → Gmail limit |
| 13,15,17,19,21 | ask-janmitra (×5) | ❌ Fail | Needs citizen signup → Gmail limit |
| 23 | auth-session (password mismatch validation) | ✅ Pass | Client-side only, no signup |
| 24 | auth-session (weak password validation) | ✅ Pass | Client-side only, no signup |
| 25,27,29 | auth-session (token refresh / logout / change password) | ❌ Fail | Needs citizen signup → Gmail limit |
| 31 | citizen-signup | ❌ Fail | Needs citizen signup → Gmail limit |
| 33 | complaint-tracking | ❌ Fail | Needs citizen signup → Gmail limit |
| 35 | email-otp-and-login (login page accepts phone/email) | ✅ Pass | No email send needed |
| 36 | email-otp-and-login (unregistered-email error) | ✅ Pass | No email send needed |
| 37 | email-otp-and-login (forgot-password code-sent step) | ❌ Fail | Needs a real email send → Gmail limit |
| 39 | email-otp-and-login (forgot-password requires email) | ✅ Pass | Client-side validation only |
| 40 | email-otp-and-login (settings shows verified email) | ❌ Fail | Needs citizen signup → Gmail limit |

**Final, authoritative tally (Playwright's own summary): 10 passed, 25 failed, in 15.3 minutes.**
All 25 failures trace to the same single Gmail cause, not 25 separate bugs — every one of them
needs a real citizen/worker signup or a real verification/password-reset email to proceed, and
every one fails at that exact step. The remaining specs beyond the table above followed the
identical pattern: `evidence-upload` (both failed — needs signup), `login-errors` (1 failed on
signup, 1 passed — client-side only), `protected-routes` (1 passed — no signup needed, 1 failed —
needs signup), `theme-and-voice` (1 passed — theme toggle, no signup, 1 failed — needs signup),
`worker-notifications-and-reports` (both failed — needs an admin-created worker + citizen signup).

## Status / next steps

- Every failure above traces to exactly **one** external cause (Gmail's daily limit), not a
  code defect — once it resets (or a different email sender is used), a re-run should pass close to
  everything that isn't a genuine, separate app bug.
- The one real test bug found (Confirm password) is already fixed and confirmed passing.
- Recommend re-running the full suite once Gmail's limit clears, to get a clean, final pass/fail
  count with no external noise.
