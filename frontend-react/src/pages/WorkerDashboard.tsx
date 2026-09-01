import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import CategoryBadge from "../components/CategoryBadge";
import RejectComplaintModal from "../components/RejectComplaintModal";
import StartWorkModal from "../components/StartWorkModal";
import ProgressUpdateModal from "../components/ProgressUpdateModal";
import CompleteComplaintModal from "../components/CompleteComplaintModal";
import ReportModal from "../components/ReportModal";
import SummaryModal from "../components/SummaryModal";
import DownloadReportButton from "../components/DownloadReportButton";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint, type DailyComplaintTrend, type ServiceStatusCount } from "../lib/api";
import { useToast } from "../lib/toast";
import SearchWithDateFilter from "../components/SearchWithDateFilter";
import ServiceDonutPanel from "../components/ServiceDonutPanel";
import ComplaintsTrendChart from "../components/ComplaintsTrendChart";
import ResolutionRateGauge from "../components/ResolutionRateGauge";
import "../styles/dashboard.css";

// LIVE-REPORTED GAP: this queue used to fetch and render EVERY complaint assigned to this worker
// in one flat column -- the "all/assigned/accepted/..." filter chips already existed, but only
// ever filtered a fully-fetched-in-one-go array client-side, with no search and no pagination at
// all. Status filter, search, and pagination are now real backend queries (GET /complaints' own
// `status`/`search`/`page`/`page_size` params), same as the Admin/Citizen dashboards and "My
// Area" -- see CitizenDashboard.tsx's own copy of this note for the fuller rationale.
const WORKER_PAGE_SIZE = 10;

const STATUS_LABEL_KEY = {
  // Same reasoning as "pending" below -- a worker shouldn't normally see a complaint still
  // sitting at "open" either, but without an explicit entry StatusBadge rendered no label text
  // at all (just its icon) on the rare complaint that does.
  open: "worker.filterAssigned",
  pending: "worker.filterAssigned", // a worker never actually sees "pending" (unassigned) items
  assigned: "worker.statusAssigned",
  accepted: "worker.statusAccepted",
  // Reuses the same "In progress" wording already shown for "accepted" -- both read as "work is
  // happening" from the worker's own point of view.
  in_progress: "worker.statusAccepted",
  resolved: "worker.resolved",
} as const;

type ActionModal = "reject" | "start" | "update" | "complete" | "report" | "summary" | null;

