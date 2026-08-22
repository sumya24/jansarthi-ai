import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import ConfirmModal from "../components/ConfirmModal";
import AssignWorkerModal from "../components/AssignWorkerModal";
import StatusBadge from "../components/StatusBadge";
import ReportModal from "../components/ReportModal";
import SummaryModal from "../components/SummaryModal";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint, type ComplaintStatus, type WorkerSummary } from "../lib/api";
import { useToast } from "../lib/toast";
import "../styles/dashboard.css";

const COMPLAINTS_PAGE_SIZE = 15;

type ComplaintFilter = "all" | ComplaintStatus;

// i18n key for each status, reused from keys that already exist elsewhere on this same page
// (the stat tiles) rather than duplicating "Pending"/"Resolved" strings under a new name.
const COMPLAINT_STATUS_LABEL_KEY: Record<ComplaintStatus | "open", string> = {
  // "open" isn't part of the ComplaintStatus type (the frontend never expected to display it as
  // a distinct state), but it's a real, if usually-transient, backend status -- the complaint's
  // brand-new status at creation, normally gone within the same request once the assignment
  // system's first pass runs (see complaint_agent.py/assignment_service.py). Real data can still
  // be seen sitting at "open" (assignment failed/was skipped), and without this entry
  // StatusBadge rendered no label text at all -- just its icon -- for any complaint in that state.
  open: "citizen.trackSubmitted",
  pending: "admin.pendingStat",
  assigned: "admin.filterAssigned",
  accepted: "admin.filterAccepted",
  // Reuses the citizen-facing tracker's own "In progress" label (already translated in all 6
  // languages) rather than adding a near-duplicate admin-specific string for the same status.
  in_progress: "citizen.trackInProgress",
  resolved: "admin.resolvedStat",
};

/** Same hand-drawn stroke language as components/ServiceIcons.tsx (viewBox 0 0 24 24,
 * ~1.6-1.8px stroke, currentColor, rounded caps/joins) -- kept local to this page since this
 * action (delete) is Admin-only and not reused elsewhere. */
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

/** The header checkbox for a paginated, multi-select table: checked when every row on the
 * CURRENT page is selected, dash ("indeterminate") when some but not all are, empty otherwise.
 * `indeterminate` isn't a settable JSX prop on <input> -- it only exists as a DOM property, so
 * this needs a ref, unlike every other controlled input on this page. */
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

/** Admin's landing page — complaint oversight and management ONLY. Worker management (add/edit/
 * delete/reset password) lives on its own page (see AdminWorkers.tsx), and AI observability on
 * its own (see AdminAiMonitoring.tsx) — both reached via the buttons in the page head, same
 * reasoning in both cases: two different kinds of record (complaints vs. workers) with
 * different columns/actions don't merge cleanly into one table, but they DID need to stop
 * competing for scroll space on one page. The single search box below searches complaints by
 * ward, summary, AND assigned worker name all at once — so finding "everything about Ramesh"
 * doesn't require a second, separate worker search. */
