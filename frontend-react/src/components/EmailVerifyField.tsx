import { useEffect, useState } from "react";
import { ApiError } from "../lib/api";
import { t, type LangCode } from "../lib/i18n";
import "../pages/Auth.css";

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// How long "Resend code" stays disabled after each send -- purely a spam-click guard on the
// button itself, unrelated to the server's own OTP_EXPIRE_MINUTES (the code stays valid there
// much longer than this; this only bounds how soon a new one can be asked for).
const RESEND_COOLDOWN_SECONDS = 30;

export interface EmailVerifyValue {
  email: string;
  verified: boolean;
  // The proof token from a fresh OTP verification this session -- null both before verification
  // AND for an email that was already verified when this field mounted (see `initialVerified`;
  // nothing to redeem there, it's already proven). The caller only needs to send a token to the
  // server when it's actually creating/changing the stored email -- see AddWorkerModal.tsx/
  // EditWorkerModal.tsx for how each uses this distinction.
  token: string | null;
}

/** The inline "email address + Send Code + confirm" flow Signup.tsx introduced, factored out so
 * it isn't hand-duplicated everywhere else this app needs a PROVEN (not just typed) email
 * address -- currently Signup itself and the admin's Add/Edit Worker forms (see
 * AddWorkerModal.tsx/EditWorkerModal.tsx), both of which redeem the exact same kind of one-time
 * proof token from their own `verifyCode`. A worker's email goes through the identical OTP round
 * trip a citizen's does at signup: `sendCode`/`verifyCode` are injected by the caller precisely so
 * this component doesn't need to know which endpoint it's hitting (backend/routes/auth.py's
 * public signup ones, or backend/routes/admin.py's admin-authenticated worker ones) -- only that
 * both hand back a redeemable token on success. See backend/routes/admin.py's
 * send_worker_email_code/verify_worker_email_code docstrings for why those reuse
 * create_signup_email_otp/verify_signup_email_otp/consume_signup_email_verification directly
 * rather than a second copy of that OTP logic -- this component is the same idea on the frontend.
 *
 * Deliberately NOT used by SettingsModal.tsx's own "add/change email" section: that one is a
 * genuinely different flow (a citizen proving an address for their OWN already-authenticated
 * account, via api.sendEmailVerification/verifyEmail, which act on `current_user` server-side and
 * need no proof token at all) -- not just a different endpoint, a different backend contract.
 * Force-fitting it into this component's token-shaped API would add branching, not remove
 * duplication. */
