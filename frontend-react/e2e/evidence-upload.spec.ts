import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySignupEmail, fillHomeLocationPicker, fillWorkerLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const PYTHON_BIN = process.platform === "win32" ? "python" : "python3";
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";

// Minimal, genuinely valid 1x1 images (not just "fake bytes with a jpeg-ish prefix") -- real
// file content, so this test proves actual images survive the full upload -> storage -> gallery
// round trip, not just that some bytes made it through.
const JPEG_1PX = Buffer.from(
  "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCdABmX/9k=",
  "base64"
);
const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg==",
  "base64"
);

test.beforeAll(() => {
  execSync(
    `${PYTHON_BIN} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Evidence Test Admin"`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );
});

async function login(page: import("@playwright/test").Page, phone: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  // Wait for the login itself to actually complete (token stored, redirected off /login) before
  // returning -- a caller that immediately does page.goto() right after this would otherwise
  // race the login POST and can navigate while still unauthenticated.
  await page.waitForURL(/\/(citizen|worker|admin)$/, { timeout: 15000 });
}

async function logout(page: import("@playwright/test").Page) {
  await page.evaluate(() => localStorage.clear());
  await page.goto("/welcome");
}

test("real multi-file evidence upload, end to end: select -> upload -> storage -> gallery -> report", async ({ page }) => {
  test.setTimeout(150000);

  const workerPhone = uniquePhone();
  const citizenPhone = uniquePhone();

  // --- Admin creates a worker. ---
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  // Scoped to the dashboard's own button (class btn-ghost) -- a nav-drawer link with the same
  // text also exists on the page (see components/NavDrawer.tsx), so an unscoped role/name
  // locator is ambiguous.
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);
  await page.getByRole("button", { name: "+ Add worker" }).click();
  await page.getByLabel("Full name").fill("Evidence Test Worker");
  await page.getByLabel("Phone number").fill(workerPhone);
  await page.getByLabel("Temporary password").fill("workerpass123");
  await page.getByLabel("Confirm password").fill("workerpass123");
  // "Assign to ward" was a free-text field, replaced by a real State->City->Ward picker (see
  // complaint-tracking.spec.ts's own matching fix). Which real ward this lands in doesn't matter
  // for who gets the assignment -- see the forced-reassignment call further down, after the
  // complaint is filed.
  const workerLocation = await fillWorkerLocationPicker(page, 3);
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "Add worker", exact: true }).click();
  await expect(page.getByText("Evidence Test Worker").first()).toBeVisible();
  await logout(page);

  // --- Citizen files a complaint with TWO real photos, selected via the actual file input. ---
  //
  // LIVE-REPORTED: fillHomeLocationPicker() independently picks its own "index 1" state/city, no
  // guarantee of landing in the same real place as the worker above -- explicitly matching
  // workerLocation's state/city instead (see complaint-tracking.spec.ts's own identical fix).
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Evidence Test Citizen");
  await page.getByLabel("Phone number").fill(citizenPhone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  const homeStateField = page.locator("#signup-home-state");
  await expect.poll(() => homeStateField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
  await homeStateField.selectOption({ label: workerLocation.state });
  const homeCityField = page.locator("#signup-home-city");
  await expect.poll(() => homeCityField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await homeCityField.evaluate((el) => el.tagName)) === "SELECT") {
    await homeCityField.selectOption({ label: workerLocation.city });
  } else {
    await homeCityField.fill(workerLocation.city);
  }
  const homeWardField = page.locator("#signup-home-ward");
  await expect.poll(() => homeWardField.isEnabled(), { timeout: 15000 }).toBe(true);
  if ((await homeWardField.evaluate((el) => el.tagName)) === "SELECT") {
    await homeWardField.selectOption({ index: 1 });
  } else {
    await homeWardField.fill("Test Ward");
  }
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // LIVE-REPORTED: the hero "a.btn-primary" button no longer exists on CitizenHome.tsx -- the
  // nav-drawer link is the only way there now, and it's a real slide-out drawer (needs opening
  // first). See citizen-signup.spec.ts's own identical fix.
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);
  await page.getByRole("button", { name: "Select location" }).click();
  // LIVE-REPORTED: selectOption(ward) (matched by VALUE, not visible text) stopped finding a
  // match -- the wizard's ward list is suffixed with the real ULB name, a different format than
  // the bare ward text fillWorkerLocationPicker returns. See complaint-tracking.spec.ts's own
  // identical fix for the full explanation.
  const wizardWardOption = page.locator("#wizard-ward option", { hasText: workerLocation.ward });
  await expect.poll(() => wizardWardOption.count(), { timeout: 15000 }).toBeGreaterThan(0);
  const wizardWardValue = await wizardWardOption.first().getAttribute("value");
  await page.locator("#wizard-ward").selectOption(wizardWardValue!);
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByPlaceholder(/Garbage not collected/).fill("Two photos of the same broken streetlight.");
  await page.getByRole("button", { name: "Next" }).click();

  // Media step: select two real files at once through the actual <input type=file multiple>.
  await page.locator('input[type="file"]').setInputFiles([
    { name: "streetlight-1.jpg", mimeType: "image/jpeg", buffer: JPEG_1PX },
    { name: "streetlight-2.png", mimeType: "image/png", buffer: PNG_1PX },
  ]);
  // Both thumbnails preview before submission.
  await expect(page.locator(".multi-photo-thumb")).toHaveCount(2);

  await page.getByRole("button", { name: "Next" }).click();
  // AI Understanding step's badge text depends on which classification layer succeeded (real
  // model vs. keyword fallback, see ReportIssue.tsx) -- assert on the stable .dev-badge class
  // instead of either layer's specific wording.
  //
  // LIVE-REPORTED: widened from 3000ms -- this is normally near-instant, but occasionally took
  // just over 3s under real, repeated-run load, same reasoning as this suite's other widened
  // timeouts (see helpers.ts's own comment on the same pattern).
  await expect(page.locator(".dev-badge")).toBeVisible({ timeout: 8000 });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();
  await expect(page.getByText("Complaint submitted successfully.")).toBeVisible({ timeout: 60000 });

  // LIVE-REPORTED: the ward this complaint just landed in already has real seed/demo workers in
  // it (see scripts/seed_multi_ward_data.py) with a lower id than "Evidence Test Worker" --
  // auto-assignment always picks the lowest-id eligible worker in a ward
  // (assignment_service.py), so the freshly created worker below would never actually receive
  // it. Rather than fake or skip that real behavior, this drives the SAME backend function a
  // real rejection uses (assign_next_worker(), via scripts/e2e_force_reassign_to_worker.py) to
  // have every other real candidate for this specific complaint step aside -- exactly what
  // happens if each of them individually rejects it -- until it lands on our own worker. No
  // seed/demo account is touched.
  execSync(
    `${PYTHON_BIN} scripts/e2e_force_reassign_to_worker.py --citizen-phone ${citizenPhone} --keep-phone ${workerPhone}`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );

  await page.getByRole("link", { name: "Track this complaint" }).click();
  await expect(page).toHaveURL(/\/citizen\/complaints$/);

  // The list dashboard is deliberately kept lightweight (no full gallery there, by design --
  // see CitizenDashboard.tsx) -- open the complaint's own detail page to see the real gallery.
  const citizenCard = page.locator(".surface-card").filter({ hasText: "Two photos of the same broken streetlight." });
  await citizenCard.getByText(/JM-\d+/).click();
  await page.waitForURL(/\/citizen\/complaints\/\d+$/);
  await expect(page.locator(".evidence-thumb")).toHaveCount(2);
  await logout(page);

  // --- Worker accepts, starts work with an evidence photo, completes with another. ---
  await login(page, workerPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  const card = page.locator(".surface-card").filter({ hasText: "Two photos of the same broken streetlight." });
  await card.locator('button:has-text("Accept")').click();
  await page.waitForTimeout(1000);
  await card.getByText(/JM-\d+/).click();
  await page.waitForURL(/\/worker\/complaints\/\d+$/);
  const complaintUrl = page.url();

  await page.getByRole("button", { name: "Start Work" }).click();
  const startModal = page.locator(".modal");
  await page.getByLabel("Initial assessment").fill("Confirmed via both citizen photos -- bulb needs replacing.");
  await startModal.locator('input[type="file"]').setInputFiles({ name: "assessment.jpg", mimeType: "image/jpeg", buffer: JPEG_1PX });
  await expect(startModal.locator(".multi-photo-thumb")).toHaveCount(1);
  await startModal.getByRole("button", { name: "Start Work", exact: true }).click();
  await page.waitForTimeout(1000);

  await page.getByRole("button", { name: "Complete Complaint" }).click();
  const completeModal = page.locator(".modal");
  await page.getByLabel("Completion status").fill("Bulb replaced, tested working.");
  await completeModal.locator('input[type="file"]').setInputFiles([
    { name: "final-1.jpg", mimeType: "image/jpeg", buffer: JPEG_1PX },
    { name: "final-2.png", mimeType: "image/png", buffer: PNG_1PX },
  ]);
  await expect(completeModal.locator(".multi-photo-thumb")).toHaveCount(2);
  await completeModal.getByRole("button", { name: "Mark Resolved", exact: true }).click();
  await page.waitForTimeout(1000);

  // Detail page shows 5 distinct real files (2 citizen + 1 initial-assessment + 2 completion) --
  // rendered twice over, once in the Updates timeline and again in the Resolution Report section
  // below it (the report intentionally repeats the same real facts already shown above, exactly
  // like the PDF does -- see ComplaintReportView.tsx), so 10 thumbnails total.
  //
  // LIVE-REPORTED: widened from the default 5s -- the Resolution Report section only renders
  // once its own separate getComplaintReport() fetch resolves (see WorkerComplaintDetail.tsx),
  // one more real round trip after the status itself already flips to resolved, so this needs
  // the same real headroom as this suite's other widened timeouts under real, repeated-run load.
  await expect(page.locator(".evidence-thumb")).toHaveCount(10, { timeout: 15000 });

  // Click a thumbnail -> lightbox opens with a real, loadable image.
  await page.locator(".evidence-thumb").first().click();
  const lightboxImg = page.locator(".evidence-lightbox-img");
  await expect(lightboxImg).toBeVisible();
  const naturalWidth = await lightboxImg.evaluate((img: HTMLImageElement) => img.naturalWidth);
  expect(naturalWidth).toBeGreaterThan(0); // the browser actually decoded a real image, not a broken link
  await page.keyboard.press("Escape");

  // Report (both JSON view and PDF) reflects the same evidence.
  await expect(page.getByText("Resolution Report", { exact: true })).toBeVisible();
  // 30s, not 15s: this report embeds 5 real images (reportlab reads + decodes each from disk),
  // legitimately slower to generate than the text-only report the shorter timeout elsewhere in
  // this suite was calibrated against.
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 30000 }),
    page.getByRole("button", { name: "Download Report" }).click(),
  ]);
  const fs = await import("node:fs");
  const downloadPath = await download.path();
  const pdfBytes = fs.readFileSync(downloadPath!);
  expect(pdfBytes.subarray(0, 4).toString()).toBe("%PDF");
  expect(pdfBytes.length).toBeGreaterThan(5000); // a report with 5 embedded images is not a tiny/empty PDF

  await logout(page);

  // --- Citizen sees the worker's evidence too, on their own detail page. Same 10 (5 real files,
  // shown twice -- Updates timeline + Resolution Report) as the worker's view above. ---
  await login(page, citizenPhone, "citizenpass123!");
  await page.goto(complaintUrl.replace("/worker/", "/citizen/"));
  // Longer timeout here specifically: a fresh full navigation (page.goto, not client-side
  // routing) has to load the app shell, authenticate, and fetch the complaint detail before
  // anything renders -- the default 5s auto-retry window this matcher normally gets isn't
  // always enough for that first paint under load.
  await expect(page.locator(".evidence-thumb")).toHaveCount(10, { timeout: 15000 });
});

