# Rate limiting

Two layers, both built on the same small hand-rolled limiter (`backend/services/rate_limiter.py`
-- stdlib-only, in-process sliding window, see its own docstring for the full reasoning):

1. **General baseline** (`backend/middleware.py`'s `GeneralRateLimitMiddleware`) -- applied
   automatically to every route except `/health`. A safety net against scripted abuse/scraping
   across the whole API.
2. **Stricter, purpose-specific limits** on the two P0 endpoint groups, layered ON TOP of the
   general one (`backend/deps.py`'s `require_login_rate_limit`/`require_ai_rate_limit`, attached
   directly to those routes) -- a request to either still also passes through the general limiter
   above; it just also has its own tighter budget.

## Protected routes

| Routes | Limit | Window | Identifier |
|---|---|---|---|
| Every route except `/health` | 120 | 60s | authenticated user id, else client IP (general baseline) |
| `POST /auth/login` | 5 | 60s | client IP |
| `POST /ask-janmitra`, `/ask-janmitra/image`, `/ask-janmitra/voice` | 10 (shared across all 3) | 60s | authenticated user id |

`/health` is the only exemption -- must stay reachable for monitoring/deploy healthchecks
regardless of load elsewhere.

## Configuration

`GENERAL_RATE_LIMIT`, `GENERAL_RATE_LIMIT_WINDOW_SECONDS`, `LOGIN_RATE_LIMIT`,
`LOGIN_RATE_LIMIT_WINDOW_SECONDS`, `AI_RATE_LIMIT`, `AI_RATE_LIMIT_WINDOW_SECONDS` (see
`.env.example`). Defaults (120/60s, 5/60s, 10/60s) are sized against this app's real measured
usage -- a location-clarification round-trip alone is 2 Ask Sarthi calls, a busy dashboard page
load is a handful of API calls -- with real headroom for a normal demo.

`GENERAL_RATE_LIMIT` was raised from its original 60 to 120 after a live-reported false trip: the
Admin dashboard's Workers/Complaints/by-ward-chart/AI-Monitoring widgets together fire roughly
5-9 requests per page load (the higher end was itself a bug, since fixed -- see
`backend/routes/admin.py`'s `ComplaintStatusCounts`, which replaced five separate per-status
requests with one), `NotificationBell` polls every 15s in the background for every logged-in
session regardless of role, and a real admin actively working the page (refreshing, switching
between sections, a second tab open) measurably approached 60/60s with nothing actually
abusive happening. 120 keeps real headroom for that genuine case across citizen, worker, and
admin alike, while a truly abusive script (200+/min) is still caught quickly.

## Identifier

- **General baseline**: authenticated user id when a valid token is present (decoded directly,
  no DB lookup, to keep the middleware cheap on every request), client IP otherwise -- e.g. for
  `/auth/signup`, which has no user yet.
- **Login** (pre-auth): client IP. Only trusts `X-Forwarded-For` when `TRUST_PROXY_HEADERS=true`
  -- off by default (local dev has no reverse proxy, so the raw TCP peer is the real client);
  `docker-compose.prod.yml` sets it true, because that deployment's backend container publishes
  no port of its own, so every request genuinely passed through Caddy first and can't spoof the
  header by reaching the backend directly.
- **Ask Sarthi** (authenticated): the real user id from the already-verified JWT, not a header.

## 429 response

Same plain `{"detail": "..."}` shape every other error in this API already uses (FastAPI's
default `HTTPException` handling -- no custom handler needed), plus a `Retry-After` header in
seconds.

## Running Playwright locally

`frontend-react/e2e/`'s specs drive one real backend from one machine, so every login they make
shares the same client IP -- a handful of spec files that each log in 2-4 times can add up faster
than a real human demo ever would within 60s. This is the limiter correctly treating "many logins
from one IP in a short window" as suspicious, which is exactly its job -- not a bug. For a full
local e2e run, start the backend with higher limits for that process only (standard test/CI
practice; production's `.env` is untouched). The specs that sign up/verify a citizen or worker
also drive `POST /auth/email/send-verification`, `POST /auth/signup/email/send-code`, and
`POST /auth/forgot-password`, which sit behind their own separate `OTP_RATE_LIMIT` (see
`.env.example`) rather than the general/login limiters above -- raise that too, or the same specs
will 429 on the email step even with everything else raised:

Bash:
```bash
GENERAL_RATE_LIMIT=1000 LOGIN_RATE_LIMIT=1000 AI_RATE_LIMIT=1000 OTP_RATE_LIMIT=1000 SIGNUP_RATE_LIMIT=1000 uvicorn backend.main:app --port 8000
```

PowerShell:
```powershell
$env:GENERAL_RATE_LIMIT=1000; $env:LOGIN_RATE_LIMIT=1000; $env:AI_RATE_LIMIT=1000
$env:OTP_RATE_LIMIT=1000; $env:SIGNUP_RATE_LIMIT=1000
uvicorn backend.main:app --port 8000
```

Raising rate limits only gets a local e2e run as far as the mailbox -- the OTP-sending routes
still make a real SMTP call, which depends on the configured provider's daily sending quota
(Gmail's free-tier limit has blocked local Playwright runs more than once, unrelated to anything
in this app). Set `EMAIL_DEV_MODE=true` in the backend's `.env` instead (see `.env.example`) to
skip the real send entirely for local/e2e use -- the OTP is still generated and cached for
`GET /auth/_dev/otp-code`, which `frontend-react/e2e/helpers.ts` already reads from, so signup/
login/forgot-password specs still run end to end without touching a real inbox or its quota. This
is the preferred approach for local e2e runs; the rate-limit env vars above are still worth raising
too, since `EMAIL_DEV_MODE` only removes the SMTP dependency, not the login/general/signup limits
other specs can also hit.

## Deployment limitation

In-process memory, not a shared/distributed store. Correct for the current deployment (a single
`uvicorn` process, no `--workers`/multi-process setup -- see `docker-compose.prod.yml`). If this
ever moves to multiple backend processes/replicas, each would count independently and the
*effective* limit would multiply by the process count -- would need a shared store (e.g. Redis) at
that point, deliberately not added now for a single-process deployment.
