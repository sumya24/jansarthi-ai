import { useUiLang } from "../lib/uiLang";
import { formatDateTime, t } from "../lib/i18n";
import { statusLabel } from "../lib/statusLabel";
import type { ComplaintReport, ComplaintStatus } from "../lib/api";
import EvidenceGallery, { type GalleryItem } from "./EvidenceGallery";
import StatusBadge from "./StatusBadge";

function toItems(filePaths: string[]): GalleryItem[] {
  return filePaths.map((filePath) => ({ filePath }));
}

/** Renders the same real, deterministically-assembled report data the PDF download contains
 * (complaint + status history + worker updates + evidence -- never LLM-generated, see
 * backend/services/complaint_report_service.py). Pure presentational -- the caller
 * (ReportModal / WorkerComplaintDetail) owns fetching. */
export default function ComplaintReportView({ report }: { report: ComplaintReport }) {
  const { lang } = useUiLang();
  const location = [report.location_ward, report.location_ulb, report.location_district, report.location_state]
    .filter(Boolean)
    .join(", ");

  return (
    <div className="report-view">
      <div style={{ textAlign: "center", marginBottom: 16 }}>
        <div className="display" style={{ fontSize: 16, fontWeight: 700 }}>JanSarthi AI</div>
        <div style={{ fontSize: 11, color: "var(--ink-3)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
          {t(lang, "worker.report.heading")}
        </div>
      </div>

      {report.resolved_at && (
        <div className="report-resolved-banner">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="8" cy="8" r="7" fill="currentColor" opacity="0.18" />
            <path d="M4.5 8.2 6.8 10.5 11.5 5.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          {t(lang, "citizen.statusResolved")}
        </div>
      )}

      <dl className="report-fields report-fields-panel">
        <dt>{t(lang, "worker.report.complaintId")}</dt>
        <dd className="mono">{report.display_id}</dd>

        <dt>{t(lang, "worker.report.filedOn")}</dt>
        <dd>{formatDateTime(report.created_at, lang)}</dd>

        <dt>{t(lang, "worker.report.description")}</dt>
        <dd>{report.service_summary}{report.original_description && report.original_description !== report.service_summary ? ` — ${report.original_description}` : ""}</dd>
      </dl>

      {report.citizen_evidence.length > 0 && (
        <div className="report-section" style={{ marginTop: 0, borderTop: "none", paddingTop: 0 }}>
          <div className="section-label" style={{ marginTop: 0 }}>{t(lang, "evidence.citizenPhotos")}</div>
          <EvidenceGallery items={toItems(report.citizen_evidence)} />
        </div>
      )}

      <dl className="report-fields report-fields-panel">
        {location && (
          <>
            <dt>{t(lang, "worker.report.location")}</dt>
            <dd>{location}{report.location_address ? ` (${report.location_address})` : ""}</dd>
          </>
        )}

        {report.assigned_worker_name && (
          <>
            <dt>{t(lang, "worker.report.assignedWorker")}</dt>
            <dd>{report.assigned_worker_name}</dd>
          </>
        )}
      </dl>

      {report.initial_assessment && (
        <div className="report-section">
          <div className="section-label">{t(lang, "worker.detail.initialAssessment")}</div>
          <p>{report.initial_assessment}</p>
          {report.initial_assessment_at && (
            <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{formatDateTime(report.initial_assessment_at, lang)}</div>
          )}
          {report.initial_assessment_evidence.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <EvidenceGallery items={toItems(report.initial_assessment_evidence)} />
            </div>
          )}
        </div>
      )}

      {report.progress_updates.length > 0 && (
        <div className="report-section">
          <div className="section-label">{t(lang, "worker.detail.progressUpdates")}</div>
          {report.progress_updates.map((u, i) => (
            <div key={i} style={{ marginBottom: 8 }}>
              <p style={{ margin: 0 }}>{u.text}</p>
              <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{formatDateTime(u.created_at, lang)}</div>
              {u.evidence.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <EvidenceGallery items={toItems(u.evidence)} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {report.completion_status && (
        <div className="report-section">
          <div className="section-label">{t(lang, "worker.detail.completion")}</div>
          <p>{report.completion_status}</p>
          {report.resolved_at && <div style={{ fontSize: 11, color: "var(--ink-3)" }}>{formatDateTime(report.resolved_at, lang)}</div>}
          {(() => {
            // completion_evidence (evidence-upload phase, multi-file) plus the legacy single
            // completion_evidence_photo for complaints resolved before that system existed.
            const items = toItems(report.completion_evidence);
            if (report.completion_evidence_photo && !report.completion_evidence.includes(report.completion_evidence_photo)) {
              items.push({ filePath: report.completion_evidence_photo });
            }
            return items.length > 0 ? (
              <div style={{ marginTop: 8 }}>
                <EvidenceGallery items={items} />
              </div>
            ) : null;
          })()}
        </div>
      )}

      {report.timeline.length > 0 && (
        <div className="report-section">
          <div className="section-label">{t(lang, "worker.detail.timeline")}</div>
          {report.timeline.map((entry, i) => (
            // Backend status codes include "open" (see _STATUS_LABEL_KEYS above), which predates
            // StatusBadge's own vocabulary -- it falls back to StatusBadge's default (pending-
            // style) badge, a reasonable stand-in for "just submitted, not yet actioned" that's
            // fine here since every StatusBadge call site already treats an unrecognized status
            // this way, not something new to this component.
            //
            // A real grid with fixed column widths (not a flex row) -- so the "from" badges line
            // up in one vertical column, the arrows in another, and the "to" badges in a third,
            // down the whole list, instead of each row's badge widths shifting everything after
            // them left/right depending on how long that row's particular status word is.
            <div key={i} className="report-timeline-row">
              <span className="report-timeline-from">
                {entry.from_status && (
                  <StatusBadge status={entry.from_status as ComplaintStatus} label={statusLabel(lang, entry.from_status)} />
                )}
              </span>
              {/* Always shown, even for a transition into "assigned" (whose own badge icon is
                  also an arrow) -- with the two badges now in their own separate grid columns,
                  this middle column's arrow reads as the row's own connector, not a literal
                  repeat sitting right next to the badge's icon the way it did before the grid
                  layout. Hiding it only for that one status made every OTHER row consistent but
                  this one row look broken/missing its arrow -- worse than the very difference it
                  was meant to avoid. */}
              <span className="report-timeline-connector">{entry.from_status ? "→" : ""}</span>
              <span className="report-timeline-to">
                <StatusBadge status={entry.to_status as ComplaintStatus} label={statusLabel(lang, entry.to_status)} />
              </span>
              <span className="mono report-timeline-time">{formatDateTime(entry.created_at, lang)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
