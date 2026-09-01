import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import ConfirmModal from "../components/ConfirmModal";
import SearchWithDateFilter from "../components/SearchWithDateFilter";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { t } from "../lib/i18n";
import { api, ApiError, type AiMonitoringSummary, type AiRequestLogEntry, type ModelCostEntry } from "../lib/api";
import { useToast } from "../lib/toast";
import "../styles/dashboard.css";

const REQUESTS_PAGE_SIZE = 20;

/** See AdminWorkers.tsx's/AdminDashboard.tsx's own copies of this component for the full
 * rationale (header checkbox reflecting the CURRENT page's selection state, `indeterminate`
 * needing a ref since it isn't a settable JSX prop). Duplicated, not shared -- each is a tiny,
 * page-scoped concern, not worth a shared component for otherwise-unrelated tables. */
function SelectAllCheckbox({ pageIds, selected, onToggle }: { pageIds: number[]; selected: Set<number>; onToggle: () => void }) {
  const ref = useRef<HTMLInputElement>(null);
  const selectedOnPage = pageIds.filter((id) => selected.has(id)).length;
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = selectedOnPage > 0 && selectedOnPage < pageIds.length;
  }, [selectedOnPage, pageIds.length]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={pageIds.length > 0 && selectedOnPage === pageIds.length}
      onChange={onToggle}
      disabled={pageIds.length === 0}
    />
  );
}

/** Same hand-drawn stroke language as AdminDashboard.tsx's own local TrashIcon (viewBox 0 0 24 24,
 * ~1.4-1.7px stroke, currentColor, rounded caps/joins) -- kept local here too since this action is
 * Admin-only and specific to this one table. */
function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
      <path d="M4.5 7h15" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M9.5 7V5.5a1.5 1.5 0 0 1 1.5-1.5h2a1.5 1.5 0 0 1 1.5 1.5V7" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path
        d="M6.5 7 7.3 19.5A1.6 1.6 0 0 0 8.9 21h6.2a1.6 1.6 0 0 0 1.6-1.5L17.5 7"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
      <path d="M10.3 10.5v7M13.7 10.5v7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

/** Its own page (not a section of AdminDashboard) so the worker-management view and the AI
 * observability view don't compete for space/scroll -- reached via the "AI Monitoring" button on
 * AdminDashboard. See docs/ask_sarthi_langsmith_observability.md for what's shown here and
 * where the numbers come from (the app's own `ai_request_logs` table, never a live LangSmith
 * call -- see that doc's "Admin Monitoring" section for why). */
