import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import ComplaintTracker from "../components/ComplaintTracker";
import ComplaintUpdatesTimeline from "../components/ComplaintUpdatesTimeline";
import EvidenceGallery, { evidenceToGalleryItems, type GalleryItem } from "../components/EvidenceGallery";
import ComplaintReportView from "../components/ComplaintReportView";
import DownloadReportButton from "../components/DownloadReportButton";
import FeedbackForm from "../components/FeedbackForm";
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
  pending: "citizen.statusPending",
  assigned: "citizen.statusAssigned",
  accepted: "citizen.statusAccepted",
  in_progress: "citizen.trackInProgress",
  resolved: "citizen.statusResolved",
} as const;

/** Citizen task-detail page -- the equivalent of WorkerComplaintDetail.tsx for the citizen side:
 * full complaint info, live tracker, the complete timestamped status timeline (status_history,
 * same data WorkerComplaintDetail.tsx already renders -- the citizen was never blocked from
 * seeing it, there was just no page here to show it), every worker-authored update, the
 * resolution report inline once resolved, and the feedback form. No accept/reject/start/complete
 * actions here -- those are worker-only (see backend/routes/complaints.py's require_role calls).
 * Reached either by clicking a card in "My Complaints" (see CitizenDashboard.tsx) or a
 * notification (see NotificationBell.tsx) -- never a dead end, same rule as the worker page. */
export default function CitizenComplaintDetail() {
  const { id } = useParams<{ id: string }>();
  const complaintId = Number(id);
  const { token } = useAuth();
  const { lang } = useUiLang();
  const location = useLocation();
  const navigate = useNavigate();

  // This page is reached from several places now (My Complaints, and a "Needs your attention"/
  // "Recent activity" notification on Home) -- a link hardcoded to "My Complaints" sent a citizen
  // who arrived from Home back to the wrong page instead of where they actually came from.
  // react-router-dom gives the very first entry in a tab's history (a fresh page load, not
  // in-app navigation) the special key "default" -- only THAT case has nowhere real to go back
  // to, so it's the one case that still falls back to My Complaints specifically.
  const canGoBack = location.key !== "default";
  function goBack() {
    if (canGoBack) navigate(-1);
    else navigate("/citizen/complaints");
  }
  const backLabel = canGoBack ? t(lang, "common.back") : t(lang, "citizen.detail.back");

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
      .getComplaintReport(token, complaintId)
      .then(setReport)
      .catch((err) => setReportError(err instanceof ApiError ? err.message : t(lang, "worker.detail.reportLoadFailed")))
      .finally(() => setReportLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [complaint?.status, token, complaintId]);

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
          <button type="button" onClick={goBack} className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
            ← {backLabel}
          </button>
          <div className="banner-error">{loadError || t(lang, "worker.detail.notFound")}</div>
        </div>
      </div>
    );
  }

  const c = complaint;

  // Citizen's own evidence (stage=CITIZEN_COMPLAINT) plus the legacy single `photo_path` for
  // complaints filed before the evidence-upload system existed -- both shown in one gallery.
  const citizenEvidence: GalleryItem[] = evidenceToGalleryItems(c.evidence.filter((e) => e.stage === "CITIZEN_COMPLAINT"));
  if (c.photo_path && !citizenEvidence.some((item) => item.filePath === c.photo_path)) {
    citizenEvidence.push({ filePath: c.photo_path, uploaderRole: "citizen" });
  }

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <button type="button" onClick={goBack} className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
          ← {backLabel}
        </button>

        <div className="page-head">
          <div>
            <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>JM-{String(c.id).padStart(5, "0")}</div>
            <h1 className="page-title display" style={{ fontSize: 24 }}>{c.display_text}</h1>
          </div>
          <StatusBadge status={c.status} label={t(lang, STATUS_LABEL_KEY[c.status])} />
        </div>

        <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
          <ComplaintTracker status={c.status} rejectionCount={c.rejection_count} lang={lang} />

          {c.status === "pending" && c.rejection_count > 0 && (
            <div style={{ fontSize: 12, color: "var(--status-open)", marginTop: 8 }}>
              {t(lang, "citizen.searchingNextWorker")}
            </div>
          )}
        </div>

        <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
          <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "citizen.detail.yourComplaint")}</div>
          <p style={{ margin: "0 0 10px" }}>{c.display_summary}</p>
          {citizenEvidence.length > 0 && <EvidenceGallery items={citizenEvidence} />}

          <div className="section-label">{t(lang, "worker.detail.location")}</div>
          <p style={{ margin: 0 }}>
            {[c.ward, c.location_ulb, c.location_district, c.location_state].filter(Boolean).join(", ") || t(lang, "citizen.noWard")}
            {c.address ? ` — ${c.address}` : ""}
          </p>

          {c.assigned_worker_name && (
            <>
              <div className="section-label">{t(lang, "citizen.assignedTo")}</div>
              <p style={{ margin: 0 }}>
                {c.assigned_worker_name}
                {c.assigned_worker_phone && (
                  <>
                    {" · "}
                    {t(lang, "citizen.workerPhone")}:{" "}
                    <a href={`tel:${c.assigned_worker_phone}`} className="mono">{c.assigned_worker_phone}</a>
                  </>
                )}
              </p>
            </>
          )}
        </div>

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

        {c.status === "resolved" && (
          <div className="surface-card" style={{ padding: 18, marginBottom: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.report")}</div>
              <DownloadReportButton complaintId={c.id} className="btn btn-ghost btn-sm" />
            </div>
            {reportLoading && <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "common.loading")}</p>}
            {reportError && <div className="banner-error">{reportError}</div>}
            {report && <ComplaintReportView report={report} />}
          </div>
        )}

        {c.status === "resolved" && token && (
          <div className="surface-card" style={{ padding: 18 }}>
            {c.feedback_rating ? (
              <>
                <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "citizen.feedbackSubmitted")}</div>
                <div>{"★".repeat(c.feedback_rating)}</div>
                {c.feedback_comment && <div style={{ marginTop: 4, color: "var(--ink-2)", fontSize: 13 }}>{c.feedback_comment}</div>}
              </>
            ) : (
              <FeedbackForm complaintId={c.id} lang={lang} token={token} onSubmitted={load} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
