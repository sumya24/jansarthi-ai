import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import CategoryBadge from "../components/CategoryBadge";
import ReportModal from "../components/ReportModal";
import SummaryModal from "../components/SummaryModal";
import DownloadReportButton from "../components/DownloadReportButton";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { formatDate, t } from "../lib/i18n";
import { localizeWardText } from "../lib/locationNames";
import { api, ApiError, type Complaint, type ComplaintStatus, type WorkerSummary } from "../lib/api";
import SearchWithDateFilter from "../components/SearchWithDateFilter";
import "../styles/dashboard.css";

// LIVE-REPORTED GAP: this page used to fetch and render EVERY complaint ever assigned to this
// worker in one response, then filter/paginate all of it client-side -- same gap the other
// dashboards had (see CitizenDashboard.tsx's identical note). Status filter, search, and
// pagination are now real backend queries (GET /complaints' own `status`/`search`/`page`/
// `page_size` params, scoped by `worker_id`).
const WORKER_DETAIL_PAGE_SIZE = 15;

// Same reuse-existing-labels approach as AdminDashboard.tsx's own copy of this map -- see that
// file for why (avoids a near-duplicate string for "In progress" under a new key name).
const COMPLAINT_STATUS_LABEL_KEY: Record<ComplaintStatus | "open", string> = {
  // See AdminDashboard.tsx's own copy of this map for why "open" needs an explicit entry --
  // without one, StatusBadge rendered no label text at all for a complaint sitting in that state.
  open: "citizen.trackSubmitted",
  pending: "admin.pendingStat",
  assigned: "admin.filterAssigned",
  accepted: "admin.filterAccepted",
  in_progress: "citizen.trackInProgress",
  resolved: "admin.resolvedStat",
};

// No "pending" chip here (unlike AdminDashboard.tsx's own filter row) -- every complaint on
// THIS page already has `assigned_worker_id` set to this specific worker (see the `worker_id`
// query param in load() below), so "pending" (unassigned) can never appear in this list.
const WORKER_COMPLAINT_FILTERS = ["all", "assigned", "accepted", "in_progress", "resolved"] as const;
type WorkerComplaintFilter = (typeof WORKER_COMPLAINT_FILTERS)[number];

/** A single worker's full performance record for the super admin: how many complaints are at
 * each stage of their own queue, plus a "View Report"/"Download" action on every RESOLVED one --
 * the same report a citizen/the worker themself can already see (GET /complaints/{id}/report is
 * already admin-visible for any complaint, see routes/complaints.py's _get_visible_complaint) --
 * this page is just the first place an admin can find that link for a SPECIFIC worker's own
 * history, for performance review, without hunting through the main Complaints table. Reached by
 * clicking a worker's name on AdminWorkers.tsx. */