export default function EmailVerifyField({
  lang, idPrefix, onChange, hasError, initialEmail, initialVerified,
  sendCode, verifyCode,
}: {
  lang: LangCode;
  idPrefix: string;
  onChange: (value: EmailVerifyValue) => void;
  hasError?: boolean;
  // Pre-fills from an existing, already-verified email (editing a worker who already has one) --
  // starts straight in the "verified" display state, same as a freshly-verified address, with the
  // same "Change email" escape hatch to replace it (which then requires a fresh OTP for whatever
  // new address is typed, same as any other change). Read once, on mount, same convention
  // WorkerLocationPicker.tsx's own `initial` prop uses.
  initialEmail?: string;
  initialVerified?: boolean;
  sendCode: (email: string) => Promise<void>;
  verifyCode: (email: string, code: string) => Promise<string>;
}) {
  const [email, setEmail] = useState(initialEmail ?? "");
  const [emailOtpSent, setEmailOtpSent] = useState(false);
  const [emailCode, setEmailCode] = useState("");
  const [emailVerified, setEmailVerified] = useState(Boolean(initialEmail && initialVerified));
  const [emailToken, setEmailToken] = useState<string | null>(null);
  const [emailOtpError, setEmailOtpError] = useState<string | null>(null);
  const [codeFieldError, setCodeFieldError] = useState(false);
  const [sendingEmailCode, setSendingEmailCode] = useState(false);
  const [verifyingEmailCode, setVerifyingEmailCode] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);

  useEffect(() => {
    if (resendCooldown <= 0) return;
    const timer = setTimeout(() => setResendCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  useEffect(() => {
    onChange({ email, verified: emailVerified, token: emailToken });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [email, emailVerified, emailToken]);

  function resetEmailVerification() {
    setEmailOtpSent(false);
    setEmailCode("");
    setEmailVerified(false);
    setEmailToken(null);
    setEmailOtpError(null);
    setResendCooldown(0);
    setCodeFieldError(false);
  }

  function handleEmailChange(value: string) {
    setEmail(value);
    // Editing the address after a code was sent (or after an already-verified one, fresh or
    // pre-existing) invalidates whatever's in flight -- the OTP/token were issued for the OLD
    // address, not this one.
    if (emailOtpSent || emailVerified) resetEmailVerification();
  }

  async function handleSendEmailCode() {
    const trimmed = email.trim();
    if (!trimmed || !EMAIL_PATTERN.test(trimmed)) return;
    setEmailOtpError(null);
    setSendingEmailCode(true);
    try {
      await sendCode(trimmed);
      setEmailOtpSent(true);
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setEmailOtpError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setSendingEmailCode(false);
    }
  }

  async function handleVerifyEmailCode() {
    setEmailOtpError(null);
    if (!emailCode.trim()) {
      setCodeFieldError(true);
      return;
    }
    setCodeFieldError(false);
    setVerifyingEmailCode(true);
    try {
      const token = await verifyCode(email.trim(), emailCode.trim());
      setEmailToken(token);
      setEmailVerified(true);
      setEmailOtpSent(false);
      setEmailCode("");
    } catch (err) {
      setEmailOtpError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setVerifyingEmailCode(false);
    }
  }

  return (
    <div className="email-verify-block">
      <div className={`field ${hasError ? "has-error" : ""}`}>
        <label htmlFor={`${idPrefix}-email`}>{t(lang, "auth.email.label")}</label>
        <div className="email-verify-row">
          <input
            id={`${idPrefix}-email`}
            type="email"
            value={email}
            disabled={emailVerified}
            onChange={(e) => handleEmailChange(e.target.value)}
          />
          {emailVerified ? (
            <span className="email-verified-badge">✓ {t(lang, "auth.email.verified")}</span>
          ) : (
            !emailOtpSent &&
            email.trim() && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={handleSendEmailCode}
                disabled={sendingEmailCode || !EMAIL_PATTERN.test(email.trim())}
              >
                {sendingEmailCode ? "…" : t(lang, "auth.email.sendCode")}
              </button>
            )
          )}
        </div>
      </div>

      {emailOtpSent && !emailVerified && (
        <div className="email-otp-inline">
          {emailOtpError && <div className="banner-error">{emailOtpError}</div>}
          <div className="field-hint">{t(lang, "auth.email.sent")}</div>
          <div className={`field ${codeFieldError ? "has-error" : ""}`}>
            <label htmlFor={`${idPrefix}-email-otp`}>{t(lang, "auth.field.otpCode")}</label>
            <input
              id={`${idPrefix}-email-otp`}
              type="text"
              inputMode="numeric"
              value={emailCode}
              onChange={(e) => setEmailCode(e.target.value)}
            />
            {codeFieldError && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
          </div>
          <button type="button" className="btn btn-primary btn-sm" onClick={handleVerifyEmailCode} disabled={verifyingEmailCode}>
            {verifyingEmailCode ? "…" : t(lang, "auth.email.verify")}
          </button>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={handleSendEmailCode}
            disabled={sendingEmailCode || resendCooldown > 0}
          >
            {sendingEmailCode ? "…" : resendCooldown > 0 ? `${t(lang, "auth.email.resend")} (${resendCooldown}s)` : t(lang, "auth.email.resend")}
          </button>
        </div>
      )}

      {emailVerified && (
        <button type="button" className="email-change-link" onClick={resetEmailVerification}>
          {t(lang, "auth.email.change")}
        </button>
      )}
    </div>
  );
}
