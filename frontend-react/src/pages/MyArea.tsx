import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import CategoryBadge from "../components/CategoryBadge";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { formatDate, t } from "../lib/i18n";
import { api, ApiError, type AreaSummary } from "../lib/api";
import type { ServiceCategory } from "../lib/ragTypes";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { SERVICE_CATEGORY_DEFS } from "../lib/serviceCategories";
import { localizeWardText } from "../lib/locationNames";
import "../styles/dashboard.css";

const STATUS_LABEL_KEY = {
  // See CitizenDashboard.tsx's own copy of this map for why "open" (the complaint's brand-new,
  // usually-transient status) needs an explicit entry -- without one, StatusBadge rendered with
  // no label text at all for a complaint sitting in that state.
  open: "citizen.trackSubmitted",
  pending: "citizen.statusPending",
  assigned: "citizen.statusAssigned",
  accepted: "citizen.statusAccepted",
  // Reuses the citizen-facing tracker's own existing "In progress" label rather than adding a
  // near-duplicate string for the same status.
  in_progress: "citizen.trackInProgress",
  resolved: "citizen.statusResolved",
} as const;

// LIVE-REPORTED GAP: this used to be a hard `.slice(0, MAX_LISTED)` with no way to see anything
// past the first 15 -- fine while a ward had few complaints, but a real ward (everyone's
// complaints, not just this citizen's own) can outgrow that fast. Search and pagination are now
// real backend queries (GET /complaints/area-summary's own `search`/`page`/`page_size` params),
// not a client-side filter over an already-fetched full list -- same reasoning as
// CitizenDashboard.tsx's "My Complaints" and the Admin/Worker dashboards.
const AREA_PAGE_SIZE = 10;

// Same 3 citizen-legible buckets as the stat cards above the list (pending groups the raw
// pending/assigned/accepted statuses into one "hasn't started" bucket -- see backend's
// get_area_summary docstring) -- LIVE-REPORTED GAP: Admin/Worker/Citizen dashboards all got a
// status filter, this page hadn't. `status` sent to the backend is the comma-separated raw set
// GET /complaints/area-summary's own `status` param now accepts (see _parse_status_filter).
const AREA_FILTERS = ["all", "pending", "in_progress", "resolved"] as const;
type AreaFilter = (typeof AREA_FILTERS)[number];
const AREA_FILTER_STATUS_PARAM: Record<Exclude<AreaFilter, "all">, string> = {
  pending: "pending,assigned,accepted",
  in_progress: "in_progress",
  resolved: "resolved",
};
const AREA_FILTER_LABEL_KEY: Record<AreaFilter, string> = {
  all: "admin.filterAll",
  pending: "admin.pendingStat",
  in_progress: "worker.filterInProgress",
  resolved: "citizen.resolved",
};

/**
 * My Area (P1, Task 10, extended): a ward-wide neighborhood dashboard -- every complaint filed
 * in the citizen's own ward, not just their own, backed by the real GET /complaints/area-summary
 * endpoint (see backend/routes/complaints.py's AreaSummaryResponse docstring). Deliberately
 * anonymized: the backend response never includes citizen_id or any complainant-identifying
 * field at all, so there's nothing here to accidentally render even if this component tried to
 * -- the "who filed it" question has no answer available client-side, by construction.
 *
 * Stat cards use the same .stat-card convention as Admin/WorkerDashboard (styles/dashboard.css)
 * for a consistent "pending / in progress / resolved" glance, then a scannable list of the ward's
 * own recent complaints, each showing its current status and the date that status was reached
 * (filed / started / completed) rather than one flat "filed on" date regardless of where it is
 * in its lifecycle.
 */
