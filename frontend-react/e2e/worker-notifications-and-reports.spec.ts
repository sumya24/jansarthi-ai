import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySignupEmail, fillWorkerLocationPicker, uniqueEmail, uniquePhone, type PickedLocation } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const PYTHON_BIN = process.platform === "win32" ? "python" : "python3";
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";

test.beforeAll(() => {
  execSync(
    `${PYTHON_BIN} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Notif Test Admin"`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );
});

async function logout(page: import("@playwright/test").Page) {
  await page.getByLabel("Settings").click();
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
}

async function login(page: import("@playwright/test").Page, phone: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Phone number").fill(phone);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
}

test("worker notification bell, detail-page card rules per status, report visibility, and cross-worker authorization", async ({ page }) => {
  // One real AI complaint submission (~20-60s, see complaint-tracking.spec.ts's own comment for
  // the measured range) plus a full accept -> start -> update -> complete -> report journey and
  // a second worker's authorization check, all in one flow.
  test.setTimeout(150000);

  const workerPhone = uniquePhone();
  const otherWorkerPhone = uniquePhone();
  const citizenPhone = uniquePhone();

  // --- Admin creates a worker in this ward, plus an unrelated worker in a different ward (used
  // later to prove that worker can't reach this complaint's detail page or report at all). ---
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  // Scoped to the dashboard's own button (class btn-ghost) -- a nav-drawer link with the same
  // text also exists on the page (see components/NavDrawer.tsx), so an unscoped role/name
  // locator is ambiguous.
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);

  // LIVE-REPORTED: "Assign to ward" used to be a plain free-text field, letting this test invent
  // its own ward name and an "(unrelated)" sibling ward for the cross-worker authorization check
  // below. It's now WorkerLocationPicker.tsx's real State->City->Ward picker (only real,
  // worker-backed wards are offered) -- see helpers.ts's fillWorkerLocationPicker docstring.
  // Distinct ward indices just keep the two workers in genuinely different real wards (so Other
  // Worker's own cross-authorization check below is against a truly different ward, not the same
  // one); which real wards they land in no longer matters for who gets the FIRST assignment --
  // see the forced-reassignment call further down, after the complaint is filed.
  let workerLocation: PickedLocation | undefined;
  for (const [phone, name, wardIndex] of [
    [workerPhone, "Notif Worker", 1],
    [otherWorkerPhone, "Other Worker", 2],
  ] as const) {
    await page.getByRole("button", { name: "+ Add worker" }).click();
    await page.getByLabel("Full name").fill(name);
    await page.getByLabel("Phone number").fill(phone);
    await page.getByLabel("Temporary password").fill("workerpass123");
    await page.getByLabel("Confirm password").fill("workerpass123");
    const location = await fillWorkerLocationPicker(page, wardIndex);
    if (name === "Notif Worker") workerLocation = location;
    await page.getByRole("button", { name: "English", exact: true }).click();
    await page.getByRole("button", { name: "Add worker", exact: true }).click();
    await expect(page.getByText(name).first()).toBeVisible();
  }
  await logout(page);

  // --- Citizen files a complaint into that ward. ---
  //
  // LIVE-REPORTED: the wizard's own ward dropdown further down is scoped to the citizen's
  // registered home CITY (see ReportIssue.tsx's own comment on this) -- explicitly aligning the
  // citizen's signup city to the worker's real city instead of the generic fillHomeLocationPicker,
  // whose own independent "index 1" pick has no guarantee of landing in the same place (same
  // reasoning as complaint-tracking.spec.ts's identical fix).
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Notif Test Citizen");
  await page.getByLabel("Phone number").fill(citizenPhone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  const homeStateField = page.locator("#signup-home-state");
  await expect.poll(() => homeStateField.locator("option").count()).toBeGreaterThan(1);
  await homeStateField.selectOption({ label: workerLocation!.state });
  const homeCityField = page.locator("#signup-home-city");
  await expect.poll(() => homeCityField.isEnabled()).toBe(true);
  if ((await homeCityField.evaluate((el) => el.tagName)) === "SELECT") {
    await homeCityField.selectOption({ label: workerLocation!.city });
  } else {
    await homeCityField.fill(workerLocation!.city);
  }
  const homeWardField = page.locator("#signup-home-ward");
  await expect.poll(() => homeWardField.isEnabled()).toBe(true);
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
  // LIVE-REPORTED: selectOption(ward) (a bare string, matched against each <option>'s VALUE, not
  // its visible text) stopped finding a match -- this dropdown's ward list is suffixed with the
  // real ULB name (e.g. "Ward 3, Bruhat Bengaluru Mahanagara Palike (BBMP)"), a different, more
  // specific format than the bare ward text fillWorkerLocationPicker captured from the Add Worker
  // modal's own, differently-formatted picker. Matching by substring against the real rendered
  // option instead of assuming the two pickers share one text format (same fix as
  // complaint-tracking.spec.ts).
  const wizardWardOption = page.locator("#wizard-ward option", { hasText: workerLocation!.ward });
  await expect.poll(() => wizardWardOption.count()).toBeGreaterThan(0);
  const wizardWardValue = await wizardWardOption.first().getAttribute("value");
  await page.locator("#wizard-ward").selectOption(wizardWardValue!);
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByPlaceholder(/Garbage not collected/).fill("Overflowing garbage bin near the bus stop.");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  // AI Understanding step's badge text depends on which classification layer succeeded (real
  // model vs. keyword fallback, see ReportIssue.tsx) -- assert on the stable .dev-badge class
  // instead of either layer's specific wording.
  await expect(page.locator(".dev-badge")).toBeVisible({ timeout: 3000 });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();
  await expect(page.getByText("Complaint submitted successfully.")).toBeVisible({ timeout: 60000 });
  await logout(page);

  // LIVE-REPORTED: the ward this complaint just landed in already has real seed/demo workers in
  // it (see scripts/seed_multi_ward_data.py) with a lower id than "Notif Worker" -- auto-
  // assignment always picks the lowest-id eligible worker in a ward (assignment_service.py), so
  // the freshly created worker below would never actually receive it. Rather than fake or skip
  // that real behavior, this drives the SAME backend function a real rejection uses
  // (assign_next_worker(), via scripts/e2e_force_reassign_to_worker.py) to have every other real
  // candidate for this specific complaint step aside -- exactly what happens if each of them
  // individually rejects it -- until it lands on Notif Worker. No seed/demo account is touched.
  execSync(
    `${PYTHON_BIN} scripts/e2e_force_reassign_to_worker.py --citizen-phone ${citizenPhone} --keep-phone ${workerPhone}`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );

  // --- Worker logs in: a notification for the assignment is waiting, with an unread badge. ---
  //
  // LIVE-REPORTED: the notification title is "Complaint reassigned to you", not "New complaint
  // assigned" -- assign_next_worker() picks the title based on whether any ComplaintRejection
  // rows exist for this complaint (is_reassignment), and the forced-reassignment call above
  // legitimately created some (one per real seed worker it stepped aside), same as if each of
  // them had genuinely rejected it first. That's the real, correct title for what actually
  // happened here, not a workaround.
  await login(page, workerPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  const bell = page.getByLabel("Notifications");
  await expect(bell).toBeVisible();
  await expect(page.locator(".notif-badge")).toHaveText("1");

  await bell.click();
  await expect(page.getByText("Complaint reassigned to you")).toBeVisible();
  // Opening the panel must NOT itself mark anything read -- the badge must still read 1.
  await expect(page.locator(".notif-badge")).toHaveText("1");

  // Clicking the notification opens the complaint's detail page (never a dead end) and marks it read.
  await page.getByText("Complaint reassigned to you").click();
  await expect(page).toHaveURL(/\/worker\/complaints\/\d+$/);
  await expect(page.getByText("Overflowing garbage bin near the bus stop.").first()).toBeVisible();
  await bell.click();
  await expect(page.locator(".notif-badge")).not.toBeVisible();
  await bell.click(); // close the panel again

  const complaintUrl = page.url();

  // --- Card rules per status, checked on the detail page as the complaint moves through the
  // workflow: exactly the right actions for the current status, nothing else. ---
  await expect(page.getByRole("button", { name: "Accept" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reject" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start Work" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "View Report" })).not.toBeVisible();

  await page.getByRole("button", { name: "Accept" }).click();
  await expect(page.getByRole("button", { name: "Start Work" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Accept" })).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Reject" })).not.toBeVisible();

  // The modal's own submit button shares the trigger's exact text ("Start Work") -- scope to
  // `.modal` so the click lands on the submit button, not the trigger behind the overlay.
  await page.getByRole("button", { name: "Start Work" }).click();
  const modal = page.locator(".modal");
  await page.getByLabel("Initial assessment").fill("Bin is overflowing -- scheduling an extra pickup today.");
  await modal.getByRole("button", { name: "Start Work", exact: true }).click();
  await expect(page.getByRole("button", { name: "Add Update" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Complete Complaint" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start Work" })).not.toBeVisible();

  // Report must not be reachable before resolution -- no report section renders at all while
  // in_progress (WorkerComplaintDetail only renders it once status === "resolved"). Scoped with
  // exact:true -- ComplaintReportView's own internal heading ("Complaint Resolution Report")
  // contains this text as a substring once the section does render, later in this test.
  await expect(page.getByText("Resolution Report", { exact: true })).not.toBeVisible();

  // Optional progress update -- again, the modal's submit button shares the trigger's text.
  await page.getByRole("button", { name: "Add Update" }).click();
  // getByRole("textbox", ...) + exact -- plain getByLabel("Update") now also matches the modal's
  // own dialog element: it's accessible-named "Add Progress Update" via aria-labelledby (added
  // for real screen-reader users, see useModalA11y.ts), which contains "Update" as a substring.
  await page.getByRole("textbox", { name: "Update", exact: true }).fill("Extra pickup completed, bin is now empty.");
  await modal.getByRole("button", { name: "Add Update", exact: true }).click();
  await expect(page.getByText("Extra pickup completed, bin is now empty.")).toBeVisible();

  // Complete -- mandatory completion status.
  await page.getByRole("button", { name: "Complete Complaint" }).click();
  await page.getByRole("button", { name: "Mark Resolved", exact: true }).click();
  await expect(page.getByText("Please provide the completion status before resolving the complaint.")).toBeVisible();
  await page.getByLabel("Completion status").fill("Bin emptied and area cleaned up.");
  await page.getByRole("button", { name: "Mark Resolved", exact: true }).click();
  // .first(): the status badge legitimately appears twice on this detail page (header + timeline),
  // same reasoning as the .first() on the completion text below.
  await expect(page.locator(".status-badge.resolved").first()).toBeVisible();

  // Now, and only now, the report is available -- with the real data from this run. The detail
  // page renders it inline (no separate "View Report" click needed -- it's "the central place",
  // see WorkerComplaintDetail.tsx); the list-view card is what has a distinct "View Report" button.
  await expect(page.getByText("Resolution Report", { exact: true })).toBeVisible();
  // .first(): the completion text legitimately appears twice -- once in the Updates timeline,
  // once again in the Resolution Report section below it -- both drawing from the same real data.
  await expect(page.getByText("Bin emptied and area cleaned up.").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Report" })).toBeVisible();
  await logout(page);

  // --- A different worker (never assigned to this complaint) cannot view or download its
  // report by hitting the URL directly -- backend-enforced, not just a hidden frontend button. ---
  await login(page, otherWorkerPhone, "workerpass123");
  // Wait for the login itself to actually complete (token stored, redirected to /worker) before
  // navigating directly to the URL -- otherwise the goto() below races the login POST and can
  // hit the complaint page unauthenticated, which redirects to /login for the wrong reason.
  await expect(page).toHaveURL(/\/worker$/);
  await page.goto(complaintUrl);
  await expect(page.getByText(/isn't assigned to you|not found/i)).toBeVisible({ timeout: 10000 });
  await logout(page);
});

test("worker rejects a complaint: admin is notified and sees the reason, citizen sees nothing", async ({ page }) => {
  // One real AI complaint submission, same generous budget as the test above.
  test.setTimeout(150000);

  const workerPhone = uniquePhone();
  const citizenPhone = uniquePhone();
  const rejectionReason = "Confidential ops note -- outside my assigned area.";

  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);
  await page.getByRole("button", { name: "+ Add worker" }).click();
  await page.getByLabel("Full name").fill("Rejecting Worker");
  await page.getByLabel("Phone number").fill(workerPhone);
  await page.getByLabel("Temporary password").fill("workerpass123");
  await page.getByLabel("Confirm password").fill("workerpass123");
  // Which real ward this lands in doesn't matter for who gets the assignment -- see the forced-
  // reassignment call further down, after the complaint is filed.
  const workerLocation = await fillWorkerLocationPicker(page, 3);
  await page.getByRole("button", { name: "English", exact: true }).click();
  await page.getByRole("button", { name: "Add worker", exact: true }).click();
  await expect(page.getByText("Rejecting Worker").first()).toBeVisible();
  await logout(page);

  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Reject Flow Citizen");
  await page.getByLabel("Phone number").fill(citizenPhone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  const homeStateField = page.locator("#signup-home-state");
  await expect.poll(() => homeStateField.locator("option").count()).toBeGreaterThan(1);
  await homeStateField.selectOption({ label: workerLocation.state });
  const homeCityField = page.locator("#signup-home-city");
  await expect.poll(() => homeCityField.isEnabled()).toBe(true);
  if ((await homeCityField.evaluate((el) => el.tagName)) === "SELECT") {
    await homeCityField.selectOption({ label: workerLocation.city });
  } else {
    await homeCityField.fill(workerLocation.city);
  }
  const homeWardField = page.locator("#signup-home-ward");
  await expect.poll(() => homeWardField.isEnabled()).toBe(true);
  if ((await homeWardField.evaluate((el) => el.tagName)) === "SELECT") {
    await homeWardField.selectOption({ index: 1 });
  } else {
    await homeWardField.fill("Test Ward");
  }
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);
  await page.getByRole("button", { name: "Select location" }).click();
  const wizardWardOption = page.locator("#wizard-ward option", { hasText: workerLocation.ward });
  await expect.poll(() => wizardWardOption.count()).toBeGreaterThan(0);
  const wizardWardValue = await wizardWardOption.first().getAttribute("value");
  await page.locator("#wizard-ward").selectOption(wizardWardValue!);
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByPlaceholder(/Garbage not collected/).fill("Streetlight has been out for a week.");
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  // AI Understanding step's badge text depends on which classification layer succeeded (real
  // model vs. keyword fallback, see ReportIssue.tsx) -- assert on the stable .dev-badge class
  // instead of either layer's specific wording.
  await expect(page.locator(".dev-badge")).toBeVisible({ timeout: 3000 });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();
  await expect(page.getByText("Complaint submitted successfully.")).toBeVisible({ timeout: 60000 });
  await page.goto("/citizen/complaints");
  // Click the mono "JM-XXXXX" reference text specifically, not the outer .surface-card itself --
  // that card also renders an inline ComplaintTracker widget below the clickable summary row, so
  // clicking the outer card's geometric center (Playwright's default click point) can land on
  // that non-interactive tracker area and miss the actual onClick div entirely.
  await page.locator(".surface-card.hoverable.enter .mono").first().click();
  await expect(page).toHaveURL(/\/citizen\/complaints\/\d+$/);
  const complaintDetailUrl = page.url();
  const complaintId = complaintDetailUrl.match(/\/complaints\/(\d+)$/)![1];
  await logout(page);

  // LIVE-REPORTED: same real-seed-worker-wins-by-lowest-id issue as the sibling test above --
  // this test's own point is exercising the real "Reject" UI action as "Rejecting Worker"
  // specifically, so that worker needs to hold the INITIAL assignment, not a later one. See
  // scripts/e2e_force_reassign_to_worker.py's own docstring.
  execSync(
    `${PYTHON_BIN} scripts/e2e_force_reassign_to_worker.py --citizen-phone ${citizenPhone} --keep-phone ${workerPhone}`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );

  // --- Worker rejects, with a reason. Navigates by id directly (same complaint, worker-side
  // route) rather than matching the citizen's original text, which the AI pipeline may have
  // reworded on the way to `display_text`. ---
  await login(page, workerPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  await page.goto(`/worker/complaints/${complaintId}`);
  await page.getByRole("button", { name: "Reject" }).click();
  await page.getByLabel("Reason for rejection").fill(rejectionReason);
  await page.locator(".modal").getByRole("button", { name: "Reject Complaint", exact: true }).click();
  // No redirect on success (WorkerComplaintDetail.tsx just toasts and reloads the same page) --
  // and since no other worker exists in this ward, the reassignment sends it back to "pending"
  // with no assigned worker, so the reload's own re-fetch now 403s the same way the OTHER
  // worker's direct-URL attempt does later in the sibling test in this file.
  await expect(page.getByText(/isn't assigned to you|not found/i)).toBeVisible({ timeout: 10000 });
  await logout(page);

  // --- Admin sees the notification, and the rejection card with the real reason once they open it. ---
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  const bell = page.getByLabel("Notifications");
  await bell.click();
  await expect(page.getByText("A worker rejected a complaint")).toBeVisible();
  await page.getByText("A worker rejected a complaint").click();
  await expect(page).toHaveURL(/\/admin\/complaints\/\d+$/);
  await expect(page.getByText("Rejection History")).toBeVisible();
  // .first(): this dev db accumulates same-named "Rejecting Worker" accounts across repeated
  // runs of this spec (a real earlier one can be a genuine candidate this run's own
  // force-reassign step displaces, logging its own "Rejected by: Rejecting Worker" entry too) --
  // same reasoning as this file's own worker-table check above.
  await expect(page.getByText("Rejecting Worker").first()).toBeVisible();
  await expect(page.getByText(rejectionReason)).toBeVisible();
  await logout(page);

  // --- Citizen sees nothing about the rejection -- just the same generic pending state. ---
  await login(page, citizenPhone, "citizenpass123!");
  await page.goto(complaintDetailUrl);
  await expect(page.getByText(rejectionReason)).not.toBeVisible();
  await expect(page.getByText("Rejecting Worker")).not.toBeVisible();
});
