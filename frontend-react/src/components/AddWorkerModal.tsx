import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t, type LangCode } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import { useToast } from "../lib/toast";
import { useModalA11y } from "../lib/useModalA11y";
import EmailVerifyField, { type EmailVerifyValue } from "./EmailVerifyField";

export default function AddWorkerModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const { token } = useAuth();
  const { lang } = useUiLang();
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [emailValue, setEmailValue] = useState<EmailVerifyValue>({ email: "", verified: false, token: null });
  const [ward, setWard] = useState("");
  // Defaults to the admin's own current UI language, same convention Signup.tsx uses for a
  // citizen's preferred_language -- was previously hardcoded to "mr" regardless of who was
  // creating the account, silently giving every new worker Marathi unless the admin noticed
  // and changed the pill themselves.
  const [language, setLanguage] = useState<LangCode>(lang);
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
    if (!ward.trim()) errors.ward = true;
    if (emailValue.email.trim() && !emailValue.verified) errors.email = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    if (!token) return;
    setSaving(true);
    try {
      await api.createWorker(token, {
        full_name: fullName.trim(),
        phone: phone.trim(),
        password,
        ward: ward.trim(),
        preferred_language: language,
        ...(emailValue.email.trim() && { email: emailValue.email.trim(), email_verification_token: emailValue.token ?? undefined }),
      });
      toast.success(`${t(lang, "addWorker.createdToast")} ${fullName.trim()}`);
      onCreated();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "addWorker.errFailed"));
    } finally {
      setSaving(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={modalRef} className="modal" role="dialog" aria-modal="true" aria-labelledby="jm-modal-title" tabIndex={-1}>
        <div className="modal-head">
          <h3 className="display" id="jm-modal-title">{t(lang, "addWorker.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="modal-note">
          {t(lang, "addWorker.note")}
        </div>

        {error && <div className="banner-error">{error}</div>}

        <form onSubmit={handleSubmit} noValidate>
          <div className={`field ${fieldErrors.fullName ? "has-error" : ""}`}>
            <label htmlFor="worker-name">{t(lang, "addWorker.fullName")}</label>
            <input id="worker-name" type="text" value={fullName} onChange={(e) => setFullName(e.target.value)} placeholder={t(lang, "addWorker.fullNamePlaceholder")} />
            {fieldErrors.fullName && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
          </div>
          <div className={`field ${fieldErrors.phone ? "has-error" : ""}`}>
            <label htmlFor="worker-phone">{t(lang, "addWorker.phone")}</label>
            <input id="worker-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="98xxxxxxxx" />
            {fieldErrors.phone && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
          </div>
          <div className={`field ${fieldErrors.password ? "has-error" : ""}`}>
            <label htmlFor="worker-password">{t(lang, "addWorker.tempPassword")}</label>
            <input id="worker-password" type="text" value={password} onChange={(e) => setPassword(e.target.value)} />
            {fieldErrors.password && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
          </div>
          <div className={`field ${fieldErrors.confirmPassword ? "has-error" : ""}`}>
            <label htmlFor="worker-confirm-password">{t(lang, "auth.field.confirmPassword")}</label>
            <input
              id="worker-confirm-password"
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
            idPrefix="worker-add"
            onChange={setEmailValue}
            hasError={fieldErrors.email}
            sendCode={(email) => api.sendWorkerEmailCode(token!, email)}
            verifyCode={async (email, code) => (await api.verifyWorkerEmailCode(token!, email, code)).email_verification_token}
          />
          {fieldErrors.email && <div className="field-error">{t(lang, "auth.signup.verifyEmailFirst")}</div>}
          <div className={`field ${fieldErrors.ward ? "has-error" : ""}`}>
            <label htmlFor="worker-ward">{t(lang, "addWorker.ward")}</label>
            <input id="worker-ward" type="text" value={ward} onChange={(e) => setWard(e.target.value)} placeholder={t(lang, "addWorker.wardPlaceholder")} />
            {fieldErrors.ward && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
          </div>
          <div className="field">
            <label id="worker-language-label">{t(lang, "addWorker.preferredLanguage")}</label>
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

          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              {t(lang, "addWorker.cancel")}
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? t(lang, "addWorker.submitting") : t(lang, "addWorker.submit")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
