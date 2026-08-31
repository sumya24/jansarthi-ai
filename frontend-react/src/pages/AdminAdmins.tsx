import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import AddAdminModal from "../components/AddAdminModal";
import EditAdminModal from "../components/EditAdminModal";
import ConfirmModal from "../components/ConfirmModal";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { t } from "../lib/i18n";
import { api, ApiError, type UserProfile } from "../lib/api";
import { useToast } from "../lib/toast";
import "../styles/dashboard.css";

const ADMINS_PAGE_SIZE = 15;

/** Same hand-drawn stroke language as AdminWorkers.tsx's own copy -- duplicated, not shared,
 * matching how that page already keeps its page-scoped action icons local rather than a shared
 * component for two otherwise-unrelated tables. */
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

/** Its own page (not a section of AdminDashboard), same reasoning as AdminWorkers.tsx. Reached
 * via the "Manage Admins" link on AdminDashboard/NavDrawer, both only shown to super admins --
 * but this page also enforces that server-side (GET/POST/DELETE /admin/admins all 403 for a
 * non-super admin), so there's no client-only gate a non-super admin could bypass by navigating
 * here directly.
 *
 * LIVE-REPORTED: this originally shipped unpaginated on the (wrong, in hindsight) assumption that
 * a real deployment has a handful of admins at most -- true for genuinely distinct admins, but the
 * local dev database had accumulated 80 rows from earlier automated test runs, making an
 * unpaginated table genuinely unusable during manual QA. Search + pagination now match
 * AdminWorkers.tsx's own contract (backend's list_admins() same opt-in page/page_size params) --
 * simpler than that page otherwise (no bulk-select/date-filter/edit -- an admin account has no
 * ward or workload stats to bulk-manage the way a worker's does). */
export default function AdminAdmins() {
  const { token, user } = useAuth();
  const { lang } = useUiLang();
  const toast = useToast();

  const [admins, setAdmins] = useState<UserProfile[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAddAdmin, setShowAddAdmin] = useState(false);
  const [editTarget, setEditTarget] = useState<UserProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<UserProfile | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const debouncedSearch = useDebouncedValue(search);

  // Same isFirstLoad split as AdminWorkers.tsx -- only the very first fetch shows the skeleton;
  // a later reload (paging, search) must not unmount the search bar the admin is still using.
  const isFirstLoad = useRef(true);

  async function load() {
    if (!token) return;
    if (isFirstLoad.current) setLoading(true);
    setLoadError(null);
    try {
      const result = await api.listAdmins(token, {
        search: debouncedSearch || undefined,
        page,
        pageSize: ADMINS_PAGE_SIZE,
      });
      setAdmins(result.items);
      setTotal(result.total);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "admin.errLoadFailed"));
    } finally {
      if (isFirstLoad.current) {
        setLoading(false);
        isFirstLoad.current = false;
      }
    }
  }

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, debouncedSearch, page]);

  async function confirmDelete() {
    if (!token || !deleteTarget) return;
    setDeleting(true);
    try {
      await api.deleteAdmin(token, deleteTarget.id);
      toast.success(`${t(lang, "addAdmin.deletedToast")} ${deleteTarget.full_name}`);
      setDeleteTarget(null);
      load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "addAdmin.deleteErrFailed"));
    } finally {
      setDeleting(false);
    }
  }

  const pageCount = Math.max(1, Math.ceil(total / ADMINS_PAGE_SIZE));

  return (
    <div>
      <TopBar />
      <div className="page-admin" id="main-content">
        <div className="page-head">
          <div>
            <Link to="/admin" style={{ fontSize: 12.5, color: "var(--ink-2)", display: "inline-block", marginBottom: 8 }}>
              {t(lang, "admin.backToDashboard")}
            </Link>
            <h1 className="page-title display">{t(lang, "admin.adminsSection")}</h1>
            <p className="page-sub">{t(lang, "admin.adminsPageSubtitle")}</p>
          </div>
          <button className="btn btn-primary btn-sm" onClick={() => setShowAddAdmin(true)}>
            {t(lang, "admin.addAdmin")}
          </button>
        </div>

        {loadError && <div className="banner-error">{loadError}</div>}
        {loading && (
          <div className="surface-card" style={{ padding: 18 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ width: "100%", height: 18, marginBottom: i < 2 ? 14 : 0 }} />
            ))}
          </div>
        )}

        {!loading && !loadError && (
          <>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={t(lang, "admin.searchAdmins")}
                style={{
                  width: 280, border: "1px solid var(--line)", background: "var(--paper)", color: "var(--ink)",
                  borderRadius: 8, padding: "8px 12px", fontSize: 13, fontFamily: "inherit",
                }}
              />
            </div>

            {total === 0 ? (
              <p style={{ color: "var(--ink-2)" }}>{debouncedSearch ? t(lang, "admin.noAdminSearchResults") : t(lang, "admin.noAdmins")}</p>
            ) : (
              <div className="surface-card table-scroll" style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: 560 }}>
                  <thead>
                    <tr>
                      {[
                        t(lang, "admin.colName"),
                        t(lang, "admin.colPhone"),
                        t(lang, "admin.colLanguage"),
                        t(lang, "admin.colRole"),
                        "",
                      ].map((h, i) => (
                        <th key={i} style={{ textAlign: "left", fontSize: 10.5, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 700, padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {admins.map((a, i) => (
                      <tr key={a.id} className="table-row-hover enter" style={{ "--stagger": Math.min(i, 6) } as React.CSSProperties}>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", fontWeight: 700 }}>{a.full_name}</td>
                        <td className="mono" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{a.phone}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", color: "var(--ink-2)" }}>{a.preferred_language}</td>
                        <td style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
                          {a.super_admin ? (
                            <span className="status-badge assigned">
                              {t(lang, "admin.superAdminBadge")}
                            </span>
                          ) : (
                            <span className="status-badge accepted">
                              {t(lang, "admin.adminBadge")}
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" }}>
                          <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}>
                            <button
                              className="icon-action-btn"
                              aria-label={t(lang, "admin.editAdminAction")}
                              title={t(lang, "admin.editAdminAction")}
                              onClick={() => setEditTarget(a)}
                            >
                              <EditIcon />
                            </button>
                            <button
                              className="icon-action-btn danger"
                              aria-label={t(lang, "admin.deleteAction")}
                              title={a.id === user?.id ? t(lang, "addAdmin.cannotDeleteSelf") : t(lang, "admin.deleteAction")}
                              disabled={a.id === user?.id}
                              onClick={() => setDeleteTarget(a)}
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

      {showAddAdmin && <AddAdminModal onClose={() => setShowAddAdmin(false)} onCreated={load} />}

      {editTarget && <EditAdminModal admin={editTarget} onClose={() => setEditTarget(null)} onUpdated={load} />}

      {deleteTarget && (
        <ConfirmModal
          title={t(lang, "addAdmin.deleteConfirmTitle")}
          message={`${t(lang, "addAdmin.deleteConfirmMessage")} ${deleteTarget.full_name}?`}
          confirmLabel={t(lang, "admin.deleteAction")}
          cancelLabel={t(lang, "addWorker.cancel")}
          closeLabel={t(lang, "common.close")}
          danger
          saving={deleting}
          onConfirm={confirmDelete}
          onClose={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
