import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

/** Covers the production-grade auth upgrade: access+refresh tokens, silent refresh on an
 * expired/invalid access token, real server-side logout, self-service password change, and the
 * signup form's new confirm-password/password-strength validation. See backend/routes/auth.py's
 * /refresh, /logout, /change-password (unit-tested directly in tests/test_auth_refresh.py) --
 * this file instead confirms the FRONTEND actually wires those up through the real UI, which a
 * backend-only test can't. */

async function signUpCitizen(page: import("@playwright/test").Page, phone: string, password: string) {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Session Test Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill(password);
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
}

test("signup blocks submit when confirm password does not match", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Mismatch Tester");
  await page.getByLabel("Phone number").fill(uniquePhone());
  await page.getByLabel("Password", { exact: true }).fill("goodpass123!");
  await page.locator("#signup-confirm-password").fill("different123!");
  await fillHomeLocationPicker(page);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page.getByText("Passwords don't match.")).toBeVisible();
  await expect(page).toHaveURL(/\/signup$/);
});

test("signup blocks submit for a password that fails the strength rule", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Weak Password Tester");
  await page.getByLabel("Phone number").fill(uniquePhone());
  // No digit -- fails the 8+ chars / letter+digit rule.
  await page.getByLabel("Password", { exact: true }).fill("noDigitsHere");
  await page.locator("#signup-confirm-password").fill("noDigitsHere");
  await fillHomeLocationPicker(page);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(
    page.getByText("Password must be at least 8 characters, with a letter, a number, and a special character.")
  ).toBeVisible();
  await expect(page).toHaveURL(/\/signup$/);
});

test("an expired/invalid access token is silently refreshed instead of logging the citizen out", async ({ page }) => {
  const phone = uniquePhone();
  await signUpCitizen(page, phone, "sessiontest123!");

  // Corrupt ONLY the access token -- the refresh token (still real and valid) is what the
  // interceptor in lib/api.ts should use to silently recover. Any invalid string triggers the
  // same 401 path a genuinely time-expired JWT would (get_current_user rejects both identically).
  await page.evaluate(() => localStorage.setItem("janmitra.token", "corrupted-invalid-token"));

  // Reload triggers auth.tsx's boot-time /auth/me check with the now-bad access token.
  await page.reload();

  // Must land back on the citizen dashboard, NOT bounce to /login -- proves the silent refresh
  // (POST /auth/refresh using the still-good refresh token) actually recovered the session.
  await expect(page).toHaveURL(/\/citizen$/);
  await expect(page.getByText("Session Test Citizen")).toBeVisible();

  // The access token in storage must now be a real, different value -- not the corrupted one
  // still sitting there (which would mean the app just silently swallowed the 401 with no
  // real recovery).
  const refreshedToken = await page.evaluate(() => localStorage.getItem("janmitra.token"));
  expect(refreshedToken).not.toBe("corrupted-invalid-token");
  expect(refreshedToken).toBeTruthy();
});

test("logout revokes the refresh token server-side, not just locally", async ({ page }) => {
  const phone = uniquePhone();
  await signUpCitizen(page, phone, "sessiontest123!");

  const refreshToken = await page.evaluate(() => localStorage.getItem("janmitra.refreshToken"));
  expect(refreshToken).toBeTruthy();

  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  // Both tokens must be gone from local storage...
  const tokenAfterLogout = await page.evaluate(() => localStorage.getItem("janmitra.token"));
  const refreshAfterLogout = await page.evaluate(() => localStorage.getItem("janmitra.refreshToken"));
  expect(tokenAfterLogout).toBeNull();
  expect(refreshAfterLogout).toBeNull();

  // ...and the OLD refresh token must be genuinely dead server-side too (not just forgotten
  // locally) -- the real proof this is a server-side revocation, not a client-only clear.
  // Full absolute URL to the backend (not a relative path): page.request's own baseURL is the
  // Vite dev server on :5173 (per playwright.config.ts), which doesn't proxy /auth/* -- only the
  // React app itself talks to the backend directly, via VITE_API_URL (see lib/api.ts).
  const refreshAttempt = await page.request.post("http://localhost:8000/auth/refresh", {
    data: { refresh_token: refreshToken },
  });
  expect(refreshAttempt.status()).toBe(401);
});

test("citizen can change their own password and log in with the new one", async ({ page }) => {
  const phone = uniquePhone();
  await signUpCitizen(page, phone, "oldpassword1!");

  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Change Password" }).click();
  await page.getByLabel("Current password").fill("oldpassword1!");
  await page.getByLabel("New password", { exact: true }).fill("newpassword2!");
  await page.getByLabel("Confirm new password").fill("newpassword2!");
  await page.getByRole("button", { name: "Update password" }).click();

  await expect(page.getByText("Password updated. You've been logged out of your other devices.")).toBeVisible();

  // Log out (still available in the same open modal) and confirm the credential change actually
  // took effect for real login -- not just that the form reported success.
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("oldpassword1!");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.locator(".banner-error")).toContainText("Incorrect phone number/email or password");

  await page.getByLabel("Password", { exact: true }).fill("newpassword2!");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/citizen$/);
});