test("a file that isn't really an image is rejected with a clear error, not a silent success or a crash", async ({ page }) => {
  test.setTimeout(90000); // real backend round-trip -- see the 60s assertion below for why
  // Self-contained (its own worker, not the main test's) -- must not depend on the main test
  // above having already run first. This test doesn't check WHICH ward/worker the complaint
  // lands on (only that an invalid file gets rejected), so unlike the main test above, there's no
  // need to align the citizen's home city with the worker's, or to force-reassign anything --
  // the WORKER CREATION step itself just needs a real, valid pick.
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);
  await page.getByRole("button", { name: "+ Add worker" }).click();
  await page.getByLabel("Full name").fill("Invalid Upload Test Worker");
  await page.getByLabel("Phone number").fill(uniquePhone());
  await page.getByLabel("Temporary password").fill("workerpass123");
  await page.getByLabel("Confirm password").fill("workerpass123");
  await fillWorkerLocationPicker(page, 2);
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "Add worker", exact: true }).click();
  await expect(page.getByText("Invalid Upload Test Worker").first()).toBeVisible();
  await logout(page);

  // Real backend content validation (see backend/services/evidence_service.py) can't be checked
  // client-side -- it requires actually decoding the bytes, which only the backend does. So this
  // is a genuine server round-trip: the file previews client-side exactly like a real photo would
  // (nothing about it looks wrong until submission), and the rejection only surfaces once the
  // real backend inspects the actual content.
  const notReallyAnImage = Buffer.from("This is a plain text file, not an image, just renamed to look like one.");

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Invalid Upload Tester");
  await page.getByLabel("Phone number").fill(uniquePhone());
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // LIVE-REPORTED: the hero "a.btn-primary" button no longer exists on CitizenHome.tsx -- see
  // citizen-signup.spec.ts's own identical fix.
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);
  await page.getByRole("button", { name: "Select location" }).click();
  // #wizard-ward renders as either a <select> (a manageable real ward list) or a free-text
  // <input> fallback, depending on how many real wards exist in this run's database -- same
  // defensive shape check as helpers.ts's fillHomeLocationPicker, never assume one or the other.
  //
  // LIVE-REPORTED: selectOption(localWard) (a fabricated name, matched by VALUE) stopped working
  // once real wards became available for most cities -- this test doesn't care WHICH ward gets
  // picked (only that an invalid file is rejected later), so picking whatever's real and first
  // works fine, unlike the main test above which needs a SPECIFIC ward matching its own worker.
  // LIVE-REPORTED: this field doesn't keep one persistent element and toggle `disabled` while its
  // real ward list loads -- LocationPicker.tsx renders a free-text <input> when `wards.length ===
  // 0` and swaps to an entirely different <select> once the list populates. A single "check
  // tagName, then act" is racy against that swap: reading the <input> tag a moment before the
  // fetch resolves, then acting after it already resolved, targets a locator that has since
  // re-resolved to the new <select> underneath it -- confirmed live (a "fill" landed on an
  // already-swapped <select> and failed). Retries the whole check-and-act once rather than
  // papering over it with an arbitrary sleep.
  const wardField = page.locator("#wizard-ward");
  await expect(async () => {
    if ((await wardField.evaluate((el) => el.tagName)) === "SELECT") {
      await expect.poll(() => wardField.locator("option").count(), { timeout: 15000 }).toBeGreaterThan(1);
      await wardField.selectOption({ index: 1 });
    } else {
      await wardField.fill("Test Ward");
    }
  }).toPass({ intervals: [0], timeout: 10000 });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByPlaceholder(/Garbage not collected/).fill("Text file disguised as a photo.");
  await page.getByRole("button", { name: "Next" }).click();

  await page.locator('input[type="file"]').setInputFiles({
    name: "totally-a-photo.jpg", mimeType: "image/jpeg", buffer: notReallyAnImage,
  });
  // The client-side preview has no way to know yet -- it renders exactly like a real photo would.
  await expect(page.locator(".multi-photo-thumb")).toHaveCount(1);

  await page.getByRole("button", { name: "Next" }).click();
  // No .dev-badge assertion here (unlike the other wizard-flow specs): this test's complaint text
  // ("Text file disguised as a photo.") is deliberately non-civic, since it's testing invalid-image
  // rejection, not classification -- the keyword fallback legitimately finds no category match for
  // it, so the AI Understanding step shows wizard.ai.noMatch instead of a badge. That's correct
  // behavior, not something to assert around; a category isn't required to proceed.
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();

  // The real backend rejects it -- a clear error, never a silent "success" and never a crash.
  // 60s, not 15s: evidence validation runs AFTER complaint creation's own real Sarvam translate/
  // summarize call already completes (ComplaintEvidence.complaint_id needs a real complaint id
  // first) -- same real-network-latency budget the "successfully submitted" assertions elsewhere
  // in this file already need, not a fast-fail.
  await expect(page.locator(".banner-error")).toBeVisible({ timeout: 60000 });
  await expect(page.getByText("Complaint submitted successfully.")).toHaveCount(0);
});
