import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

test("theme toggle cycles system -> light -> dark -> system and persists across reload", async ({ page }) => {
  await page.goto("/welcome");

  const html = page.locator("html");
  const toggle = page.locator(".theme-toggle");

  // Starts in "system" (no data-theme attribute).
  await expect(html).not.toHaveAttribute("data-theme", /.+/);

  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "light");

  await toggle.click();
  await expect(html).toHaveAttribute("data-theme", "dark");

  // Persists across a reload.
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");

  await toggle.click();
  await expect(html).not.toHaveAttribute("data-theme", /.+/);
});

test("citizen can switch to voice input, record a complaint, and submit it", async ({ page }) => {
  // Playwright's default 30s per-test timeout is shorter than the up-to-30s the final
  // assertion below alone is allowed to wait -- the test would get killed before that
  // assertion's own timeout could ever be honored. Needs real headroom above it, plus the
  // signup/recording steps before it.
  test.setTimeout(90000);
  const phone = uniquePhone();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Voice User");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("voice-pass1");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("voice-pass1");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // The complaint form now lives in the Report an Issue wizard (Phase 1), not directly on the
  // citizen Home screen — get there first.
  //
  // LIVE-REPORTED: the hero "a.btn-primary" button no longer exists on CitizenHome.tsx -- the
  // nav-drawer link is the only way there now, and it's a real slide-out drawer (needs opening
  // first). See citizen-signup.spec.ts's own identical fix.
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);

  // Location step: pick a ward if the list is non-empty (other specs sharing this dev db may
  // have already seeded one, which the step then requires); otherwise it's optional free text
  // and can be left blank either way.
  await page.getByRole("button", { name: "Select location" }).click();
  const wardField = page.locator("#wizard-ward");
  if ((await wardField.evaluate((el) => el.tagName)) === "SELECT") {
    await wardField.selectOption({ index: 1 });
  }
  await page.getByRole("button", { name: "Next" }).click();

  // Description step: a single always-editable textarea -- voice and typing both write into the
  // same field (see ReportIssue.tsx's own comment on this), no separate text/voice "mode" to
  // switch between any more.
  await expect(page.locator("#complaint-text")).toBeVisible();

  // Trying to move on with neither typed text nor a recording shows an inline error, not a crash.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.locator(".banner-error")).toContainText("Please describe the problem before submitting.");

  // Record a short voice note using the fake mic device configured in playwright.config.ts.
  await page.getByRole("button", { name: "🎙️ Start recording" }).click();
  await expect(page.getByRole("button", { name: "⏹ Stop" })).toBeVisible();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "⏹ Stop" }).click();

  // Back to idle -- no separate "playable recording" UI in the current unified composer (see
  // ReportIssue.tsx: the mic button itself just reverts once a segment is captured).
  await expect(page.getByRole("button", { name: "🎙️ Start recording" })).toBeVisible();
  await expect(page.getByRole("button", { name: "🔁 Record again" })).toBeVisible();

  // Advancing past this step at all (rather than hitting the "please describe" error again)
  // proves a real audio segment was captured, even if the fake/silent mic device produced no
  // live speech-to-text transcript text.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.locator(".banner-error")).not.toBeVisible();

  // Photo step (optional, skip) -> AI Understanding (brief client-side mock, wait for it to
  // finish) -> Preview -> submit.
  await page.getByRole("button", { name: "Next" }).click();
  // No .dev-badge assertion here: with a silent fake mic device, the live transcript is likely
  // empty, so (same reasoning as evidence-upload.spec.ts's invalid-image test) the keyword
  // fallback may legitimately find no category to badge. A category isn't required to proceed.
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();

  // Submitting now reaches the backend's real AI pipeline. Whether this environment's
  // SARVAM_API_KEY is currently working determines success (JM-##### success screen) or a
  // graceful failure (banner-error) — both are acceptable here; what must never happen is a
  // crash or an indefinite hang. See MEMORY.md: this ambiguity predates this test's wizard
  // navigation and isn't something a UI change can resolve on its own.
  //
  // Widened from 45000 after measuring the real pipeline directly (integration/stability phase):
  // this path chains FOUR sequential real Sarvam calls (transcribe -> normalize -> translate ->
  // summarize, one more than the text-only path -- see backend/services/complaint_agent.py),
  // each genuinely dependent on the previous one's output. Three live timed runs of just the
  // three-call text-only chain measured 17.7s/28.5s/18.7s -- a ~60% swing -- so the four-call
  // voice chain has strictly more accumulated variance, not less. 45s (already a widening from an
  // original 20s, per this test's history) still flaked under full-suite load in this phase's own
  // validation runs; 60s gives real, measured headroom instead of guessing again.
  await expect(page.locator(".mono", { hasText: /^JM-\d{5}$/ }).or(page.locator(".banner-error"))).toBeVisible({
    timeout: 60000,
  });
});
