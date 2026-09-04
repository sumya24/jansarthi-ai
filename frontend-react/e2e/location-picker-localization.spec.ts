import { test, expect } from "@playwright/test";
import { fillHomeLocationPicker, uniqueEmail, uniquePhone, verifySignupEmail } from "./helpers";

const ENGLISH_DISTRICT_NAMES = [
  "Ahmedabad", "Alluri Sitharama Raju", "Bengaluru Urban", "Chennai", "Coimbatore",
  "Dakshina Kannada", "Howrah", "Kanpur Nagar", "Kolkata", "Lucknow", "Madurai",
  "Mumbai", "Mysuru", "Nagpur", "Paschim Bardhaman", "Pune", "Surat", "Vadodara", "Varanasi",
];

// The word "Ward" itself (not a real ward's full name) -- a translated ward option should never
// contain this bare English word, even if it also carries the translated municipal name/number.
const ENGLISH_MUNICIPAL_WORDS = ["Ward", "M Corp", "Prabhag", "Municipal"];

/** LIVE-REPORTED BUG: `uiLang` is scoped to the browser (localStorage), not the account -- once a
 * citizen has ever switched the app to their own language (via Settings), that choice sticks
 * across logout, so the very next Signup page they see (creating a second account, e.g. for a
 * family member) renders in that language too. Signup's own field labels were already correctly
 * localized, but the State/City picker's dropdown OPTIONS (real data from GET /locations/states
 * and GET /locations/{state_id}/cities) were always rendered in raw English underneath an
 * otherwise fully-translated form. Confirmed directly: a Marathi-language signup form showed
 * "Mumbai" / "Pune" / "Bengaluru Urban" etc. as literal English option text. Doesn't assert an
 * exact state (whichever real state/city happens to sort first varies) -- asserts the more robust,
 * deterministic thing this bug actually was: once a real city option renders, it's never the raw
 * English district name, and it does carry a real Devanagari translation. */
test("the Signup page's State/City picker follows the app's own language, not just its field labels", async ({ page }) => {
  // Get a first account into Marathi via the normal in-app Settings flow, then log out --
  // `uiLang` persists in the browser across that logout, exactly like a citizen re-opening
  // Signup for a second account would see.
  const firstPhone = uniquePhone();
  const firstEmail = uniqueEmail();
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Location Localization Setup Citizen");
  await page.getByLabel("Phone number").fill(firstPhone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(firstEmail);
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  await page.locator("button.icon-btn").last().click();
  await page.getByRole("button", { name: "मराठी" }).click();
  await page.locator(".modal-actions button.btn-primary").click();
  await expect(page.locator(".modal")).toHaveCount(0, { timeout: 8000 });

  // `uiLang` lives in localStorage, entirely independent of auth state (see uiLang.tsx) -- clear
  // just the auth cookies (not localStorage) to land back on a signed-out screen the same way a
  // real logout would, without depending on the Log out button's own now-Marathi label.
  await page.context().clearCookies();

  // Now the actual bug surface: a fresh Signup page, in a browser whose uiLang is already
  // Marathi from the account above -- nothing about THIS account has selected a language.
  await page.goto("/signup");
  const stateField = page.locator("#signup-home-state");
  await expect.poll(() => stateField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
  await stateField.selectOption({ index: 1 });

  const cityField = page.locator("#signup-home-city");
  await expect.poll(() => cityField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await cityField.evaluate((el) => el.tagName)) !== "SELECT") {
    // This state's real data has no seeded cities to pick from -- nothing to assert here, the
    // free-text fallback is correct behavior, not this bug.
    test.skip();
  }
  const cityOptionTexts = await cityField.locator("option").allInnerTexts();
  const realCityOptions = cityOptionTexts.filter((txt) => txt.trim() !== "" && txt !== "शहर निवडा" && !txt.toLowerCase().includes("select"));
  expect(realCityOptions.length, "expected at least one real city option to assert against").toBeGreaterThan(0);

  for (const text of realCityOptions) {
    for (const englishName of ENGLISH_DISTRICT_NAMES) {
      expect(text, `city option still shows the raw English district name "${englishName}": ${text}`).not.toBe(englishName);
    }
    expect(text, `city option has no Devanagari text at all: ${text}`).toMatch(/[ऀ-ॿ]/);
  }

  // Cascade into Ward: pick the first real city, then check the Ward dropdown the exact same way.
  await cityField.selectOption({ index: 1 });
  const wardField = page.locator("#signup-home-ward");
  await expect.poll(() => wardField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await wardField.evaluate((el) => el.tagName)) === "SELECT") {
    const wardOptionTexts = await wardField.locator("option").allInnerTexts();
    const realWardOptions = wardOptionTexts.filter((txt) => txt.trim() !== "" && txt !== "वॉर्ड निवडा");
    for (const text of realWardOptions) {
      for (const englishWord of ENGLISH_MUNICIPAL_WORDS) {
        expect(text, `ward option still shows the raw English word "${englishWord}": ${text}`).not.toContain(englishWord);
      }
      expect(text, `ward option has no Devanagari text at all: ${text}`).toMatch(/[ऀ-ॿ]/);
    }

    // Cascade into Area: pick the first real ward, then check the Area dropdown too, if this
    // particular ward has any seeded localities under it (most don't -- only 6 real localities
    // exist at all -- so this degrades to the free-text fallback for most real wards, which is
    // correct behavior, not a gap).
    await wardField.selectOption({ index: 1 });
    const areaField = page.locator("#signup-home-area");
    await expect.poll(() => areaField.isEnabled(), { timeout: 15000 }).toBe(true);
    if ((await areaField.evaluate((el) => el.tagName)) === "SELECT") {
      const areaOptionTexts = await areaField.locator("option").allInnerTexts();
      const realAreaOptions = areaOptionTexts.filter((txt) => txt.trim() !== "" && !txt.toLowerCase().includes("select"));
      for (const text of realAreaOptions) {
        expect(text, `area option has no Devanagari text at all: ${text}`).toMatch(/[ऀ-ॿ]/);
      }
    }
  }
});
