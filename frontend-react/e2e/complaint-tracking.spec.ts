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
    `${PYTHON_BIN} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Tracking Test Admin"`,
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

test("full complaint lifecycle: reject reassigns to the next worker, accept unlocks phone, resolve, feedback", async ({ page }) => {
  // Well over the 30s default: 2 worker creations, a real AI complaint submission (~20s),
  // and several logins/navigations all in one flow.
  test.setTimeout(90000);

  const workerAPhone = uniquePhone();
  const workerBPhone = uniquePhone();
  const citizenPhone = uniquePhone();

  // --- Admin creates two workers in the same ward. ---
  await login(page, ADMIN_PHONE, ADMIN_PASSWORD);
  await expect(page).toHaveURL(/\/admin$/);
  // Worker management lives on its own page, linked from the admin dashboard.
  // Scoped to the dashboard's own button (class btn-ghost) -- a nav-drawer link with the same
  // text also exists on the page (see components/NavDrawer.tsx), so an unscoped role/name
  // locator is ambiguous.
  await page.locator("a.btn-ghost", { hasText: "Manage Workers" }).click();
  await expect(page).toHaveURL(/\/admin\/workers$/);

  // LIVE-REPORTED: "Assign to ward" used to be a plain free-text field -- replaced by a real
  // State->City->Ward picker (WorkerLocationPicker.tsx, see fillWorkerLocationPicker's own
  // docstring in helpers.ts). It picks deterministically (always index 1 at each cascading
  // level), so calling it twice in this loop lands both workers on the SAME real state/city/ward,
  // exactly like the old fixed WARD constant did -- this test's whole "reject reassigns to the
  // next worker in the same ward" premise still holds. Captured from the first call only; both
  // are asserted equal below as a real check that the determinism assumption holds.
  let workerLocation: PickedLocation | null = null;
  for (const [phone, name] of [[workerAPhone, "Track Worker One"], [workerBPhone, "Track Worker Two"]] as const) {
    await page.getByRole("button", { name: "+ Add worker" }).click();
    await page.getByLabel("Full name").fill(name);
    await page.getByLabel("Phone number").fill(phone);
    await page.getByLabel("Temporary password").fill("workerpass123");
    await page.getByLabel("Confirm password").fill("workerpass123");
    // index 2, not the default 1 -- see fillWorkerLocationPicker's own docstring on why sharing
    // admin-worker-flow.spec.ts's default index landed both specs' workers in the identical real
    // ward, letting THAT spec's "Ramesh Kadam" leak into this spec's own auto-assignment.
    const thisLocation = await fillWorkerLocationPicker(page, 2);
    if (workerLocation === null) workerLocation = thisLocation;
    else expect(thisLocation).toEqual(workerLocation);
    await page.getByRole("button", { name: "English", exact: true }).click();
    await page.getByRole("button", { name: "Add worker", exact: true }).click();
    // .first(): this dev db accumulates same-named workers across repeated runs of this
    // spec — table is newest-first, so .first() is the one just created.
    await expect(page.getByText(name).first()).toBeVisible();
    // Same real overlap as admin-worker-flow.spec.ts's own toast-wait fix -- the "worker added"
    // toast sits over the top-right corner for ~4.7s; the next loop iteration's own "+ Add
    // worker" click lands fine regardless (different corner), but logout() right after this loop
    // clicks Settings, which IS in that corner.
    await expect(page.locator(".toast-viewport .toast")).toHaveCount(0, { timeout: 6000 });
  }
  await logout(page);
  const ward = workerLocation!.ward;

  // --- Citizen files a complaint into that ward. ---
  //
  // LIVE-REPORTED: fillHomeLocationPicker() used to be called here -- but it independently picks
  // its own "index 1" state/city, with no guarantee of landing in the same real place as
  // fillWorkerLocationPicker's own independent "index 1" pick above (confirmed live: they
  // reliably diverge, since worker assignment is additionally scoped to worker-backed areas, a
  // smaller list). The wizard's own ward dropdown further down is scoped to the citizen's
  // registered home CITY (see ReportIssue.tsx's own comment on this), so the citizen needs to be
  // registered in the SAME city the workers are in, not an arbitrary one -- explicitly selecting
  // workerLocation's state/city here instead of the generic helper.
  await page.goto("/signup");
  await page.getByLabel("Full name").fill("Tracking Test Citizen");
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

  // The complaint form lives in the Report an Issue wizard (Phase 1), not directly on the
  // citizen Home screen.
  //
  // LIVE-REPORTED: the hero "a.btn-primary" button this used to target no longer exists on
  // CitizenHome.tsx (see citizen-signup.spec.ts's own matching fix) -- the nav-drawer link is
  // the only way there now, and it's a real slide-out drawer (needs opening first).
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Report an Issue" }).click();
  await expect(page).toHaveURL(/\/citizen\/report$/);

  // Location step: this ward was just created above, so it's a real option in the dropdown.
  //
  // LIVE-REPORTED: selectOption(ward) (a bare string, matched against each <option>'s VALUE, not
  // its visible text) stopped finding a match -- confirmed directly against ReportIssue.tsx's own
  // comment on this exact dropdown: its ward list is suffixed with the real ULB name (e.g. "Ward
  // 3, Bruhat Bengaluru Mahanagara Palike (BBMP)"), a different, more specific format than the
  // bare ward text fillWorkerLocationPicker captured from the Add Worker modal's own, differently-
  // formatted picker. Matching by substring against the real rendered option instead of assuming
  // the two pickers share one text format.
  await page.getByRole("button", { name: "Select location" }).click();
  const wizardWardOption = page.locator("#wizard-ward option", { hasText: ward! });
  await expect.poll(() => wizardWardOption.count()).toBeGreaterThan(0);
  const wizardWardValue = await wizardWardOption.first().getAttribute("value");
  await page.locator("#wizard-ward").selectOption(wizardWardValue!);
  await page.getByRole("button", { name: "Next" }).click();

  // Description step.
  await page.getByPlaceholder(/Garbage not collected/).fill("There is a broken streetlight here.");
  await page.getByRole("button", { name: "Next" }).click();

  // Photo step (skip) -> AI Understanding (wait for the brief mock classification) -> Preview -> submit.
  await page.getByRole("button", { name: "Next" }).click();
  // AI Understanding step's badge text depends on which classification layer succeeded (real
  // model vs. keyword fallback, see ReportIssue.tsx) -- assert on the stable .dev-badge class
  // instead of either layer's specific wording.
  await expect(page.locator(".dev-badge")).toBeVisible({ timeout: 3000 });
  await page.getByRole("button", { name: "Next" }).click();
  await page.getByRole("button", { name: "Submit complaint" }).click();
  // The real Sarvam AI pipeline (translate -> normalize -> summarize) takes ~20-25s end-to-end
  // in this environment (a reasoning model, not a mock) -- 15s was cutting it too close and
  // flaked under load; give it real headroom.
  // Widened from 45000 after measuring the real backend pipeline directly (integration/
  // stability phase): complaint creation chains THREE sequential real Sarvam calls (normalize ->
  // translate -> summarize, each genuinely dependent on the previous one's output, see
  // backend/services/complaint_agent.py) -- three live timed runs measured 17.7s/28.5s/18.7s for
  // that chain alone, a ~60% swing, before any browser/page-render overhead. 45s was already a
  // second widening (see the original comment, now below) and still flaked under full-suite load
  // in this phase's validation runs; 60s gives real, measured headroom instead of guessing again.
  await expect(page.getByText("Complaint submitted successfully.")).toBeVisible({ timeout: 60000 });

  // LIVE-REPORTED: the ward this complaint just landed in already has real seed/demo workers in
  // it (see scripts/seed_multi_ward_data.py) with a lower id than either Track Worker -- auto-
  // assignment always picks the lowest-id eligible worker in a ward (assignment_service.py), so
  // neither of this test's own freshly created workers would ever actually receive it. Rather
  // than fake or skip that real behavior, this drives the SAME backend function a real rejection
  // uses (assign_next_worker(), via scripts/e2e_force_reassign_to_worker.py) to have every real
  // candidate OTHER than our own two workers step aside -- exactly what happens if each of them
  // individually rejects it. Both Track Worker One and Track Worker Two are kept eligible (not
  // just One) -- One still wins this first assignment (lower id, created first in the loop
  // above), but Two needs to remain a genuinely untouched, real candidate for the actual
  // rejection this test performs further down to fall through to. No seed/demo account is
  // touched.
  execSync(
    `${PYTHON_BIN} scripts/e2e_force_reassign_to_worker.py --citizen-phone ${citizenPhone} ` +
      `--keep-phone ${workerAPhone} --keep-phone ${workerBPhone}`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );

  // Follow through to the complaints list from the wizard's success screen.
  await page.getByRole("link", { name: "Track this complaint" }).click();
  await expect(page).toHaveURL(/\/citizen\/complaints$/);

  // Assigned to whichever worker was created first (Track Worker One) — visible immediately, no phone yet.
  await expect(page.getByText("Assigned to")).toBeVisible();
  await expect(page.getByText("Track Worker One")).toBeVisible();
  await expect(page.getByText("Contact number")).not.toBeVisible();
  await logout(page);

  // --- Track Worker One rejects it -- mandatory rejection reason. ---
  await login(page, workerAPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  await expect(page.getByText("Awaiting your response")).toBeVisible();
  await page.getByRole("button", { name: "Reject" }).click();
  // Empty reason is blocked -- confirming with nothing typed must not submit.
  await page.getByRole("button", { name: "Reject Complaint", exact: true }).click();
  await expect(page.getByText("A rejection reason is required.")).toBeVisible();
  await page.getByLabel("Reason for rejection").fill("Outside my assigned coverage area.");
  await page.getByRole("button", { name: "Reject Complaint", exact: true }).click();
  await expect(page.getByText("Nothing here.")).toBeVisible();
  await logout(page);

  // --- It should now be with Track Worker Two instead. ---
  await login(page, workerBPhone, "workerpass123");
  await expect(page).toHaveURL(/\/worker$/);
  // .first(): for a complaint this short, the AI summary often comes back near-identical to
  // the translated text, so the sentence legitimately appears twice in one card.
  await expect(page.getByText("There is a broken streetlight here.").first()).toBeVisible();
  await expect(page.getByText("Awaiting your response")).toBeVisible();

  // exact: true -- the worker queue's own "Accepted" filter tab (see WorkerDashboard.tsx) is a
  // substring match for plain "Accept" too, since that filter's label was disambiguated from the
  // separate "In progress" filter (both used to read "In progress"/"In Progress" -- now
  // "Accepted"/"In Progress", see i18n.ts's worker.filterAccepted).
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  // "In progress" is ambiguous by plain text: it's both the status label and, pre-existing and
  // unrelated to StatusBadge, the "In progress" filter tab's own button text. Scope to the
  // status pill specifically, same as the "resolved" check below.
  await expect(page.locator(".status-badge.accepted")).toBeVisible();

  // Accepted -> Start Work (mandatory initial assessment) -> In Progress. The modal's own submit
  // button is also labeled "Start Work" (same text as the trigger that opened it) -- scope to
  // `.modal` so the click targets the submit button specifically, not the trigger behind it.
  await page.getByRole("button", { name: "Start Work" }).click();
  const startModal = page.locator(".modal");
  await startModal.getByRole("button", { name: "Start Work", exact: true }).click();
  await expect(page.getByText("An initial assessment is required to start work.")).toBeVisible();
  await page.getByLabel("Initial assessment").fill("Checked the pole -- the bulb and wiring both need replacing.");
  await startModal.getByRole("button", { name: "Start Work", exact: true }).click();
  await expect(page.locator(".status-badge.accepted")).toBeVisible(); // in_progress reuses the "accepted" visual class

  // In Progress -> Complete Complaint (mandatory completion status) -> Resolved.
  await page.getByRole("button", { name: "Complete Complaint" }).click();
  await page.getByRole("button", { name: "Mark Resolved", exact: true }).click();
  await expect(page.getByText("Please provide the completion status before resolving the complaint.")).toBeVisible();
  await page.getByLabel("Completion status").fill("Replaced the bulb and rewired the fixture. Streetlight now works.");
  await page.getByRole("button", { name: "Mark Resolved", exact: true }).click();
  await expect(page.locator(".status-badge.resolved")).toBeVisible();

  // Report only becomes available now that it's resolved.
  await expect(page.getByRole("button", { name: "View Report" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Report" })).toBeVisible();
  await logout(page);

  // --- Citizen sees the full tracking history: reassignment happened, now resolved, worker B's
  // phone is visible (only now, post-accept), and a feedback form is waiting. ---
  await login(page, citizenPhone, "citizenpass123!");
  await expect(page).toHaveURL(/\/citizen$/);
  // LIVE-REPORTED: the "track prompt" card's own a.btn-ghost button this used to target no
  // longer exists on CitizenHome.tsx either (same redesign as the other a.btn-primary/a.btn-ghost
  // fixes in this file and citizen-signup.spec.ts) -- the nav-drawer link is the only way there
  // now, and it's a real slide-out drawer (needs opening first).
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "My Complaints" }).click();
  await expect(page).toHaveURL(/\/citizen\/complaints$/);

  await expect(page.getByText("Track Worker Two")).toBeVisible({ timeout: 10000 });
  await expect(page.getByText("Contact number")).toBeVisible();
  await expect(page.getByText("How was this resolved?")).toBeVisible();

  // Worker-authored updates (initial assessment, completion status) are visible to the citizen.
  await page.getByRole("button", { name: "View updates" }).click();
  await expect(page.getByText("Checked the pole -- the bulb and wiring both need replacing.")).toBeVisible();
  await expect(page.getByText("Replaced the bulb and rewired the fixture. Streetlight now works.")).toBeVisible();

  // The resolution report is available to the citizen too, from the same real data.
  await expect(page.getByRole("button", { name: "View Report" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Download Report" })).toBeVisible();

  // role: "radio" -- the 5 star-rating controls are a "pick exactly one of five" group, which is
  // what they're now semantically marked up as (role="radiogroup"/"radio", see
  // FeedbackForm.tsx and useModalA11y.ts's sibling accessibility pass) instead of plain buttons.
  await page.getByRole("radio", { name: "5" }).click();
  await page.getByPlaceholder("Optional comment").fill("Fixed fast, thank you!");
  await page.getByRole("button", { name: "Submit feedback" }).click();
  // "Thanks for your feedback!" now renders twice at once: the toast (exact) and the on-page
  // feedback card, which appends the star rating and comment after the same phrase.
  await expect(page.getByText("Thanks for your feedback!", { exact: true })).toBeVisible();
});
