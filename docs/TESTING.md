# Testing — Strategy, Tools, and Why Each One Was Chosen

*Written for someone who wants to actually understand this, not just skim it — including "why did you build it this way" answers you could give in an interview.*

> Part of the JanSarthi AI documentation set. See [`README.md`](../README.md) for the full index of every document.

---

## 1. The testing pyramid, and where this project's tests sit

A common way to think about a test suite is as a pyramid: lots of small, fast, cheap tests at the bottom (**unit tests**), fewer, slower, more realistic tests near the top (**end-to-end tests**), because end-to-end tests are valuable but expensive to run and maintain.

This project has, deliberately, both ends of that pyramid, using different tools for different jobs:

| Layer | Tool | What it checks | Speed |
|---|---|---|---|
| Unit / integration (backend) | **pytest** | Individual functions and API routes, in isolation, with all external AI calls **mocked** | Fast (the whole suite runs in ~40s) |
| Property-based (backend) | **Hypothesis** | Invariants that should hold for *any* input, not just hand-picked examples | Fast |
| End-to-end | **Playwright** | The real app — real browser, real backend, real database, a couple of tests hitting the real Sarvam API | Slow (tens of seconds per test) |

**Why not just end-to-end tests for everything?** A fair question, and a good one to have a real answer for: e2e tests are the most realistic, but also the slowest, the most likely to be flaky (network timing, animation timing), and the hardest to pin down exactly *why* something failed when they do fail (a failure could be anywhere in the whole stack). Fast, isolated unit tests catch the vast majority of logic bugs immediately, with a precise failure location; e2e tests exist to catch the things unit tests structurally can't — real integration between frontend and backend, real browser behavior, real auth flows end to end.

---

## 2. Backend testing: pytest, and why mocking is the default

```bash
pytest tests/ -v
```