export default function AdminDashboard() {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const toast = useToast();
  const [workers, setWorkers] = useState<WorkerSummary[]>([]);
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [complaintFilter, setComplaintFilter] = useState<ComplaintFilter>("pending");
  const [complaintSearch, setComplaintSearch] = useState("");
  const [complaintPage, setComplaintPage] = useState(1);

  const [deleteComplaintTarget, setDeleteComplaintTarget] = useState<Complaint | null>(null);
  const [assignComplaintTarget, setAssignComplaintTarget] = useState<Complaint | null>(null);
  const [reportTargetId, setReportTargetId] = useState<number | null>(null);
  const [summaryComplaint, setSummaryComplaint] = useState<Complaint | null>(null);
  const [actionSaving, setActionSaving] = useState(false);

  // Bulk selection -- a plain id Set (not "select all filtered/paged") so a selection made on
  // page 1 survives paging to page 2, matching how most admin bulk-action tables behave. Cleared
  // on filter/search change (see below) since a row that's no longer visible for either reason
  // shouldn't stay silently selected.
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleteConfirm, setBulkDeleteConfirm] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  async function load() {
    if (!token) return;
    setLoading(true);
    setLoadError(null);
    try {
      // `workers` is fetched too -- not rendered as its own table here (see AdminWorkers.tsx),
      // but needed for the "Assign" modal's worker picker and to match a search term against a
      // complaint's assigned worker's name.
      const [workersResult, complaintsResult] = await Promise.all([
        api.listWorkers(token),
        api.listComplaints(token),
      ]);
      setWorkers(workersResult);
      setComplaints(complaintsResult);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "admin.errLoadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function confirmDeleteComplaint() {
    if (!token || !deleteComplaintTarget) return;
    setActionSaving(true);
    try {
      await api.deleteComplaint(token, deleteComplaintTarget.id);
      toast.success(t(lang, "admin.complaintDeletedToast"));
      setDeleteComplaintTarget(null);
      load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "admin.complaintDeleteErrFailed"));
    } finally {
      setActionSaving(false);
    }
  }

  async function confirmBulkDelete() {
    if (!token || selectedIds.size === 0) return;
    setBulkDeleting(true);
    const ids = [...selectedIds];
    const results = await Promise.allSettled(ids.map((id) => api.deleteComplaint(token, id)));
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

  const totalPending = complaints.filter((c) => c.status === "pending").length;
  const totalOpen = complaints.filter(
    (c) => c.status === "assigned" || c.status === "accepted" || c.status === "in_progress"
  ).length;
  const totalResolved = complaints.filter((c) => c.status === "resolved").length;

  const filteredComplaints = useMemo(() => {
    let list = complaintFilter === "all" ? complaints : complaints.filter((c) => c.status === complaintFilter);
    const q = complaintSearch.trim().toLowerCase();
    if (q) {
      const qId = q.replace(/^#/, ""); // "#15" and "15" both match complaint id 15
      list = list.filter(
        (c) =>
          String(c.id).includes(qId) ||
          (c.ward ?? "").toLowerCase().includes(q) ||
          (c.display_summary || c.summary || "").toLowerCase().includes(q) ||
          (c.assigned_worker_name ?? "").toLowerCase().includes(q)
      );
    }
    return list;
  }, [complaints, complaintFilter, complaintSearch]);

  const complaintPageCount = Math.max(1, Math.ceil(filteredComplaints.length / COMPLAINTS_PAGE_SIZE));
  const complaintCurrentPage = Math.min(complaintPage, complaintPageCount);
  const pagedComplaints = filteredComplaints.slice(
    (complaintCurrentPage - 1) * COMPLAINTS_PAGE_SIZE,
    complaintCurrentPage * COMPLAINTS_PAGE_SIZE
  );
  const pagedIds = pagedComplaints.map((c) => c.id);

  return (
    <div>
      <TopBar />
      <div className="page-admin" id="main-content">
        <div className="page-head">
          <div>
            <h1 className="page-title display">{t(lang, "admin.title")}</h1>
            <p className="page-sub">{t(lang, "admin.subtitle")}</p>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Link to="/admin/workers" className="btn btn-ghost btn-sm">
              {t(lang, "admin.manageWorkers")}
            </Link>
            <Link to="/admin/ai-monitoring" className="btn btn-ghost btn-sm">
              {t(lang, "admin.viewAiMonitoring")}
            </Link>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 30 }}>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.pendingStat")}</div>
            <div className="display stat-value" style={{ color: "var(--status-critical)" }}>{totalPending}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.openComplaintsStat")}</div>
            <div className="display stat-value" style={{ color: "var(--status-open)" }}>{totalOpen}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.resolvedStat")}</div>
            <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{totalResolved}</div>
          </div>
          <div className="surface-card hoverable stat-card">
            <div className="stat-label">{t(lang, "admin.workersStat")}</div>
            <div className="display stat-value">{workers.length}</div>
          </div>
        </div>

        <div className="section-label">
          <span>{t(lang, "admin.complaintsSection")}</span>
        </div>

        {!loading && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {(["all", "pending", "assigned", "accepted", "in_progress", "resolved"] as const).map((f) => {
                  const count = f === "all" ? complaints.length : complaints.filter((c) => c.status === f).length;
                  const labelKey = f === "all" ? "admin.filterAll" : COMPLAINT_STATUS_LABEL_KEY[f];
                  const active = complaintFilter === f;
                  return (
                    <button
                      key={f}
                      className={`filter-chip btn btn-sm ${active ? "btn-primary" : "btn-ghost"}`}
                      onClick={() => {
                        setComplaintFilter(f);
                        setComplaintPage(1);
                        setSelectedIds(new Set());
                      }}
                    >
                      {t(lang, labelKey)}
                      <span className="mono" style={{ opacity: 0.75, marginLeft: 2 }}>
                        {count}
                      </span>
                    </button>
                  );
                })}
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8, marginLeft: "auto", flex: "0 1 380px", minWidth: 0, maxWidth: 380 }}>
                {selectedIds.size > 0 && (
                  <button className="btn btn-danger btn-sm" onClick={() => setBulkDeleteConfirm(true)}>
                    {t(lang, "admin.deleteSelected")} ({selectedIds.size})
                  </button>
                )}
                {/* This row's own width now comes from `flex: 0 1 380px` above (with `minWidth: 0`
                    so shrinking below content size is actually allowed -- flex items default to
                    `min-width: auto` otherwise) -- a real, definite size the flex algorithm can
                    shrink on a narrow viewport (was: a 380px child forcing an unshrinkable content-
                    sized parent, which caused ~18px of page-level horizontal scroll on mobile) or
                    wrap onto its own line on. `width: 100%` here now resolves against that real
                    size instead of an indefinite one -- previously it silently resolved to
                    whatever sliver of space happened to be left on the flex line, squeezing the
                    input (and its placeholder text) far narrower than the intended 380px even when
                    the line hadn't actually wrapped. */}
                <div className="field" style={{ margin: 0, width: "100%" }}>
                  <input
                    type="text"
                    aria-label={t(lang, "admin.searchComplaintsAndWorkers")}
                    placeholder={t(lang, "admin.searchComplaintsAndWorkers")}
                    value={complaintSearch}
                    onChange={(e) => {
                      setComplaintSearch(e.target.value);
                      setComplaintPage(1);
                      setSelectedIds(new Set());
                    }}
                  />
                </div>
              </div>
            </div>

            {complaints.length === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noComplaints")}</p>
            ) : filteredComplaints.length === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{t(lang, "admin.noComplaintsFiltered")}</p>
            ) : (
              <div className="surface-card table-scroll" style={{ overflowX: "auto", marginBottom: 34 }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 760 }}>
                  <thead>
                    <tr>
                      <th style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", width: 1 }}>
                        <SelectAllCheckbox pageIds={pagedIds} selected={selectedIds} onToggle={() => togglePage(pagedIds)} />
                      </th>
                      {[
                        t(lang, "admin.colId"),
                        t(lang, "admin.colSummary"),
                        t(lang, "admin.colWard"),
                        t(lang, "admin.colStatus"),
                        t(lang, "admin.colWorker"),
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
                    {pagedComplaints.map((c, i) => (
                      <tr key={c.id} className="table-row-hover enter" style={{ "--stagger": Math.min(i, 6) } as React.CSSProperties}>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <input type="checkbox" checked={selectedIds.has(c.id)} onChange={() => toggleOne(c.id)} />
                        </td>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <Link to={`/admin/complaints/${c.id}`} style={{ color: "var(--accent-fg)" }}>#{c.id}</Link>
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          <Link to={`/admin/complaints/${c.id}`} style={{ color: "inherit" }}>{c.display_summary || c.summary}</Link>
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{c.ward ?? "—"}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          <StatusBadge status={c.status} label={t(lang, COMPLAINT_STATUS_LABEL_KEY[c.status])} />
                        </td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{c.assigned_worker_name ?? "—"}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>
                          {new Date(c.created_at).toLocaleDateString()}
                        </td>
                        <td style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                          <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                            {c.status === "pending" && (
                              <button className="btn btn-ghost btn-sm" onClick={() => setAssignComplaintTarget(c)}>
                                {t(lang, "admin.assignAction")}
                              </button>
                            )}
                            {c.status === "resolved" && (
                              <button className="btn btn-ghost btn-sm" onClick={() => setSummaryComplaint(c)}>
                                {t(lang, "worker.viewSummary")}
                              </button>
                            )}
                            {c.status === "resolved" && (
                              <button className="btn btn-ghost btn-sm" onClick={() => setReportTargetId(c.id)}>
                                {t(lang, "worker.viewReport")}
                              </button>
                            )}
                            <button
                              className="icon-action-btn danger"
                              aria-label={t(lang, "admin.deleteComplaintAction")}
                              title={t(lang, "admin.deleteComplaintAction")}
                              onClick={() => setDeleteComplaintTarget(c)}
                            >
                              <TrashIcon />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {complaintPageCount > 1 && (
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "12px 16px", borderTop: "1px solid var(--line)" }}>
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={complaintCurrentPage <= 1}
                      onClick={() => setComplaintPage((p) => Math.max(1, p - 1))}
                    >
                      {t(lang, "admin.paginationPrev")}
                    </button>
                    <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
                      {complaintCurrentPage} / {complaintPageCount}
                    </span>
                    <button
                      className="btn btn-ghost btn-sm"
                      disabled={complaintCurrentPage >= complaintPageCount}
                      onClick={() => setComplaintPage((p) => Math.min(complaintPageCount, p + 1))}
                    >
                      {t(lang, "admin.paginationNext")}
                    </button>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {loadError && <div className="banner-error">{loadError}</div>}
        {loading && (
          <div className="surface-card" style={{ padding: 18 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ width: "100%", height: 18, marginBottom: i < 2 ? 14 : 0 }} />
            ))}
          </div>
        )}
      </div>

      {deleteComplaintTarget && (
        <ConfirmModal
          title={t(lang, "admin.deleteComplaintConfirmTitle")}
          message={`${t(lang, "admin.deleteComplaintConfirmMessage")} #${deleteComplaintTarget.id}?`}
          confirmLabel={t(lang, "admin.deleteAction")}
          cancelLabel={t(lang, "addWorker.cancel")}
          closeLabel={t(lang, "common.close")}
          danger
          saving={actionSaving}
          onConfirm={confirmDeleteComplaint}
          onClose={() => setDeleteComplaintTarget(null)}
        />
      )}

      {bulkDeleteConfirm && (
        <ConfirmModal
          title={t(lang, "admin.bulkDeleteConfirmTitle")}
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

      {assignComplaintTarget && (
        <AssignWorkerModal
          complaint={assignComplaintTarget}
          workers={workers}
          onClose={() => setAssignComplaintTarget(null)}
          onAssigned={load}
        />
      )}

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
