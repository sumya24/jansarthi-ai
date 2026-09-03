import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import ComplaintUpdatesTimeline from "../components/ComplaintUpdatesTimeline";
import EvidenceGallery, { evidenceToGalleryItems, type GalleryItem } from "../components/EvidenceGallery";
import ComplaintReportView from "../components/ComplaintReportView";
import DownloadReportButton from "../components/DownloadReportButton";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { statusLabel } from "../lib/statusLabel";
import { api, ApiError, type ComplaintDetail, type ComplaintReport } from "../lib/api";

const STATUS_LABEL_KEY = {
  // See CitizenDashboard.tsx's own copy of this map for why "open" (the complaint's brand-new,
  // usually-transient status) needs an explicit entry -- without one, StatusBadge rendered with
  // no label text at all for a complaint sitting in that state.
  open: "citizen.trackSubmitted",
  pending: "admin.pendingStat",
  assigned: "admin.filterAssigned",
  accepted: "admin.filterAccepted",
  in_progress: "citizen.trackInProgress",
  resolved: "admin.resolvedStat",
} as const;

/** Admin's read-only view of one complaint -- the same full picture the worker sees on their own
 * task-detail page (citizen's original complaint + photos, location, assigned worker, every
 * worker-authored update, the full status timeline, and the resolution report once resolved),
 * but with no action buttons: admin oversees, they don't accept/reject/start/complete a
 * complaint on a worker's behalf (see routes/complaints.py's worker-only action endpoints -- this
 * page never calls any of them). Reached by clicking a complaint's ID on the main Admin Dashboard
 * table, or a future complaint-linked admin notification (see NotificationBell.tsx). */
export default function AdminComplaintDetail() {
  const { id } = useParams<{ id: string }>();
  const complaintId = Number(id);
  const { token } = useAuth();
  const { lang } = useUiLang();

  const [complaint, setComplaint] = useState<ComplaintDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [report, setReport] = useState<ComplaintReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(false);

  async function load() {
    if (!token || !Number.isFinite(complaintId)) return;
    setLoading(true);
    setLoadError(null);
    try {
      setComplaint(await api.getComplaint(token, complaintId, lang));
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : t(lang, "worker.detail.loadFailed"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, complaintId, lang]);

  useEffect(() => {
    if (complaint?.status !== "resolved" || !token) return;
    setReportLoading(true);
    setReportError(null);
    api
      .getComplaintReport(token, complaintId, lang)
      .then(setReport)
      .catch((err) => setReportError(err instanceof ApiError ? err.message : t(lang, "worker.detail.reportLoadFailed")))
      .finally(() => setReportLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complaint?.status, token, complaintId, lang]);

  if (loading) {
    return (
      <div>
        <TopBar />
        <div className="page" id="main-content">
          <div className="skeleton" style={{ width: "40%", height: 20, marginBottom: 16 }} />
          <div className="skeleton" style={{ width: "100%", height: 120 }} />
        </div>
      </div>
    );
  }

  if (loadError || !complaint) {
    return (
      <div>
        <TopBar />
        <div className="page" id="main-content">
          <Link to="/admin" className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
            ← {t(lang, "worker.detail.back")}
          </Link>
          <div className="banner-error">{loadError || t(lang, "worker.detail.notFound")}</div>
        </div>
      </div>
    );
  }

  const c = complaint;

  const citizenEvidence: GalleryItem[] = evidenceToGalleryItems(c.evidence.filter((e) => e.stage === "CITIZEN_COMPLAINT"));
  if (c.photo_path && !citizenEvidence.some((item) => item.filePath === c.photo_path)) {
    citizenEvidence.push({ filePath: c.photo_path, uploaderRole: "citizen" });
  }

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <Link to="/admin" className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
          ← {t(lang, "worker.detail.back")}
        </Link>

        <div className="page-head">
          <div>
            <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>JM-{String(c.id).padStart(5, "0")}</div>
            <h1 className="page-title display" style={{ fontSize: 24 }}>{c.display_text}</h1>
          </div>
          <StatusBadge status={c.status} label={t(lang, STATUS_LABEL_KEY[c.status])} />
        </div>

        <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
          <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.citizenComplaint")}</div>
          <p style={{ margin: "0 0 10px" }}>{c.display_summary}</p>
          {citizenEvidence.length > 0 && <EvidenceGallery items={citizenEvidence} />}

          <div className="section-label">{t(lang, "worker.detail.location")}</div>
          <p style={{ margin: 0 }}>
            {[c.ward, c.location_ulb, c.location_district, c.location_state].filter(Boolean).join(", ") || t(lang, "worker.noWard")}
            {c.address ? ` — ${c.address}` : ""}
          </p>

          {c.assigned_worker_name && (
            <>
              <div className="section-label">{t(lang, "worker.detail.assignedTo")}</div>
              <p style={{ margin: 0 }}>{c.assigned_worker_name}</p>
            </>
          )}
        </div>

        {c.status === "resolved" && (
          <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
            <DownloadReportButton complaintId={c.id} className="btn btn-primary" />
          </div>
        )}

        <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
          <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.updates")}</div>
          <ComplaintUpdatesTimeline updates={c.updates} />
        </div>

        <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
          <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.timeline")}</div>
          {c.status_history.length === 0 && <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "updates.none")}</p>}
          {c.status_history.map((h, i) => (
            <div key={i} className="status-history-entry">
              <span>{h.from_status ? `${statusLabel(lang, h.from_status)} → ${statusLabel(lang, h.to_status)}` : statusLabel(lang, h.to_status)}</span>
              <span className="mono" style={{ color: "var(--ink-3)" }}>{new Date(h.created_at).toLocaleString()}</span>
            </div>
          ))}
        </div>

        {c.rejections.length > 0 && (
          <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
            <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "admin.rejectionHistory")}</div>
            {c.rejections.map((r, i) => (
              <div key={i} className="status-history-entry" style={{ display: "block" }}>
                <div>
                  <strong>{t(lang, "admin.rejectedBy")}:</strong> {r.worker_name}
                  <span className="mono" style={{ color: "var(--ink-3)", marginLeft: 10 }}>
                    {new Date(r.created_at).toLocaleString()}
                  </span>
                </div>
                {r.reason && (
                  <div style={{ marginTop: 4 }}>
                    <strong>{t(lang, "admin.rejectionReason")}:</strong> {r.reason}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {c.status === "resolved" && (
          <div className="surface-card" style={{ padding: 18 }}>
            <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.report")}</div>
            {reportLoading && <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "common.loading")}</p>}
            {reportError && <div className="banner-error">{reportError}</div>}
            {report && <ComplaintReportView report={report} />}
          </div>
        )}
      </div>
    </div>
  );
}
