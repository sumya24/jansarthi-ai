# Error Monitoring — A Complete Guide (Basic to Advanced)

This is a plain-language walkthrough of the error monitoring system built for JanSarthi AI.
It starts from "what even is this" and ends with "here's exactly how the code works." If you
just want the short technical reference (for a future developer, not a first read), see
`docs/ERROR_MONITORING.md` instead — this document is the teaching version.

---

## Part 1: The Basics

### 1.1 What problem does this solve?

Before this feature existed: if something broke in the live app — a crash, a bug, a failed
request — **nobody found out**. The only way to know was if a citizen complained, or if someone
happened to be manually reading server logs at that exact moment. For a real product, that's not
good enough.

### 1.2 The one-sentence explanation

We added a tool called **Sentry** that watches the app 24/7 and automatically tells you the
instant something breaks — like a smoke detector for software.

### 1.3 What is Sentry, exactly?

Sentry is a third-party service (a website + a small piece of code inside our app) that:
1. Sits quietly inside the app, watching.
2. The moment an error happens, it captures everything useful about it (what broke, where,
   why, how often).
3. Sends you an email immediately.
4. Keeps a dashboard on sentry.io where you can browse every error that's ever happened.

It's free for a project this size (see the "free forever" plan discussed earlier — 5,000
errors/month, one user account, no credit card).

### 1.4 What is a "DSN"?

Think of a DSN as an **address + a password combined into one string**. It tells our app "send
your error reports to THIS specific mailbox on Sentry's website." Each Sentry project (backend,
frontend) has its own DSN. Without a DSN, the app doesn't know where to send anything — so it
just... doesn't. That's the "off by default" behavior mentioned throughout this doc.

### 1.5 The most important design rule: "off unless you turn it on"

Every single piece of this feature is **disabled by default**. Nothing about the app changes,
nothing costs anything, nothing runs, until you personally:
1. Create a Sentry account (your own, free).
2. Get a DSN from it.
3. Paste that DSN into a config file.

Until step 3 happens, the app behaves *exactly* as if none of this code existed. This is a
deliberate, repeated pattern in this codebase (the same is true for other optional add-ons like
LangSmith) — nothing "extra" ever runs unless it's explicitly switched on.

---

## Part 2: The Five Things Sentry Can Do (In Plain Language)

Sentry isn't just one feature — it's five, and each one has its own on/off switch. Here's what
each one actually means, with a real example from this app.

### 2.1 Error Monitoring (the core feature)

**What it means:** if the code crashes — an unhandled exception, a bug — the full details (what
line of code, what the input was, what the error message was) get sent to you automatically.

**Real example:** if a bug ever caused `POST /complaints` to crash instead of saving a citizen's
complaint, you'd get an email within seconds showing the exact line that failed.

**Switch:** just setting `SENTRY_DSN` turns this on. It's the baseline — everything else below
is additional, optional detail on top of this.

### 2.2 Logs

**What it means:** this app already writes little notes to itself while it runs — things like
"New citizen account created" or "Too many login attempts" (you can see these if you run the
server and watch its terminal output). Turning on "Logs" means those same notes *also* get sent
to Sentry, so you can look them up later on Sentry's website instead of needing terminal access.

**Real example:** if a citizen says "my complaint didn't save right," you could search Sentry's
Logs for their phone number's activity around that time and see exactly what happened, step by
step.

**Switch:** `SENTRY_ENABLE_LOGS=true`. No new code was needed anywhere for this — it rides on
logging the app already does.

### 2.3 Application Metrics

**What it means:** simple running counts of real things happening in the app — not errors, just
normal activity, tracked as numbers over time.

**Real example — the 3 counters we actually built:**
| Counter | What it counts | Why it's useful |
|---|---|---|
| `complaint.created` | Every complaint filed, grouped by ward | "Which ward is generating the most complaints this week?" |
| `ask_sarthi.request` | Every Ask Sarthi question, grouped by text/photo/voice | "Are people using voice more than typing?" |
| `rate_limit.exceeded` | Every time someone got blocked for making too many requests | "Is someone trying to abuse the system?" |

**Switch:** `SENTRY_ENABLE_METRICS=true`.

### 2.4 Tracing

**What it means:** a detailed timeline of one single request from start to finish — e.g. "this
Ask Sarthi question took 2.3 seconds total: 0.1s to check the database, 1.9s waiting for the AI
to respond, 0.3s to save the answer." Useful for answering "why is this slow?"

**Switch:** `SENTRY_TRACES_SAMPLE_RATE` — a number from 0 (never) to 1 (always trace). Currently
set to `1.0` (trace everything) for testing.

### 2.5 Profiling

**What it means:** goes one level deeper than tracing — instead of just "the AI call took 1.9s,"
it can show *which exact function/line of Python code* was using the most time or CPU during
that request. Think of Tracing as "which room in the house is slow" and Profiling as "which
exact appliance in that room is slow."

