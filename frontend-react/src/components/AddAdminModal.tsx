import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t, type LangCode } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import { useToast } from "../lib/toast";
import { useModalA11y } from "../lib/useModalA11y";
import EmailVerifyField, { type EmailVerifyValue } from "./EmailVerifyField";

// Near-mirror of AddWorkerModal.tsx minus everything ward/operational-area-specific (an admin's
// access isn't scoped to a ward the way a worker's is) -- same admin-sets-a-temp-password +
// optional-but-OTP-proven-email pattern, reusing the exact same sendWorkerEmailCode/
// verifyWorkerEmailCode calls (see api.ts's createAdmin docstring for why there's no separate
// admin-specific pair of OTP endpoints).
export default function AddAdminModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [emailValue, setEmailValue] = useState<EmailVerifyValue>({ email: "", verified: false, token: null });
  const [language, setLanguage] = useState<LangCode>(lang);
  const [grantSuperAdmin, setGrantSuperAdmin] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    const errors: Record<string, boolean> = {};
    if (!fullName.trim()) errors.fullName = true;
    if (!phone.trim()) errors.phone = true;
    if (!password) errors.password = true;
    if (!confirmPassword) errors.confirmPassword = true;
    else if (confirmPassword !== password) errors.confirmPassword = true;
    if (emailValue.email.trim() && !emailValue.verified) errors.email = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    if (!token) return;
    setSaving(true);
    try {
      await api.createAdmin(token, {
        full_name: fullName.trim(),
        phone: phone.trim(),
        password,
        preferred_language: language,
        super_admin: grantSuperAdmin,
        ...(emailValue.email.trim() && { email: emailValue.email.trim(), email_verification_token: emailValue.token ?? undefined }),
      });
      toast.success(`${t(lang, "addAdmin.createdToast")} ${fullName.trim()}`);
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "addAdmin.errFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
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
          <h3 className="display" id="jm-modal-title">{t(lang, "addAdmin.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={{ overflowY: "auto", padding: "18px 26px 22px" }}>
          <div className="modal-note">{t(lang, "addAdmin.note")}</div>

          {error && <div className="banner-error">{error}</div>}

          <form onSubmit={handleSubmit} noValidate>
            <div className={`field ${fieldErrors.fullName ? "has-error" : ""}`}>
              <label htmlFor="admin-name">
                {t(lang, "addWorker.fullName")}
                <span className="field-required-mark" aria-hidden="true" />
              </label>
              <input id="admin-name" type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder={t(lang, "addWorker.fullNamePlaceholder")} />
              {fieldErrors.fullName && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
            </div>
            <div className={`field ${fieldErrors.phone ? "has-error" : ""}`}>
              <label htmlFor="admin-phone">
                {t(lang, "addWorker.phone")}
                <span className="field-required-mark" aria-hidden="true" />
              </label>
              <input id="admin-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="98xxxxxxxx" />
              {fieldErrors.phone && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
            </div>
            <div className={`field ${fieldErrors.password ? "has-error" : ""}`}>
              <label htmlFor="admin-password">
                {t(lang, "addWorker.tempPassword")}
                <span className="field-required-mark" aria-hidden="true" />
              </label>
              <input id="admin-password" type="text" value={password} onChange={(e) => setPassword(e.target.value)} />
              {fieldErrors.password && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
            </div>
            <div className={`field ${fieldErrors.confirmPassword ? "has-error" : ""}`}>
              <label htmlFor="admin-confirm-password">
                {t(lang, "auth.field.confirmPassword")}
                <span className="field-required-mark" aria-hidden="true" />
              </label>
              <input
                id="admin-confirm-password"
                type="text"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
              {fieldErrors.confirmPassword && (
                <div className="field-error">{t(lang, confirmPassword ? "auth.field.passwordMismatch" : "common.fieldRequired")}</div>
              )}
            </div>
            <EmailVerifyField
              lang={lang}
              idPrefix="admin-add"
              onChange={setEmailValue}
              hasError={fieldErrors.email}
              sendCode={(email) => api.sendWorkerEmailCode(token!, email)}
              verifyCode={async (email, code) => (await api.verifyWorkerEmailCode(token!, email, code)).email_verification_token}
            />
            {fieldErrors.email && <div className="field-error">{t(lang, "auth.signup.verifyEmailFirst")}</div>}
            <div className="field">
              <label id="admin-language-label">{t(lang, "addWorker.preferredLanguage")}</label>
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
            {/* Deliberately NOT className="field" -- that class's own ".field input" rule (width:
                100%, padding, border-radius; meant for text/select/textarea inputs) also matches a
                checkbox by tag selector, stretching it into a full-width bar. Plain div + an inline
                width/padding/border-radius reset on the checkbox itself avoids that collision. */}
            <label style={{ display: "flex", flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 14, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={grantSuperAdmin}
                onChange={(e) => setGrantSuperAdmin(e.target.checked)}
                style={{ width: 16, height: 16, padding: 0, borderRadius: 4, flexShrink: 0 }}
              />
              <span>{t(lang, "addAdmin.grantSuperAdmin")}</span>
            </label>
            <p style={{ fontSize: 12, color: "var(--ink-2)", marginTop: -8, marginBottom: 16 }}>
              {t(lang, "addAdmin.grantSuperAdminHint")}
            </p>

            <div className="modal-actions">
              <button type="button" className="btn btn-ghost" onClick={onClose}>
                {t(lang, "addWorker.cancel")}
              </button>
              <button type="submit" className="btn btn-primary" disabled={saving}>
                {saving ? t(lang, "addAdmin.submitting") : t(lang, "addAdmin.submit")}
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
