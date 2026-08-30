import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint } from "../lib/api";
import MultiPhotoUpload from "./MultiPhotoUpload";
import { useModalA11y } from "../lib/useModalA11y";

/** Mandatory completion-status modal, shown only while a complaint is IN_PROGRESS. Submitting
 * moves the complaint straight to RESOLVED -- the backend writes the completion record BEFORE
 * flipping the status (see backend/routes/complaints.py's resolve_complaint() docstring), so
 * there's no window where a complaint is RESOLVED without a completion status behind it.
 * Evidence photos are optional, zero or more -- reuses MultiPhotoUpload, same as the citizen
 * complaint form. */
export default function CompleteComplaintModal({
  complaintId,
  onClose,
  onResolved,
}: {
  complaintId: number;
  onClose: () => void;
  onResolved: (updated: Complaint) => void;
}) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [completionStatus, setCompletionStatus] = useState("");
  const [photos, setPhotos] = useState<File[]>([]);
  const [fieldError, setFieldError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!completionStatus.trim()) {
      setFieldError(true);
      return;
    }
    if (!token) return;
    setSaving(true);
    try {
      const updated = await api.resolveComplaint(token, complaintId, completionStatus.trim(), photos);
      onResolved(updated);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "worker.complete.errFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="jm-modal-title" tabIndex={-1}>
        <div className="modal-head">
          <h3 className="display" id="jm-modal-title">{t(lang, "worker.complete.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose} disabled={saving}>
            ✕
          </button>
        </div>

        {error && <div className="banner-error">{error}</div>}

        <form onSubmit={handleSubmit} noValidate>
          <div className={`field ${fieldError ? "has-error" : ""}`}>
            <label htmlFor="completion-status">
              {t(lang, "worker.complete.statusLabel")}
              <span className="field-required-mark" aria-hidden="true">*</span>
            </label>
            <textarea
              id="completion-status"
              rows={4}
              value={completionStatus}
              onChange={(e) => {
                setCompletionStatus(e.target.value);
                if (fieldError) setFieldError(false);
              }}
              placeholder={t(lang, "worker.complete.statusPlaceholder")}
            />
            {fieldError && <div className="field-error">{t(lang, "worker.complete.statusRequired")}</div>}
          </div>
          <div className="field">
            <label>
              {t(lang, "worker.complete.photoLabel")}
              <span className="field-optional-mark">{t(lang, "signup.homeLocation.optional")}</span>
            </label>
            <MultiPhotoUpload photos={photos} onChange={setPhotos} />
          </div>

          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              {t(lang, "common.cancel")}
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? t(lang, "worker.complete.submitting") : t(lang, "worker.complete.confirm")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
