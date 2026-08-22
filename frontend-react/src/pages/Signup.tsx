import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { useAuth } from "../lib/auth";
import { t } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import ThemeToggle from "../components/ThemeToggle";
import AuthPanel from "../components/AuthPanel";
import AuthFormBrand from "../components/AuthFormBrand";
import HomeLocationPicker, { type HomeLocationValue } from "../components/HomeLocationPicker";
import EmailVerifyField, { type EmailVerifyValue } from "../components/EmailVerifyField";
import "./Auth.css";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// Email verification is mandatory before an account exists at all, but lives inline on the
// email field itself (a "Send code" button, then a confirm-code step right there), not as a
// separate page/step after the rest of the form -- see backend/routes/auth.py's module
// docstring, and EmailVerifyField.tsx (which owns that inline flow -- the same component the
// admin's Add/Edit Worker forms use). The rest of the form stays visible and editable throughout:
// a citizen can verify their email first, fill in the rest, then submit -- or fill in everything
// else first, verify email last, then submit. "Create account" itself is clickable the whole time
// (not hard-disabled until verified -- same as every other field here, which surfaces problems as
// an inline error on submit, not a disabled button); handleSubmit shows a clear error if email
// isn't verified yet, matching the honest-validation pattern the rest of the form already uses.
// The server independently re-checks via email_verification_token (see api.signup) regardless --
// the client-side check here is just UX, never the actual gate.
export default function Signup() {
  const { lang } = useUiLang();
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [emailValue, setEmailValue] = useState<EmailVerifyValue>({ email: "", verified: false, token: null });
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  // The State/City/Ward/Area picker below IS the "Area / ward" field -- see
  // HomeLocationPicker.tsx's own docstring for why this used to be two separate sections.
  const [homeLocation, setHomeLocation] = useState<HomeLocationValue>({ ward: "" });
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  // Client-side echo of backend/routes/auth.py's _validate_password_strength -- the server stays
  // the source of truth (this is only for immediate feedback), but a citizen shouldn't have to
  // submit the form to discover their password is too weak when the rule is this simple.
  const passwordTooWeak =
    password.length > 0 &&
    (password.length < 8 || !/[A-Za-z]/.test(password) || !/\d/.test(password) || !/[^A-Za-z0-9]/.test(password));

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    const errors: Record<string, boolean> = {};
    if (!fullName.trim()) errors.fullName = true;
    if (!phone.trim()) errors.phone = true;
    else if (!/^[6-9]\d{9}$/.test(phone.trim())) errors.phone = true;
    if (!emailValue.email.trim()) errors.email = true;
    else if (!EMAIL_PATTERN.test(emailValue.email.trim())) errors.email = true;
    if (!password) errors.password = true;
    else if (passwordTooWeak) errors.password = true;
    if (!confirmPassword) errors.confirmPassword = true;
    else if (confirmPassword !== password) errors.confirmPassword = true;
    if (!homeLocation.ward.trim()) errors.ward = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    // Belt-and-suspenders: the "Create account" button is already disabled until this is true,
    // but the server is the actual source of truth (see backend/routes/auth.py's signup(), which
    // independently re-checks email_verification_token) -- this just gives an honest message
    // instead of a raw 400 in the unlikely event this branch is ever reached.
    if (!emailValue.verified || !emailValue.token) {
      setFormError(t(lang, "auth.signup.verifyEmailFirst"));
      return;
    }

    setSubmitting(true);
    try {
      const { access_token, refresh_token, user } = await api.signup({
        full_name: fullName.trim(),
        phone: phone.trim(),
        email: emailValue.email.trim(),
        email_verification_token: emailValue.token,
        password,
        preferred_language: lang,
        ...homeLocation,
        ward: homeLocation.ward.trim(),
      });
      setSession(access_token, refresh_token, user);
      navigate("/citizen");
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
          <div className="authtabs">
            <Link to="/login" className="authtab">
              {t(lang, "auth.tab.login")}
            </Link>
            <span className="authtab active">{t(lang, "auth.tab.signup")}</span>
          </div>

          {formError && <div className="banner-error">{formError}</div>}

          <form onSubmit={handleSubmit} noValidate>
            <div className={`field ${fieldErrors.fullName ? "has-error" : ""}`}>
              <label htmlFor="signup-name">{t(lang, "auth.field.name")}</label>
              <input
                id="signup-name"
                type="text"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                aria-invalid={fieldErrors.fullName || undefined}
                aria-describedby={fieldErrors.fullName ? "signup-name-error" : undefined}
              />
              {fieldErrors.fullName && (
                <div className="field-error" id="signup-name-error">
                  {t(lang, "common.fieldRequired")}
                </div>
              )}
            </div>
            <div className={`field ${fieldErrors.phone ? "has-error" : ""}`}>
              <label htmlFor="signup-phone">{t(lang, "auth.field.phone")}</label>
              <input
                id="signup-phone"
                type="tel"
                placeholder="98xxxxxxxx"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                aria-invalid={fieldErrors.phone || undefined}
                aria-describedby={fieldErrors.phone ? "signup-phone-error" : undefined}
              />
              {fieldErrors.phone && (
                <div className="field-error" id="signup-phone-error">
                  {t(lang, phone.trim() ? "common.invalidPhone" : "common.fieldRequired")}
                </div>
              )}
            </div>

            <EmailVerifyField
              lang={lang}
              idPrefix="signup"
              onChange={setEmailValue}
              hasError={fieldErrors.email}
              sendCode={(email) => api.sendSignupEmailCode({ email })}
              verifyCode={async (email, code) => (await api.verifySignupEmailCode({ email, code })).email_verification_token}
            />
            {fieldErrors.email && <div className="field-error home-location-error">{t(lang, "common.fieldRequired")}</div>}

            <div className={`field ${fieldErrors.password ? "has-error" : ""}`}>
              <label htmlFor="signup-password">{t(lang, "auth.field.password")}</label>
              <input
                id="signup-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={fieldErrors.password || undefined}
                aria-describedby={fieldErrors.password ? "signup-password-error" : "signup-password-hint"}
              />
              {fieldErrors.password ? (
                <div className="field-error" id="signup-password-error">
                  {t(lang, password ? "auth.field.passwordWeak" : "common.fieldRequired")}
                </div>
              ) : (
                <div className="field-hint" id="signup-password-hint">
                  {t(lang, "auth.field.passwordHint")}
                </div>
              )}
            </div>
            <div className={`field ${fieldErrors.confirmPassword ? "has-error" : ""}`}>
              <label htmlFor="signup-confirm-password">{t(lang, "auth.field.confirmPassword")}</label>
              <input
                id="signup-confirm-password"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                aria-invalid={fieldErrors.confirmPassword || undefined}
                aria-describedby={fieldErrors.confirmPassword ? "signup-confirm-password-error" : undefined}
              />
              {fieldErrors.confirmPassword && (
                <div className="field-error" id="signup-confirm-password-error">
                  {t(lang, confirmPassword ? "auth.field.passwordMismatch" : "common.fieldRequired")}
                </div>
              )}
            </div>
            <HomeLocationPicker lang={lang} onChange={setHomeLocation} hasError={fieldErrors.ward} />
            {fieldErrors.ward && <div className="field-error home-location-error">{t(lang, "signup.homeLocation.required")}</div>}

            <div className="worker-note">{t(lang, "auth.signup.workernote")}</div>

            <button type="submit" className="btn btn-primary full" disabled={submitting}>
              {submitting ? "…" : t(lang, "auth.signup.button")}
            </button>
          </form>

          <div className="switchline">
            {t(lang, "auth.signup.switch")} <Link to="/login">{t(lang, "auth.signup.switchlink")}</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
