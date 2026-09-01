import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type ComplaintUpdateEntry } from "../lib/api";
import MultiPhotoUpload from "./MultiPhotoUpload";
import { useModalA11y } from "../lib/useModalA11y";

/** OPTIONAL progress-update modal, shown only while a complaint is IN_PROGRESS. Calling this at
 * all is optional (zero, one, or many updates) -- but any update that IS submitted must have
 * non-empty text; evidence photos are always optional, zero or more (reuses MultiPhotoUpload,
 * the same select/preview/remove control the citizen complaint form already uses). */
export default function ProgressUpdateModal({
  complaintId,
  onClose,
  onAdded,
}: {
  complaintId: number;
  onClose: () => void;
  onAdded: (update: ComplaintUpdateEntry) => void;
}) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [text, setText] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [fieldError, setFieldError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!text.trim()) {
      setFieldError(true);
      return;
    }
    if (!token) return;
    setSaving(true);
    try {
      const update = await api.addProgressUpdate(token, complaintId, text.trim(), photos);
      onAdded(update);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "worker.update.errFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="jm-modal-title" tabIndex={-1}>
        <div className="modal-head">
          <h3 className="display" id="jm-modal-title">{t(lang, "worker.update.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose} disabled={saving}>
            ✕
          </button>
        </div>

        {error && <div className="banner-error">{error}</div>}

        <form onSubmit={handleSubmit} noValidate>
          <div className={`field ${fieldError ? "has-error" : ""}`}>
            <label htmlFor="progress-update-text">
              {t(lang, "worker.update.textLabel")}
              <span className="field-required-mark" aria-hidden="true" />
            </label>
            <textarea
              id="progress-update-text"
              rows={3}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                if (fieldError) setFieldError(false);
              }}
              placeholder={t(lang, "worker.update.textPlaceholder")}
            />
            {fieldError && <div className="field-error">{t(lang, "worker.update.textRequired")}</div>}
          </div>
          <div className="field">
            <label>
              {t(lang, "worker.update.photoLabel")}
              <span className="field-optional-mark">{t(lang, "signup.homeLocation.optional")}</span>
            </label>
            <MultiPhotoUpload photos={photos} onChange={setPhotos} />
          </div>

          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              {t(lang, "common.cancel")}
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? t(lang, "worker.update.submitting") : t(lang, "worker.update.confirm")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
