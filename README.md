# JanSarthi AI

A civic complaint app where a citizen can report a problem (starting with garbage collection) by speaking in their own language. The app transcribes, translates, and routes the complaint to the right worker — who sees it in their own language — without the citizen and worker ever needing to share a common language.

**Why it matters:** In India, most civic apps assume citizen and worker share a language. This one doesn't have to.

## 📚 Full documentation

This README is a quick reference and setup guide. Everything else — the actual depth, written so it makes sense whether or not you already write code, and detailed enough to explain confidently in an interview — lives in `docs/`. **Start with `PROJECT_OVERVIEW.md`** for the big picture, then go to whichever specific doc covers what you're trying to understand:

| If you want to understand... | Read this |
|---|---|
| The big picture: what this app is, how its pieces fit together, every file's purpose, with diagrams | **[`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md)** |
| The backend: FastAPI, why it was chosen, how routes/services/models are layered, CORS | **[`docs/BACKEND.md`](docs/BACKEND.md)** |
| The frontend: React, why it was chosen, component structure, state management, routing | **[`docs/FRONTEND.md`](docs/FRONTEND.md)** |
| The database: SQLAlchemy, schema design, caching, why SQLite (and its real limits) | **[`docs/DATABASE.md`](docs/DATABASE.md)** |
| Login, passwords, JWTs, and roles — what a JWT actually is and why this project hand-rolled its own | **[`docs/AUTHENTICATION.md`](docs/AUTHENTICATION.md)** |
| The AI pipeline: speech-to-text, translation, summarization, and the real limits measured against the live Sarvam API | **[`docs/AI_AGENT.md`](docs/AI_AGENT.md)** |
| Testing strategy: pytest, mocking, property-based tests, Playwright end-to-end tests | **[`docs/TESTING.md`](docs/TESTING.md)** |
| Tracing (LangSmith), Phoenix, real ₹ cost tracking, and the Admin AI Monitoring alerts | **[`docs/ask_janmitra_langsmith_observability.md`](docs/ask_janmitra_langsmith_observability.md)** + **[`docs/OBSERVABILITY.md`](docs/OBSERVABILITY.md)** |
| What's deliberately out of scope so far | **[`future_work.md`](future_work.md)** |

Every doc in `docs/` ends with a **"Likely interview questions about this part of the project"** section — real questions with real, specific answers grounded in this actual codebase, not generic advice.

## Current status

Full citizen/worker/Super Admin roles with real JWT authentication, ward-scoped complaint assignment (with automatic reassignment on rejection), cached on-demand translation, and voice complaints that aren't capped at Sarvam's 30-second-per-request speech-to-text limit (recordings are chunked client-side and stitched back together). The UI supports 6 languages: English, Hindi, Marathi, Odia, Gujarati, Bengali.

This replaced an earlier, simpler version (hardcoded single citizen/worker, no login, Streamlit-only frontend) — those Streamlit apps (`frontend/citizen_app.py`, `frontend/worker_app.py`) still exist in this repo for reference but are superseded by the React frontend below.

## Architecture

```
   Citizen / Worker / Admin (React + TypeScript SPA)
                    │
                    │  HTTPS, JWT bearer token
                    ▼
              FastAPI Backend
                    │
      ┌─────────────┼──────────────┐
      ▼             ▼              ▼
  SQLite       Sarvam AI       uploads/
  Database     (external:      folder
  (users,       STT, translate, (complaint
  complaints,   chat completion) photos)
  translations)
```

