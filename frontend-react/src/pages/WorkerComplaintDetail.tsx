import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import TopBar from "../components/TopBar";
import StatusBadge from "../components/StatusBadge";
import ComplaintUpdatesTimeline from "../components/ComplaintUpdatesTimeline";
import EvidenceGallery, { evidenceToGalleryItems, type GalleryItem } from "../components/EvidenceGallery";
import RejectComplaintModal from "../components/RejectComplaintModal";
import StartWorkModal from "../components/StartWorkModal";
import ProgressUpdateModal from "../components/ProgressUpdateModal";
import CompleteComplaintModal from "../components/CompleteComplaintModal";
import ComplaintReportView from "../components/ComplaintReportView";
import DownloadReportButton from "../components/DownloadReportButton";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { formatDateTime, t } from "../lib/i18n";
import { localizeWardText } from "../lib/locationNames";
import { statusLabel } from "../lib/statusLabel";
import { api, ApiError, type ComplaintDetail, type ComplaintReport } from "../lib/api";
import { useToast } from "../lib/toast";

const STATUS_LABEL_KEY = {
  // See WorkerDashboard.tsx's own copy of this map -- without an explicit "open" entry,
  // StatusBadge rendered no label text at all for a complaint sitting in that state.
  open: "worker.filterAssigned",
  pending: "worker.filterAssigned",
  assigned: "worker.statusAssigned",
  accepted: "worker.statusAccepted",
  in_progress: "worker.statusAccepted",
  resolved: "worker.resolved",
} as const;

/** Worker task-detail page -- the central place to manage one complaint: full complaint info,
 * location, citizen's original complaint + photo, current status, status timeline, every
 * worker-authored update, and every action available for the complaint's current status (the
 * same functional rules as the list-view card, see WorkerDashboard.tsx). Reached either by
 * clicking a card in the queue or a notification (see NotificationBell.tsx) -- never a dead
 * end. */
export default function WorkerComplaintDetail() {
  const { id } = useParams<{ id: string }>();
  const complaintId = Number(id);
  const { token } = useAuth();
  const { lang } = useUiLang();
  const toast = useToast();

  const [complaint, setComplaint] = useState<ComplaintDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);

  const [modal, setModal] = useState<"reject" | "start" | "update" | "complete" | null>(null);

  const [report, setReport] = useState<ComplaintReport | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(false);

  // LIVE-REPORTED, same real race as WorkerDashboard.tsx's own identical fix (see that file's
  // comment for the full story): this page calls load() from FIVE separate places -- the initial
  // mount effect, accept(), and every one of the four action modals' own onClose/onDone callbacks
  // (Reject/StartWork/ProgressUpdate/CompleteComplaint) -- with nothing stopping an earlier-issued
  // call (e.g. the initial mount's own load(), still reflecting the complaint's PRE-action status)
  // from resolving AFTER a later one (reflecting the just-actioned status), silently overwriting
  // fresh data with stale data purely by network/scheduling luck. Confirmed live: after "Start
  // Work", the "Add Update" button (which only renders once complaint.status === "in_progress")
  // sometimes never appeared, and after "Mark Resolved" the resolved status badge sometimes never
  // appeared either -- exactly this stale-overwrite pattern. loadRequestIdRef tags each load()
  // call with a strictly-increasing id and only ever commits state from the most recently issued
  // call, so a late-arriving stale response is silently discarded instead of winning the race.
  const loadRequestIdRef = useRef(0);

  async function load() {
    if (!token || !Number.isFinite(complaintId)) return;
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getComplaint(token, complaintId, lang);
      if (requestId !== loadRequestIdRef.current) return; // superseded by a newer load() -- discard
      setComplaint(data);
    } catch (err) {
      if (requestId !== loadRequestIdRef.current) return; // superseded -- discard this error too
      setLoadError(err instanceof ApiError ? err.message : t(lang, "worker.detail.loadFailed"));
    } finally {
      if (requestId === loadRequestIdRef.current) setLoading(false);
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

  async function accept() {
    if (!token || !complaint) return;
    setActing(true);
    try {
      await api.acceptComplaint(token, complaint.id);
      toast.success(t(lang, "worker.acceptedToast"));
      await load();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t(lang, "worker.errAcceptFailed"));
    } finally {
      setActing(false);
    }
  }

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
          <Link to="/worker" className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
            ← {t(lang, "worker.detail.back")}
          </Link>
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
        <Link to="/worker" className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }}>
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
            {[c.ward ? localizeWardText(c.ward, lang) : null, c.location_ulb, c.location_district, c.location_state].filter(Boolean).join(", ") || t(lang, "worker.noWard")}
            {c.address ? ` — ${c.address}` : ""}
          </p>

          {c.assigned_worker_name && (
            <>
              <div className="section-label">{t(lang, "worker.detail.assignedTo")}</div>
              <p style={{ margin: 0 }}>{c.assigned_worker_name}</p>
            </>
          )}
        </div>

        {/* Actions -- exactly the same per-status rules as the queue card (see
            WorkerDashboard.tsx's STATUS_LABEL_KEY / action block). */}
        <div className="surface-card" style={{ padding: 18, marginBottom: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {c.status === "assigned" && (
            <>
              <button className="btn btn-primary" onClick={accept} disabled={acting}>
                {acting ? t(lang, "worker.accepting") : t(lang, "worker.accept")}
              </button>
              <button className="btn btn-ghost" onClick={() => setModal("reject")} disabled={acting}>
                {t(lang, "worker.reject")}
              </button>
            </>
          )}
          {c.status === "accepted" && (
            <button className="btn btn-primary" onClick={() => setModal("start")}>
              {t(lang, "worker.startWork")}
            </button>
          )}
          {c.status === "in_progress" && (
            <>
              <button className="btn btn-ghost" onClick={() => setModal("update")}>
                {t(lang, "worker.addUpdate")}
              </button>
              <button className="btn btn-primary" onClick={() => setModal("complete")}>
                {t(lang, "worker.completeComplaint")}
              </button>
            </>
          )}
          {c.status === "resolved" && <DownloadReportButton complaintId={c.id} className="btn btn-primary" />}
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
              <span className="mono" style={{ color: "var(--ink-3)" }}>{formatDateTime(h.created_at, lang)}</span>
            </div>
          ))}
        </div>

        {c.status === "resolved" && (
          <div className="surface-card" style={{ padding: 18 }}>
            <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "worker.detail.report")}</div>
            {reportLoading && <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "common.loading")}</p>}
            {reportError && <div className="banner-error">{reportError}</div>}
            {report && <ComplaintReportView report={report} />}
          </div>
        )}
      </div>

      {modal === "reject" && (
        <RejectComplaintModal complaintId={c.id} onClose={() => setModal(null)} onRejected={() => { toast.success(t(lang, "worker.rejectedConfirm")); load(); }} />
      )}
      {modal === "start" && (
        <StartWorkModal complaintId={c.id} onClose={() => setModal(null)} onStarted={() => { toast.success(t(lang, "worker.start.startedToast")); load(); }} />
      )}
      {modal === "update" && (
        <ProgressUpdateModal complaintId={c.id} onClose={() => setModal(null)} onAdded={() => { toast.success(t(lang, "worker.update.addedToast")); load(); }} />
      )}
      {modal === "complete" && (
        <CompleteComplaintModal complaintId={c.id} onClose={() => setModal(null)} onResolved={() => { toast.success(t(lang, "worker.resolvedToast")); load(); }} />
      )}
    </div>
  );
}
