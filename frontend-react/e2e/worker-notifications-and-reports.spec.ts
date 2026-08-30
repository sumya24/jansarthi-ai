import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySignupEmail, fillHomeLocationPicker, uniqueEmail, uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";
const WARD = `Notif Test Ward ${Date.now()}`;

test.beforeAll(() => {
  const pythonBin = process.platform === "win32" ? "python" : "python3";
  execSync(
    `${pythonBin} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Notif Test Admin"`,
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

  for (const [phone, name, ward] of [
    [workerPhone, "Notif Worker", WARD],
    [otherWorkerPhone, "Other Worker", `${WARD} (unrelated)`],
  ] as const) {
    await page.getByRole("button", { name: "+ Add worker" }).click();
    await page.getByLabel("Full name").fill(name);
    await page.getByLabel("Phone number").fill(phone);
    await page.getByLabel("Temporary password").fill("workerpass123");
    await page.getByLabel("Confirm password").fill("workerpass123");
    await page.getByLabel("Assign to ward").fill(ward);
    await page.getByRole("button", { name: "English", exact: true }).click();
    await page.getByRole("button", { name: "Add worker", exact: true }).click();
    await expect(page.getByText(name).first()).toBeVisible();
  }
  await logout(page);

  // --- Citizen files a complaint into that ward. ---
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Notif Test Citizen");
  await page.getByLabel("Phone number").fill(citizenPhone);
  await page.getByLabel("Password", { exact: true }).fill("citizenpass123!");
  await page.getByLabel("Email address").fill(uniqueEmail());
  await page.locator("#signup-confirm-password").fill("citizenpass123!");
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  // Scoped to the hero's own primary button -- a nav-drawer link with the same text also exists
  // on the page (see components/NavDrawer.tsx), so an unscoped role/name locator is ambiguous.
  await page.locator("a.btn-primary", { hasText: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);
  await page.getByRole("button", { name: "Select location" }).click();
  await page.locator("#wizard-ward").selectOption(WARD);
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

  // --- Worker logs in: a notification for the new assignment is waiting, with an unread badge. ---
  await login(page, workerPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  const bell = page.getByLabel("Notifications");
  await expect(bell).toBeVisible();
  await expect(page.locator(".notif-badge")).toHaveText("1");

  await bell.click();
  await expect(page.getByText("New complaint assigned")).toBeVisible();
  // Opening the panel must NOT itself mark anything read -- the badge must still read 1.
  await expect(page.locator(".notif-badge")).toHaveText("1");

  // Clicking the notification opens the complaint's detail page (never a dead end) and marks it read.
  await page.getByText("New complaint assigned").click();
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
  await page.getByLabel("Assign to ward").fill(WARD);
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
  await fillHomeLocationPicker(page);
  await verifySignupEmail(page);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/citizen$/);

  await page.locator("a.btn-primary", { hasText: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);
  await page.getByRole("button", { name: "Select location" }).click();
  await page.locator("#wizard-ward").selectOption(WARD);
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
  await expect(page.getByText("Rejecting Worker")).toBeVisible();
  await expect(page.getByText(rejectionReason)).toBeVisible();
  await logout(page);

  // --- Citizen sees nothing about the rejection -- just the same generic pending state. ---
  await login(page, citizenPhone, "citizenpass123!");
  await page.goto(complaintDetailUrl);
  await expect(page.getByText(rejectionReason)).not.toBeVisible();
  await expect(page.getByText("Rejecting Worker")).not.toBeVisible();
});
