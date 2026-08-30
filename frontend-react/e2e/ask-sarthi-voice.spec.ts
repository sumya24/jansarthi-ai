import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

/**
 * E2E coverage for Ask Sarthi's voice-to-voice assistant ("Mic 2", phase 6 of the multimodal
 * upgrade) against the REAL backend (POST /ask-sarthi/voice) -- not a mock. Uses the fake mic
 * device configured in playwright.config.ts, same as theme-and-voice.spec.ts's existing real
 * voice-complaint test -- and, like that test, tolerates either a real successful response or a
 * graceful error for the actual STT/answer content (a fake device streams synthetic silence, so
 * Sarvam's real transcript for it is not something this test can predict) while asserting the
 * one thing that must always be true: no crash, no indefinite hang, and the UI always returns to
 * a valid state.
 */
async function signUpAndReachCitizenHome(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page).toHaveURL(/\/signup$/);

  const phone = uniquePhone();
  await page.getByLabel("Full name").fill("Ask Sarthi Voice Tester");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("secret123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("secret123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
}

test("Ask Sarthi: Mic 2 opens a distinct voice overlay from Mic 1, and a full turn never crashes or hangs", async ({ page }) => {
  test.setTimeout(90000);

  await signUpAndReachCitizenHome(page);
  await page.getByRole("button", { name: "Ask Sarthi" }).click();

  // Mic 1 (voice-to-text) and Mic 2 (voice assistant) are two distinct, separately labeled
  // buttons -- not the same control wearing two hats.
  await expect(page.getByRole("button", { name: "Speak your question" })).toBeVisible();
  const mic2 = page.getByRole("button", { name: "Voice Assistant" });
  await expect(mic2).toBeVisible();
  await mic2.click();

  // A dedicated modal opens -- not the same panel Mic 1 fills.
  const overlay = page.locator(".voice-overlay-panel");
  await expect(overlay).toBeVisible();
  await expect(overlay.getByText("Tap the mic to speak")).toBeVisible();

  // Record a short turn via the fake mic device, then stop.
  const micButton = overlay.locator(".voice-overlay-mic");
  await micButton.click();
  await expect(overlay.getByText("Listening...")).toBeVisible();
  await page.waitForTimeout(1200);
  await micButton.click();
  await expect(overlay.getByText("Thinking...")).toBeVisible();

  // Real backend round-trip (STT -> graph -> TTS). Whatever Sarvam's real STT makes of the fake
  // device's synthetic audio determines success (a real spoken/text response) or a graceful
  // error banner -- both acceptable, matching theme-and-voice.spec.ts's own established
  // tolerance for this exact ambiguity. What must never happen is staying stuck on "Thinking...".
  await expect(
    overlay.locator(".voice-overlay-response").or(overlay.locator(".banner-error"))
  ).toBeVisible({ timeout: 60000 });

  // End conversation closes the overlay cleanly.
  await overlay.getByRole("button", { name: "End conversation" }).click();
  await expect(overlay).not.toBeVisible();

  // The rest of the page (text chat, Mic 1) is completely unaffected.
  await expect(page.getByPlaceholder(/Ask about a civic service/i)).toBeVisible();
});