export default function AdminWorkerDetail() {
  const { id } = useParams<{ id: string }>();
  const workerId = Number(id);
  const { token } = useAuth();
  const { lang } = useUiLang();

  const [worker, setWorker] = useState<WorkerSummary | null>(null);
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportTargetId, setReportTargetId] = useState<number | null>(null);
  const [summaryComplaint, setSummaryComplaint] = useState<Complaint | null>(null);
  const [filter, setFilter] = useState<WorkerComplaintFilter>("all");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  // Decoupled from `complaints` (the filtered+paged current-page list) on purpose -- these four
  // chip counts are meant to read as "this worker's whole history," not "whatever's on the
  // current page" -- same reasoning as every other dashboard's stat cards/chip counts.
  const [statusCounts, setStatusCounts] = useState<Record<Exclude<WorkerComplaintFilter, "all">, number>>({
    assigned: 0, accepted: 0, in_progress: 0, resolved: 0,
  });
  const debouncedSearch = useDebouncedValue(search);

  // `loading` only gates the page's initial skeleton (before the FIRST fetch resolves) -- a later
  // reload (paging, filter chip, search, or picking a date in SearchWithDateFilter) must not flip
  // it back to true, since the whole complaints section (including the search bar) sits behind
  // `!loading` further down. Without this split, entering a date immediately re-triggered
  // `load()`, which unmounted that section -- including the open date popover the admin was still
  // typing into -- for the fetch's duration. Same fix as AdminWorkers.tsx's/
  // AdminAiMonitoring.tsx's own isFirstLoad ref.
  const isFirstLoad = useRef(true);

  async function load() {
    if (!token || !Number.isFinite(workerId)) return;
    if (isFirstLoad.current) setLoading(true);
    setLoadError(null);
    try {
      // No single-worker endpoint exists yet -- the worker list is small enough (this app's own
      // scale, matching GET /admin/workers' own aggregation-in-Python precedent) that fetching
      // it and finding the one row is simpler than adding a new endpoint for one lookup.
      const [workers, workerComplaints] = await Promise.all([
        api.listWorkers(token),
        api.listComplaints(token, {
          lang,
          workerId,
          status: filter === "all" ? undefined : filter,
          search: debouncedSearch || undefined,
          dateFrom: dateFrom || undefined,
          dateTo: dateTo || undefined,
          page,
          pageSize: WORKER_DETAIL_PAGE_SIZE,
        }),
      ]);
      setWorker(workers.items.find((w) => w.id === workerId) ?? null);
      setComplaints(workerComplaints.items);
      setTotal(workerComplaints.total);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "admin.errLoadFailed"));
    } finally {
      if (isFirstLoad.current) {
        setLoading(false);
        isFirstLoad.current = false;
      }
    }
  }

  async function loadStats() {
    if (!token || !Number.isFinite(workerId)) return;
    try {
      const statuses = ["assigned", "accepted", "in_progress", "resolved"] as const;
      const results = await Promise.all(
        statuses.map((s) => api.listComplaints(token, { workerId, status: s, page: 1, pageSize: 1 }))
      );
      const counts = {} as Record<(typeof statuses)[number], number>;
      statuses.forEach((s, i) => { counts[s] = results[i].total; });
      setStatusCounts(counts);
    } catch {
      // Non-critical -- the chip counts just keep their last known values on a transient failure.
    }
  }

  // A search edit or filter-chip click always jumps back to page 1 -- the previous page number
  // almost never still makes sense against a newly-narrowed result set.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filter, dateFrom, dateTo]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, lang, workerId, filter, debouncedSearch, dateFrom, dateTo, page]);

  useEffect(() => {
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, workerId]);

  const pageCount = Math.max(1, Math.ceil(total / WORKER_DETAIL_PAGE_SIZE));
  const totalAll = statusCounts.assigned + statusCounts.accepted + statusCounts.in_progress + statusCounts.resolved;

  return (
    <div>
      <TopBar />
      <div className="page-admin" id="main-content">
        <div className="page-head">
          <div>
            <Link to="/admin/workers" style={{ fontSize: 12.5, color: "var(--ink-2)", display: "inline-block", marginBottom: 8 }}>
              {t(lang, "admin.backToWorkers")}
            </Link>
            <h1 className="page-title display">{worker ? worker.full_name : t(lang, "admin.workerDetailTitle")}</h1>
            {worker && (
              <p className="page-sub">
                {worker.phone} · {worker.ward ? localizeWardText(worker.ward, lang) : "—"} · {worker.preferred_language}
              </p>
            )}
          </div>
        </div>

        {loadError && <div className="banner-error">{loadError}</div>}
        {loading && (
          <div className="surface-card" style={{ padding: 18 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ width: "100%", height: 18, marginBottom: i < 2 ? 14 : 0 }} />
            ))}
          </div>
        )}

        {!loading && !worker && !loadError && <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.workerNotFound")}</p>}

        {!loading && worker && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12, marginBottom: 30 }}>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.filterAssigned")}</div>
                <div className="display stat-value" style={{ color: "var(--status-open)" }}>{statusCounts.assigned}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.filterAccepted")}</div>
                <div className="display stat-value" style={{ color: "var(--status-open)" }}>{statusCounts.accepted}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "citizen.trackInProgress")}</div>
                <div className="display stat-value" style={{ color: "var(--status-open)" }}>{statusCounts.in_progress}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.resolvedStat")}</div>
                <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{statusCounts.resolved}</div>
              </div>
            </div>

            <div className="section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
              <span>{t(lang, "admin.complaintsSection")}</span>
              {totalAll > 0 && (
                <SearchWithDateFilter
                  searchValue={search}
                  onSearchChange={setSearch}
                  searchPlaceholder={t(lang, "admin.searchComplaintsAndWorkers")}
                  dateFrom={dateFrom}
                  dateTo={dateTo}
                  onDateFromChange={setDateFrom}
                  onDateToChange={setDateTo}
                  lang={lang}
                  width={320}
                />
              )}
            </div>

            {totalAll > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
                {WORKER_COMPLAINT_FILTERS.map((f) => {
                  const count = f === "all" ? totalAll : statusCounts[f];
                  const labelKey = f === "all" ? "admin.filterAll" : COMPLAINT_STATUS_LABEL_KEY[f];
                  const active = filter === f;
                  return (
                    <button
                      key={f}
                      className={`filter-chip btn btn-sm ${active ? "btn-primary" : "btn-ghost"}`}
                      onClick={() => setFilter(f)}
                    >
                      {t(lang, labelKey)}
                      <span className="mono" style={{ opacity: 0.75, marginLeft: 2 }}>
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            {totalAll === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noWorkerComplaints")}</p>
            ) : total === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noComplaintsFiltered")}</p>
            ) : (
              <div className="surface-card table-scroll" style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 760 }}>
                  <thead>
                    <tr>
                      {[
                        t(lang, "admin.colId"),
                        t(lang, "admin.colSummary"),
                        t(lang, "admin.colCategory"),
                        t(lang, "admin.colStatus"),
                        t(lang, "admin.colCreated"),
                        "",
                      ].map((h, i) => (
                        <th key={i} style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {complaints.map((c, i) => (
                      <tr key={c.id} className="table-row-hover enter" style={{ "--stagger": Math.min(i, 6) } as React.CSSProperties}>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <Link to={`/admin/complaints/${c.id}`} style={{ color: "var(--accent-fg)" }}>#{c.id}</Link>
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          <Link to={`/admin/complaints/${c.id}`} style={{ color: "inherit" }}>{c.display_summary || c.summary}</Link>
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <CategoryBadge category={c.service_category} lang={lang} />
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <StatusBadge status={c.status} label={t(lang, COMPLAINT_STATUS_LABEL_KEY[c.status])} />
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>
                          {formatDate(c.created_at, lang)}
                        </td>
                        <td style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                          {c.status === "resolved" && (
                            <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                              <button className="btn btn-ghost btn-sm" onClick={() => setSummaryComplaint(c)}>
                                {t(lang, "worker.viewSummary")}
                              </button>
                              <button className="btn btn-ghost btn-sm" onClick={() => setReportTargetId(c.id)}>
                                {t(lang, "worker.viewReport")}
                              </button>
                              <DownloadReportButton complaintId={c.id} className="btn btn-ghost btn-sm" />
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {pageCount > 1 && (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderTop: "1px solid var(--line)" }}>
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                    >
                      {t(lang, "admin.paginationPrev")}
                    </button>
                    <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
                      {page} / {pageCount}
                    </span>
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={page >= pageCount}
                      onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                    >
                      {t(lang, "admin.paginationNext")}
                    </button>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {reportTargetId !== null && <ReportModal complaintId={reportTargetId} onClose={() => setReportTargetId(null)} />}
      {summaryComplaint && (
        <SummaryModal
          complaint={summaryComplaint}
          statusLabel={t(lang, COMPLAINT_STATUS_LABEL_KEY[summaryComplaint.status])}
          onClose={() => setSummaryComplaint(null)}
        />
      )}
    </div>
  );
}