export default function AdminAiMonitoring() {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const toast = useToast();

  const [deleteTarget, setDeleteTarget] = useState<AiRequestLogEntry | null>(null);
  const [deleting, setDeleting] = useState(false);

  // Bulk selection -- see AdminWorkers.tsx's/AdminDashboard.tsx's identical field for the
  // reasoning (a plain id Set, persists across pages so a selection made on page 1 survives
  // paging to page 2, matching how those two tables' own bulk-delete already behaves).
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const [summary, setSummary] = useState<AiMonitoringSummary | null>(null);
  // Loaded independently from `summary` (its own loading flag, never blocks the tiles above) --
  // this reads Phoenix directly (see backend's tracing.get_model_cost_summary() docstring), so a
  // Phoenix outage should only ever empty this one panel, never the rest of the page.
  const [modelCosts, setModelCosts] = useState<ModelCostEntry[] | null>(null);
  const [modelCostsLoading, setModelCostsLoading] = useState(true);
  // LIVE-REPORTED race, found via /code-review: the effect below re-fires loadModelCosts()
  // whenever `token` changes -- including a silent access-token refresh, which can happen mid-
  // session with no user action at all (see api.ts's shared refreshInFlight). If an earlier call
  // (old token) is still in flight when a newer one (new token) starts, and the OLDER one happens
  // to resolve LAST (e.g. it hit a slower Phoenix query), its stale data would silently overwrite
  // the newer, correct data. This ref counts each call; a response only gets applied if it's still
  // the most recent one requested by the time it resolves.
  const modelCostsRequestId = useRef(0);
  const [requests, setRequests] = useState<AiRequestLogEntry[]>([]);
  // LIVE-REPORTED GAP: this table always fetched just the newest 20 requests, with no way to see
  // any older ones -- same real, server-side pagination pattern as the main Admin Dashboard's
  // complaints table (X-Total-Count header, page/page_size params), not a client-side veneer.
  const [requestsPage, setRequestsPage] = useState(1);
  const [requestsTotal, setRequestsTotal] = useState(0);
  // Matches request_id/intent/routed_to (the same three columns list_workers()'s own `search`
  // targets on that page: the columns actually visible in the row).
  const [requestsSearch, setRequestsSearch] = useState("");
  const debouncedRequestsSearch = useDebouncedValue(requestsSearch);
  // Plain YYYY-MM-DD strings straight from <input type="date"> -- no debounce needed, a date
  // picker fires far less often than a text field's every-keystroke onChange.
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  // LIVE-REPORTED BUG: loadSummary() and loadRequests() used to share this one `error` state, but
  // only loadRequests() ever cleared it on success -- loadSummary() set it on failure and NEVER
  // cleared it on a later success. The two fire concurrently on mount (separate useEffects
  // below); if loadSummary() failed even once (any transient blip -- a mid-restart request, a
  // momentary network hiccup) and its retry later succeeded AFTER loadRequests() had already run,
  // the error banner stuck around permanently, showing "Could not load AI monitoring data" even
  // while Cost by Model and Recent Requests both displayed real, current, successfully-loaded
  // data right below it -- confirmed live, repeatedly, on production. Split into its own state so
  // each independently-fetched section's error is only ever set/cleared by that section's own
  // load function -- no race between them either direction.
  const [summaryError, setSummaryError] = useState<string | null>(null);
  // `loading` gates the page's initial skeleton (stat tiles + table both hidden until the FIRST
  // fetch resolves). `tableBusy` is separate and only used for subsequent page-change refetches
  // -- without the split, clicking Prev/Next re-triggered the same `loading` flag that also hides
  // the stat tiles above, so paging through requests made the unrelated tiles flicker out and
  // back in on every click.
  const [loading, setLoading] = useState(true);
  const [tableBusy, setTableBusy] = useState(false);
  const isFirstRequestsLoad = useRef(true);

  async function loadSummary() {
    if (!token) return;
    try {
      setSummary(await api.aiMonitoringSummary(token));
      setSummaryError(null);
    } catch (err) {
      setSummaryError(err instanceof ApiError ? err.message : t(lang, "admin.aiErrLoadFailed"));
    }
  }

  async function loadModelCosts() {
    if (!token) return;
    const requestId = ++modelCostsRequestId.current;
    setModelCostsLoading(true);
    try {
      const result = await api.aiMonitoringModelCosts(token);
      if (requestId !== modelCostsRequestId.current) return; // a newer call has since started
      setModelCosts(result);
    } catch {
      // Best-effort, silent -- a Phoenix outage here shouldn't plant a second error banner next
      // to the main one above; an empty panel (see the render below) already reads as "nothing
      // to show" without needing its own error message. Also reached on the 15s client-side
      // timeout (see api.ts's aiMonitoringModelCosts) so a hung request degrades to "nothing to
      // show" instead of leaving the skeleton up forever.
      if (requestId !== modelCostsRequestId.current) return;
      setModelCosts([]);
    } finally {
      if (requestId === modelCostsRequestId.current) setModelCostsLoading(false);
    }
  }

  async function loadRequests() {
    if (!token) return;
    const first = isFirstRequestsLoad.current;
    if (first) setLoading(true);
    else setTableBusy(true);
    setError(null);
    try {
      const result = await api.aiMonitoringRequestsPage(
        token,
        requestsPage,
        REQUESTS_PAGE_SIZE,
        debouncedRequestsSearch || undefined,
        dateFrom || undefined,
        dateTo || undefined
      );
      setRequests(result.items);
      setRequestsTotal(result.total);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "admin.aiErrLoadFailed"));
    } finally {
      if (first) {
        setLoading(false);
        isFirstRequestsLoad.current = false;
      } else {
        setTableBusy(false);
      }
    }
  }

  async function confirmDeleteRequest() {
    if (!token || !deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteAiRequestLog(token, deleteTarget.id);
      toast.success(t(lang, "admin.aiRequestDeletedToast"));
      setDeleteTarget(null);
      // Re-fetch this same page (and the summary tiles, since a deleted request also drops out
      // of those live-computed totals) rather than just splicing the row out client-side --
      // matches how deleting a complaint/worker elsewhere in Admin already reloads from the
      // server instead of trusting a local guess at the new state.
      await Promise.all([loadSummary(), loadRequests()]);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "admin.aiRequestDeleteErrFailed"));
    } finally {
      setDeleting(false);
    }
  }

  async function confirmBulkDelete() {
    if (!token || selectedIds.size === 0) return;
    setBulkDeleting(true);
    const ids = [...selectedIds];
    const results = await Promise.allSettled(ids.map((id) => api.deleteAiRequestLog(token, id)));
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - succeeded;
    if (failed === 0) {
      toast.success(`${t(lang, "admin.bulkDeleteSuccessToast")} ${succeeded}`);
    } else {
      toast.error(`${t(lang, "admin.bulkDeletePartialToast")} ${succeeded}/${results.length}`);
    }
    setSelectedIds(new Set());
    setBulkDeleteConfirm(false);
    setBulkDeleting(false);
    await Promise.all([loadSummary(), loadRequests()]);
  }

  function toggleOne(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function togglePage(pageIds: number[]) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      const allSelected = pageIds.length > 0 && pageIds.every((id) => next.has(id));
      for (const id of pageIds) {
        if (allSelected) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }

  useEffect(() => {
    loadSummary();
    loadModelCosts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // A search edit always jumps back to page 1 -- the previous page number almost never still
  // makes sense against a newly-narrowed result set (same behavior as AdminWorkers.tsx's own
  // search box).
  useEffect(() => {
    setRequestsPage(1);
  }, [debouncedRequestsSearch, dateFrom, dateTo]);

  useEffect(() => {
    loadRequests();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, requestsPage, debouncedRequestsSearch, dateFrom, dateTo]);

  const requestsPageCount = Math.max(1, Math.ceil(requestsTotal / REQUESTS_PAGE_SIZE));
  const pagedIds = requests.map((r) => r.id);

  // Click-to-sort by latency -- LIVE-REPORTED gap: clicking a "High AI latency" notification
  // landed here with no way to actually spot which requests were slow. REQUESTS_PAGE_SIZE (20)
  // deliberately matches the alert's own rolling window (_ALERT_WINDOW_SIZE in
  // ai_request_log_repository.py), so the default first page IS the same 20 requests the alert's
  // average was computed from -- sorting that page by latency descending surfaces the real
  // culprits directly. Sorts only the current page's display order (selection/pagedIds above stay
  // keyed off the unsorted `requests` array, so this never affects which rows are selected).
  const [latencySort, setLatencySort] = useState<"none" | "desc" | "asc">("none");
  const sortedRequests = useMemo(() => {
    if (latencySort === "none") return requests;
    return [...requests].sort((a, b) => (latencySort === "desc" ? b.latency_ms - a.latency_ms : a.latency_ms - b.latency_ms));
  }, [requests, latencySort]);
  function toggleLatencySort() {
    setLatencySort((prev) => (prev === "desc" ? "asc" : prev === "asc" ? "none" : "desc"));
  }

  return (
    <div>
      <TopBar />
      <div className="page-admin" id="main-content">
        <div className="page-head">
          <div>
            <Link to="/admin" style={{ fontSize: 12.5, color: "var(--ink-2)", display: "inline-block", marginBottom: 8 }}>
              {t(lang, "admin.backToDashboard")}
            </Link>
            <h1 className="page-title display">{t(lang, "admin.aiSection")}</h1>
            <p className="page-sub">{t(lang, "admin.aiSubtitle")}</p>
          </div>
        </div>

        {error && <div className="banner-error">{error}</div>}
        {summaryError && <div className="banner-error">{summaryError}</div>}
        {loading && (
          <div className="surface-card" style={{ padding: 18 }}>
            {[0, 1].map((i) => (
              <div key={i} className="skeleton" style={{ width: "100%", height: 18, marginBottom: i < 1 ? 14 : 0 }} />
            ))}
          </div>
        )}

        {!loading && summary && (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 12 }}>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.aiTotalRequests")}</div>
                <div className="display stat-value">{summary.total_requests}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.aiSuccessful")}</div>
                <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{summary.successful_requests}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.aiFailed")}</div>
                <div className="display stat-value" style={{ color: "var(--status-critical)" }}>{summary.failed_requests}</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.aiErrorRate")}</div>
                <div className="display stat-value">{(summary.error_rate * 100).toFixed(1)}%</div>
              </div>
              <div className="surface-card hoverable stat-card">
                <div className="stat-label">{t(lang, "admin.aiAvgLatency")}</div>
                <div className="display stat-value">{Math.round(summary.average_latency_ms)}ms</div>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10, marginBottom: 30 }}>
              {[
                [t(lang, "admin.aiRag"), summary.rag_requests],
                [t(lang, "admin.aiComplaints"), summary.complaint_requests],
                [t(lang, "admin.aiStatus"), summary.status_requests],
                [t(lang, "admin.aiOutOfScope"), summary.out_of_scope_requests],
                [t(lang, "admin.aiClarification"), summary.clarification_requests],
              ].map(([label, value]) => (
                <div key={label as string} className="surface-card" style={{ padding: "10px 14px" }}>
                  <div style={{ fontSize: 10.5, color: "var(--ink-2)", textTransform: "uppercase", fontWeight: 700 }}>{label}</div>
                  <div className="mono" style={{ fontSize: 18, marginTop: 4 }}>{value}</div>
                </div>
              ))}
            </div>
          </>
        )}

        <div className="section-label" style={{ marginBottom: 2 }}>
          <span>{t(lang, "admin.aiModelCosts")}</span>
        </div>
        <p style={{ fontSize: 12, color: "var(--ink-3)", margin: "0 0 12px" }}>{t(lang, "admin.aiModelCostsSub")}</p>

        {modelCostsLoading && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginBottom: 30 }}>
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="surface-card" style={{ padding: 16 }}>
                <div className="skeleton" style={{ width: "60%", height: 11, marginBottom: 10 }} />
                <div className="skeleton" style={{ width: "80%", height: 22, marginBottom: 10 }} />
                <div className="skeleton" style={{ width: "50%", height: 11 }} />
              </div>
            ))}
          </div>
        )}

        {!modelCostsLoading && modelCosts && modelCosts.length === 0 && (
          <p style={{ color: "var(--ink-2)", marginBottom: 30 }}>{t(lang, "admin.aiEmpty")}</p>
        )}

        {!modelCostsLoading && modelCosts && modelCosts.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12, marginBottom: 30 }}>
            {modelCosts.map((m, i) => {
              // Relative to the BUSIEST of the 5 models right now, not a fixed ceiling -- whichever
              // model has the most requests this moment defines "full"; every other tile's bar is
              // shown as its own share of that, and it silently re-bases itself next time this loads
              // if a different model becomes the busiest one.
              const maxRequestCount = Math.max(...modelCosts.map((e) => e.request_count), 1);
              const meterPct = Math.max((m.request_count / maxRequestCount) * 100, 3);
              const accentColor = m.is_free ? "var(--status-resolved)" : "var(--accent-fg)";
              // Meter fill gets its own per-model identity color (same fixed, validated palette
              // used everywhere else in this section's history -- dataviz skill) rather than
              // collapsing every billed model to the same blue: 5 tiles side by side read faster
              // when each one's bar is visually its own, not 4 identical bars plus 1 green one.
              const meterColor = `var(--chart-series-${(i % 5) + 1})`;
              const meterTitle = `${m.request_count} / ${maxRequestCount} ${t(lang, "admin.aiModelCostsRequests")} — ${t(lang, "admin.aiModelCostsMeterHint")}`;
              return (
              <div
                key={m.model_name}
                className="surface-card hoverable ai-model-cost-card"
                style={{ padding: "14px 16px", borderTop: `3px solid ${accentColor}` }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                  <span style={{ fontSize: 10.5, textTransform: "uppercase", fontWeight: 700, color: "var(--ink-2)", letterSpacing: "0.03em" }}>
                    {m.label}
                  </span>
                  {/* Vendor + model id share one right-aligned column, model id directly under its
                      own vendor -- keeps the card to 3 lines total instead of a separate full-width
                      model-id row further down. Model id itself stays on a single line (ellipsis +
                      a native title tooltip if a card ever gets too narrow to fit it) rather than
                      wrapping mid-word. */}
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 2, minWidth: 0, flexShrink: 1 }}>
                    <span style={{ fontSize: 10, color: "var(--ink-3)", whiteSpace: "nowrap" }}>{m.vendor}</span>
                    <span
                      className="mono"
                      title={m.model_name}
                      style={{
                        fontSize: 8.5,
                        color: "var(--ink-3)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        maxWidth: "100%",
                      }}
                    >
                      {m.model_name}
                    </span>
                  </div>
                </div>
                <div
                  className="display"
                  style={{
                    fontSize: 24,
                    marginTop: 8,
                    fontVariantNumeric: "tabular-nums",
                    color: m.is_free ? "var(--status-resolved)" : "var(--ink)",
                  }}
                >
                  {m.is_free ? t(lang, "admin.aiModelCostsFree") : `₹${m.total_cost_inr.toFixed(4)}`}
                </div>
                {/* Usage meter -- see the maxRequestCount comment above for what "full" means here.
                    Native title tooltip carries that same explanation on hover, so the meaning
                    isn't only ever explained in chat -- it's on the element itself. LIVE-REPORTED:
                    `cursor: "help"` here drew the OS's own "?" badge on the pointer on hover, read
                    as a stray/confusing question mark rather than an affordance -- default cursor
                    instead, same as the rest of this static card. */}
                <div
                  title={meterTitle}
                  style={{ height: 6, borderRadius: 3, background: "var(--surface-2)", overflow: "hidden", marginTop: 10 }}
                >
                  <div
                    style={{
                      height: "100%",
                      borderRadius: 3,
                      width: `${meterPct}%`,
                      background: meterColor,
                      transition: "width 0.5s ease",
                    }}
                  />
                </div>
                <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 7 }}>
                  {m.total_tokens.toLocaleString()} · {m.request_count} {t(lang, "admin.aiModelCostsRequests")}
                </div>
              </div>
              );
            })}
          </div>
        )}

        <div className="section-label">
          <span>{t(lang, "admin.aiRecentRequests")}</span>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
          {selectedIds.size > 0 && (
            <button className="btn btn-danger btn-sm" onClick={() => setBulkDeleteConfirm(true)}>
              {t(lang, "admin.deleteSelected")} ({selectedIds.size})
            </button>
          )}
          <SearchWithDateFilter
            searchValue={requestsSearch}
            onSearchChange={setRequestsSearch}
            searchPlaceholder={t(lang, "admin.searchAiRequests")}
            dateFrom={dateFrom}
            dateTo={dateTo}
            onDateFromChange={setDateFrom}
            onDateToChange={setDateTo}
            lang={lang}
            width={340}
            onAnyChange={() => setSelectedIds(new Set())}
          />
        </div>

        {!loading && requests.length === 0 && (
          <p style={{ color: "var(--ink-2)" }}>{t(lang, debouncedRequestsSearch ? "admin.aiNoSearchResults" : "admin.aiEmpty")}</p>
        )}

        {!loading && requests.length > 0 && (
          <div className="surface-card table-scroll" style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 640 }}>
              <thead>
                <tr>
                  <th style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", width: 1 }}>
                    <SelectAllCheckbox pageIds={pagedIds} selected={selectedIds} onToggle={() => togglePage(pagedIds)} />
                  </th>
                  {[
                    t(lang, "admin.aiColTime"),
                    t(lang, "admin.aiColRequestId"),
                    t(lang, "admin.aiColRoute"),
                    t(lang, "admin.aiColIntent"),
                  ].map((h) => (
                    <th key={h} style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                      {h}
                    </th>
                  ))}
                  <th style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                    <button
                      type="button"
                      onClick={toggleLatencySort}
                      title={t(lang, "admin.aiSortByLatency")}
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4,
                        background: "none",
                        border: "none",
                        padding: 0,
                        font: "inherit",
                        textTransform: "inherit",
                        letterSpacing: "inherit",
                        color: latencySort === "none" ? "var(--ink-3)" : "var(--ink)",
                        cursor: "pointer",
                      }}
                    >
                      {t(lang, "admin.aiColLatency")}
                      <span aria-hidden="true">{latencySort === "desc" ? "▼" : latencySort === "asc" ? "▲" : "⇅"}</span>
                    </button>
                  </th>
                  {[t(lang, "admin.aiColCost"), t(lang, "admin.aiColStatus"), t(lang, "admin.aiColTrace")].map((h) => (
                    <th key={h} style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                      {h}
                    </th>
                  ))}
                  <th style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }} />
                </tr>
              </thead>
              <tbody>
                {sortedRequests.map((r, i) => {
                  const isSlow = summary != null && r.latency_ms >= summary.latency_alert_threshold_ms;
                  return (
                  <tr key={r.id} className="table-row-hover enter" style={{ "--stagger": Math.min(i, 6) } as React.CSSProperties}>
                    <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                      <input type="checkbox" checked={selectedIds.has(r.id)} onChange={() => toggleOne(r.id)} />
                    </td>
                    <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                    <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }} title={r.request_id}>
                      {r.request_id}
                    </td>
                    <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>{r.routed_to}</td>
                    <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{r.intent ?? "—"}</td>
                    {/* Highlighted whenever this ONE request's own latency already crosses the same
                        threshold the "High AI latency" alert uses for its 20-request average --
                        the alert can fire even if no single request looks this bad (many
                        moderately-slow ones), so this is a helpful pointer, not a guarantee every
                        alert traces back to a highlighted row here. */}
                    <td
                      className="mono"
                      style={{
                        padding: "12px 16px",
                        borderBottom: "1px solid var(--line)",
                        color: isSlow ? "var(--status-critical)" : undefined,
                        fontWeight: isSlow ? 700 : undefined,
                      }}
                      title={isSlow ? t(lang, "admin.aiLatencyAboveThreshold") : undefined}
                    >
                      {Math.round(r.latency_ms)}ms{isSlow ? " ⚠" : ""}
                    </td>
                    <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                      {r.ai_cost_inr != null ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                          <span>₹{r.ai_cost_inr.toFixed(4)}</span>
                          {r.ai_model_name && (
                            <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
                              {r.ai_model_name}
                              {r.ai_total_tokens != null ? ` · ${r.ai_total_tokens} tok` : ""}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span style={{ color: "var(--ink-3)" }}>—</span>
                      )}
                    </td>
                    <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: r.success ? "var(--status-resolved)" : "var(--status-critical)" }}>
                      {r.success ? t(lang, "admin.aiStatusOk") : t(lang, "admin.aiStatusFailed")}
                    </td>
                    <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {r.trace_url ? (
                          <a href={r.trace_url} target="_blank" rel="noopener noreferrer">
                            {t(lang, "admin.aiViewTrace")}
                          </a>
                        ) : (
                          <span style={{ color: "var(--ink-3)" }}>{t(lang, "admin.aiNoTraceLink")}</span>
                        )}
                        {r.phoenix_trace_url ? (
                          <a href={r.phoenix_trace_url} target="_blank" rel="noopener noreferrer">
                            {t(lang, "admin.aiViewPhoenixTrace")}
                          </a>
                        ) : null}
                      </div>
                    </td>
                    <td style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                      <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                        <button
                          className="icon-action-btn danger"
                          aria-label={t(lang, "admin.deleteAiRequestAction")}
                          title={t(lang, "admin.deleteAiRequestAction")}
                          onClick={() => setDeleteTarget(r)}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>

            {requestsPageCount > 1 && (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderTop: "1px solid var(--line)" }}>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={requestsPage <= 1 || tableBusy}
                  onClick={() => setRequestsPage((p) => Math.max(1, p - 1))}
                >
                  {t(lang, "admin.paginationPrev")}
                </button>
                <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
                  {requestsPage} / {requestsPageCount}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={requestsPage >= requestsPageCount || tableBusy}
                  onClick={() => setRequestsPage((p) => Math.min(requestsPageCount, p + 1))}
                >
                  {t(lang, "admin.paginationNext")}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {deleteTarget && (
        <ConfirmModal
          title={t(lang, "admin.deleteAiRequestConfirmTitle")}
          message={`${t(lang, "admin.deleteAiRequestConfirmMessage")} ${deleteTarget.request_id}`}
          confirmLabel={t(lang, "admin.deleteAction")}
          cancelLabel={t(lang, "addWorker.cancel")}
          closeLabel={t(lang, "common.close")}
          danger
          saving={deleting}
          onConfirm={confirmDeleteRequest}
          onClose={() => setDeleteTarget(null)}
        />
      )}

      {bulkDeleteConfirm && (
        <ConfirmModal
          title={t(lang, "admin.bulkDeleteAiRequestsConfirmTitle")}
          message={`${t(lang, "admin.bulkDeleteConfirmMessage")} ${selectedIds.size}?`}
          confirmLabel={t(lang, "admin.deleteAction")}
          cancelLabel={t(lang, "addWorker.cancel")}
          closeLabel={t(lang, "common.close")}
          danger
          saving={bulkDeleting}
          onConfirm={confirmBulkDelete}
          onClose={() => setBulkDeleteConfirm(false)}
        />
      )}
    </div>
  );
}
