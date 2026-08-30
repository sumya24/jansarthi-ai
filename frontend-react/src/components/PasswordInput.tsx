import { useState, type InputHTMLAttributes } from "react";
import { t, type LangCode } from "../lib/i18n";

type PasswordInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  lang: LangCode;
};

/** A password <input> with a show/hide toggle -- drop-in replacement for every bare
 * `<input type="password">` in the app (Login, Signup's password + confirm, ForgotPassword's
 * new password, and SettingsModal's change-password current/new/confirm). All other props
 * (id, value, onChange, aria-invalid, aria-describedby, ...) pass straight through to the
 * underlying <input> exactly as they did before -- only `type` is taken over, since that's what
 * the toggle actually flips.
 *
 * Visibility is local `useState`, not shared: SettingsModal's three password fields on one form
 * toggle independently of each other, same as any other unconnected pair of these. */
export default function PasswordInput({ lang, className, ...inputProps }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <div className="password-input-wrap">
      <input {...inputProps} type={visible ? "text" : "password"} className={className} />
      <button
        type="button"
        className="password-toggle-btn"
        onClick={() => setVisible((v) => !v)}
        aria-label={t(lang, visible ? "auth.field.hidePassword" : "auth.field.showPassword")}
        aria-pressed={visible}
      >
        {visible ? <EyeOffIcon /> : <EyeIcon />}
      </button>
    </div>
  );
}

function EyeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M9.9 10a3 3 0 0 0 4.2 4.2"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M6.7 6.7C4.3 8.2 2.4 10.3 1.5 12c1.5 3 5.5 7 10.5 7 1.7 0 3.2-.4 4.6-1.1M17.4 17.4C19.6 15.9 21.2 14 22.5 12c-1-2-3-4.4-5.6-5.9A10.9 10.9 0 0 0 12 5c-.6 0-1.2 0-1.8.1"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M3 3l18 18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
