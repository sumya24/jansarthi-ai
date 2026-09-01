import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySignupEmail, fillHomeLocationPicker, fillWorkerLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
// A fixed phone here would silently reuse whatever account already has that number across every
// run all session (including manual testing against the same dev db) — its preferred_language
// can drift from whatever it was first seeded with, which then changes what language the UI
// renders in and breaks assertions that expect English. A unique phone per run guarantees a
// fresh account seeded fresh every time.
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";

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
  // LIVE-REPORTED: this test now does noticeably more real backend round-trips than when it was
  // written -- fillWorkerLocationPicker (see helpers.ts) makes 3 real, sequential network calls
  // (state list, then city list, then ward list, each cascading off the previous choice) that
  // the old plain free-text ward field never needed. Confirmed directly: the exact same steps run
  // by hand comfortably finish well under 30s, but the full test (this flow + the two
  // getByText(...).toBeVisible() assertions right after, waiting on the worker table's own
  // refresh) was intermittently exceeding the default 30s test timeout, failing on the LATER
  // Settings/Log out click even though that step itself works instantly once reached -- the
  // budget was just gone by the time the test got there.
  test.setTimeout(60000);
  const workerPhone = uniquePhone();

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(ADMIN_PHONE);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN_PASSWORD);
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
  // Real State->City->Ward picker (WorkerLocationPicker.tsx), replacing what used to be a plain
  // free-text "Assign to ward" field -- see fillWorkerLocationPicker's own docstring. Only a
  // real, seeded ward can be assigned now, so the exact ward text is whatever this helper
  // actually picked, not a value this test can decide up front.
  const { ward } = await fillWorkerLocationPicker(page);
  // The modal defaults to Marathi — pick English explicitly so the worker's dashboard (and the
  // assertions below) render in the language this test actually checks.
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "Add worker", exact: true }).click();

  // .first(): "Ramesh Kadam" as a name is still reused each run (the ward is a real, shared,
  // pre-existing one too now), so either alone can still match older rows — .first() is the
  // just-created one (table is newest-first).
  await expect(page.getByText("Ramesh Kadam").first()).toBeVisible();
  await expect(page.getByText(ward).first()).toBeVisible();

  // LIVE-REPORTED, real overlap (not just a test artifact): toasts render position: fixed, top
  // right, z-index 200 (see global.css's .toast-viewport) -- the exact corner Settings/
  // Notifications/Theme live in, with real pointer-events while shown. The "worker added" toast
  // from the submit above was still covering Settings, blocking the click below (a real admin
  // clicking that fast would hit the same thing) -- toast.tsx auto-dismisses after 4.5s + a 220ms
  // leave animation, so wait for it to actually be gone rather than racing it.
  await expect(page.locator(".toast-viewport .toast")).toHaveCount(0, { timeout: 6000 });

  // Log out, then log in as the newly created worker.
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);

  await page.goto("/login");
  await page.getByLabel("Phone number").fill(workerPhone);
  await page.getByLabel("Password", { exact: true }).fill("workerpass123");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page).toHaveURL(/\/worker$/);
  await expect(page.getByText(`Ward: ${ward}`)).toBeVisible();
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
