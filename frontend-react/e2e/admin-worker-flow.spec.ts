import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
// A fixed phone here would silently reuse whatever account already has that number across every
// run all session (including manual testing against the same dev db) — its preferred_language
// can drift from whatever it was first seeded with, which then changes what language the UI
// renders in and breaks assertions that expect English. A unique phone per run guarantees a
// fresh account seeded fresh every time.
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";
// A fixed ward name here would create a fresh duplicate "Ramesh Kadam" on the same ward every
// time this spec runs — harmless to the test itself, but confusing for real manual testing
// later (a citizen's complaint can get correctly reassigned to a *different* same-named
// duplicate after a rejection, and look on screen like nothing happened).
const WARD = `Ward 14 — Rukadi Road ${Date.now()}`;

test.beforeAll(() => {
  // Simulates how a real deployment provisions its first Super Admin: seeded
  // directly into the database, never through sign-up. --language defaults to "en" in
  // seed_admin.py, which the assertions below rely on.
  //
  // The python3 binary name isn't universal — Windows installs from python.org
  // only register "python", not "python3" — so pick per-platform.
  const pythonBin = process.platform === "win32" ? "python" : "python3";
  execSync(
    `${pythonBin} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Anjali Kulkarni"`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );
});

test("super admin creates a worker, who can then log in and see their (empty) queue", async ({ page }) => {
  const workerPhone = uniquePhone();

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(ADMIN_PHONE);
  await page.getByLabel("Password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/admin$/);
  await expect(page.getByText("Super Admin", { exact: true })).toBeVisible();

  // Worker management now lives on its own page (AdminDashboard.tsx links out to it rather than
  // embedding the worker table + "+ Add worker" inline).
  // Scoped to the dashboard's own button (class btn-ghost) -- a nav-drawer link with the same
  // text also exists on the page (see components/NavDrawer.tsx), so an unscoped role/name
  // locator is ambiguous.
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);

  await page.getByRole("button", { name: "+ Add worker" }).click();
  await page.getByLabel("Full name").fill("Ramesh Kadam");
  await page.getByLabel("Phone number").fill(workerPhone);
  await page.getByLabel("Temporary password").fill("workerpass123");
  await page.getByLabel("Confirm password").fill("workerpass123");
  await page.getByLabel("Assign to ward").fill(WARD);
  // The modal defaults to Marathi — pick English explicitly so the worker's dashboard (and the
  // assertions below) render in the language this test actually checks.
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "Add worker", exact: true }).click();

  // .first(): "Ramesh Kadam" as a name is still reused each run (the ward isn't), so the name
  // alone can still match older rows — .first() is the just-created one (table is newest-first).
  await expect(page.getByText("Ramesh Kadam").first()).toBeVisible();
  await expect(page.getByText(WARD)).toBeVisible();

  // Log out, then log in as the newly created worker.
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(workerPhone);
  await page.getByLabel("Password").fill("workerpass123");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page).toHaveURL(/\/worker$/);
  await expect(page.getByText(`Ward: ${WARD}`)).toBeVisible();
  await expect(page.getByText("Nothing here.")).toBeVisible();
});

test("a citizen cannot create a worker account (no such option exists)", async ({ page }) => {
  const phone = uniquePhone();
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Just A Citizen");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill("secret123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("secret123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // There is no "Add worker" button, no role picker, nothing — a citizen's
  // only actions are reporting and viewing their own complaints.
  await expect(page.getByRole("button", { name: "+ Add worker" })).toHaveCount(0);
  await expect(page.getByText(/Super Admin/i)).toHaveCount(0);
});
