import { useEffect, useState } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { formatDateTime, t } from "../lib/i18n";
import { api, type Complaint, type ComplaintReport } from "../lib/api";
import StatusBadge from "./StatusBadge";
import ComplaintReportView from "./ComplaintReportView";
import "../styles/dashboard.css";
import { useModalA11y } from "../lib/useModalA11y";
import { localizeWardText } from "../lib/locationNames";

/** "Summary" -- a quick glance at a complaint (id, status, filed date, location, description,
 * assigned worker) built entirely from the row's own `Complaint` object, so it always paints
 * instantly with no network call, for every status.
 *
 * For a RESOLVED complaint, it ALSO fetches and shows the same full resolution report the
 * detail pages (CitizenComplaintDetail / WorkerComplaintDetail / AdminComplaintDetail) already
 * inline once a complaint is resolved -- initial assessment, progress updates, completion,
 * evidence, translated status timeline -- via the same `ComplaintReportView` component, so a
 * citizen/worker/admin doesn't need to leave the list view to see it. Only attempted when
 * `complaint.status === "resolved"`: the backend's own report endpoint 404s for anything
 * earlier (see complaint_report_service.py), and there's genuinely no assessment/completion/
 * timeline content to show yet for an unresolved complaint -- so this never fires a
 * guaranteed-404 request or flashes an error state for the common case. If the fetch itself
 * fails for a resolved complaint (network issue), the quick fields below still stand alone --
 * never a broken/empty-looking modal.
 *
 * This is deliberately NOT the same experience as "View Report" (ReportModal): that one embeds
 * the actual generated, downloadable/shareable PDF; this renders the same underlying data as a
 * plain in-page view, same as the detail pages do. Both stay meaningfully distinct -- one is the
 * official document, this is a fast look at its content without leaving the list. */
export default function SummaryModal({
  complaint,
  statusLabel,
  onClose,
}: {
  complaint: Complaint;
  statusLabel: string;
  onClose: () => void;
}) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const location = [
    complaint.ward ? localizeWardText(complaint.ward, lang) : null,
    complaint.location_ulb, complaint.location_district, complaint.location_state,
  ]
    .filter(Boolean)
    .join(", ");

  const [report, setReport] = useState<ComplaintReport | null>(null);

  useEffect(() => {
    if (!token || complaint.status !== "resolved") return;
    let cancelled = false;
    setReport(null);
    api
      .getComplaintReport(token, complaint.id, lang)
      .then((r) => !cancelled && setReport(r))
      .catch(() => {
        /* Resolved but the full report couldn't be fetched -- the quick fields below already
         * cover the essentials, so this stays silent rather than showing an error banner over an
         * otherwise-working summary. */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, complaint.id, complaint.status, lang]);

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={modalRef}
        className="modal"
        // Plain overflow-y:auto on this rounded-corner box (the base .modal class's own default)
        // lets the browser's native scrollbar sit flush against the right edge, which is a
        // straight rectangle that doesn't respect border-radius -- it visually squares off the
        // top/bottom-right corners even though the CSS radius is still applied on all four.
        // padding:0 + overflow:hidden here, with scrolling moved to an inner wrapper below,
        // clips correctly instead.
        style={{ maxWidth: 640, width: "100%", padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="jm-modal-title"
        tabIndex={-1}
      >
        <div className="modal-head" style={{ margin: 0, padding: "20px 26px 0" }}>
          <h3 className="display" id="jm-modal-title">{t(lang, "worker.summary.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={{ overflowY: "auto", padding: "18px 26px 22px" }}>
          {report ? (
            <ComplaintReportView report={report} />
          ) : (
            <div className="report-view">
              <dl className="report-fields">
                <dt>{t(lang, "worker.report.complaintId")}</dt>
                <dd className="mono">JM-{String(complaint.id).padStart(5, "0")}</dd>

                <dt>{t(lang, "admin.colStatus")}</dt>
                <dd>
                  <StatusBadge status={complaint.status} label={statusLabel} />
                </dd>

                <dt>{t(lang, "worker.report.filedOn")}</dt>
                <dd>{formatDateTime(complaint.created_at, lang)}</dd>

                <dt>{t(lang, "worker.report.description")}</dt>
                <dd>{complaint.display_summary || complaint.summary}</dd>

                {location && (
                  <>
                    <dt>{t(lang, "worker.report.location")}</dt>
                    <dd>{location}{complaint.address ? ` (${complaint.address})` : ""}</dd>
                  </>
                )}

                {complaint.assigned_worker_name && (
                  <>
                    <dt>{t(lang, "worker.report.assignedWorker")}</dt>
                    <dd>{complaint.assigned_worker_name}</dd>
                  </>
                )}
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