**Switch:** `SENTRY_PROFILE_SESSION_SAMPLE_RATE`. Important detail: Profiling only ever works
*while Tracing is also happening* — if Tracing is off, Profiling has nothing to attach to and
does nothing, no matter what it's set to.

---

## Part 3: How It's Actually Built (Getting More Technical)

### 3.1 The two halves

This app has two separate programs: the **backend** (Python, does the real work — database,
AI calls, business logic) and the **frontend** (the website you click around in, built with
React). Sentry is wired into both, separately, because they're two different programs that can
each fail independently.

- **Backend Sentry** — all 5 products above (`backend/main.py`, `backend/config.py`).
- **Frontend Sentry** — just Error Monitoring + Tracing, plus one extra thing: a "crash screen"
  (see 3.4 below).

### 3.2 Where the backend switches live

Open `.env` (a file that's never uploaded to GitHub, since it holds secrets) and you'll find:

```
SENTRY_DSN=                              <- the "address" from Sentry.io. Empty = fully off.
SENTRY_ENVIRONMENT=development           <- just a label, so test errors don't mix with real ones
SENTRY_TRACES_SAMPLE_RATE=0.0            <- Tracing: 0 = off, 1 = trace everything
SENTRY_ENABLE_LOGS=false                 <- Logs: on/off
SENTRY_ENABLE_METRICS=false              <- Metrics: on/off
SENTRY_PROFILE_SESSION_SAMPLE_RATE=0.0   <- Profiling: 0 = off, 1 = profile everything
```

Each of these has a matching line in `backend/config.py` that reads it, and `backend/main.py`
has one function, `init_error_monitoring()`, that reads all of them and turns Sentry on with
exactly those settings.

### 3.3 Where the 3 metrics actually get counted

If you're curious where in the code a number actually gets counted, here's exactly where:

- `backend/routes/complaints.py` — right after a complaint is successfully saved, one line adds
  1 to the `complaint.created` counter.
- `backend/routes/ask_sarthi.py` — right when a question comes in (before it's even answered),
  one line adds 1 to `ask_sarthi.request`.
- `backend/middleware.py` and `backend/deps.py` — right when someone gets blocked by a rate
  limit, one line adds 1 to `rate_limit.exceeded`.

Each of these lines calls a small helper (`backend/services/metrics.py`), not Sentry's own
counting function directly — that helper is what actually checks whether Metrics is turned on
before sending anything. (Why not just rely on Sentry's own on/off setting for this? See 4.3
below — it turned out not to work.)

### 3.4 The frontend's "crash screen"

Separately from Sentry reporting, we also added a safety net: if the website's code ever crashes
while someone's using it, instead of the screen going completely blank (which used to happen),
they now see a friendly "Something went wrong — Reload page" message. This part works **even if
Sentry isn't configured at all** — it's a basic safety feature, not tied to the alerting system.
(Code: `frontend-react/src/components/CrashFallback.tsx`, wired up in `main.tsx`.)

### 3.5 Privacy — what does NOT get sent

Citizens' phone numbers and complaint text are real personal information. Sentry is explicitly
told **not** to attach extra personal details to error reports (`send_default_pii=False` on the
backend, `sendDefaultPii=False` on the frontend) — you get enough information to know *what
broke*, without a copy of what a citizen actually typed being sent to a third-party service.

---

## Part 4: The Interesting Bugs Found Along the Way

Worth knowing about, since they show *why* real testing (not just writing code) matters:

### 4.1 The "wrong word" bug

The very first version of the Metrics code used a setting called `tags` to label each counter
(e.g. "this complaint was in Ward 5"). Sentry's own example code online also showed `tags`. But
the actual version of Sentry's software installed in this project uses a *different* word for
the same idea: `attributes`. Using the wrong word didn't show up as an obvious error message —
it would have silently crashed 3 real features (filing a complaint, asking Sarthi, and hitting a
rate limit) the moment Metrics was turned on. Running the full test suite caught this
immediately — 48 tests failed at once, which pointed straight at the problem. Fixed by simply
using the correct word everywhere.

### 4.2 The "leaking into your dashboard" bug

The very first version turned Sentry on the instant the app's code was *loaded* — not just when
it actually started running. This sounds like a small technical distinction, but it had a real
consequence: the automated test suite *also* loads that same code every time it runs, so every
test run would have secretly turned on real Sentry reporting, using your real account. Since
tests deliberately trigger fake errors on purpose (to check the app handles them correctly),
those fake test errors would have shown up in your Sentry dashboard looking like real bugs.

Fixed by moving the "turn Sentry on" step so it only happens when the app is genuinely started
for real use (`uvicorn`, Docker) — never just from loading the code for a test. Confirmed fixed
by checking that running the tests no longer touches Sentry at all.

### 4.3 The "off switch that didn't switch anything off" bug

This is the most important one, and it wasn't caught by my own testing — a second, independent
live-verification pass against your real Sentry account found it.

The Logs and Metrics on/off switches (`SENTRY_ENABLE_LOGS`, `SENTRY_ENABLE_METRICS`) are simple
settings this app defines. But *turning that setting into Sentry actually obeying it* originally
relied on handing it straight to Sentry's own software as `enable_logs=`/`enable_metrics=`. It
turns out the specific version of Sentry's software installed here has quietly stopped listening
to those two settings — it still accepts them without complaint, but internally it now always
builds both the Logs and Metrics machinery regardless, and only prints a small warning in the
background saying "this setting will be removed later." Nothing crashes, nothing looks wrong on
the surface — which is exactly why it's a dangerous kind of bug: it looks like it's working.

The real, concrete consequence: **Metrics were being sent even with the switch set to off**
(traced and proven live: switched it off, tripped a real rate limit through the real app, and
watched a metrics report get sent to your real Sentry project anyway). **Logs weren't being sent
in either position of the switch** — on or off, zero log messages ever arrived.

The fix required going one level deeper than "pass a setting" for each:
- **Metrics** — instead of trusting Sentry's own off switch, this app now checks its own setting
  itself, in one small helper function, before ever calling Sentry's counting function at all.
  If the switch is off, that helper simply never makes the call — a decision made in *our* code,
  not handed off to a Sentry setting that turned out not to work.
- **Logs** — turns out there's a *different*, correct way to turn this on: instead of the simple
  top-level switch, you have to explicitly build a small configuration object
  (`LoggingIntegration(capture_sentry_logs=True)`) and hand that object to Sentry directly, only
  when the switch is on. That's the version that's actually still listened to.

Both fixes were then verified live, the honest way: real DSN, real activity through the real
app, with Sentry's own internal debug output turned on temporarily so every single "sending
this to Sentry now" moment was visible in the terminal — not just "the code didn't crash," but
"I watched the exact message get sent, and watched it correctly *not* get sent when switched
off." Confirmed working in both directions before removing the temporary debug output and
calling it done.

### 4.4 Why this matters for you

These three bugs are a good example of why "I wrote the code" and "the code works" are different
claims — and why even a first round of real testing isn't automatically the last word. The first
two were caught by running the code (the test suite, a real end-to-end trigger). The third one
needed something more: someone independently re-verifying the *specific claim* "this switch
turns things off" against real behavior, not just trusting that a setting named `enable_metrics`
does what its name says.

---

## Part 5: How to Actually Use This

### 5.1 Turning it on

1. Go to [sentry.io](https://sentry.io), sign up free.
2. Create a project, platform: **Python** (for the backend) — see the earlier walkthrough in
   this conversation for the exact screen-by-screen steps.
3. Copy the DSN it gives you.
4. Paste it into `SENTRY_DSN=` in `.env` (already done for you as part of testing this feature —
   see below).
5. Optionally set the other 4 switches (`SENTRY_ENABLE_LOGS`, etc.) to `true` / a number > 0.
6. Restart the backend server. Done — it's live.

### 5.2 Where to actually see the alerts

- **Email** — automatic, the moment a real error happens, to your Sentry account's email.
- **sentry.io dashboard** — log in any time to see the full history: Issues (errors), Logs, the
  Metrics numbers, Traces, and Profiles — all in the same project.

### 5.3 Current status of this project

As of this conversation, the real DSN you provided has already been:
- Added to `.env` (this file is never committed to GitHub — it stays private, on your machine).
- Verified end-to-end with a real triggered error, a real complaint creation, and real logging —
  confirmed working.
- All the code is sitting in Pull Request #23 on GitHub, reviewed and tested, waiting for you to
  merge it whenever you're ready.

### 5.4 If you want to turn something off again

Just set that one line back to `false` (or `0` for the sample-rate ones) in `.env` and restart
the server. Nothing needs to be uninstalled — every switch is independent and reversible.

---

## Quick Reference Table

| Setting | What it controls | Off value | On value |
|---|---|---|---|
| `SENTRY_DSN` | Everything (master switch) | *(empty)* | your DSN string |
| `SENTRY_ENABLE_LOGS` | Logs | `false` | `true` |
| `SENTRY_ENABLE_METRICS` | Metrics (the 3 counters) | `false` | `true` |
| `SENTRY_TRACES_SAMPLE_RATE` | Tracing | `0.0` | `1.0` (or anything between) |
| `SENTRY_PROFILE_SESSION_SAMPLE_RATE` | Profiling | `0.0` | `1.0` (needs Tracing on too) |

Frontend equivalents (`frontend-react/.env`, and the GitHub Actions secret for production —
see `docs/ERROR_MONITORING.md` for why the frontend one is handled differently): `VITE_SENTRY_DSN`,
`VITE_SENTRY_ENVIRONMENT`, `VITE_SENTRY_TRACES_SAMPLE_RATE`.
