import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

/**
 * E2E coverage for the combined voice+image UI wiring inside VoiceAssistantOverlay (phase 7 of
 * the multimodal upgrade) -- proves a real photo can be attached inside the voice overlay itself
 * (select/preview, reusing the exact same MultiPhotoUpload control the text/image flow uses) and
 * is cleared appropriately.
 *
 * The full real-model round trip for a combined voice+image turn (real STT + real vision
 * captioning + real graph + real TTS) is proven at the backend level instead
 * (tests/test_ask_sarthi_voice.py's phase-7 tests, 12 passing, covering caption folding,
 * image-with-no-speech clarification with real TTS, caption-failure resilience, and
 * complaint+evidence creation) -- the real vision model's multi-minute cold-load cost was already
 * paid and independently verified through the browser for image-only turns
 * (ask-sarthi-image.spec.ts) and for voice-only turns (ask-sarthi-voice.spec.ts); this test
 * only needs to prove the two real, separately-verified paths are wired together correctly in
 * the UI, not re-prove either one's own AI call is real.
 */
const JPEG_1PX = Buffer.from(
  "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k=",
  "base64"
);

async function signUpAndReachCitizenHome(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "English" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
  await page.getByRole("link", { name: "Sign up" }).click();
  await expect(page).toHaveURL(/\/signup$/);

  const phone = uniquePhone();
  await page.getByLabel("Full name").fill("Ask Sarthi Voice+Image Tester");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("secret123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("secret123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
}

test("Ask Sarthi voice overlay: a photo can be attached before speaking, previewed, and removed", async ({ page }) => {
  await signUpAndReachCitizenHome(page);
  await page.getByRole("button", { name: "Ask Sarthi" }).click();
  await page.getByRole("button", { name: "Voice Assistant" }).click();

  const overlay = page.locator(".voice-overlay-panel");
  await expect(overlay).toBeVisible();

  // The same real file-select control the text/image flow uses, reused inside the voice
  // overlay -- available while idle (before the citizen starts speaking).
  await overlay.locator('input[type="file"]').setInputFiles({ name: "streetlight.jpg", mimeType: "image/jpeg", buffer: JPEG_1PX });
  await expect(overlay.locator(".multi-photo-thumb")).toHaveCount(1);

  await overlay.locator(".multi-photo-remove").click();
  await expect(overlay.locator(".multi-photo-thumb")).toHaveCount(0);

  // Re-attach, then close the overlay entirely -- must not throw/hang with an image staged but
  // no turn ever submitted.
  await overlay.locator('input[type="file"]').setInputFiles({ name: "streetlight.jpg", mimeType: "image/jpeg", buffer: JPEG_1PX });
  await expect(overlay.locator(".multi-photo-thumb")).toHaveCount(1);
  await overlay.getByRole("button", { name: "End conversation" }).click();
  await expect(overlay).not.toBeVisible();
});
