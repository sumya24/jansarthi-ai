import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import AddWorkerModal from "../components/AddWorkerModal";
import EditWorkerModal from "../components/EditWorkerModal";
import ConfirmModal from "../components/ConfirmModal";
import SearchWithDateFilter from "../components/SearchWithDateFilter";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { t } from "../lib/i18n";
import { api, ApiError, type WorkerSummary } from "../lib/api";
import { useToast } from "../lib/toast";
import "../styles/dashboard.css";

// LIVE-REPORTED GAP: this table used to fetch and render EVERY worker in one response, then
// filter/paginate all of it client-side -- search and pagination are now real backend queries
// (GET /admin/workers' own `search`/`page`/`page_size` params), same reasoning as the complaint
// dashboards (see CitizenDashboard.tsx's identical note).
const WORKERS_PAGE_SIZE = 15;

/** Same hand-drawn stroke language as components/ServiceIcons.tsx -- see AdminDashboard.tsx's
 * own copy of this icon for the full convention note. Duplicated locally (not shared) since
 * each is a tiny, page-scoped action icon, matching how AdminDashboard.tsx already does this. */
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

function EditIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
      <path
        d="M14.7 4.7 19.3 9.3 8.5 20.1 4 20.5l.4-4.5 10.3-11.3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <path d="M13 6.5 17.5 11" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/** See AdminDashboard.tsx's own copy of this component for the full rationale (header checkbox
 * reflecting the CURRENT page's selection state, `indeterminate` needing a ref since it isn't a
 * settable JSX prop). Duplicated, not shared, matching how the two trash/edit icons above already
 * are on this page -- each is a tiny, page-scoped concern, not worth a shared component for two
 * call sites with otherwise unrelated tables. */
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

/** Its own page (not a section of AdminDashboard) -- reached via the "Manage Workers" button on
 * AdminDashboard, same reasoning as AdminAiMonitoring.tsx (a worker-management view and a
 * complaint-management view competing for space/scroll on one page stops being readable once
 * either list grows past a handful of rows). */
