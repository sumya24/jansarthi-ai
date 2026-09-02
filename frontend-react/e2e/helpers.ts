import { expect, type Page } from "@playwright/test";

let _uniquePhoneCounter = 0;

export function uniquePhone(): string {
  // 10-digit, starting with 9, unique enough per test run to avoid 409 conflicts. A trailing
  // in-process counter (not just Date.now()) matters here: two calls back-to-back with no
  // await between them can land in the same millisecond and otherwise collide.
  _uniquePhoneCounter = (_uniquePhoneCounter + 1) % 100;
  const suffix = String(_uniquePhoneCounter).padStart(2, "0");
  return "9" + String(Date.now()).slice(-7) + suffix;
}

let _uniqueEmailCounter = 0;

export function uniqueEmail(): string {
  _uniqueEmailCounter = (_uniqueEmailCounter + 1) % 100;
  const suffix = String(_uniqueEmailCounter).padStart(2, "0");
  return `test${Date.now()}${suffix}@example.com`;
}

/** Completes Signup.tsx's inline, mandatory email-verification step -- email verification is
 * mandatory at signup (see backend/routes/auth.py's module docstring), but lives inline on the
 * email field itself (a "Send code" button, then a confirm-code step right there), not as a
 * separate page/step after the rest of the form. The "Create account" button stays disabled
 * until this succeeds, so every spec that signs up a citizen needs this called after the
 * #signup-email field has a value and BEFORE clicking "Create account" -- exactly where in the
 * rest of the form-filling that happens doesn't matter, since verifying the email doesn't
 * disturb any other already-filled field. Reads the email back from the #signup-email input
 * itself (rather than requiring callers to pass it in) so callers don't need to thread a
 * variable through.
 *
 * Fetches the code from GET /auth/_dev/otp-code (backend/routes/auth.py's dev/test-only
 * endpoint, 404s in production) instead of reading a real inbox -- this project's local .env may
 * have real SMTP credentials configured for manual testing, and e2e specs must never depend on
 * (or spam) a real mailbox. Full absolute backend URL, not a relative path: page.request's own
 * baseURL is the Vite dev server on :5173 (per playwright.config.ts), which doesn't proxy
 * /auth/* -- only the React app itself talks to the backend directly, via VITE_API_URL (see
 * lib/api.ts). */
export async function verifySignupEmail(page: Page): Promise<void> {
  const email = await page.locator("#signup-email").inputValue();
  await page.getByRole("button", { name: "Send code" }).click();
  // Longer-than-default timeout: the send-code call does a real (non-mocked) SMTP send before
  // returning, and that occasionally takes several seconds -- observed causing flaky/failed
  // e2e runs at the default 5000ms even though the backend request itself always succeeds.
  // Matches this suite's existing precedent of widening waits around real backend round-trips
  // (see fillHomeLocationPicker's expect.poll below).
  await expect(page.getByLabel("Verification code")).toBeVisible({ timeout: 15000 });
  const otpResponse = await page.request.get(
    `http://localhost:8000/auth/_dev/otp-code?email=${encodeURIComponent(email)}`
  );
  const { code } = (await otpResponse.json()) as { code: string };
  await page.getByLabel("Verification code").fill(code);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByText("Verified", { exact: false })).toBeVisible();
}

/** Fills Signup's mandatory State/City/Ward/Area picker (HomeLocationPicker.tsx) -- the single
 * shared implementation every spec that signs up a citizen should call, replacing what used to
 * be 12 separate copies of near-identical select-or-freetext logic against the old single "Area
 * / ward" field that component replaced (see its own docstring for why there's now one merged
 * picker instead of two separate location sections).
 *
 * Locates by element id (#signup-home-state/city/ward), not label text -- several callers sign
 * up in non-English UI languages, and ids stay the same across all 6 while the label text is
 * translated. State is always a real `<select>` (options are whatever states
 * backend/routes/locations.py's _COVERED_STATE_CODES currently covers, plus an always-present
 * "Other" -- index 1 is always a real state, never "Other", since it's appended last); City and
 * Ward each independently render as either a `<select>` (real seeded data for that branch) or a
 * plain `<input>` (free-text fallback -- true for most of those states' cities today) depending
 * on what this test run's database actually has, never assume one shape. Area is left untouched: it's
 * optional, and no spec's actual test logic depends on the citizen's own signup-time ward text
 * matching anything else (workers are assigned separately by admin; a citizen's own complaints
 * are filed with their own explicit ward picker in the complaint wizard) -- any valid, non-empty
 * ward value from this picker is sufficient to get past the "ward is required" validation. */