Full diagram + explanation: [`docs/PROJECT_OVERVIEW.md §2`](docs/PROJECT_OVERVIEW.md#2-system-architecture). Complaint lifecycle diagram: [`docs/PROJECT_OVERVIEW.md §4`](docs/PROJECT_OVERVIEW.md#4-how-a-complaint-moves-through-the-system). AI pipeline diagram: [`docs/AI_AGENT.md §2`](docs/AI_AGENT.md#2-the-pipeline-visually).

All AI calls (speech-to-text, spelling cleanup, translation, summarization) go through the **Sarvam AI** SDK — one vendor for everything. Direct calls from FastAPI to Sarvam; no agent framework, queue, or orchestration layer. See [`docs/AI_AGENT.md`](docs/AI_AGENT.md) for exactly how, and what its real, measured limits are.

## Tech Stack

- **Frontend:** React + TypeScript (Vite), plain CSS with light/dark/system theming — `frontend-react/`
- **Backend:** FastAPI (Python)
- **Auth:** JWT (HS256), implemented against the standard library directly — no third-party JWT package
- **Database:** SQLite (via SQLAlchemy), no migration framework (see [`docs/PROJECT_OVERVIEW.md §9`](docs/PROJECT_OVERVIEW.md#9-a-known-limitation-no-database-migrations))
- **AI:** Sarvam AI — `saaras:v3` (speech-to-text), `sarvam-translate:v1` (translation), `sarvam-105b` (chat-completion for spelling cleanup + summaries)
- **Storage:** Local filesystem for photos
- **Testing:** pytest (backend, all AI calls mocked) + Playwright (end-to-end, against real running dev servers) + Hypothesis (property-based tests for auth/token logic)
- **Legacy:** `frontend/citizen_app.py` / `worker_app.py` — the original Streamlit frontend, superseded by `frontend-react/`, kept for reference

## Project Structure

```
janmitra-ai/
├── backend/
│   ├── config.py                    # All settings, loaded from .env
│   ├── main.py                      # FastAPI app entry point
│   ├── models.py                    # users, complaints, complaint_rejections, complaint_translations
│   ├── database.py                  # Engine/session setup, init_db()
│   ├── deps.py                      # JWT verification + role-checking dependencies
│   ├── routes/
│   │   ├── auth.py                  # Sign-up, login, profile
│   │   ├── admin.py                 # Super-admin: create/list workers
│   │   └── complaints.py            # Create/list/accept/reject/resolve/feedback, ward list
│   └── services/
│       ├── sarvam_client.py         # STT + translation, direct Sarvam SDK calls
│       ├── translation_service.py   # Language-code mapping + translate calls
│       ├── normalization_service.py # Spelling cleanup (best-effort, never blocks)
│       ├── summary_service.py       # Short summary via Sarvam chat completion
│       ├── complaint_agent.py       # Orchestrates the full AI pipeline + storage
│       ├── complaint_translation_cache.py  # Caches per-language translations
│       ├── assignment_service.py    # Ward-scoped worker assignment + reassignment
│       └── auth_service.py          # Password hashing + JWT issuing/verification
├── frontend-react/                  # Current frontend — see docs/PROJECT_OVERVIEW.md §7
│   ├── src/pages/                   # One file per screen
│   ├── src/components/              # Reusable UI pieces
│   ├── src/lib/                     # API client, auth, i18n, audio recording, theming
│   └── e2e/                         # Playwright end-to-end tests
├── frontend/                        # Legacy Streamlit apps (superseded, kept for reference)
├── prompts/                         # AI prompt text, never hardcoded in Python
├── scripts/                         # One-off admin/seed/migration/i18n-build scripts
├── docs/                            # Full documentation — see links at the top of this file
├── uploads/                         # Stored complaint photos
├── tests/                           # pytest (backend) — mocked AI calls
├── requirements.txt
└── .env.example
```

## Setup

**Fastest path (Windows, PowerShell):** clone the repo, then run `.\setup.ps1` from the project
root. It creates a real isolated virtual environment for the backend (`.venv/`), installs the
frontend's npm packages, and sets up Phoenix's own separate environment (`.phoenix-venv/` — kept
separate on purpose, see [`docs/OBSERVABILITY.md §2`](docs/OBSERVABILITY.md#2-two-python-environments-on-purpose--dont-merge-them)
for why merging it in would actually break things). Safe to re-run. Skip to step 3 below to
configure `.env` once it finishes.

The manual steps it automates, if you'd rather do them yourself or aren't on Windows:

1. **Clone and install backend dependencies**

   ```bash
   git clone https://github.com/sumya24/janmitra-ai
   cd janmitra-ai
   python -m venv .venv
   .venv\Scripts\activate        # Windows; on macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Install frontend dependencies**

   ```bash
   cd frontend-react
   npm install
   cd ..
   ```

3. **Configure environment variables**

   ```bash
   cp .env.example .env
   ```

   | Variable | Description |
   |---|---|
   | `SARVAM_API_KEY` | Your Sarvam AI subscription key (used for STT and translation) |
   | `SARVAM_BASE_URL` | Sarvam API base URL (defaults to `https://api.sarvam.ai`) |
   | `LLM_API_KEY` | Key used for the chat-completion calls (normalize/summarize). Leave blank to reuse `SARVAM_API_KEY`. |
   | `LLM_MODEL` | Chat model used (defaults to `sarvam-105b`) |
   | `LLM_MAX_TOKENS` | Token budget per chat-completion call — see [`docs/AI_AGENT.md §4`](docs/AI_AGENT.md#4-why-every-step-is-best-effort-not-a-bigger-token-budget) for why this needs real headroom (4096, not a smaller "should be enough" number) |
   | `UPLOAD_FOLDER` | Local folder for stored complaint photos (defaults to `uploads`) |
   | `DATABASE_URL` | SQLite connection string |
   | `BACKEND_URL` | Legacy — only used by the old Streamlit frontends |
   | `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API (defaults cover the Vite dev server) |
   | `JWT_SECRET_KEY` | Secret used to sign login tokens. **Set this explicitly for any real deployment** — if left blank, a random key is generated per process, invalidating every session on restart |
   | `JWT_EXPIRE_MINUTES` | How long a login session lasts (defaults to 1440 = 24h) |

   Get a Sarvam AI API key at [sarvam.ai](https://www.sarvam.ai/).

4. **Seed the first Super Admin account**

   ```bash
   python scripts/seed_admin.py
   ```

   This is the *only* way a Super Admin account ever gets created — there's no sign-up path for it. Safe to re-run; it checks for an existing account with the same phone number first.

5. **Run the backend**

   ```bash
   python -m uvicorn backend.main:app --reload
   ```

   API docs available at `http://localhost:8000/docs`.

6. **Run the frontend** (in a separate terminal)

   ```bash
   cd frontend-react
   npm run dev
   ```

   Open `http://localhost:5173`.

## Demo Workflow

1. Open the app, pick a UI language, and sign up as a citizen (phone + password).
2. Log in as the Super Admin you seeded, and create a worker account for a specific ward.
3. Log back in as the citizen, pick that same ward, and either record or type a complaint (e.g. "कचरा उचलला नाही" — "Garbage has not been collected").
4. Submit — the backend transcribes (if voice), cleans up spelling, translates to English, and generates a summary. The complaint is immediately assigned to the worker you created in that ward.
5. Log in as that worker, see the complaint (translated into the worker's own preferred language), and **Accept** it — this unlocks the worker's phone number for the citizen.
6. Mark it **Resolved**.
7. Log back in as the citizen — the complaint now shows **Resolved**, with a step-by-step tracker, and a 1-5★ feedback form appears.

## API Endpoints

| Method | Endpoint | Who | Description |
|---|---|---|---|
| `POST` | `/auth/signup` | Anyone | Create a citizen account and log in immediately |
| `POST` | `/auth/login` | Anyone | Log in with phone + password, any role |
| `GET` | `/auth/me` | Authenticated | Current user's profile |
| `PATCH` | `/auth/me` | Authenticated | Update your own display name / preferred language |
| `POST` | `/admin/workers` | Admin | Create a worker account for a ward |
| `GET` | `/admin/workers` | Admin | List every worker with open/resolved complaint counts |
| `GET` | `/complaints/wards` | Authenticated | List wards that currently have a worker (backs the ward picker) |
| `POST` | `/complaints` | Citizen | Create a complaint from typed text or (possibly chunked) voice, with an optional photo |
| `GET` | `/complaints?lang=hi` | Authenticated | List complaints visible to you, translated into `lang` on read (scoped by role — see [`docs/PROJECT_OVERVIEW.md §4`](docs/PROJECT_OVERVIEW.md#4-how-a-complaint-moves-through-the-system)) |
| `POST` | `/complaints/{id}/accept` | Worker | Accept a complaint assigned to you |
| `POST` | `/complaints/{id}/reject` | Worker | Reject it — reassigns to the next eligible worker in the ward |
| `POST` | `/complaints/{id}/resolve` | Worker | Mark an accepted complaint resolved |
| `POST` | `/complaints/{id}/feedback` | Citizen | Leave a 1-5★ rating (+ optional comment) on your own resolved complaint |

## Testing

```bash
# Backend — mocks all external AI calls, no API keys needed
pytest tests/ -v

# End-to-end — needs both dev servers actually running first (see Setup above)
cd frontend-react
npx playwright test
```

## Known limitations

- **No database migrations** — adding a column to an existing table needs a manual one-off script. See [`docs/PROJECT_OVERVIEW.md §9`](docs/PROJECT_OVERVIEW.md#9-a-known-limitation-no-database-migrations).
- **AI steps have real, measured limits** — a 30-second-per-request cap on voice input (worked around via chunking) and unpredictable reliability on longer text inputs. Full detail, with real numbers: [`docs/AI_AGENT.md`](docs/AI_AGENT.md).
- **`LLM_TIMEOUT_SECONDS` isn't currently wired up** — it's documented in `.env.example` but nothing in the code reads it yet; the Sarvam SDK's default (60s) applies instead. See [`docs/AI_AGENT.md §5`](docs/AI_AGENT.md#5-real-measured-limits-not-guessed).

## Roadmap

See [`future_work.md`](future_work.md) for the full list.
