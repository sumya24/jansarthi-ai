import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import ThemeToggle from "../components/ThemeToggle";
import AuthPanel from "../components/AuthPanel";
import AuthFormBrand from "../components/AuthFormBrand";
import PasswordInput from "../components/PasswordInput";
import "./Auth.css";

// Two-step form: (1) email -> request code -- a clear error if the email isn't a registered,
// verified account (see backend/routes/auth.py's forgot_password for why this app deliberately
// doesn't hide that), staying on this step; (2) code + new password -> submit, then back to
// /login.
export default function ForgotPassword() {
  const { lang } = useUiLang();
  const navigate = useNavigate();

  const [step, setStep] = useState<"email" | "reset">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Same client-side echo of backend/routes/auth.py's _validate_password_strength as Signup.tsx.
  const passwordTooWeak =
    newPassword.length > 0 &&
    (newPassword.length < 8 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword) || !/[^A-Za-z0-9]/.test(newPassword));

  async function handleRequestCode(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!email.trim()) {
      setFieldErrors({ email: true });
      return;
    }
    setFieldErrors({});
    setSubmitting(true);
    try {
      await api.forgotPassword({ email: email.trim() });
      setStep("reset");
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleReset(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    const errors: Record<string, boolean> = {};
    if (!code.trim()) errors.code = true;
    if (passwordTooWeak || !newPassword) errors.newPassword = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      await api.resetPassword({ email: email.trim(), code: code.trim(), new_password: newPassword });
      // replace: true -- same reasoning as Login.tsx's post-login navigate: without it, Back from
      // the login page re-opened the (already-used) reset-code form instead of leaving the flow.
      navigate("/login", { state: { passwordReset: true }, replace: true });
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="authwrap">
      <AuthPanel lang={lang} />
      <div className="auth-form-side">
        <div className="auth-form-side-bg" aria-hidden="true" />
        <ThemeToggle className="theme-toggle" />
        <AuthFormBrand />
        <div className="authcard enter">
          <div className="forgot-password-head">
            <Link to="/login" className="forgot-password-back">
              ← {t(lang, "auth.forgotPassword.backToLogin")}
            </Link>
            <h2 className="display">{t(lang, "auth.forgotPassword.title")}</h2>
          </div>

          {formError && <div className="banner-error">{formError}</div>}

          {step === "email" ? (
            <form onSubmit={handleRequestCode} noValidate>
              <p className="field-hint">{t(lang, "auth.forgotPassword.emailHint")}</p>
              <div className={`field ${fieldErrors.email ? "has-error" : ""}`}>
                <label htmlFor="forgot-email">
                  {t(lang, "auth.email.label")}
                  <span className="field-required-mark" aria-hidden="true">*</span>
                </label>
                <input
                  id="forgot-email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  aria-invalid={fieldErrors.email || undefined}
                  aria-describedby={fieldErrors.email ? "forgot-email-error" : undefined}
                />
                {fieldErrors.email && (
                  <div className="field-error" id="forgot-email-error">
                    {t(lang, "common.fieldRequired")}
                  </div>
                )}
              </div>
              <button type="submit" className="btn btn-primary full" disabled={submitting}>
                {submitting ? "…" : t(lang, "auth.email.sendCode")}
              </button>
            </form>
          ) : (
            <form onSubmit={handleReset} noValidate>
              <div className="banner-success">{t(lang, "auth.forgotPassword.sentNotice")}</div>
              <p className="field-hint">{t(lang, "auth.forgotPassword.codeHint")}</p>
              <div className={`field ${fieldErrors.code ? "has-error" : ""}`}>
                <label htmlFor="forgot-code">
                  {t(lang, "auth.field.otpCode")}
                  <span className="field-required-mark" aria-hidden="true">*</span>
                </label>
                <input
                  id="forgot-code"
                  type="text"
                  inputMode="numeric"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  aria-invalid={fieldErrors.code || undefined}
                  aria-describedby={fieldErrors.code ? "forgot-code-error" : undefined}
                />
                {fieldErrors.code && (
                  <div className="field-error" id="forgot-code-error">
                    {t(lang, "common.fieldRequired")}
                  </div>
                )}
              </div>
              <div className={`field ${fieldErrors.newPassword ? "has-error" : ""}`}>
                <label htmlFor="forgot-new-password">
                  {t(lang, "auth.changePassword.new")}
                  <span className="field-required-mark" aria-hidden="true">*</span>
                </label>
                <PasswordInput
                  lang={lang}
                  id="forgot-new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  aria-invalid={fieldErrors.newPassword || undefined}
                  aria-describedby={fieldErrors.newPassword ? "forgot-new-password-error" : undefined}
                />
                {passwordTooWeak ? (
                  <div className="field-error" id="forgot-new-password-error">
                    {t(lang, "auth.field.passwordWeak")}
                  </div>
                ) : (
                  <div className="field-hint">{t(lang, "auth.field.passwordHint")}</div>
                )}
              </div>
              <button type="submit" className="btn btn-primary full" disabled={submitting}>
                {submitting ? "…" : t(lang, "auth.forgotPassword.resetButton")}
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
