import { test, expect } from "@playwright/test";
import { fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

test("language gate -> landing -> signup validation -> citizen dashboard -> graceful complaint error", async ({ page }) => {
  await page.goto("/");

  // Language gate is the very first thing shown.
  await expect(page.getByText("Choose your language")).toBeVisible();
  await page.getByRole("button", { name: /मराठी/ }).click();

  // Landing page text should now be in Marathi.
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByText("तुमच्या भाषेत तक्रार करा")).toBeVisible();

  await page.getByRole("link", { name: "साइन अप" }).click();
  await expect(page).toHaveURL(/\/signup$/);

  // Submitting empty should show inline validation errors, not crash. Signed up in Marathi,
  // so the validation message correctly renders in Marathi too, not English.
  await page.getByRole("button", { name: "खाते तयार करा" }).click();
  await expect(page.getByText("हे क्षेत्र आवश्यक आहे.").first()).toBeVisible();

  // Fill in a valid signup and submit.
  const phone = uniquePhone();
  const email = uniqueEmail();
  await page.getByLabel("पूर्ण नाव").fill("Priya Deshmukh");
  await page.getByLabel("फोन नंबर").fill(phone);
  await page.getByLabel("ईमेल पत्ता").fill(email);
  await page.getByLabel("पासवर्ड", { exact: true }).fill("secret123!");
  await page.locator("#signup-confirm-password").fill("secret123!");
  await fillHomeLocationPicker(page);

  // Mandatory inline email verification, still in Marathi (see backend/routes/auth.py's module
  // docstring on why this is mandatory, and Signup.tsx for why it's a "Send code"/"Verify" pair
  // right next to the email field, not a separate page/step). Longer-than-default timeout: the
  // send-code call does a real (non-mocked) SMTP send before returning, which can occasionally
  // take several seconds -- see helpers.ts's verifySignupEmail for the same reasoning (this file
  // doesn't use that shared helper, since its labels are Marathi, not English).
  await page.getByRole("button", { name: "कोड पाठवा" }).click();
  await expect(page.getByLabel("पडताळणी कोड")).toBeVisible({ timeout: 15000 });
  const otpResponse = await page.request.get(
    `http://localhost:8000/auth/_dev/otp-code?email=${encodeURIComponent(email)}`
  );
  const { code } = (await otpResponse.json()) as { code: string };
  await page.getByLabel("पडताळणी कोड").fill(code);
  await page.getByRole("button", { name: "पडताळणी करा" }).click();
  await expect(page.getByText("पडताळणी झाली", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: "खाते तयार करा" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
  await expect(page.getByText("Priya Deshmukh")).toBeVisible();
  await expect(page.getByText("नागरिक", { exact: true })).toBeVisible(); // role pill — also renders in Marathi

  // Start reporting an issue. The complaint form lives in the Report an Issue wizard (Phase 1),
  // still in Marathi since that's the signed-up language.
  //
  // LIVE-REPORTED: this used to be scoped to a hero "a.btn-primary" button on this exact page
  // (Citizen Home), deliberately distinct from the nav-drawer link with the same text, per this
  // comment's own original note about avoiding ambiguity between the two. That hero button no
  // longer exists on CitizenHome.tsx -- confirmed directly, it now lives only on
  // CitizenDashboard.tsx (the separate "My Complaints" page) -- so the nav-drawer link is the
  // only "समस्या नोंदवा" control on this page today, and the ambiguity this comment used to guard
  // against no longer exists either. It's a real slide-out drawer (off-screen via CSS transform
  // until opened, confirmed directly -- "element is outside of the viewport"), not an
  // always-visible sidebar, so it needs opening first.
  await page.getByRole("button", { name: "मेनू उघडा" }).click();
  await page.getByRole("link", { name: "समस्या नोंदवा" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);

  // Try to skip straight through without filling anything in — whichever step's client-side
  // validation fires first (the ward, if other specs sharing this dev db already seeded one;
  // otherwise the empty-description check on the next step) must show a clear inline error in
  // Marathi, not crash or silently advance.
  await page.getByRole("button", { name: "पुढे" }).click();
  if (!(await page.locator(".banner-error").isVisible())) {
    await page.getByRole("button", { name: "पुढे" }).click();
  }
  await expect(page.locator(".banner-error")).toBeVisible({ timeout: 10000 });
});
