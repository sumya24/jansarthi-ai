import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import TopBar from "../components/TopBar";
import ComplaintTracker from "../components/ComplaintTracker";
import StatusBadge from "../components/StatusBadge";
import ComplaintUpdatesTimeline from "../components/ComplaintUpdatesTimeline";
import CategoryBadge from "../components/CategoryBadge";
import ReportModal from "../components/ReportModal";
import SummaryModal from "../components/SummaryModal";
import DownloadReportButton from "../components/DownloadReportButton";
import FeedbackForm from "../components/FeedbackForm";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint, type ComplaintUpdateEntry } from "../lib/api";
import type { ServiceCategory } from "../lib/ragTypes";
import { SERVICE_CATEGORY_DEFS } from "../lib/serviceCategories";
import SearchWithDateFilter from "../components/SearchWithDateFilter";

const STATUS_LABEL_KEY = {
  // "open" is the complaint's brand-new status, set at creation and normally gone within the
  // same request once the assignment system's first pass runs (see complaint_agent.py/
  // assignment_service.py) -- but real data can still be seen sitting at "open" (assignment
  // failed/was skipped), and this map had no entry for it at all, so StatusBadge rendered with
  // no label text -- just its icon, no words -- for any complaint in that state.
  open: "citizen.trackSubmitted",
  pending: "citizen.statusPending",
  assigned: "citizen.statusAssigned",
  accepted: "citizen.statusAccepted",
  // Reuses the citizen-facing tracker's own existing "In progress" label rather than adding a
  // near-duplicate string for the same status.
  in_progress: "citizen.trackInProgress",
  resolved: "citizen.statusResolved",
} as const;

// Polling interval for "live" tracking updates (accept/reject/reassignment/resolve) while
// anything is still in flight — this app has no websockets/SSE, so a short poll is the fast,
// simple way to make status changes show up without the citizen having to manually refresh.
const LIVE_POLL_MS = 8000;

// LIVE-REPORTED GAP: this list used to fetch and render EVERY one of a citizen's own complaints
// in one flat column with no way to narrow it down -- fine for a handful of complaints, unusable
// once someone has filed dozens over time. Status filter, search, and pagination are now real
// backend queries (GET /complaints' own `status`/`search`/`page`/`page_size` params), not a
// client-side filter over an already-fetched full list -- same reasoning as the Admin/Worker
// dashboards and "My Area".
const COMPLAINTS_PAGE_SIZE = 10;

const CITIZEN_STATUS_FILTERS = ["all", "pending", "assigned", "accepted", "in_progress", "resolved"] as const;
type CitizenStatusFilter = (typeof CITIZEN_STATUS_FILTERS)[number];

