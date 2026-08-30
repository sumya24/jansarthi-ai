import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

/** Covers the email-OTP / login-by-email / forgot-password feature's FRONTEND wiring through the
 * real UI (unit coverage for the OTP/email logic itself lives in tests/test_email_otp.py). The
 * parts that need a real received email (completing add-and-verify-email, and a full
 * forgot-password round trip past the "code sent" step) aren't automatable here without real SMTP
 * credentials configured on the backend -- those need a live manual check once real credentials
 * are in place, same as this project's other "needs a real provider key" features. What IS
 * covered here works regardless of whether SMTP is configured: the login form's relabeled
 * identifier field, the forgot-password link, and forgot-password's clear "not registered" error
 * for an unregistered email -- deliberately NOT no-enumeration (see backend/routes/auth.py's
 * forgot_password for why this app makes that tradeoff differently than login does). */

async function signUpCitizen(page: import("@playwright/test").Page, phone: string, password: string) {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Email OTP Test Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.locator("#signup-confirm-password").fill(password);
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
}

test("login page accepts either a phone number or an email, and links to forgot-password", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByLabel("Phone number or email")).toBeVisible();

  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);
});

test("forgot password shows a clear error for an unregistered email, not a fake success", async ({ page }) => {
  await page.goto("/forgot-password");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.getByRole("button", { name: "Send code" }).click();

  await expect(page.getByText("This email isn't registered. Please sign up instead.")).toBeVisible();
  await expect(page.getByLabel("Verification code")).not.toBeVisible();
  await expect(page).toHaveURL(/\/forgot-password$/);
});

test("forgot password shows the code-sent step for a real, verified email", async ({ page }) => {
  const phone = uniquePhone();
  const email = uniqueEmail();
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Forgot Password Test Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("otptest12345!");
  await page.locator("#signup-confirm-password").fill("otptest12345!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/forgot-password");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send code" }).click();

  // Longer-than-default timeout: POST /auth/forgot-password does a real (non-mocked) SMTP send
  // before returning, same reasoning as helpers.ts's verifySignupEmail.
  await expect(page.getByText("A code has been sent to your email.")).toBeVisible({ timeout: 15000 });
  await expect(page.getByLabel("Verification code")).toBeVisible();
  await expect(page.getByLabel("New password")).toBeVisible();
});

test("forgot password requires an email before sending a code", async ({ page }) => {
  await page.goto("/forgot-password");
  await page.getByRole("button", { name: "Send code" }).click();
  await expect(page.getByText("This field is required.")).toBeVisible();
  await expect(page).toHaveURL(/\/forgot-password$/);
});

test("settings shows the account's verified email from signup, with no add-email section", async ({ page }) => {
  // Email verification is now mandatory at signup (see backend/routes/auth.py's module
  // docstring), so every citizen reaching /citizen already has a verified email -- the
  // "add & verify email" section in Settings only renders when one is still missing
  // (SettingsModal.tsx), which is no longer a reachable state for a freshly signed-up citizen.
  const phone = uniquePhone();
  await signUpCitizen(page, phone, "otptest12345!");

  await page.getByLabel("Settings").click();
  // SettingsModal.tsx renders the verified email as a disabled input's VALUE, not text content --
  // getByText only matches rendered text nodes, so this reads the value directly instead.
  await expect(page.locator("#settings-email")).toHaveValue(/\(Verified\)/);
  await expect(page.getByRole("button", { name: "Email", exact: true })).not.toBeVisible();
});

test("login still works with the phone number after the identifier field is relabeled", async ({ page }) => {
  const phone = uniquePhone();
  await signUpCitizen(page, phone, "otptest12345!");
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/login");
  await page.getByLabel("Phone number or email").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("otptest12345!");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
});