Every backend test lives in [`tests/`](../tests/), run against an **in-memory SQLite database** created fresh for each test (`conftest.py`'s `db_session` fixture) — so tests never touch the real `jansarthi.db` file, never leave stray data behind, and never interfere with each other.

**Every external AI call (Sarvam speech-to-text, translation, chat completion) is mocked** — replaced with a fake object that returns a predetermined result instead of making a real network call. See, for example, [`tests/test_summary_service.py`](../tests/test_summary_service.py):

```python
fake_client = Mock()
fake_client.chat.completions.return_value = _fake_chat_response("Garbage not collected near the house.")
monkeypatch.setattr("backend.services.summary_service.SarvamAI", lambda api_subscription_key: fake_client)
```

**Why mock instead of hitting the real API in every test run?** Several concrete reasons, all worth being able to state:
1. **Speed** — a real API call takes seconds; a mock returns instantly. Multiply by dozens of tests and the difference is the whole suite running in 40 seconds vs. potentially many minutes.
2. **Cost** — every real call to Sarvam costs money. A test suite that runs on every commit shouldn't have a real dollar cost per run.
3. **Reliability** — a test suite that depends on a real third-party service being up, fast, and configured with a valid API key is a test suite that fails for reasons that have nothing to do with your code being wrong. Tests should fail because of *your* bugs, not because an external vendor had a slow moment.
4. **Determinism** — a mocked response is exactly the same every time, which is what lets you assert on it precisely. A real AI model's response can vary between calls even for the same input (see [`docs/AI_AGENT.md`](AI_AGENT.md) for a whole investigation into exactly how much).

**The one deliberate exception:** `frontend-react/e2e/complaint-tracking.spec.ts` (see [§4](#4-end-to-end-testing-playwright)) does make one real AI call, because testing the *actual* full lifecycle — a real complaint really getting transcribed/translated/summarized and showing up correctly for a worker — is exactly the kind of thing a unit test's mock can't verify. This is the pyramid in action: mostly fast and mocked, with a small number of slow, real, end-to-end checks where realism specifically matters.

### `conftest.py` — shared setup, not repeated in every test

Fixtures like `db_session`, `client`, `make_citizen`, `make_admin`, `make_worker` live in one shared file so every test file doesn't reinvent "how do I get a logged-in citizen to test with." `make_worker`, for instance, actually goes through the real API (seeding a bootstrap admin, then calling `POST /admin/workers` as that admin) rather than inserting a `User` row directly — so tests that use it are implicitly also exercising that whole creation flow correctly, "for free."

---

## 3. Property-based testing: Hypothesis

Most tests in this project are **example-based** — you pick a specific input, assert a specific output (`assert to_bcp47("mr") == "mr-IN"`). [`tests/test_property_based.py`](../tests/test_property_based.py) takes a different approach for a handful of specifically security/correctness-critical functions: instead of picking examples, you state a **property** that should hold for *any* valid input, and the Hypothesis library generates hundreds of varied inputs (including deliberately weird edge cases you probably wouldn't think to write by hand) to try to break it.

```python
@given(password=password_strategy)
def test_hash_password_round_trips_for_any_password(password: str) -> None:
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True
```

This doesn't test "does this one password work" — it tests "does *every* password, including ones with unusual characters, extreme lengths, or unlucky byte patterns, correctly round-trip through hashing." That's a meaningfully stronger guarantee than a handful of hand-picked examples, for exactly the kind of function (password hashing, JWT encode/decode) where a single unhandled edge case is a real security bug, not just an inconvenience.

**Why not use this for everything?** Property-based tests are genuinely harder to write well — you need to find a *true* property, not just re-implement the function's own logic as the "expected" answer. It's specifically valuable for pure, deterministic functions with a clear invariant (hashing, encoding, validation) — it's a poor fit for, say, "does this API endpoint return the right HTTP status," where example-based tests are simpler and just as effective.

---

## 4. End-to-end testing: Playwright

```bash
cd frontend-react
npx playwright test
```

Playwright drives a **real Chromium browser**, clicking real buttons and filling real forms, against the **actual running dev servers** (both backend and frontend need to already be started — unlike the backend's self-contained pytest suite, e2e tests are not self-contained; see [`README.md`](../README.md) Setup). This is deliberately the most realistic layer of testing: it's the closest thing to "does this actually work for a real user," because it *is* a real user's interactions, just automated.

A few specific things worth understanding about how this suite is built:

- **`e2e/helpers.ts`'s `uniquePhone()`** — every test needs a phone number that doesn't already exist in the (real, persistent) database this suite runs against. Rather than a fixed test phone number (which would collide the second time the suite runs), a time-based counter generates a fresh one per call.
- **Microphone testing without a real microphone** — `playwright.config.ts` launches Chromium with `--use-fake-device-for-media-stream --use-fake-ui-for-media-stream` and grants the `microphone` permission automatically, so `e2e/theme-and-voice.spec.ts`'s voice-recording test can exercise the real `MediaRecorder` code path without needing an actual physical microphone or a human clicking "Allow" on a permission prompt.
- **The full-lifecycle test is the slowest, on purpose** — `complaint-tracking.spec.ts` runs a genuinely real AI complaint submission (`test.setTimeout(90000)` — it explicitly expects to take a while) as part of testing reject → reassign → accept → resolve → feedback, end to end, for real. This is the one place in the whole test suite that intentionally trades speed for maximum realism.

**A real lesson worth being able to tell as a story:** during development, this suite briefly showed **7 failing tests** that looked like a serious regression. The actual cause: the backend dev server simply wasn't running at the time — every test that needed a real API call failed the same way. Restarting both dev servers fixed all 7 immediately. The lesson, and a genuinely good thing to say in an interview: **before assuming a bunch of failures means a real bug, check whether your test environment itself is actually in the state your tests assume it's in** — a wide, uniform failure pattern across unrelated tests is a strong signal to check infrastructure first, not to start debugging application code.

---

## Likely interview questions about this part of the project

**"What's your testing strategy?"** — A pyramid: fast, mocked pytest unit/integration tests for the vast majority of coverage, a handful of property-based tests for security-critical pure functions, and a smaller set of slow, realistic Playwright end-to-end tests (including one that hits the real AI API) for the things only a real browser+backend+AI integration can actually verify. See [§1](#1-the-testing-pyramid-and-where-this-projects-tests-sit).

**"Why do you mock external API calls in tests?"** — speed, cost, reliability (tests should fail because of your bugs, not a vendor's downtime), and determinism. See [§2](#2-backend-testing-pytest-and-why-mocking-is-the-default).

**"What's property-based testing, and when would you use it?"** — testing that an invariant holds for a wide range of generated inputs instead of a handful of hand-picked examples; best suited to pure, deterministic functions with a clear correctness property (hashing, encode/decode), not general application logic. See [§3](#3-property-based-testing-hypothesis).

**"Tell me about debugging a confusing test failure."** — the "7 tests suddenly failing" story: the real cause was the backend dev server not running, not a code regression; a uniform failure pattern across unrelated tests pointed at infrastructure, not application code. See [§4](#4-end-to-end-testing-playwright).

---

*Related reading: [`docs/BACKEND.md`](BACKEND.md), [`docs/AUTHENTICATION.md`](AUTHENTICATION.md), [`docs/AI_AGENT.md`](AI_AGENT.md), [`docs/PROJECT_OVERVIEW.md`](PROJECT_OVERVIEW.md).*
