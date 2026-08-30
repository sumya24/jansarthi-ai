# The Backend — FastAPI, from the Ground Up

*Written for someone who wants to actually understand this, not just skim it — including "why did you build it this way" answers you could give in an interview.*

> Part of the JanSarthi AI documentation set. See [`README.md`](../README.md) for the full index of every document.

---

## 1. What a "backend" even is

If you're newer to web development: a web app is really two programs talking to each other.

- The **frontend** runs in the user's browser. It's what you see and click — buttons, forms, text. It cannot be trusted with anything sensitive, because anyone can open their browser's developer tools and read or change whatever the frontend is doing.
- The **backend** runs on a server somewhere, out of the user's reach. It's the only place allowed to touch the real database, check passwords, decide who's allowed to do what, and call outside services (like Sarvam AI) using secret API keys. The frontend asks the backend to do things; the backend decides whether to actually do them.

This split exists because **you can never trust anything the browser sends you** — a user could, in principle, rewrite the frontend's JavaScript entirely and send whatever request they want. So every real rule (`only a citizen can create a complaint`, `only this complaint's assigned worker can resolve it`) has to be enforced on the backend, not just hidden by the frontend not showing a button.

JanSarthi AI's backend is written in **Python**, using a framework called **FastAPI**. Everything in this document lives under [`backend/`](../backend/).

---

## 2. What FastAPI is, and why it was chosen

FastAPI is a Python framework for building **APIs** — a backend that doesn't render web pages itself, just accepts requests (usually as JSON) and sends back responses (also usually JSON). The frontend is a completely separate program that calls this API.

**Why FastAPI specifically, over the alternatives:**

| Alternative | Why it wasn't chosen here |
|---|---|
| **Flask** (also Python) | Flask is intentionally minimal — you'd have to bolt on request validation, automatic docs, and async support yourself. FastAPI includes all of that out of the box. |
| **Django** (also Python) | Django is a full "batteries-included" framework built around server-rendered HTML pages and its own ORM/admin panel — a lot of that machinery is dead weight for a pure JSON API with a separate React frontend, and its ORM would compete with SQLAlchemy rather than complement it. |
| **Express.js** (Node/JavaScript) | Would mean writing the backend in JavaScript instead of Python. Python was preferred here specifically because Sarvam AI's official SDK (and most AI/ML tooling generally) is Python-first — see [`docs/AI_AGENT.md`](AI_AGENT.md). |

The two features that matter most in practice, in this codebase:

1. **Automatic request validation via type hints.** A route like `def signup(body: SignupRequest)` (see [`routes/auth.py`](../backend/routes/auth.py)) doesn't need any manual "check that the request has these fields" code — FastAPI reads the Python type hint, and if the incoming request doesn't match the shape of `SignupRequest`, it's rejected automatically before your function even runs, with a clear error explaining what was wrong.
2. **Automatic interactive API documentation.** Every route, its expected input, and its response shape is available at `http://localhost:8000/docs` with zero extra work — FastAPI generates it from the same type hints used for validation.

### Pydantic — the validation library underneath

Those `SignupRequest`, `ComplaintResponse`, etc. classes throughout the routes are **Pydantic models** — plain-looking Python classes where each field has a type (`full_name: str`, `rating: int = Field(ge=1, le=5)`). Pydantic is what actually does the validation work; FastAPI just wires it into the request/response cycle automatically. This is why, e.g., `FeedbackRequest`'s `rating: int = Field(ge=1, le=5)` in [`routes/complaints.py`](../backend/routes/complaints.py) rejects a rating of 6 or 0 without a single `if` statement written for it.

---

## 3. The layered structure, and why it's shaped this way

```
Request comes in
      │
      ▼
  routes/*.py     — "what URL, what HTTP method, what shape of input/output"
      │
      ▼
  deps.py         — "is this request even allowed to be here"
      │
      ▼
  services/*.py   — "the actual business logic and rules"
      │
      ▼
  models.py       — "what the database actually looks like"
```

This is a deliberate separation of concerns, and it's worth being able to explain *why* each layer exists rather than just that it does:

