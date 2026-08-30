import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

/**
 * E2E coverage for Ask Sarthi against the REAL backend (POST /ask-sarthi) — not a mock.
 * This is the RAG retrieval + location-aware AI foundation phase's Playwright requirement:
 * "At minimum verify: ... Ask Sarthi ... location ... source links".
 *
 * Uses a real Sarvam AI call end-to-end (no SARVAM_API_KEY mocking at this layer, matching how
 * theme-and-voice.spec.ts's real voice-complaint test already works) — timeouts sized generously
 * for that, same reasoning already documented in this project for the other AI-backed E2E test.
 */

async function signUpAndReachCitizenHome(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page).toHaveURL(/\/signup$/);

  const phone = uniquePhone();
  await page.getByLabel("Full name").fill("Ask Sarthi Tester");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill("secret123!");
  await page.locator("#signup-confirm-password").fill("secret123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
}

test("Ask Sarthi: a located, in-scope information question returns a grounded answer with a verified source", async ({ page }) => {
  test.setTimeout(60000); // real Sarvam LLM call, same generous budget as the voice-complaint test

  await signUpAndReachCitizenHome(page);
  // Ask Sarthi is a floating widget (opens a slide-out panel), not a nav tab -- see
  // CitizenNav.tsx/AskSarthiWidget.tsx. No route change on open, so no toHaveURL assertion
  // here the way there used to be.
  await page.getByRole("button", { name: "Ask Sarthi" }).click();
  // Deliberately a TYPE_B (service-information) phrasing, not a "...is not working" complaint-
  // shaped one -- since the LangGraph orchestration phase, a complaint-shaped question routes to
  // complaint_flow (files a real complaint, see the test below) instead of answering via RAG, so
  // this test exercises the RAG+citation path specifically with a query that stays TYPE_B.
  await page.getByPlaceholder(/Ask about a civic service/i).fill("Who do I contact about street lights in Mohali?");
  // exact: true -- Playwright's accessible-name matching is substring by default, and "Ask"
  // would otherwise also match the floating widget's own "Ask Sarthi" button, which stays
  // rendered (not hidden) while its panel is open.
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  // Real backend round-trip (RAG retrieval + LLM answer generation) — give it real time.
  await expect(page.locator(".ask-chat-row-assistant .ask-chat-text").last()).toBeVisible({ timeout: 30000 });

  // A source card should render for this VERIFIED, city-resolved question, with a real
  // clickable official-source link (not a fabricated one, not absent).
  await expect(page.getByText("Official source", { exact: true })).toBeVisible();
  const sourceLink = page.getByRole("link", { name: /View official source/i });
  await expect(sourceLink).toBeVisible();
  const href = await sourceLink.getAttribute("href");
  expect(href).toMatch(/^https:\/\//);
});

test("Ask Sarthi: a complaint-shaped question with location files a real complaint", async ({ page }) => {
  test.setTimeout(60000);

  await signUpAndReachCitizenHome(page);
  // Ask Sarthi is a floating widget (opens a slide-out panel), not a nav tab -- see
  // CitizenNav.tsx/AskSarthiWidget.tsx. No route change on open, so no toHaveURL assertion
  // here the way there used to be.
  await page.getByRole("button", { name: "Ask Sarthi" }).click();
  // A genuinely complaint-shaped ("...is not working") question with a resolvable location now
  // files a real complaint via the LangGraph orchestrator's complaint_flow (see
  // docs/ask_sarthi_orchestration.md) instead of answering from RAG -- confirmed deliberate
  // behavior change, see backend/services/orchestration/nodes.py's module docstring.
  await page.getByPlaceholder(/Ask about a civic service/i).fill("Street light not working in Mohali.");
  // exact: true -- Playwright's accessible-name matching is substring by default, and "Ask"
  // would otherwise also match the floating widget's own "Ask Sarthi" button, which stays
  // rendered (not hidden) while its panel is open.
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  await expect(page.locator(".ask-chat-row-assistant .ask-chat-text").last()).toBeVisible({ timeout: 30000 });
  await expect(page.locator(".ask-chat-row-assistant .ask-chat-text").last()).toContainText(/complaint/i);
  // No RAG source card for a filed complaint -- this response didn't come from retrieval.
  await expect(page.getByText("Official source", { exact: true })).toHaveCount(0);
});

test("Ask Sarthi: a question with no location asks for clarification instead of guessing", async ({ page }) => {
  // Deliberately NOT fillHomeLocationPicker() here: that helper just picks whichever state
  // happens to be first alphabetically, and since the location-resolution fallback chain
  // includes the citizen's own registered ward as a last resort (see nodes.py's
  // _resolve_location), landing on a state/city seeded for one of the RAG knowledge base's 30
  // covered cities (e.g. Ahmedabad/Kolkata/Bengaluru) would resolve a real location and this
  // test would no longer be testing what its name says -- caught live when this test started
  // failing after that fallback shipped (the app correctly stopped asking, because it correctly
  // now knows where the citizen lives). This test's actual job is verifying the "nothing
  // resolves anywhere, including the account" case, so it needs a ward that's real (seeded,
  // satisfies the mandatory signup field) but genuinely NOT one of the RAG gazetteer's 30 cities
  // -- Pune is seeded in this project's own multi-ward test data (real State->City->Ward
  // hierarchy, see backend/routes/locations.py) but is not in that 30-city list (see
  // location_extractor.py's module docstring on the two separate, differently-sized location
  // datasets), so explicitly picking it here (rather than an arbitrary index) keeps this test's
  // premise deterministic.
  await page.goto("/");
  await page.getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page).toHaveURL(/\/signup$/);
  const phone = uniquePhone();
  await page.getByLabel("Full name").fill("Ask Sarthi Tester");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill("secret123!");
  await page.locator("#signup-confirm-password").fill("secret123!");
  const stateField = page.locator("#signup-home-state");
  await expect.poll(() => stateField.locator("option").count()).toBeGreaterThan(1);
  await stateField.selectOption({ label: "Maharashtra" });
  const cityField = page.locator("#signup-home-city");
  await expect.poll(() => cityField.isEnabled()).toBe(true);
  await cityField.selectOption({ label: "Pune" });
  const wardField = page.locator("#signup-home-ward");
  await expect.poll(() => wardField.isEnabled()).toBe(true);
  await wardField.selectOption({ index: 1 });
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // Ask Sarthi is a floating widget (opens a slide-out panel), not a nav tab -- see
  // CitizenNav.tsx/AskSarthiWidget.tsx. No route change on open, so no toHaveURL assertion
  // here the way there used to be.
  await page.getByRole("button", { name: "Ask Sarthi" }).click();

  // TYPE_B (service-information) phrasing, not the "...is not working" complaint-shaped form
  // used by the test above -- a complaint-shaped question here would route to complaint_flow,
  // which resolves a real complaint location straight from the citizen's own registered ward
  // text (see nodes.py's _resolve_location) independently of the RAG gazetteer this test is
  // actually probing, and would file a real complaint (confirmation prompt) instead of ever
  // reaching the generic location-clarification path this test asserts on. Verified live against
  // the real backend before this change: this exact phrasing, for this exact Pune-registered
  // account, reliably returns routed_to="NONE_CLARIFICATION_NEEDED" with the three follow-up
  // options asserted below -- confirming the citizen's own ward genuinely does not resolve via
  // the RAG gazetteer (Pune is not one of its 30 covered cities), so this test's "nothing
  // resolves, including the account" premise still holds with this phrasing.
  await page.getByPlaceholder(/Ask about a civic service/i).fill("Who do I contact about street lights?");
  // exact: true -- Playwright's accessible-name matching is substring by default, and "Ask"
  // would otherwise also match the floating widget's own "Ask Sarthi" button, which stays
  // rendered (not hidden) while its panel is open.
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  // No location given anywhere -> the app must ask, not silently pick a city.
  await expect(page.getByText("Please clarify:")).toBeVisible({ timeout: 15000 });
  await expect(page.getByRole("button", { name: "Use current location" })).toBeVisible();
});

