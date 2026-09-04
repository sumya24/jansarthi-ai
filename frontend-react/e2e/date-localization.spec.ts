import { test, expect } from "@playwright/test";
import { fillHomeLocationPicker, uniqueEmail, uniquePhone, verifySignupEmail } from "./helpers";

const ENGLISH_WEEKDAYS_AND_MONTHS = [
  "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** LIVE-REPORTED BUG: every date/time shown anywhere in the app (Date#toLocaleString/
 * toLocaleDateString/toLocaleTimeString, ~20+ call sites) was called with no locale argument --
 * `undefined`, which formats using the BROWSER's own locale, completely independent of the
 * citizen's own in-app language choice. Confirmed directly against the real citizen Home screen:
 * a citizen using the app in Marathi still saw "Friday, September 4, 2026" in plain English right
 * next to an otherwise fully-Marathi greeting. Doesn't assert an exact string (the day this test
 * happens to run varies) -- asserts the more robust, deterministic thing this bug actually was:
 * no English weekday/month name appears anywhere in that date line, once the UI is in Marathi. */
test("the Home screen's date line follows the app's own language, not the browser's", async ({ page }) => {
  const phone = uniquePhone();
  const email = uniqueEmail();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Date Localization Test Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(email);
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // Switch to Marathi via Settings -- the account signs up in English by default.
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "मराठी" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.locator(".modal")).toHaveCount(0, { timeout: 8000 });

  await page.goto("/citizen");
  const dateLine = page.locator(".page-sub").first();
  await expect(dateLine).toBeVisible({ timeout: 10000 });
  const text = await dateLine.innerText();

  for (const word of ENGLISH_WEEKDAYS_AND_MONTHS) {
    expect(text, `date line still contains the English word "${word}": ${text}`).not.toContain(word);
  }
  // A real Devanagari character somewhere in the line -- confirms this isn't just "no English",
  // but an actual translated Marathi weekday/month name (the bug's fix, not an empty string).
  expect(text, `date line has no Devanagari text at all: ${text}`).toMatch(/[ऀ-ॿ]/);
});