export default function WorkerDashboard() {
  const { user, token } = useAuth();
  const { lang } = useUiLang();
  const navigate = useNavigate();
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const toast = useToast();
  const [actingId, setActingId] = useState<number | null>(null);
  // Defaults to "all", not "assigned" — accepting a complaint moves it to "accepted", which
  // would otherwise make it silently vanish from a narrower default filter right after acting
  // on it (confusing: "where did it go?"). The worker can still narrow down manually.
  const [filter, setFilter] = useState<"all" | "assigned" | "accepted" | "in_progress" | "resolved">("all");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  // Decoupled from `complaints` (the filtered+paged current-page list) on purpose -- see
  // CitizenDashboard.tsx's identical stat cards for the fuller rationale.
  const [totalCount, setTotalCount] = useState(0);
  const [resolvedCount, setResolvedCount] = useState(0);
  // The 3 mutually-exclusive status buckets that make up totalCount (assignedCount +
  // inProgressCount + resolvedCount === totalCount), plus a real all-time rejection tally --
  // NOT a 4th bucket of the same total (a rejection doesn't change the complaint's status, it
  // just sends it back to pending/reassignment -- see backend/routes/complaints.py's
  // complaints_trend docstring), shown alongside the others in the Resolution Rate card anyway
  // since it's a real, meaningful count of this worker's own past decisions.
  const [assignedCount, setAssignedCount] = useState(0);
  const [inProgressCount, setInProgressCount] = useState(0);
  const [rejectedCount, setRejectedCount] = useState(0);
  // Decoupled from `loading`/search/filter for the same reason as totalCount/resolvedCount above
  // -- this worker's own service breakdown should read as "your whole queue," not flicker every
  // time a search/date/page change re-triggers `load()`'s own loading flag.
  const [serviceRows, setServiceRows] = useState<ServiceStatusCount[]>([]);
  // Decoupled from `loading`/search/filter for the same reason as totalCount/resolvedCount above
  // -- feeds the "Opened vs. resolved" chart, which should read as "the last 7 real days," not
  // flicker every time a search/date/page change re-triggers `load()`'s own loading flag.
  const [trendData, setTrendData] = useState<DailyComplaintTrend[]>([]);
  const debouncedSearch = useDebouncedValue(search);

  // Which complaint has an action modal open, and which modal -- one at a time, keyed by
  // complaint id so a re-render of the list (e.g. after a poll/reload) doesn't lose track of
  // which card's modal is showing.
  const [modalFor, setModalFor] = useState<{ id: number; type: ActionModal } | null>(null);

  // LIVE-REPORTED, real race (not test flakiness -- confirmed directly, see below): two `load()`
  // calls can be in flight at once -- the filter/page/search-driven effect below and accept()'s
  // own explicit `reload()` right after a status change both call it, and nothing stops an
  // EARLIER-issued call (still reflecting the complaint's PRE-accept status) from resolving
  // AFTER a later one (reflecting the just-accepted status), silently overwriting the fresh data
  // with stale data purely by network/scheduling luck. Confirmed live with direct instrumentation:
  // accept()'s own reload() fetched and set status "accepted" correctly every time, yet the UI
  // still occasionally never showed it -- exactly this stale-overwrite pattern, not a bug in
  // accept() or reload() themselves. `loadRequestIdRef` tags each call with a strictly-increasing
  // id and only ever commits state from the MOST RECENTLY ISSUED call, so a late-arriving stale
  // response is silently discarded instead of winning the race.
  const loadRequestIdRef = useRef(0);

  async function load() {
    if (!token) return;
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.listComplaints(token, {
        lang,
        status: filter === "all" ? undefined : filter,
        search: debouncedSearch || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        page,
        pageSize: WORKER_PAGE_SIZE,
      });
      if (requestId !== loadRequestIdRef.current) return; // superseded by a newer load() -- discard
      setComplaints(data.items);
      setTotal(data.total);
    } catch (err) {
      if (requestId !== loadRequestIdRef.current) return; // superseded -- discard this error too
      setLoadError(err instanceof ApiError ? err.message : t(lang, "worker.errLoadFailed"));
    } finally {
      if (requestId === loadRequestIdRef.current) setLoading(false);
    }
  }

  async function loadStats() {
    if (!token) return;
    try {
      const [all, resolved, assigned, inProgress] = await Promise.all([
        api.listComplaints(token, { page: 1, pageSize: 1 }),
        api.listComplaints(token, { status: "resolved", page: 1, pageSize: 1 }),
        api.listComplaints(token, { status: "assigned", page: 1, pageSize: 1 }),
        // Comma-separated multi-status filter -- same syntax the status filter chips above
        // already send (see backend/routes/complaints.py's _parse_status_filter).
        api.listComplaints(token, { status: "accepted,in_progress", page: 1, pageSize: 1 }),
      ]);
      setTotalCount(all.total);
      setResolvedCount(resolved.total);
      setAssignedCount(assigned.total);
      setInProgressCount(inProgress.total);
    } catch {
      // Non-critical -- the stat cards just keep their last known values on a transient failure.
    }
  }

  async function loadRejectedTotal() {
    if (!token) return;
    try {
      // Reuses the daily trend endpoint rather than a dedicated summary one -- a rejection isn't
      // a status, so it can't be counted via listComplaints' status filter like the others above;
      // this is the only endpoint that already queries ComplaintRejection scoped to this worker.
      // `days: 3650` is a practical "since the start of this dataset" window (the earliest real
      // row here is from 2026, well under 10 years back), not a literal all-time guarantee.
      const daily = await api.complaintsTrend(token, { days: 3650 });
      setRejectedCount(daily.reduce((sum, d) => sum + d.rejected, 0));
    } catch {
      // Non-critical -- keeps its last known value on a transient failure, same as loadStats.
    }
  }

  async function loadServiceBreakdown() {
    if (!token) return;
    try {
      setServiceRows(await api.complaintsByService(token));
    } catch {
      // Non-critical -- the donut just keeps its last known values on a transient failure.
    }
  }

  async function loadTrend() {
    if (!token) return;
    try {
      setTrendData(await api.complaintsTrend(token, { days: 7 }));
    } catch {
      // Non-critical -- the chart just keeps its last known values (or renders its empty state)
      // on a transient failure, same as the other stat/breakdown loaders on this page.
    }
  }

  async function reload() {
    await Promise.all([load(), loadStats(), loadServiceBreakdown(), loadTrend(), loadRejectedTotal()]);
  }

  // A search edit or filter-chip click always jumps back to page 1 -- the previous page number
  // almost never still makes sense against a newly-narrowed result set.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filter, dateFrom, dateTo]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, lang, filter, debouncedSearch, dateFrom, dateTo, page]);

  useEffect(() => {
    loadStats();
    loadServiceBreakdown();
    loadTrend();
    loadRejectedTotal();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function accept(id: number) {
    if (!token) return;
    setActingId(id);
    try {
      await api.acceptComplaint(token, id);
      toast.success(t(lang, "worker.acceptedToast"));
      await reload();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "worker.errAcceptFailed"));
    } finally {
      setActingId(null);
    }
  }

  function closeModal() {
    setModalFor(null);
  }


  // A worker's queue only ever contains complaints currently assigned to them (see
  // backend/routes/complaints.py) — so "open" here means everything short of resolved.
  const openCount = totalCount - resolvedCount;
  const pageCount = Math.max(1, Math.ceil(total / WORKER_PAGE_SIZE));

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <div className="page-head">
          <div>
            <h1 className="page-title display">{t(lang, "worker.title")}</h1>
            <p className="page-sub">{user?.ward ? `${t(lang, "worker.wardPrefix")}: ${user.ward}` : t(lang, "worker.noWard")}</p>
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, marginBottom: 20 }}>
          <div className="surface-card hoverable stat-card" style={{ flex: 1 }}>
            <div className="stat-label">{t(lang, "worker.open")}</div>
            <div className="display stat-value" style={{ color: "var(--status-open)" }}>{openCount}</div>
          </div>
          <div className="surface-card hoverable stat-card" style={{ flex: 1 }}>
            <div className="stat-label">{t(lang, "worker.resolved")}</div>
            <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{resolvedCount}</div>
          </div>
        </div>

        {/* Same 3-card row style as AdminDashboard's own location/AI-health/service row
            (.admin-loc-ai-row.three-col + .surface-card.admin-loc-ai-panel) -- reused here rather
            than invented fresh, so a worker's dashboard and an admin's read as the same design
            language. */}
        <div className="admin-loc-ai-row three-col" style={{ marginBottom: 20 }}>
          {serviceRows.length > 0 && (
            <div className="surface-card admin-loc-ai-panel">
              <h6>
                <span>{t(lang, "admin.serviceChartTitle")}</span>
              </h6>
              <ServiceDonutPanel rows={serviceRows} lang={lang} statusLabel={(status) => t(lang, STATUS_LABEL_KEY[status])} />
            </div>
          )}
          <div className="surface-card admin-loc-ai-panel">
            <h6>
              <span>{t(lang, "worker.trendTitle")}</span>
            </h6>
            <ComplaintsTrendChart
              daily={trendData}
              emptyLabel={t(lang, "worker.trendEmpty")}
              legends={{
                opened: t(lang, "worker.open"),
                resolved: t(lang, "worker.resolved"),
              }}
            />
          </div>
          {totalCount > 0 && (
            <div className="surface-card admin-loc-ai-panel">
              <h6>
                <span>{t(lang, "worker.resolutionRate")}</span>
              </h6>
              <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <ResolutionRateGauge
                  totalCount={totalCount}
                  resolvedCount={resolvedCount}
                  assignedCount={assignedCount}
                  inProgressCount={inProgressCount}
                  rejectedCount={rejectedCount}
                  resolvedLabel={t(lang, "worker.resolved")}
                  stillOpenLabel={t(lang, "worker.stillOpen")}
                  needsResponseLabel={t(lang, "worker.filterAssigned")}
                  inProgressLabel={t(lang, "worker.filterInProgress")}
                  rejectedLabel={t(lang, "worker.rejected")}
                  hintText={t(lang, "worker.resolutionRateHint")}
                  backLabel={t(lang, "common.back")}
                  openLabel={t(lang, "worker.openPlusRejected")}
                  gaugeLabel={t(lang, "worker.resolutionRate")}
                />
              </div>
            </div>
          )}
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {(["all", "assigned", "accepted", "in_progress", "resolved"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  background: filter === f ? "var(--ink)" : "var(--surface)",
                  color: filter === f ? "var(--paper)" : "var(--ink-2)",
                  border: "1px solid var(--line)", borderRadius: 20, padding: "7px 14px", fontSize: 12.5, fontWeight: 600,
                }}
              >
                {f === "all" ? t(lang, "worker.filterAll")
                  : f === "assigned" ? t(lang, "worker.filterAssigned")
                  : f === "accepted" ? t(lang, "worker.filterAccepted")
                  : f === "in_progress" ? t(lang, "worker.filterInProgress")
                  : t(lang, "worker.filterResolved")}
              </button>
            ))}
          </div>
          {totalCount > 0 && (
            <SearchWithDateFilter
              searchValue={search}
              onSearchChange={setSearch}
              searchPlaceholder={t(lang, "worker.searchComplaints")}
              dateFrom={dateFrom}
              dateTo={dateTo}
              onDateFromChange={setDateFrom}
              onDateToChange={setDateTo}
              lang={lang}
              width={320}
            />
          )}
        </div>

        {loadError && <div className="banner-error">{loadError}</div>}
        {loading && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="surface-card" style={{ padding: "14px 16px" }}>
                <div className="skeleton" style={{ width: "60%", height: 15, marginBottom: 10 }} />
                <div className="skeleton" style={{ width: "35%", height: 12 }} />
              </div>
            ))}
          </div>
        )}
        {!loading && total === 0 && <p style={{ color: "var(--ink-2)" }}>{t(lang, "worker.nothingHere")}</p>}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {complaints.map((c, i) => (
            <div key={c.id} className="surface-card hoverable enter" style={{ padding: "14px 16px", "--stagger": Math.min(i, 6) } as React.CSSProperties}>
              <div
                style={{ display: "flex", justifyContent: "space-between", gap: 16, flexWrap: "wrap", cursor: "pointer" }}
                onClick={() => navigate(`/worker/complaints/${c.id}`)}
              >
                <div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>JM-{String(c.id).padStart(5, "0")}</div>
                  <div style={{ fontWeight: 600, margin: "3px 0" }}>{c.display_text}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{c.display_summary}</div>
                  <div style={{ marginTop: 4 }}><CategoryBadge category={c.service_category} lang={lang} /></div>
                </div>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 8 }} onClick={(e) => e.stopPropagation()}>
                  <StatusBadge status={c.status} label={t(lang, STATUS_LABEL_KEY[c.status])} />

                  {/* Per-status action rules -- exactly one of these blocks renders per card, no
                      irrelevant actions for the current status:
                        ASSIGNED    -> [Reject] [Accept]
                        ACCEPTED    -> [Start Work]
                        IN_PROGRESS -> [Add Update] [Complete Complaint]
                        RESOLVED    -> [View Report] [Download Report]
                      See also WorkerComplaintDetail.tsx, which applies the identical rules. */}
                  {c.status === "assigned" && (
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="btn btn-ghost btn-sm" onClick={() => setModalFor({ id: c.id, type: "reject" })} disabled={actingId === c.id}>
                        {t(lang, "worker.reject")}
                      </button>
                      <button className="btn btn-primary btn-sm" onClick={() => accept(c.id)} disabled={actingId === c.id}>
                        {actingId === c.id ? t(lang, "worker.accepting") : t(lang, "worker.accept")}
                      </button>
                    </div>
                  )}
                  {c.status === "accepted" && (
                    <button className="btn btn-primary btn-sm" onClick={() => setModalFor({ id: c.id, type: "start" })}>
                      {t(lang, "worker.startWork")}
                    </button>
                  )}
                  {c.status === "in_progress" && (
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="btn btn-ghost btn-sm" onClick={() => setModalFor({ id: c.id, type: "update" })}>
                        {t(lang, "worker.addUpdate")}
                      </button>
                      <button className="btn btn-primary btn-sm" onClick={() => setModalFor({ id: c.id, type: "complete" })}>
                        {t(lang, "worker.completeComplaint")}
                      </button>
                    </div>
                  )}
                  {c.status === "resolved" && (
                    <div style={{ display: "flex", gap: 6 }}>
                      <button className="btn btn-ghost btn-sm" onClick={() => setModalFor({ id: c.id, type: "summary" })}>
                        {t(lang, "worker.viewSummary")}
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setModalFor({ id: c.id, type: "report" })}>
                        {t(lang, "worker.viewReport")}
                      </button>
                      <DownloadReportButton complaintId={c.id} />
                    </div>
                  )}
                </div>
              </div>
              {c.photo_path && (
                <img
                  src={api.photoUrl(c.photo_path)}
                  alt={t(lang, "worker.attached")}
                  style={{ marginTop: 10, maxWidth: 160, borderRadius: 8, border: "1px solid var(--line)" }}
                  onClick={(e) => e.stopPropagation()}
                />
              )}
            </div>
          ))}
        </div>

        {pageCount > 1 && (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 4px" }}>
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

      {modalFor?.type === "reject" && (
        <RejectComplaintModal
          complaintId={modalFor.id}
          onClose={closeModal}
          onRejected={() => { toast.success(t(lang, "worker.rejectedConfirm")); reload(); }}
        />
      )}
      {modalFor?.type === "start" && (
        <StartWorkModal
          complaintId={modalFor.id}
          onClose={closeModal}
          onStarted={() => { toast.success(t(lang, "worker.start.startedToast")); reload(); }}
        />
      )}
      {modalFor?.type === "update" && (
        <ProgressUpdateModal
          complaintId={modalFor.id}
          onClose={closeModal}
          onAdded={() => { toast.success(t(lang, "worker.update.addedToast")); reload(); }}
        />
      )}
      {modalFor?.type === "complete" && (
        <CompleteComplaintModal
          complaintId={modalFor.id}
          onClose={closeModal}
          onResolved={() => { toast.success(t(lang, "worker.resolvedToast")); reload(); }}
        />
      )}
      {modalFor?.type === "report" && <ReportModal complaintId={modalFor.id} onClose={closeModal} />}
      {modalFor?.type === "summary" && (() => {
        const target = complaints.find((c) => c.id === modalFor.id);
        return target ? (
          <SummaryModal complaint={target} statusLabel={t(lang, STATUS_LABEL_KEY[target.status])} onClose={closeModal} />
        ) : null;
      })()}
    </div>
  );
}
