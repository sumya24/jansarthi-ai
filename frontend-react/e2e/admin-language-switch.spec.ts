import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const PYTHON_BIN = process.platform === "win32" ? "python" : "python3";
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";

test.beforeAll(() => {
  execSync(
    `${PYTHON_BIN} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Lang Switch Test Admin"`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );
});

async function login(page: import("@playwright/test").Page, phone: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

/** LIVE-REPORTED BUG, found via a broader audit after the same bug was already fixed on the
 * citizen-side Resolution Report (see complaint-tracking.spec.ts's own matching test):
 * AdminDashboard.tsx's main complaint table and AdminWorkerDetail.tsx's per-worker complaint
 * table both called listComplaints() with no `lang` at all, and neither loading effect depended
 * on it either -- so switching the UI's display language while an admin was looking at either
 * page never re-fetched the complaint list.
 *
 * Deliberately doesn't create its own worker/citizen/complaint: this dev db already carries
 * hundreds of real complaints from every other spec that's run against it (see e.g.
 * complaint-tracking.spec.ts), and this regression is about a language switch re-issuing the
 * request at all -- it doesn't need a SPECIFIC complaint to exist, just that the admin's own
 * queue is non-empty, which it always is here. Asserted at the network level (a real
 * GET .../complaints?lang=... re-fetch), same as complaint-tracking.spec.ts's own citizen-side
 * version of this exact check. */
test("admin dashboard and admin worker-detail re-fetch complaints when the UI language is switched", async ({ page }) => {
  test.setTimeout(60000);
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);

  const adminListRefetch = page.waitForRequest(
    (req) => /\/complaints(\?|$)/.test(req.url()) && req.url().includes("lang=hi"),
    { timeout: 15000 }
  );
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "हिन्दी" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.locator(".modal")).toHaveCount(0, { timeout: 8000 });
  // With the bug: no new request at all here (the effect never re-runs) -- this would time out.
  await adminListRefetch;

  // --- Admin worker-detail page: same check, a distinct component with its own separate
  // listComplaints() call. Any real worker in this shared dev db has at least a few complaints
  // by now -- picks whichever row Manage Workers happens to show first. Navigated by URL
  // directly, not by clicking a labeled nav link -- the UI is now rendered in Hindi (the switch
  // above already took effect), so an English-text locator for the nav link wouldn't match. ---
  await page.goto("/admin/workers");
  await expect(page).toHaveURL(/\/admin\/workers$/);
  await page.locator("table tbody tr").first().locator("a").first().click();
  await expect(page).toHaveURL(/\/admin\/workers\/\d+$/);

  // Still "hi" from the check above (uiLang is browser-local-storage-scoped, not per-account --
  // see uiLang.tsx) -- Marathi here is what actually proves a real transition re-fetches.
  const workerDetailRefetch = page.waitForRequest(
    (req) => /\/complaints\?.*worker_id=/.test(req.url()) && req.url().includes("lang=mr"),
    { timeout: 15000 }
  );
  // Not getByLabel("Settings") here -- the UI is already in Hindi from the switch above, so the
  // Settings button's own aria-label is now "सेटिंग्स", not "Settings". It's reliably the LAST of
  // TopBar's two icon-btn buttons (ThemeToggle, then Settings -- see TopBar.tsx), which stays
  // true regardless of which language is currently active.
  await page.locator("button.icon-btn").last().click();
  await page.getByRole("button", { name: "मराठी" }).click();
  // Same reasoning as above -- "Save changes" is now "बदलावों को सुरक्षित करें" in the already-
  // Hindi UI. SettingsModal.tsx's own modal-actions row has exactly one btn-primary (Save); the
  // other action there (Logout) is btn-ghost.
  await page.locator(".modal-actions button.btn-primary").click();
  await expect(page.locator(".modal")).toHaveCount(0, { timeout: 8000 });
  await workerDetailRefetch;
});