export default function CitizenDashboard() {
  const { token } = useAuth();
  const navigate = useNavigate();
  // Single source of language for this whole page: the account's preferred language (kept in
  // sync with uiLang — see auth.tsx/SettingsModal). It drives both what language the complaint
  // list displays in *and* what language a new complaint is submitted as — no separate "what
  // language is this complaint in" picker; Settings is the one place to change either.
  const { lang } = useUiLang();
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Worker-authored updates (initial assessment / progress updates / completion status) --
  // lazily fetched per complaint only when the citizen actually expands that card, not eagerly
  // for the whole list (list load stays a cheap query -- see backend/routes/complaints.py's
  // ComplaintDetailResponse docstring).
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [updatesById, setUpdatesById] = useState<Record<number, ComplaintUpdateEntry[]>>({});
  const [updatesLoadingId, setUpdatesLoadingId] = useState<number | null>(null);
  const [updatesError, setUpdatesError] = useState<Record<number, string>>({});
  const [reportModalId, setReportModalId] = useState<number | null>(null);
  const [summaryComplaint, setSummaryComplaint] = useState<Complaint | null>(null);
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [statusFilter, setStatusFilter] = useState<CitizenStatusFilter>("all");
  // Same "picking a service reframes the stat cards" pattern as MyArea.tsx's own categoryFilter --
  // LIVE-REPORTED GAP: My Area got this, My Complaints hadn't, even though both are complaint
  // lists with the same service_category data available.
  const [categoryFilter, setCategoryFilter] = useState<ServiceCategory | "all">("all");
  // Decoupled from `complaints`/the filtered+paged list below on purpose -- these two stat cards
  // are meant to read as "your account overall", the same way MyArea.tsx's stat cards stay
  // ward-wide regardless of that page's own search, and AdminDashboard.tsx's/WorkerDashboard.tsx's
  // stay role-wide regardless of their own filter. Two cheap page_size=1 calls (the payload is
  // thrown away, only `total` from each is read) rather than fetching every complaint just to
  // count them.
  const [totalCount, setTotalCount] = useState(0);
  const [resolvedCount, setResolvedCount] = useState(0);
  // LIVE-REPORTED BUG: totalCount above is category-scoped (so the stat cards can reframe to a
  // selected service, by design) -- but the filter row/search box/status chips were gated on
  // `totalCount > 0` too, so picking a category with zero matches hid those controls entirely,
  // including the one "All" chip needed to undo the filter. A citizen filed into that state had
  // no way back except leaving the page. This tracks whether the account has ANY complaint at
  // all, regardless of which category is currently selected, specifically to gate those controls
  // instead -- same role as MyArea.tsx's `ward` check (never conditioned on the current filter).
  const [hasAnyComplaints, setHasAnyComplaints] = useState(false);
  const debouncedSearch = useDebouncedValue(search);

  async function toggleUpdates(id: number) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(id);
    if (!updatesById[id] && token) {
      setUpdatesLoadingId(id);
      try {
        const detail = await api.getComplaint(token, id, lang);
        setUpdatesById((prev) => ({ ...prev, [id]: detail.updates }));
      } catch (err) {
        setUpdatesError((prev) => ({ ...prev, [id]: err instanceof ApiError ? err.message : t(lang, "updates.errLoadFailed") }));
      } finally {
        setUpdatesLoadingId(null);
      }
    }
  }

  async function loadComplaints() {
    if (!token) return;
    setLoadError(null);
    try {
      const data = await api.listComplaints(token, {
        lang,
        status: statusFilter === "all" ? undefined : statusFilter,
        category: categoryFilter === "all" ? undefined : categoryFilter,
        search: debouncedSearch || undefined,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
        page,
        pageSize: COMPLAINTS_PAGE_SIZE,
      });
      setComplaints(data.items);
      setTotal(data.total);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "citizen.errLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  async function loadStats() {
    if (!token) return;
    try {
      // Category-scoped when a service is selected (same "reframe the stat cards" behavior as
      // MyArea.tsx's own loadStats-equivalent) -- otherwise these two stay account-wide.
      const category = categoryFilter === "all" ? undefined : categoryFilter;
      const [all, resolved] = await Promise.all([
        api.listComplaints(token, { category, page: 1, pageSize: 1 }),
        api.listComplaints(token, { status: "resolved", category, page: 1, pageSize: 1 }),
      ]);
      setTotalCount(all.total);
      setResolvedCount(resolved.total);
    } catch {
      // Non-critical -- the stat cards just keep their last known values on a transient failure.
    }
  }

  // Deliberately NEVER category-scoped -- see hasAnyComplaints' own comment above for why this
  // has to stay independent of loadStats' category-scoped totalCount.
  async function loadHasAnyComplaints() {
    if (!token) return;
    try {
      const all = await api.listComplaints(token, { page: 1, pageSize: 1 });
      setHasAnyComplaints(all.total > 0);
    } catch {
      // Non-critical -- worst case the filter row stays hidden/shown as it already was.
    }
  }

  // A search edit or status/category-chip click always jumps back to page 1 -- the previous page
  // number almost never still makes sense against a newly-narrowed result set.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, statusFilter, categoryFilter, dateFrom, dateTo]);

  useEffect(() => {
    setLoading(true);
    loadComplaints();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, lang, statusFilter, categoryFilter, debouncedSearch, dateFrom, dateTo, page]);

  useEffect(() => {
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, categoryFilter]);

  useEffect(() => {
    loadHasAnyComplaints();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Live tracking: while anything on THIS page is still moving through the pipeline (not
  // resolved), poll both the list and the stat cards so an accept/reject/reassignment shows up
  // without a manual refresh.
  useEffect(() => {
    if (!token) return;
    const hasActiveComplaint = complaints.some((c) => c.status !== "resolved");
    if (!hasActiveComplaint) return;
    const interval = setInterval(() => {
      loadComplaints();
      loadStats();
    }, LIVE_POLL_MS);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, lang, complaints]);

  const openCount = totalCount - resolvedCount;
  const pageCount = Math.max(1, Math.ceil(total / COMPLAINTS_PAGE_SIZE));

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <div className="page-head">
          <div>
            <h1 className="page-title display">{t(lang, "citizen.myComplaintsTitle")}</h1>
            <p className="page-sub">{t(lang, "citizen.myComplaintsSubtitle")}</p>
          </div>
          <Link to="/citizen/report" className="btn btn-primary">
            {t(lang, "home.hero.reportCta")}
          </Link>
        </div>

        {loadError && <div className="banner-error">{loadError}</div>}

        {/* LIVE-REPORTED REQUEST: distinct layout from MyArea.tsx (not just distinct copy) so the
            two pages read as structurally different, not just re-skinned copies of each other --
            stats come FIRST here (this page's original, longer-standing order), with the newer
            category filter placed after them, whereas MyArea.tsx puts its category filter first
            since the whole page is organized around "which service am I looking at." Reframing
            behavior (picking a category still changes the two numbers above) is unchanged either
            way -- only the visual order differs. */}
        <div className="statstrip" style={{ display: "flex", gap: 10, marginBottom: 22 }}>
          <div className="surface-card hoverable" style={{ padding: "10px 16px", flex: 1 }}>
            <div className="display" style={{ fontSize: 26, color: "var(--status-open)" }}>{openCount}</div>
            <div style={{ fontSize: 11, color: "var(--ink-2)" }}>{t(lang, "citizen.open")}</div>
          </div>
          <div className="surface-card hoverable" style={{ padding: "10px 16px", flex: 1 }}>
            <div className="display" style={{ fontSize: 26, color: "var(--status-resolved)" }}>{resolvedCount}</div>
            <div style={{ fontSize: 11, color: "var(--ink-2)" }}>{t(lang, "citizen.resolved")}</div>
          </div>
        </div>

        {hasAnyComplaints && (
          <div style={{ marginBottom: 22 }}>
            <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "area.categoryFilterLabel")}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              <button
                className={`filter-chip btn btn-sm ${categoryFilter === "all" ? "btn-primary" : "btn-ghost"}`}
                onClick={() => setCategoryFilter("all")}
              >
                {t(lang, "admin.filterAll")}
              </button>
              {SERVICE_CATEGORY_DEFS.map((def) => {
                const active = categoryFilter === def.id;
                return (
                  <button
                    key={def.id}
                    className={`filter-chip btn btn-sm ${active ? "btn-primary" : "btn-ghost"}`}
                    onClick={() => setCategoryFilter(def.id)}
                    style={active ? { background: `var(--service-${def.color})`, borderColor: `var(--service-${def.color})`, color: "#fff" } : undefined}
                  >
                    <span className="filter-chip-icon">{def.icon}</span>
                    {t(lang, def.titleKey)}
                  </button>
                );
              })}
            </div>
            <p style={{ fontSize: 12, color: "var(--ink-2)", margin: "8px 0 0" }}>
              {t(lang, "area.showingLabel")}:{" "}
              <strong style={{ color: "var(--ink)" }}>
                {categoryFilter === "all"
                  ? t(lang, "area.allServicesLabel")
                  : t(lang, SERVICE_CATEGORY_DEFS.find((d) => d.id === categoryFilter)?.titleKey ?? "area.allServicesLabel")}
              </strong>
            </p>
          </div>
        )}

        <div className="section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
          <span>{t(lang, "citizen.yourComplaints")}</span>
          {hasAnyComplaints && (
            <SearchWithDateFilter
              searchValue={search}
              onSearchChange={setSearch}
              searchPlaceholder={t(lang, "citizen.searchComplaints")}
              dateFrom={dateFrom}
              dateTo={dateTo}
              onDateFromChange={setDateFrom}
              onDateToChange={setDateTo}
              lang={lang}
              width={320}
            />
          )}
        </div>

        {hasAnyComplaints && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 14 }}>
            {CITIZEN_STATUS_FILTERS.map((f) => (
              <button
                key={f}
                className={`filter-chip btn btn-sm ${statusFilter === f ? "btn-primary" : "btn-ghost"}`}
                onClick={() => setStatusFilter(f)}
              >
                {f === "all" ? t(lang, "admin.filterAll") : t(lang, STATUS_LABEL_KEY[f])}
              </button>
            ))}
          </div>
        )}

        {loading && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {[0, 1].map((i) => (
              <div key={i} className="surface-card" style={{ padding: "14px 16px" }}>
                <div className="skeleton" style={{ width: "70%", height: 15, marginBottom: 10 }} />
                <div className="skeleton" style={{ width: "40%", height: 12, marginBottom: 14 }} />
                <div className="skeleton" style={{ width: "100%", height: 30 }} />
              </div>
            ))}
          </div>
        )}
        {!loading && total === 0 && statusFilter === "all" && categoryFilter === "all" && !debouncedSearch && !dateFrom && !dateTo && (
          <p style={{ color: "var(--ink-2)" }}>{t(lang, "citizen.noComplaints")}</p>
        )}
        {!loading && total === 0 && (statusFilter !== "all" || categoryFilter !== "all" || debouncedSearch || dateFrom || dateTo) && (
          <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noComplaintsFiltered")}</p>
        )}

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {complaints.map((c, i) => (
            <div key={c.id} className="surface-card hoverable enter" style={{ padding: "14px 16px", "--stagger": Math.min(i, 6) } as React.CSSProperties}>
              {/* Same click-to-detail pattern as WorkerDashboard.tsx's queue cards -- only the
                  summary row navigates (cursor:pointer), everything below (tracker, updates
                  toggle, report/download, feedback) stays outside it, so those controls don't
                  need stopPropagation. */}
              <div
                style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", cursor: "pointer" }}
                onClick={() => navigate(`/citizen/complaints/${c.id}`)}
              >
                <div>
                  <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>JM-{String(c.id).padStart(5, "0")}</div>
                  <div style={{ fontWeight: 600, margin: "3px 0" }}>{c.display_text}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{c.display_summary}</div>
                  <div style={{ marginTop: 4 }}><CategoryBadge category={c.service_category} lang={lang} /></div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <StatusBadge status={c.status} label={t(lang, STATUS_LABEL_KEY[c.status])} />
                  <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 6 }}>{new Date(c.created_at).toLocaleString()}</div>
                </div>
              </div>

              <ComplaintTracker status={c.status} rejectionCount={c.rejection_count} lang={lang} />

              {/* Tracking details: who it's with, live reassignment status, contact once accepted. */}
              {c.status === "pending" && c.rejection_count > 0 && (
                <div style={{ fontSize: 12, color: "var(--status-open)", marginTop: 8 }}>
                  {t(lang, "citizen.searchingNextWorker")}
                </div>
              )}
              {c.assigned_worker_name && (
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 8 }}>
                  {t(lang, "citizen.assignedTo")}: <strong style={{ color: "var(--ink)" }}>{c.assigned_worker_name}</strong>
                  {c.assigned_worker_phone && (
                    <>
                      {" · "}
                      {t(lang, "citizen.workerPhone")}:{" "}
                      <a href={`tel:${c.assigned_worker_phone}`} className="mono">{c.assigned_worker_phone}</a>
                    </>
                  )}
                </div>
              )}

              {/* Worker-authored updates -- initial assessment / optional progress updates /
                  completion status. Only worth offering to expand once there's a chance
                  something exists (in_progress or resolved); never internal-only info (no
                  rejection reasons, no admin notes -- see ComplaintUpdatesTimeline, which only
                  ever renders the three citizen-visible update types). */}
              {(c.status === "in_progress" || c.status === "resolved") && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => toggleUpdates(c.id)}>
                    {expandedId === c.id ? t(lang, "updates.hide") : t(lang, "updates.view")}
                  </button>
                  {expandedId === c.id && (
                    <div style={{ marginTop: 10 }}>
                      {updatesLoadingId === c.id && <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "common.loading")}</p>}
                      {updatesError[c.id] && <div className="banner-error">{updatesError[c.id]}</div>}
                      {updatesById[c.id] && <ComplaintUpdatesTimeline updates={updatesById[c.id]} />}
                    </div>
                  )}
                </div>
              )}

              {c.status === "resolved" && (
                <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSummaryComplaint(c)}>
                    {t(lang, "worker.viewSummary")}
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setReportModalId(c.id)}>
                    {t(lang, "worker.viewReport")}
                  </button>
                  <DownloadReportButton complaintId={c.id} />
                </div>
              )}

              {c.status === "resolved" && token && (
                c.feedback_rating ? (
                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line)", fontSize: 12, color: "var(--ink-2)" }}>
                    {t(lang, "citizen.feedbackSubmitted")} {"★".repeat(c.feedback_rating)}
                    {c.feedback_comment && <div style={{ marginTop: 4 }}>{c.feedback_comment}</div>}
                  </div>
                ) : (
                  <FeedbackForm complaintId={c.id} lang={lang} token={token} onSubmitted={loadComplaints} />
                )
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
      {reportModalId !== null && <ReportModal complaintId={reportModalId} onClose={() => setReportModalId(null)} />}
      {summaryComplaint && (
        <SummaryModal
          complaint={summaryComplaint}
          statusLabel={t(lang, STATUS_LABEL_KEY[summaryComplaint.status])}
          onClose={() => setSummaryComplaint(null)}
        />
      )}
    </div>
  );
}
