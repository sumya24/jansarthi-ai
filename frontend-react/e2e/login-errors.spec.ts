import { test, expect } from "@playwright/test";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

test("login with wrong password shows an error and does not navigate away", async ({ page }) => {
  const phone = uniquePhone();

  // Create a real account first (via the UI) so we have known-good credentials to get wrong.
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Test User");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.getByLabel("Password", { exact: true }).fill("correct-password1");
  await page.locator("#signup-confirm-password").fill("correct-password1");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // Log out via Settings, then try the wrong password.
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("wrong-password");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page.locator(".banner-error")).toContainText("Incorrect phone number/email or password");
  await expect(page).toHaveURL(/\/login$/);
});

test("login requires both fields", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByText("This field is required.").first()).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