export async function fillHomeLocationPicker(page: Page): Promise<void> {
  const stateField = page.locator("#signup-home-state");
  // The state list itself loads async (GET /locations/states, fired on mount) -- explicitly wait
  // for it to actually be populated rather than leaning on selectOption's own built-in retry
  // budget, which is bounded by the TEST's timeout, not this helper's. Real, observed failure
  // this fixes: under load (backend still warming up, or contending with other requests), the
  // fetch can take longer than expected, and a bare selectOption({index: 1}) against a select
  // that still only has its placeholder option just spins until the whole test times out.
  //
  // LIVE-REPORTED: the default 5s poll timeout wasn't enough margin -- a real, repeated-run
  // machine (many e2e runs back to back, real backend + real browser, no mocking of this
  // endpoint) occasionally took just over 5s to answer this one call, flaking a handful of
  // otherwise-solid tests. 15s is real, measured headroom (every observed case resolved in
  // 1-2s even when "slow"), not a guess -- same "widen based on live timing, not intuition"
  // approach this suite already uses for the AI-pipeline waits elsewhere. This is the ONE
  // shared place every one of this suite's location-picker polls was widened from, in every
  // spec file that has its own inline copy of this same wait.
  await expect.poll(() => stateField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
  await stateField.selectOption({ index: 1 });

  const cityField = page.locator("#signup-home-city");
  await expect.poll(() => cityField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await cityField.evaluate((el) => el.tagName)) === "SELECT") {
    await cityField.selectOption({ index: 1 });
  } else {
    await cityField.fill("Test City");
  }

  const wardField = page.locator("#signup-home-ward");
  await expect.poll(() => wardField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await wardField.evaluate((el) => el.tagName)) === "SELECT") {
    await wardField.selectOption({ index: 1 });
  } else {
    await wardField.fill("Test Ward");
  }
}

export type PickedLocation = { state: string; city: string; ward: string };

/** Fills the Add/Edit Worker modal's State/City/Ward picker (WorkerLocationPicker.tsx) and
 * returns the REAL state/city/ward text that ended up selected.
 *
 * LIVE-REPORTED GAP: admin-worker-flow.spec.ts used to assign a worker to a made-up, self-
 * generated ward string ("Ward 14 — Rukadi Road <timestamp>") via a plain free-text field --
 * that field was later replaced by this real State->City->Ward picker (same redesign
 * fillHomeLocationPicker's own docstring describes for Signup), which only offers REAL, seeded
 * wards to choose from. A fabricated ward name can no longer be assigned at all, so the caller
 * can no longer decide what the "assigned ward" text will be up front -- this helper picks a
 * real one and hands back its exact displayed text so the caller's own later assertions
 * (checking the worker table / the worker's own dashboard shows that ward) can assert on
 * whatever ward genuinely got selected, not a value that was never realistically assignable.
 *
 * Returns state/city too (not just ward), added when complaint-tracking.spec.ts live-caught a
 * second gap: a citizen signed up via fillHomeLocationPicker() lands in whatever state/city THAT
 * picker's own "index 1" resolves to, independently of wherever THIS picker's own "index 1"
 * landed for the workers -- two separately-first-alphabetically picks with no guarantee of
 * landing in the same place at all (worker assignment is further scoped to worker-backed areas
 * specifically, an even smaller list, so the two reliably diverge). A caller that needs a citizen
 * and a worker in the SAME real place needs to explicitly select this returned state/city for the
 * citizen too, rather than calling fillHomeLocationPicker() independently.
 *
 * `wardIndex` (default 1): LIVE-REPORTED, a THIRD gap the same fix uncovered -- the old fake,
 * self-generated ward name (e.g. "Tracking Test Ward <timestamp>") gave every spec file genuine
 * isolation for free, since no other spec could ever land in the same one by accident. A real,
 * shared, worker-backed ward has no such guarantee: two spec files both calling this with the
 * default index 1 land in the literally identical real ward, so a worker created by ONE spec
 * (e.g. admin-worker-flow.spec.ts's "Ramesh Kadam") becomes a real, eligible candidate for the
 * OTHER spec's own auto-assignment logic too -- confirmed live: complaint-tracking.spec.ts's
 * complaint was auto-assigned to "Ramesh Kadam" instead of either of ITS OWN two just-created
 * workers, simply because he'd been created earlier (alphabetically-earlier spec file, same
 * ward). Passing a different index per spec file (each still a real, worker-backed ward -- large
 * ULBs like the ones this ends up landing on have hundreds of wards, so collisions across a
 * handful of spec files stay very unlikely) restores real isolation without reintroducing a fake
 * location. */
export async function fillWorkerLocationPicker(page: Page, wardIndex = 1): Promise<PickedLocation> {
  const stateField = page.locator("#worker-location-state");
  await expect.poll(() => stateField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
  await stateField.selectOption({ index: 1 });
  const state = await stateField.evaluate((el) => (el as HTMLSelectElement).selectedOptions[0].textContent ?? "");

  const cityField = page.locator("#worker-location-city");
  await expect.poll(() => cityField.isEnabled(), { timeout: 15000 }).toBe(true);
  let city: string;
  if ((await cityField.evaluate((el) => el.tagName)) === "SELECT") {
    await cityField.selectOption({ index: 1 });
    city = await cityField.evaluate((el) => (el as HTMLSelectElement).selectedOptions[0].textContent ?? "");
  } else {
    city = "Test City";
    await cityField.fill(city);
  }

  const wardField = page.locator("#worker-location-ward");
  await expect.poll(() => wardField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await wardField.evaluate((el) => el.tagName)) === "SELECT") {
    await expect.poll(() => wardField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(wardIndex);
    await wardField.selectOption({ index: wardIndex });
    const ward = await wardField.evaluate((el) => (el as HTMLSelectElement).selectedOptions[0].textContent ?? "");
    return { state, city, ward };
  }
  const ward = "Test Ward";
  await wardField.fill(ward);
  return { state, city, ward };
}
