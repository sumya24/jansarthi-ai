import { useEffect, useState } from "react";
import { useUiLang } from "../lib/uiLang";
import { formatDateTime, t } from "../lib/i18n";
import { api, type EvidenceFile } from "../lib/api";

/**
 * Reusable evidence viewer: a thumbnail grid that opens a full-size, keyboard-accessible
 * lightbox on click. Used everywhere evidence is shown -- citizen complaint tracking, worker
 * task detail, report view -- one implementation rather than a bespoke gallery per screen (see
 * this phase's "don't create multiple duplicate preview components" instruction).
 *
 * Accepts either real EvidenceFile rows (evidence-upload phase, has uploader/stage/timestamp
 * metadata) or plain filenames (the legacy single-photo columns) -- both render as the same
 * thumbnail grid, so a complaint with a mix of old and new photos still shows one gallery.
 */
export interface GalleryItem {
  filePath: string;
  fileName?: string;
  uploaderRole?: "citizen" | "worker";
  createdAt?: string;
}

export function evidenceToGalleryItems(evidence: EvidenceFile[]): GalleryItem[] {
  return evidence.map((e) => ({
    filePath: e.file_path,
    fileName: e.file_name,
    uploaderRole: e.uploader_role,
    createdAt: e.created_at,
  }));
}

export default function EvidenceGallery({ items }: { items: GalleryItem[] }) {
  const { lang } = useUiLang();
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  useEffect(() => {
    if (openIndex === null) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpenIndex(null);
      if (e.key === "ArrowRight") setOpenIndex((i) => (i === null ? i : Math.min(i + 1, items.length - 1)));
      if (e.key === "ArrowLeft") setOpenIndex((i) => (i === null ? i : Math.max(i - 1, 0)));
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [openIndex, items.length]);

  if (items.length === 0) return null;

  const open = openIndex !== null ? items[openIndex] : null;

  return (
    <>
      <div className="evidence-gallery">
        {items.map((item, i) => (
          <button
            key={`${item.filePath}-${i}`}
            type="button"
            className="evidence-thumb"
            onClick={() => setOpenIndex(i)}
            aria-label={t(lang, "evidence.viewPhoto")}
          >
            <img src={api.photoUrl(item.filePath)} alt={item.fileName || t(lang, "photo.previewAlt")} loading="lazy" />
          </button>
        ))}
      </div>

      {open && (
        <div className="evidence-lightbox-overlay" onClick={(e) => e.target === e.currentTarget && setOpenIndex(null)}>
          <button type="button" className="evidence-lightbox-close" aria-label={t(lang, "common.close")} onClick={() => setOpenIndex(null)}>
            ✕
          </button>
          <img className="evidence-lightbox-img" src={api.photoUrl(open.filePath)} alt={open.fileName || t(lang, "photo.previewAlt")} />
          {(open.uploaderRole || open.createdAt) && (
            <div className="evidence-lightbox-meta">
              {open.uploaderRole && t(lang, open.uploaderRole === "citizen" ? "evidence.fromCitizen" : "evidence.fromWorker")}
              {open.uploaderRole && open.createdAt && " · "}
              {open.createdAt && formatDateTime(open.createdAt, lang)}
            </div>
          )}
        </div>
      )}
    </>
  );
}