test("Ask Sarthi: the floating widget shows the mascot, and voice input toggles a real listening state when the browser supports it", async ({ page, context }) => {
  await signUpAndReachCitizenHome(page);

  // The FAB's icon is the mascot (Mascot.tsx) now, not the old chat-bubble-with-dots icon --
  // see AskSarthiWidget.tsx.
  await expect(page.locator(".ask-widget-fab img.mascot")).toBeVisible();

  await page.getByRole("button", { name: "Ask Sarthi" }).click();

  // Mic button is only rendered when the browser actually exposes SpeechRecognition -- true
  // graceful absence, not a disabled ghost control, so assert whichever branch is real for this
  // browser instead of assuming support (see useSpeechToText.ts).
  const supportsSpeechRecognition = await page.evaluate(() => {
    const w = window as unknown as { SpeechRecognition?: unknown; webkitSpeechRecognition?: unknown };
    return Boolean(w.SpeechRecognition || w.webkitSpeechRecognition);
  });

  // Selected by a dedicated class (ask-chat-mic1-btn) rather than by accessible name (which
  // changes between "Speak your question"/"Stop recording") or by [aria-pressed] alone (the
  // composer's attach button also has aria-pressed now, for its own open/closed state).
  const micButton = page.locator("form.ask-chat-composer button.ask-chat-mic1-btn");

  if (!supportsSpeechRecognition) {
    await expect(micButton).toHaveCount(0);
    return;
  }

  await context.grantPermissions(["microphone"]);
  await expect(micButton).toBeVisible();
  await expect(micButton).toHaveAttribute("aria-pressed", "false");

  await micButton.click();
  // Real recording state, not a fake indicator -- the button itself flips to "pressed", driven
  // by useSpeechToText's actual status (see AskSarthi.tsx's mascotState derivation). The
  // composer's own persistent mascot indicator this used to also assert on (ask-chat-composer-
  // mascot) was intentionally removed from the composer row per explicit product direction --
  // only the welcome-screen mascot and per-message avatars remain, neither of which reflects a
  // live per-keystroke recording state -- so the mic button's own aria-pressed toggle is now the
  // sole, still-real signal this test verifies.
  await expect(micButton).toHaveAttribute("aria-pressed", "true");

  await micButton.click();
  await expect(micButton).toHaveAttribute("aria-pressed", "false");
});

