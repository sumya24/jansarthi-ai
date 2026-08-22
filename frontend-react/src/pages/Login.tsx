import { useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { useAuth } from "../lib/auth";
import { t } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import ThemeToggle from "../components/ThemeToggle";
import AuthPanel from "../components/AuthPanel";
import AuthFormBrand from "../components/AuthFormBrand";
import "./Auth.css";

export default function Login() {
  const { lang } = useUiLang();
  const { setSession } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Set by ProtectedRoute when it bounced an unauthenticated visit here (e.g. a "View complaint"
  // link from an email) -- go back to that page instead of always landing on the generic
  // dashboard. Not further validated against the logged-in user's role: if it doesn't apply to
  // them, ProtectedRoute re-checks role on render and redirects to their own home anyway, so a
  // mismatched `from` is at worst one harmless extra bounce, never a wrong page shown.
  const from = (location.state as { from?: string } | null)?.from;

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, boolean>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);

    const errors: Record<string, boolean> = {};
    if (!identifier.trim()) errors.identifier = true;
    if (!password) errors.password = true;
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setSubmitting(true);
    try {
      const { access_token, refresh_token, user } = await api.login({ identifier: identifier.trim(), password });
      setSession(access_token, refresh_token, user);
      navigate(from || (user.role === "citizen" ? "/citizen" : user.role === "worker" ? "/worker" : "/admin"));
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
            <span className="authtab active">{t(lang, "auth.tab.login")}</span>
            <Link to="/signup" className="authtab">
              {t(lang, "auth.tab.signup")}
            </Link>
          </div>

          {formError && <div className="banner-error">{formError}</div>}

          <form onSubmit={handleSubmit} noValidate>
            <div className={`field ${fieldErrors.identifier ? "has-error" : ""}`}>
              <label htmlFor="login-identifier">{t(lang, "auth.field.identifier")}</label>
              <input
                id="login-identifier"
                type="text"
                placeholder="98xxxxxxxx or you@example.com"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                aria-invalid={fieldErrors.identifier || undefined}
                aria-describedby={fieldErrors.identifier ? "login-identifier-error" : undefined}
              />
              {fieldErrors.identifier && (
                <div className="field-error" id="login-identifier-error">
                  {t(lang, "common.fieldRequired")}
                </div>
              )}
            </div>
            <div className={`field ${fieldErrors.password ? "has-error" : ""}`}>
              <label htmlFor="login-password">{t(lang, "auth.field.password")}</label>
              <input
                id="login-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={fieldErrors.password || undefined}
                aria-describedby={fieldErrors.password ? "login-password-error" : undefined}
              />
              {fieldErrors.password && (
                <div className="field-error" id="login-password-error">
                  {t(lang, "common.fieldRequired")}
                </div>
              )}
            </div>
            <div className="switchline" style={{ textAlign: "right", marginTop: "-0.5rem" }}>
              <Link to="/forgot-password">{t(lang, "auth.login.forgotLink")}</Link>
            </div>
            <button type="submit" className="btn btn-primary full" disabled={submitting}>
              {submitting ? "…" : t(lang, "auth.login.button")}
            </button>
          </form>

          <div className="switchline">
            {t(lang, "auth.login.switch")} <Link to="/signup">{t(lang, "auth.login.switchlink")}</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