export default function MyArea() {
  const { token, user } = useAuth();
  const { lang } = useUiLang();
  const [summary, setSummary] = useState<AreaSummary | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<AreaFilter>("all");
  // LIVE-REPORTED REQUEST: a per-service (Waste/Water/Roads/Streetlights) dashboard view --
  // picking one reframes the 3 stat cards into THAT service's own pending/in_progress/resolved
  // breakdown (see backend's get_area_summary docstring), and narrows the list below it, too.
  // Deliberately a SEPARATE row from the 4 service cards further down (which still just link to
  // Report an Issue, unchanged) -- the user explicitly chose keeping those two concerns apart
  // rather than repurposing the cards themselves into filters.
  const [categoryFilter, setCategoryFilter] = useState<ServiceCategory | "all">("all");
  const debouncedSearch = useDebouncedValue(search);

  const ward = user?.ward ?? null;

  // A search edit or filter-chip click always jumps back to page 1 -- the previous page number
  // almost never still makes sense against a newly-narrowed result set.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filter, categoryFilter]);

  useEffect(() => {
    if (!token || !ward) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setLoadError(null);
    api
      .getAreaSummary(token, {
        lang,
        status: filter === "all" ? undefined : AREA_FILTER_STATUS_PARAM[filter],
        category: categoryFilter === "all" ? undefined : categoryFilter,
        search: debouncedSearch || undefined,
        page,
        pageSize: AREA_PAGE_SIZE,
      })
      .then(setSummary)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : t(lang, "area.emptyNoWard")))
      .finally(() => setLoading(false));
  }, [token, lang, ward, filter, categoryFilter, debouncedSearch, page]);

  const pageCount = Math.max(1, Math.ceil((summary?.total ?? 0) / AREA_PAGE_SIZE));

  function statusDateLabel(status: string): string {
    if (status === "resolved") return t(lang, "area.completedOn");
    if (status === "in_progress") return t(lang, "area.startedOn");
    return t(lang, "area.filedOn");
  }

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <div className="page-head">
          <div>
            <h1 className="page-title display">{t(lang, "area.title")}</h1>
            <p className="page-sub">{ward ? localizeWardText(ward, lang) : t(lang, "area.noWardSet")}</p>
          </div>
        </div>

        {/* LIVE-REPORTED REQUEST: a persistent sidebar instead of two chip rows -- this page's
            list (the whole ward, not just one citizen's own complaints) tends to run long enough
            that always-visible filters earn their keep, unlike My Complaints' typically-short
            personal list (which kept the lighter chip-row layout instead; see CitizenDashboard.tsx
            for that comparison). Category still reframes the 3 stat cards exactly as before --
            only the filter controls' own presentation changed, not the data flow. */}
        <div className="area-with-sidebar">
          {ward && (
            <aside className="area-sidebar">
              <h4>{t(lang, "area.categoryFilterLabel")}</h4>
              <button
                type="button"
                className={`area-side-item ${categoryFilter === "all" ? "active" : ""}`}
                onClick={() => setCategoryFilter("all")}
              >
                {t(lang, "admin.filterAll")}
              </button>
              {SERVICE_CATEGORY_DEFS.map((def) => (
                <button
                  key={def.id}
                  type="button"
                  className={`area-side-item ${categoryFilter === def.id ? "active" : ""}`}
                  onClick={() => setCategoryFilter(def.id)}
                >
                  <span className="area-side-dot" style={{ background: `var(--service-${def.color})` }} />
                  {t(lang, def.titleKey)}
                </button>
              ))}

              <h4 className="second">{t(lang, "admin.colStatus")}</h4>
              {AREA_FILTERS.map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`area-side-item ${filter === f ? "active" : ""}`}
                  onClick={() => setFilter(f)}
                >
                  {t(lang, AREA_FILTER_LABEL_KEY[f])}
                </button>
              ))}
            </aside>
          )}

          <div className="area-main-col">
            {ward && !loading && !loadError && summary && (
              <>
                <p style={{ fontSize: 12, color: "var(--ink-2)", margin: "0 0 8px" }}>
                  {t(lang, "area.showingLabel")}:{" "}
                  <strong style={{ color: "var(--ink)" }}>
                    {categoryFilter === "all"
                      ? t(lang, "area.allServicesLabel")
                      : t(lang, SERVICE_CATEGORY_DEFS.find((d) => d.id === categoryFilter)?.titleKey ?? "area.allServicesLabel")}
                  </strong>
                </p>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginBottom: 30 }}>
                  <div className="surface-card hoverable stat-card">
                    <div className="stat-label">{t(lang, "admin.pendingStat")}</div>
                    <div className="display stat-value" style={{ color: "var(--status-critical)" }}>{summary.pending_count}</div>
                  </div>
                  <div className="surface-card hoverable stat-card">
                    <div className="stat-label">{t(lang, "worker.filterInProgress")}</div>
                    <div className="display stat-value" style={{ color: "var(--status-open)" }}>{summary.in_progress_count}</div>
                  </div>
                  <div className="surface-card hoverable stat-card">
                    <div className="stat-label">{t(lang, "citizen.resolved")}</div>
                    <div className="display stat-value" style={{ color: "var(--status-resolved)" }}>{summary.resolved_count}</div>
                  </div>
                </div>
              </>
            )}

            <div className="section-label">{t(lang, "area.servicesLabel")}</div>
            <div className="service-grid" style={{ marginBottom: 30 }}>
              {SERVICE_CATEGORY_DEFS.map((def) => (
                <Link key={def.id} to={`/citizen/report?service=${def.id}`} className={`service-card service-card-${def.color} surface-card hoverable`}>
                  <div className="service-card-icon">{def.icon}</div>
                  <h3>{t(lang, def.titleKey)}</h3>
                  <p>{t(lang, def.descriptionKey)}</p>
                </Link>
              ))}
            </div>

            <div className="section-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: 10 }}>
              <span>{t(lang, "area.recentLabel")}</span>
              {ward && (
                <div className="field" style={{ margin: 0, width: "100%", maxWidth: 320 }}>
                  <input
                    type="text"
                    aria-label={t(lang, "area.searchComplaints")}
                    placeholder={t(lang, "area.searchComplaints")}
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
              )}
            </div>
            {loading && (
              <div className="surface-card" style={{ padding: "14px 16px" }}>
                <div className="skeleton" style={{ width: "60%", height: 14 }} />
              </div>
            )}
            {!loading && !ward && (
              <div className="surface-card" style={{ padding: 20, color: "var(--ink-2)", fontSize: 13 }}>
                {t(lang, "area.emptyNoWard")}
              </div>
            )}
            {!loading && ward && loadError && <div className="banner-error">{loadError}</div>}
            {!loading && ward && !loadError && summary && summary.total === 0 && filter === "all" && categoryFilter === "all" && !debouncedSearch && (
              <div className="surface-card" style={{ padding: 20, color: "var(--ink-2)", fontSize: 13 }}>
                {t(lang, "area.emptyNoComplaints")}
              </div>
            )}
            {!loading && ward && !loadError && summary && summary.total === 0 && (filter !== "all" || categoryFilter !== "all" || debouncedSearch) && (
              <div className="surface-card" style={{ padding: 20, color: "var(--ink-2)", fontSize: 13 }}>
                {t(lang, "admin.noComplaintsFiltered")}
              </div>
            )}
            {!loading && ward && !loadError && summary && summary.total > 0 && (
              <>
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  {summary.complaints.map((c) => (
                    <div key={c.id} className="surface-card" style={{ padding: "12px 16px", display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                      <div>
                        <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>JM-{String(c.id).padStart(5, "0")}</div>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>{c.display_text}</div>
                        <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2, display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                          <span>{statusDateLabel(c.status)} {formatDate(c.status_updated_at, lang)}</span>
                          <CategoryBadge category={c.service_category} lang={lang} />
                        </div>
                      </div>
                      <StatusBadge status={c.status} label={t(lang, STATUS_LABEL_KEY[c.status])} />
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
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
