import { useUiLang } from "../lib/uiLang";
import { formatDateTime, t } from "../lib/i18n";
import { type ComplaintUpdateEntry, type ComplaintUpdateType } from "../lib/api";
import EvidenceGallery, { evidenceToGalleryItems, type GalleryItem } from "./EvidenceGallery";

const KIND_LABEL: Record<ComplaintUpdateType, string> = {
  INITIAL_ASSESSMENT: "updates.kind.initialAssessment",
  PROGRESS_UPDATE: "updates.kind.progressUpdate",
  COMPLETION: "updates.kind.completion",
};

/** Shared worker-authored-update timeline: the initial assessment, any optional progress
 * updates, and the final completion status, in chronological order. Used on both the worker
 * task-detail page and the citizen-facing complaint view -- one rendering, so a citizen and the
 * worker are always looking at the exact same real data (see this phase's spec: the citizen
 * must see the worker's initial assessment / progress updates / completion status, but nothing
 * else — this component renders exactly those three update_types, nothing internal-only). */
export default function ComplaintUpdatesTimeline({ updates }: { updates: ComplaintUpdateEntry[] }) {
  const { lang } = useUiLang();
  if (updates.length === 0) return <p style={{ color: "var(--ink-2)", fontSize: 13 }}>{t(lang, "updates.none")}</p>;

  return (
    <div>
      {updates.map((u) => {
        // Evidence-upload-phase files (multi-file) plus the legacy single `photo_path` (old
        // rows written before that system existed) -- both render in the same gallery so a
        // complaint with a mix of old and new evidence still shows everything in one place.
        const items: GalleryItem[] = evidenceToGalleryItems(u.evidence);
        if (u.photo_path && !u.evidence.some((e) => e.file_path === u.photo_path)) {
          items.push({ filePath: u.photo_path, uploaderRole: "worker", createdAt: u.created_at });
        }
        return (
          <div key={u.id} className="update-entry">
            <div className="update-entry-marker" />
            <div className="update-entry-body">
              <div className="update-entry-kind">{t(lang, KIND_LABEL[u.update_type])}</div>
              <div className="update-entry-text">{u.text}</div>
              <div className="update-entry-meta">
                {u.worker_name && `${u.worker_name} · `}
                {formatDateTime(u.created_at, lang)}
              </div>
              {items.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <EvidenceGallery items={items} />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
