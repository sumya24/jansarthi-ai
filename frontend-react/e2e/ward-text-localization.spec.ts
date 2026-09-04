import { test, expect } from "@playwright/test";
import { uniqueEmail, uniquePhone, verifySignupEmail } from "./helpers";

/** LIVE-REPORTED BUG: a citizen's own ward (Home's location line, My Area, every complaint's
 * "Location:" field, ...) is never just a bare Ward.name -- HomeLocationPicker's own composeWard()
 * always bakes in at least a city, and often a locality too: "{ward}[ — {locality}], {city}"
 * (e.g. "Surat (M Corp.) - Ward No.1, Surat"). Confirmed directly, live: a citizen using the app in
 * Marathi/Odia still saw this composed text in plain English on the Home screen, right next to an
 * otherwise fully-translated greeting and date -- localizeCityName/localizeWardName alone (which
 * only translate a bare dropdown OPTION while picking) never touched this already-composed,
 * already-stored string.
 *
 * Doesn't assert an exact ward (whichever real ward happens to sort first at index 1 varies) --
 * asserts the more robust, deterministic thing this bug actually was: the composed text shown on
 * Home, in Marathi, is never byte-for-byte the same as the raw English text the picker itself
 * showed while still in English. */
test("the Home screen's own ward/location line follows the app's own language, not just the picker", async ({ page }) => {
  const phone = uniquePhone();
  const email = uniqueEmail();

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Ward Text Localization Test Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(email);
  await page.locator("#signup-confirm-password").fill("citizenpass123!");

  // Pick State -> City -> Ward manually (rather than the shared fillHomeLocationPicker helper) so
  // this test can capture the RAW English ward text at the exact moment it's picked, while the
  // page is still in English -- the one moment this exact string is guaranteed untranslated.
  const stateField = page.locator("#signup-home-state");
  await expect.poll(() => stateField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
  await stateField.selectOption({ index: 1 });

  const cityField = page.locator("#signup-home-city");
  await expect.poll(() => cityField.isEnabled(), { timeout: 15000 }).toBe(true);
  test.skip((await cityField.evaluate((el) => el.tagName)) !== "SELECT", "this state has no real seeded cities to pick from");
  await cityField.selectOption({ index: 1 });

  const wardField = page.locator("#signup-home-ward");
  await expect.poll(() => wardField.isEnabled(), { timeout: 15000 }).toBe(true);
  test.skip((await wardField.evaluate((el) => el.tagName)) !== "SELECT", "this city has no real seeded wards to pick from");
  await wardField.selectOption({ index: 1 });
  const rawWardText = await wardField.locator("option:checked").innerText();

  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // Switch to Marathi via Settings, same as every other language-switch test in this suite.
  await page.locator("button.icon-btn").last().click();
  await page.getByRole("button", { name: "मराठी" }).click();
  await page.locator(".modal-actions button.btn-primary").click();
  await expect(page.locator(".modal")).toHaveCount(0, { timeout: 8000 });

  await page.goto("/citizen");
  const locationLine = page.locator(".page-sub").first();
  await expect(locationLine).toBeVisible({ timeout: 10000 });
  const text = await locationLine.innerText();

  expect(text, `still shows the raw English ward text unchanged: ${text}`).not.toContain(rawWardText);
  expect(text, `no Devanagari text found in the ward/location line: ${text}`).toMatch(/[ऀ-ॿ]/);
});
