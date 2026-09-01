import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { uniquePhone } from "./helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "../..");
// Same reasoning as admin-worker-flow.spec.ts: a fresh seeded admin per run, never a fixed
// phone, so this never silently reuses a stale account across runs/manual testing.
const ADMIN_PHONE = uniquePhone();
const ADMIN_PASSWORD = "adminpass123";

test.beforeAll(() => {
  const pythonBin = process.platform === "win32" ? "python" : "python3";
  execSync(
    `${pythonBin} scripts/seed_admin.py --phone ${ADMIN_PHONE} --password ${ADMIN_PASSWORD} --name "Priya Nair"`,
    { cwd: REPO_ROOT, stdio: "pipe" }
  );
});

/** E2E coverage for the Admin AI Monitoring page against the REAL backend (GET
 * /admin/ai-monitoring + /admin/ai-monitoring/requests) — not a mock. Both endpoints are
 * sourced entirely from the app's own ai_request_logs table (see AdminAiMonitoring.tsx's
 * docstring and docs/ask_sarthi_langsmith_observability.md), so this test asserts on labeled
 * structure and real, already-logged data from earlier tests in this suite — never on specific
 * counts, since those grow as the suite runs. */
test("admin can view AI Monitoring: real request stats, breakdown, and a recent-requests table", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Phone number").fill(ADMIN_PHONE);
  await page.getByLabel("Password", { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/admin$/);

  // Scoped to the dashboard's own button -- a nav-drawer link with the same text also exists on
  // the page (see components/NavDrawer.tsx), so an unscoped role/name locator is ambiguous.
  await page.locator("a.btn-ghost", { hasText: /AI Monitoring/i }).click();
  await expect(page).toHaveURL(/\/admin\/ai-monitoring$/);

  // No load failure against the real backend.
  await expect(page.locator(".banner-error")).toHaveCount(0);

  // The 5 top-line stat tiles render with real (not hardcoded) numeric text. Sentence case,
  // matching src/lib/i18n.ts's actual strings exactly -- the visual all-caps look is CSS
  // text-transform only, the underlying text content is lowercase after the first word.
  for (const label of ["Total requests", "Successful", "Failed", "Error rate", "Avg. latency"]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }

  // The 5 intent-breakdown tiles. .first(): "RAG"/"Complaints" etc. also appear as literal
  // `routed_to` values in the recent-requests table further down the same page (real data, not
  // a selector accident) -- the breakdown tile itself is unambiguously first in document order,
  // since the tiles render above the table.
  for (const label of ["RAG", "Complaints", "Status checks", "Out of scope", "Clarifications"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }

  await expect(page.getByText("Recent requests", { exact: true })).toBeVisible();

  // By the time this test runs, earlier specs in the suite (ask-sarthi.spec.ts etc.) have
  // already made real /ask-sarthi calls, so there should be at least one logged row -- assert
  // the table's real structure, not a specific row count.
  const table = page.locator("table");
  await expect(table).toBeVisible();
  for (const header of ["Time", "Route", "Intent", "Latency", "Status", "Trace"]) {
    await expect(table.getByText(header, { exact: true })).toBeVisible();
  }
  const rows = table.locator("tbody tr");
  await expect(rows.first()).toBeVisible();

  // trace_url is only non-null when LANGSMITH_TRACE_URL_TEMPLATE is configured for that
  // request (see backend/routes/admin.py) -- assert each row shows one of the two honest
  // states, never assert which, since that depends on env config this test doesn't control.
  //
  // LIVE-REPORTED: a trailing "Delete request" action column was added after Trace (an empty
  // <th /> in the header, see AdminAiMonitoring.tsx) -- .last() on <td> now grabs that action
  // cell instead of Trace. Matching directly on the Trace text itself instead of a positional
  // offset from the end, so this doesn't silently break again the next time a column is added.
  const firstRowTraceCell = rows.first().getByText(/View trace|Not linked/);
  await expect(firstRowTraceCell).toBeVisible();

  // "Back to dashboard" returns to /admin, confirming the two admin sections are actually
  // linked both ways, not a dead end.
  await page.getByRole("link", { name: /Back to dashboard/i }).click();
  await expect(page).toHaveURL(/\/admin$/);
});
