import { useEffect, useMemo, useRef, useState } from "react";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";

const MAX_FILES = 10;
const ALLOWED_TYPES = ["image/jpeg", "image/png"];
const MAX_SIZE_BYTES = 5 * 1024 * 1024;

/**
 * Multi-file evidence attach/preview/remove control -- used everywhere a citizen or worker can
 * attach evidence photos (complaint creation, initial assessment, progress updates, completion).
 * Replaces the old single-file PhotoUpload component (removed -- this fully superseded it).
 *
 * Client-side validation (type/size/count) is a UX convenience only -- the backend independently
 * re-validates every file authoritatively (content-type + size, see backend/routes/complaints.py's
 * _validate_and_write); this component's checks exist so a citizen/worker sees a clear error
 * immediately instead of waiting on a round-trip for something obviously wrong.
 */
export default function MultiPhotoUpload({
  photos,
  onChange,
  maxFiles = MAX_FILES,
  placeholderKey = "citizen.photoPlaceholder",
}: {
  photos: File[];
  onChange: (files: File[]) => void;
  maxFiles?: number;
  placeholderKey?: string;
}) {
  const { lang } = useUiLang();
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // LIVE-REPORTED: attaching a photo, then reopening/closing the native file picker (or any
  // other re-render this component sees) could make an already-attached photo's preview flash
  // to a blank or seemingly wrong image. Root cause was two compounding bugs:
  //
  // 1. `URL.createObjectURL(photo)` was called directly inside JSX -- on EVERY render, not just
  //    when a photo was actually added, so the same File got a brand-new, different blob: URL
  //    each time, and none of the old ones were ever released (a real memory leak, worse the
  //    more a citizen interacts with the page before submitting).
  // 2. Each thumbnail's React key was `${photo.name}-${i}` -- real photos from a phone camera
  //    very commonly share the exact same filename (IMG_2026...jpg), so removing one photo could
  //    shift a later same-named photo into a key React had already used for a DIFFERENT File at
  //    that index; React then reused the old <img> DOM node instead of remounting it, so it kept
  //    showing the previous (now wrong, or since-revoked/blank) image.
  //
  // Fixed by giving each File a stable identity (assigned once, on first sight, via a WeakMap --
  // File objects are unique instances even when two happen to share a filename) and deriving one
  // object URL per File exactly once, releasing it only when that File actually leaves `photos`.
  const fileIds = useRef(new WeakMap<File, string>()).current;
  function idFor(file: File): string {
    let id = fileIds.get(file);
    if (!id) {
      id = typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `${file.name}-${file.size}-${file.lastModified}-${Math.random()}`;
      fileIds.set(file, id);
    }
    return id;
  }

  const urlCache = useRef(new Map<string, string>()).current;
  const previews = useMemo(() => {
    const currentIds = new Set(photos.map(idFor));
    for (const [id, url] of urlCache) {
      if (!currentIds.has(id)) {
        URL.revokeObjectURL(url);
        urlCache.delete(id);
      }
    }
    return photos.map((photo) => {
      const id = idFor(photo);
      let url = urlCache.get(id);
      if (!url) {
        url = URL.createObjectURL(photo);
        urlCache.set(id, url);
      }
      return { id, url, photo };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [photos]);

  // Release every remaining cached URL when the whole component unmounts (e.g. navigating away
  // mid-draft) -- the per-photo revocation above only covers photos removed while still mounted.
  useEffect(() => {
    return () => {
      for (const url of urlCache.values()) URL.revokeObjectURL(url);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function addFiles(incoming: FileList | File[]) {
    setError(null);
    const candidates = Array.from(incoming);
    if (photos.length + candidates.length > maxFiles) {
      setError(t(lang, "photo.tooMany").replace("{max}", String(maxFiles)));
      return;
    }
    const accepted: File[] = [];
    for (const file of candidates) {
      if (!ALLOWED_TYPES.includes(file.type)) {
        setError(t(lang, "photo.unsupportedType"));
        continue;
      }
      if (file.size > MAX_SIZE_BYTES) {
        setError(t(lang, "photo.tooLarge"));
        continue;
      }
      accepted.push(file);
    }
    if (accepted.length) onChange([...photos, ...accepted]);
  }

  function removeAt(index: number) {
    onChange(photos.filter((_, i) => i !== index));
  }

  return (
    <div>
      {error && <div className="field-error" style={{ marginBottom: 8 }}>{error}</div>}

      {previews.length > 0 && (
        <div className="multi-photo-grid">
          {previews.map(({ id, url }, i) => (
            <div key={id} className="multi-photo-thumb">
              <img src={url} alt={t(lang, "photo.previewAlt")} />
              <button
                type="button"
                className="multi-photo-remove"
                aria-label={t(lang, "photo.remove")}
                onClick={() => removeAt(i)}
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}

      {photos.length < maxFiles && (
        <label
          className={`file-drop${dragOver ? " drag-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="5" width="18" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.7" />
            <circle cx="9" cy="11" r="2.2" stroke="currentColor" strokeWidth="1.5" />
            <path d="M21 16l-5.5-5-4 4-2-1.5L3 18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span>{photos.length > 0 ? t(lang, "photo.addMore") : t(lang, placeholderKey)}</span>
          <input
            type="file"
            accept="image/jpeg,image/png"
            multiple
            onChange={(e) => {
              if (e.target.files?.length) addFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      )}
    </div>
  );
}