- **Routes** only handle HTTP-shaped things: parsing the request, checking basic validity (does this language code exist?), calling into a service, and shaping the response. They should never contain complex logic themselves.
- **`deps.py`** ("dependencies," in FastAPI's terminology) handles cross-cutting concerns that apply to *many* routes at once — specifically, "who is this request from, and are they allowed here." `Depends(get_current_user)` and `Depends(require_role("admin"))` are FastAPI's **dependency injection** system: instead of every route function manually checking a header and querying the database, they just declare "I need a `current_user`," and FastAPI runs `get_current_user` for them and hands the result in as a normal function argument. This is testable (you can swap in a fake dependency for tests) and impossible to forget, since it's part of the function's own signature, not a line of code that could be accidentally skipped.
- **Services** hold the actual logic — "how do you decide which worker gets a complaint," "how do you turn speech into stored text." Crucially, services don't know or care whether they were called from a real HTTP request or from a test — [`ComplaintAgent`](../backend/services/complaint_agent.py) is called identically from [`routes/complaints.py`](../backend/routes/complaints.py) and from [`tests/test_complaint_agent.py`](../tests/test_complaint_agent.py). This is what makes the test suite fast and not dependent on a running server.
- **Models** are the database schema — covered in full in [`docs/DATABASE.md`](DATABASE.md).

**If asked in an interview "why not just put the logic directly in the route function?"** — the honest answer: for a small route that's a fine shortcut, but the moment logic needs to be reused (e.g., seeding a worker via a script, not just via the API — see `scripts/seed_admin.py`) or tested without spinning up a whole HTTP server, having it in a separate, framework-agnostic service pays for itself immediately.

---

## 4. Walking through the route files

### `routes/auth.py` — sign-up, login, profile

The only file with any code path that can create an account, and it always creates a **citizen**. There's no `role` field anywhere on the sign-up request — that's not an accident, it's the whole point: nobody can sign up as a worker or admin, no matter what they send. See [`docs/AUTHENTICATION.md`](AUTHENTICATION.md) for how login itself works.

Worth noting: login returns the *same* error message ("Incorrect phone number or password") whether the phone number doesn't exist at all or the password is just wrong. This is deliberate — telling an attacker "that phone number isn't registered" vs "that password is wrong" leaks which phone numbers have accounts, which is exactly the kind of small detail that comes up in security-focused interview questions.

### `routes/admin.py` — Super Admin only

Every single route here is gated with `Depends(require_role("admin"))`. Two endpoints: create a worker, list all workers with their live open/resolved complaint counts (computed by querying `Complaint` filtered on `assigned_worker_id`, not by a stored counter — see [`docs/DATABASE.md`](DATABASE.md) for why a computed value beats a stored, potentially-stale one here).

### `routes/complaints.py` — the biggest file, the actual product

- `POST /complaints` — create one. Handles both typed text and voice (accepting *multiple* audio files under one field name, because a long recording is chunked client-side — see [`docs/AI_AGENT.md`](AI_AGENT.md)). Delegates all the actual AI work to `ComplaintAgent`, then immediately calls `assign_next_worker` to try to route it to a worker.
- `GET /complaints` — scoped by role: a citizen sees only their own, a worker sees only what's assigned to them, an admin sees everything. This scoping lives in the route (`if current_user.role == "citizen": query = query.filter(...)`) rather than the database itself, which is a legitimate but worth-noting design choice — the alternative would be row-level security enforced by the database, which SQLite doesn't support anyway.
- `POST /complaints/{id}/accept` / `/reject` / `/resolve` — each checks the complaint is actually assigned to *this* worker and in the right status before doing anything (`_get_owned_complaint`), so a worker can't accept a complaint that isn't theirs just by guessing an ID in the URL.
- `POST /complaints/{id}/feedback` — same ownership check, but for the citizen who filed it, and only once it's resolved.

### PDF reports — translated, and actually readable in every supported script

`GET /complaints/{id}/report/view` / `.../report/download` (both accept an optional `?lang=` query
param) generate a resolved complaint's report as a real PDF via **ReportLab**. Two non-obvious
things worth knowing if you ever touch this code:

- **Font registration, not just a language string.** ReportLab's default font (Helvetica) has no
  Devanagari/Bengali/Gujarati/Oriya glyphs — text in those scripts rendered as literally nothing,
  not even placeholder boxes, until Google's **Noto Sans** fonts were registered per script
  (`backend/assets/fonts/`, OFL-licensed). One subtlety: a script-specific Noto font only covers
  its own script plus Latin, not general symbols — the ✓ and → glyphs used in the status timeline
  went missing until those two characters specifically were pinned to Helvetica instead.
- **Auto-detecting translation, not an assumed source language.** Worker notes are translated via
  `translate_auto_detecting_source()` (`backend/services/translation_service.py`), which uses
  Sarvam's `mayura:v1` model with `source_language_code="auto"` — genuinely detecting the language
  a note was written in, rather than assuming it matches the worker's `preferred_language` (a real
  bug found in practice: a worker's profile language was Marathi, but a specific note they'd typed
  was actually in English — the naive assumption mistranslated it). Sarvam's other translation
  model, `sarvam-translate:v1`, doesn't support auto-detection. `ComplaintUpdateTranslation`
  caching checks the database for an existing translation before ever calling the API, same
  "compute once, cache, serve from cache" pattern as [`docs/DATABASE.md §4`](DATABASE.md#4-the-translation-cache--a-real-caching-pattern).

---

## 5. CORS — why the backend needs to explicitly allow the frontend

Browsers enforce a rule called the **same-origin policy**: by default, JavaScript running on `http://localhost:5173` (the React dev server) is *not allowed* to make requests to `http://localhost:8000` (the API) — different port counts as a different "origin," and the browser blocks it for security, even though both are running on your own machine.

**CORS** (Cross-Origin Resource Sharing) is the mechanism that relaxes this, on purpose, for specific trusted origins. `main.py` adds `CORSMiddleware` configured with `settings.CORS_ORIGINS` — an explicit allowlist of which origins are allowed to call this API from a browser. Without this, the React app simply couldn't talk to the backend at all; the browser would block every request before it even left the page. This is a very common thing to be asked to explain in a frontend/backend interview, precisely because it trips people up the first time they build a separate frontend and backend.

---

## 6. Configuration — `config.py`

One rule this codebase holds to strictly: **nothing else in the codebase reads `os.environ` directly.** Every setting — API keys, model names, the JWT secret, file size limits — is read once, in `config.py`, into a single `settings` object that everything else imports. This has a concrete benefit: if a secret is missing or malformed, you find out from one place, and you can see every configurable value in the app by reading one file, instead of grepping the whole codebase for `os.getenv`.

---

## Likely interview questions about this part of the project

**"Walk me through what happens when a citizen submits a complaint."**
`POST /complaints` in `routes/complaints.py` receives the request → `require_role("citizen")` (a dependency) confirms the caller is logged in as a citizen → the route reads any uploaded audio chunks and photo → calls `ComplaintAgent.create_complaint()`, which runs the AI pipeline and saves the row → the route then calls `assign_next_worker()` to try to route it to an eligible worker in that ward → returns the created complaint as JSON.

**"How do you handle authorization — making sure someone can only see/do what they're allowed to?"**
Two layers: `deps.py`'s `require_role(...)` gates entire routes by account type (citizen/worker/admin), and inside routes that need finer-grained checks (like "is this complaint actually yours"), an explicit ownership check like `_get_owned_complaint` runs before anything happens.

**"Why is validation automatic here instead of you writing `if` checks?"**
FastAPI + Pydantic derive validation from Python type hints on request models — a field typed `int = Field(ge=1, le=5)` is rejected outside that range with no manual code, and a request missing a required field never reaches the route function at all.

**"What would you change if this needed to scale to a real city's worth of traffic?"**
The most honest answer, and a good one to give: swap SQLite for PostgreSQL (see [`docs/DATABASE.md`](DATABASE.md)), and reconsider role-scoping being done in Python instead of the database if query volume grew large. The layered structure (routes/services/models) wouldn't need to change — that's exactly the point of keeping business logic out of the route functions.

---

*Related reading: [`docs/DATABASE.md`](DATABASE.md), [`docs/AUTHENTICATION.md`](AUTHENTICATION.md), [`docs/AI_AGENT.md`](AI_AGENT.md), [`docs/PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md).*
