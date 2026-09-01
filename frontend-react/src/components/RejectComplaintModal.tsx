import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint } from "../lib/api";
import { useModalA11y } from "../lib/useModalA11y";

/** Mandatory rejection-reason modal -- the worker cannot reject a complaint without one
 * (client-side blocks an empty/whitespace-only reason; the backend independently re-validates
 * the same rule -- see backend/routes/complaints.py's reject_complaint()). On success, the
 * complaint has already been reassigned (or returned to "pending") by the backend's own
 * assign_next_worker() -- see that function's docstring. */
export default function RejectComplaintModal({
  complaintId,
  onClose,
  onRejected,
}: {
  complaintId: number;
  onClose: () => void;
  onRejected: (updated: Complaint) => void;
}) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [reason, setReason] = useState("");
  const [fieldError, setFieldError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!reason.trim()) {
      setFieldError(true);
      return;
    }
    if (!token) return;
    setSaving(true);
    try {
      const updated = await api.rejectComplaint(token, complaintId, reason.trim());
      onRejected(updated);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "worker.reject.errFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="jm-modal-title" tabIndex={-1}>
        <div className="modal-head">
          <h3 className="display" id="jm-modal-title">{t(lang, "worker.reject.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose} disabled={saving}>
            ✕
          </button>
        </div>

        {error && <div className="banner-error">{error}</div>}

        <form onSubmit={handleSubmit} noValidate>
          <div className={`field ${fieldError ? "has-error" : ""}`}>
            <label htmlFor="reject-reason">
              {t(lang, "worker.reject.reasonLabel")}
              <span className="field-required-mark" aria-hidden="true" />
            </label>
            <textarea
              id="reject-reason"
              rows={3}
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                if (fieldError) setFieldError(false);
              }}
              placeholder={t(lang, "worker.reject.reasonPlaceholder")}
            />
            {fieldError && <div className="field-error">{t(lang, "worker.reject.reasonRequired")}</div>}
          </div>

          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
              {t(lang, "common.cancel")}
            </button>
            <button type="submit" className="btn btn-danger" disabled={saving}>
              {saving ? t(lang, "worker.reject.submitting") : t(lang, "worker.reject.confirm")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