export default function AdminWorkers() {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const toast = useToast();

  const [workers, setWorkers] = useState<WorkerSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAddWorker, setShowAddWorker] = useState(false);
  const [editTarget, setEditTarget] = useState<WorkerSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkerSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [search, setSearch] = useState("");
  // Same "every search box gets a date filter" rollout as AdminAiMonitoring.tsx -- filters on
  // this worker account's own created_at.
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  // Decoupled from `workers` (the filtered+paged current-page list) on purpose -- these two stat
  // tiles are meant to read as "your whole workforce," the same way every other dashboard's stat
  // cards stay unfiltered regardless of that page's own search/pagination. Backed by the two
  // aggregate headers GET /admin/workers now always returns (see backend's own docstring).
  const [totalOpenComplaints, setTotalOpenComplaints] = useState(0);
  const [totalResolvedComplaints, setTotalResolvedComplaints] = useState(0);
  const debouncedSearch = useDebouncedValue(search);

  // Bulk selection -- see AdminDashboard.tsx's identical field for the reasoning (persists
  // across pages, cleared on search change).
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  // `loading` only gates the page's initial skeleton (before the FIRST fetch resolves) -- every
  // later reload (paging, search, or picking a date in SearchWithDateFilter) must NOT flip it back
  // to true, since the whole search bar/filter row sits behind `!loading` further down. Without
  // this split, entering a date immediately re-triggered `load()`, which unmounted that row --
  // including the open date popover the admin was still typing into -- for the fetch's duration.
  // Same fix already applied in AdminAiMonitoring.tsx (see its own isFirstRequestsLoad).
  const isFirstLoad = useRef(true);

  async function load() {
    if (!token) return;
    if (isFirstLoad.current) setLoading(true);
    setLoadError(null);
    try {
      const result = await api.listWorkers(token, {
        search: debouncedSearch || undefined,
        page,
        pageSize: WORKERS_PAGE_SIZE,
        dateFrom: dateFrom || undefined,
        dateTo: dateTo || undefined,
      });
      setWorkers(result.items);
      setTotal(result.total);
      setTotalOpenComplaints(result.totalOpenComplaints);
      setTotalResolvedComplaints(result.totalResolvedComplaints);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "admin.errLoadFailed"));
    } finally {
      if (isFirstLoad.current) {
        setLoading(false);
        isFirstLoad.current = false;
      }
    }
  }

  // A search edit always jumps back to page 1 -- the previous page number almost never still
  // makes sense against a newly-narrowed result set.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, dateFrom, dateTo]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, debouncedSearch, page, dateFrom, dateTo]);

  async function confirmDelete() {
    if (!token || !deleteTarget) return;
    setDeleting(true);
    try {
      const result = await api.deleteWorker(token, deleteTarget.id);
      toast.success(
        result.reset_to_pending > 0
          ? `${t(lang, "admin.workerDeletedToast")} ${result.reset_to_pending} ${t(lang, "admin.workerDeletedResetSuffix")}`
          : t(lang, "admin.workerDeletedToast")
      );
      setDeleteTarget(null);
      load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "admin.workerDeleteErrFailed"));
    } finally {
      setDeleting(false);
    }
  }

  async function confirmBulkDelete() {
    if (!token || selectedIds.size === 0) return;
    setBulkDeleting(true);
    const ids = [...selectedIds];
    const results = await Promise.allSettled(ids.map((id) => api.deleteWorker(token, id)));
    const succeeded = results.filter((r) => r.status === "fulfilled").length;
    const failed = results.length - succeeded;
    const resetTotal = results.reduce((sum, r) => (r.status === "fulfilled" ? sum + r.value.reset_to_pending : sum), 0);
    if (failed === 0) {
      toast.success(
        resetTotal > 0
          ? `${t(lang, "admin.bulkDeleteSuccessToast")} ${succeeded}. ${resetTotal} ${t(lang, "admin.workerDeletedResetSuffix")}`
          : `${t(lang, "admin.bulkDeleteSuccessToast")} ${succeeded}`
      );
    } else {
      toast.error(`${t(lang, "admin.bulkDeletePartialToast")} ${succeeded}/${results.length}`);
    }
    setSelectedIds(new Set());
    setBulkDeleteConfirm(false);
    setBulkDeleting(false);
    load();
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

  const pageCount = Math.max(1, Math.ceil(total / WORKERS_PAGE_SIZE));
  const pagedIds = workers.map((w) => w.id);

  return (
    <div>
      <TopBar />
      <div className="page-admin" id="main-content">
        <div className="page-head">
          <div>
            <Link to="/admin" style={{ fontSize: 12.5, color: "var(--ink-2)", display: "inline-block", marginBottom: 8 }}>
              {t(lang, "admin.backToDashboard")}
            </Link>
            <h1 className="page-title display">{t(lang, "admin.workersSection")}</h1>
            <p className="page-sub">{t(lang, "admin.workersPageSubtitle")}</p>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddWorker(true)}>
            {t(lang, "admin.addWorker")}
          </button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 30 }}>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.workersStat")}</div>
            <div className="display stat-value">{total}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.openComplaintsStat")}</div>
            <div className="display stat-value" style={{ color: "var(--status-open)" }}>{totalOpenComplaints}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.resolvedStat")}</div>
            <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{totalResolvedComplaints}</div>
          </div>
        </div>

        <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: -20, marginBottom: 16 }}>{t(lang, "admin.addWorkerNote")}</p>

        {loadError && <div className="banner-error">{loadError}</div>}
        {loading && (
          <div className="surface-card" style={{ padding: 18 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ width: "100%", height: 18, marginBottom: i < 2 ? 14 : 0 }} />
            ))}
          </div>
        )}
        {!loading && total === 0 && !debouncedSearch && <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noWorkers")}</p>}

        {!loading && (total > 0 || debouncedSearch) && (
          <>
            <div style={{ display: "flex", justifyContent: "flex-end", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              {selectedIds.size > 0 && (
                <button className="btn btn-danger btn-sm" onClick={() => setBulkDeleteConfirm(true)}>
                  {t(lang, "admin.deleteSelected")} ({selectedIds.size})
                </button>
              )}
              <SearchWithDateFilter
                searchValue={search}
                onSearchChange={setSearch}
                searchPlaceholder={t(lang, "admin.searchWorkers")}
                dateFrom={dateFrom}
                dateTo={dateTo}
                onDateFromChange={setDateFrom}
                onDateToChange={setDateTo}
                lang={lang}
                width={340}
                onAnyChange={() => setSelectedIds(new Set())}
              />
            </div>

            {total === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noSearchResults")}</p>
            ) : (
              <div className="surface-card table-scroll" style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 660 }}>
                  <thead>
                    <tr>
                      <th style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", width: 1 }}>
                        <SelectAllCheckbox pageIds={pagedIds} selected={selectedIds} onToggle={() => togglePage(pagedIds)} />
                      </th>
                      {[
                        t(lang, "admin.colWorker"),
                        t(lang, "admin.colPhone"),
                        t(lang, "admin.colWard"),
                        t(lang, "admin.colOpen"),
                        t(lang, "admin.colResolved"),
                        t(lang, "admin.colLanguage"),
                        "",
                      ].map((h, i) => (
                        <th key={i} style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {workers.map((w, i) => (
                      <tr key={w.id} className="table-row-hover enter" style={{ "--stagger": Math.min(i, 6) } as React.CSSProperties}>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <input type="checkbox" checked={selectedIds.has(w.id)} onChange={() => toggleOne(w.id)} />
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", fontWeight: 700 }}>
                          <Link to={`/admin/workers/${w.id}`} style={{ color: "var(--ink)" }}>
                            {w.full_name}
                          </Link>
                        </td>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{w.phone}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{w.ward}</td>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>{w.open_complaints}</td>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>{w.resolved_complaints}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{w.preferred_language}</td>
                        <td style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                          <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                            <button
                              className="icon-action-btn"
                              aria-label={t(lang, "admin.editWorkerAction")}
                              title={t(lang, "admin.editWorkerAction")}
                              onClick={() => setEditTarget(w)}
                            >
                              <EditIcon />
                            </button>
                            <button
                              className="icon-action-btn danger"
                              aria-label={t(lang, "admin.deleteWorkerAction")}
                              title={t(lang, "admin.deleteWorkerAction")}
                              onClick={() => setDeleteTarget(w)}
                            >
                              <TrashIcon />
                            </button>
                          </div>
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

      {showAddWorker && <AddWorkerModal onClose={() => setShowAddWorker(false)} onCreated={load} />}

      {editTarget && <EditWorkerModal worker={editTarget} onClose={() => setEditTarget(null)} onUpdated={load} />}

      {deleteTarget && (
        <ConfirmModal
          title={t(lang, "admin.deleteWorkerConfirmTitle")}
          message={`${t(lang, "admin.deleteWorkerConfirmMessage")} ${deleteTarget.full_name}?`}
          confirmLabel={t(lang, "admin.deleteAction")}
          cancelLabel={t(lang, "addWorker.cancel")}
          closeLabel={t(lang, "common.close")}
          danger
          saving={deleting}
          onConfirm={confirmDelete}
          onClose={() => setDeleteTarget(null)}
        />
      )}

      {bulkDeleteConfirm && (
        <ConfirmModal
          title={t(lang, "admin.bulkDeleteWorkersConfirmTitle")}
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