test("Ask Sarthi: an out-of-scope service question says so honestly, with no fabricated source", async ({ page }) => {
  await signUpAndReachCitizenHome(page);
  // Ask Sarthi is a floating widget (opens a slide-out panel), not a nav tab -- see
  // CitizenNav.tsx/AskSarthiWidget.tsx. No route change on open, so no toHaveURL assertion
  // here the way there used to be.
  await page.getByRole("button", { name: "Ask Sarthi" }).click();

  await page.getByPlaceholder(/Ask about a civic service/i).fill("I want a new electricity connection.");
  // exact: true -- Playwright's accessible-name matching is substring by default, and "Ask"
  // would otherwise also match the floating widget's own "Ask Sarthi" button, which stays
  // rendered (not hidden) while its panel is open.
  await page.getByRole("button", { name: "Ask", exact: true }).click();

  await expect(page.locator(".ask-chat-row-assistant .ask-chat-text").last()).toBeVisible({ timeout: 15000 });
  await expect(page.locator(".ask-chat-row-assistant .ask-chat-text").last()).toContainText(/don't currently have/i);
  // No source cards should render for an out-of-scope, unanswered question.
  await expect(page.getByText("Official source", { exact: true })).not.toBeVisible();
  await expect(page.getByText("Synthetic / prototype data", { exact: true })).not.toBeVisible();
});
