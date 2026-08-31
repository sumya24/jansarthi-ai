import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t, type LangCode } from "../lib/i18n";
import { api, ApiError, type UserProfile } from "../lib/api";
import { useToast } from "../lib/toast";
import { useModalA11y } from "../lib/useModalA11y";
import EmailVerifyField, { type EmailVerifyValue } from "./EmailVerifyField";

/** Edits an admin's profile (name/language/email) and, optionally in the same submit, resets
 * their password and/or promotes/demotes super-admin status -- near-mirror of
 * EditWorkerModal.tsx's own "one action, up to two backend calls" shape (PATCH .../admins/{id}
 * and POST .../admins/{id}/reset-password, see backend/routes/admin.py), minus the ward/location
 * picker (an admin's access isn't ward-scoped).
 *
 * The "Grant super admin access" checkbox is disabled, not hidden, when editing your OWN account
 * -- the backend already refuses that specific change (see update_admin()'s own docstring on why
 * self-demotion is blocked), and a disabled control with the same explanatory hint text as
 * AddAdminModal.tsx is clearer than the control silently vanishing for one particular row. */
export default function EditAdminModal({
  admin,
  onClose,
  onUpdated,
}: {
  admin: UserProfile;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { token, user } = useAuth();
  const { lang } = useUiLang();
  const isSelf = admin.id === user?.id;
  const [fullName, setFullName] = useState(admin.full_name);
  const [emailValue, setEmailValue] = useState<EmailVerifyValue>({ email: admin.email ?? "", verified: admin.email_verified, token: null });
  const [language, setLanguage] = useState<LangCode>((admin.preferred_language as LangCode) || "en");
  const [superAdmin, setSuperAdmin] = useState(admin.super_admin);
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    const errors: Record<string, boolean> = {};
    if (!fullName.trim()) errors.fullName = true;
    if (newPassword && newPassword.length < 6) errors.newPassword = true;
    else if (newPassword && confirmNewPassword !== newPassword) errors.confirmNewPassword = true;
    if (emailValue.email.trim() && !emailValue.verified) errors.email = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    if (!token) return;
    setSaving(true);
    try {
      await api.updateAdmin(token, admin.id, {
        full_name: fullName.trim(),
        preferred_language: language,
        email: emailValue.email.trim(),
        email_verification_token: emailValue.token ?? undefined,
        super_admin: isSelf ? undefined : superAdmin,
      });
      if (newPassword) {
        await api.resetAdminPassword(token, admin.id, newPassword);
      }
      toast.success(`${t(lang, "admin.adminUpdatedToast")} ${fullName.trim()}`);
      onUpdated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "admin.adminUpdateErrFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && !saving && onClose()}>
      <div
        ref={modalRef}
        className="modal"
        style={{ maxWidth: 480, padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="jm-modal-title"
        tabIndex={-1}
      >
        <div className="modal-head" style={{ margin: 0, padding: "26px 26px 0" }}>
          <h3 className="display" id="jm-modal-title">{t(lang, "admin.editAdminTitle")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose} disabled={saving}>
            ✕
          </button>
        </div>

        <div style={{ overflowY: "auto", padding: "18px 26px 22px" }}>
          {error && <div className="banner-error">{error}</div>}

          <form onSubmit={handleSubmit} noValidate>
            <div className={`field ${fieldErrors.fullName ? "has-error" : ""}`}>
              <label htmlFor="edit-admin-name">
                {t(lang, "addWorker.fullName")}
                <span className="field-required-mark" aria-hidden="true">*</span>
              </label>
              <input id="edit-admin-name" type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} />
              {fieldErrors.fullName && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
            </div>
            <EmailVerifyField
              lang={lang}
              idPrefix="admin-edit"
              onChange={setEmailValue}
              hasError={fieldErrors.email}
              initialEmail={admin.email ?? undefined}
              initialVerified={admin.email_verified}
              sendCode={(email) => api.sendWorkerEmailCode(token!, email)}
              verifyCode={async (email, code) => (await api.verifyWorkerEmailCode(token!, email, code)).email_verification_token}
            />
            {fieldErrors.email && <div className="field-error">{t(lang, "auth.signup.verifyEmailFirst")}</div>}
            <div className="field">
              <label id="edit-admin-language-label">{t(lang, "addWorker.preferredLanguage")}</label>
              <div className="langpills">
                {(Object.keys(SUPPORTED_LANGUAGES) as LangCode[]).map((code) => (
                  <button
                    key={code}
                    type="button"
                    className={`langpill ${language === code ? "active" : ""}`}
                    onClick={() => setLanguage(code)}
                  >
                    {SUPPORTED_LANGUAGES[code].name}
                  </button>
                ))}
              </div>
            </div>
            <label
              style={{
                display: "flex", flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 14,
                cursor: isSelf ? "not-allowed" : "pointer", opacity: isSelf ? 0.6 : 1,
              }}
            >
              <input
                type="checkbox"
                checked={isSelf ? true : superAdmin}
                disabled={isSelf}
                onChange={(e) => setSuperAdmin(e.target.checked)}
                style={{ width: 16, height: 16, padding: 0, borderRadius: 4, flexShrink: 0 }}
              />
              <span>{t(lang, "addAdmin.grantSuperAdmin")}</span>
            </label>
            <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: -8, marginBottom: 16 }}>
              {isSelf ? t(lang, "addAdmin.cannotDemoteSelf") : t(lang, "addAdmin.grantSuperAdminHint")}
            </p>
            <div className={`field ${fieldErrors.newPassword ? "has-error" : ""}`}>
              <label htmlFor="edit-admin-password">
                {t(lang, "admin.editWorkerNewPassword")}
                <span className="field-optional-mark">{t(lang, "signup.homeLocation.optional")}</span>
              </label>
              <input
                id="edit-admin-password"
                type="text"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder={t(lang, "admin.editWorkerNewPasswordPlaceholder")}
              />
              {fieldErrors.newPassword ? (
                <div className="field-error">{t(lang, "admin.editWorkerPasswordTooShort")}</div>
              ) : (
                <p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 5 }}>{t(lang, "admin.editWorkerPasswordHint")}</p>
              )}
            </div>
            {newPassword && (
              <div className={`field ${fieldErrors.confirmNewPassword ? "has-error" : ""}`}>
                <label htmlFor="edit-admin-confirm-password">
                  {t(lang, "auth.field.confirmPassword")}
                  <span className="field-required-mark" aria-hidden="true">*</span>
                </label>
                <input
                  id="edit-admin-confirm-password"
                  type="text"
                  value={confirmNewPassword}
                  onChange={(e) => setConfirmNewPassword(e.target.value)}
                />
                {fieldErrors.confirmNewPassword && (
                  <div className="field-error">
                    {t(lang, confirmNewPassword ? "auth.field.passwordMismatch" : "common.fieldRequired")}
                  </div>
                )}
              </div>
            )}

            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={onClose} disabled={saving}>
                {t(lang, "addWorker.cancel")}
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? t(lang, "admin.savingWorker") : t(lang, "admin.saveWorker")}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
