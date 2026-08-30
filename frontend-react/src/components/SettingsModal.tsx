import { useState } from "react";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t, type LangCode } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import { useModalA11y } from "../lib/useModalA11y";
import HomeLocationPicker, { type HomeLocationValue } from "./HomeLocationPicker";
import PasswordInput from "./PasswordInput";

export default function SettingsModal({ onClose, onLogout }: { onClose: () => void; onLogout: () => void }) {
  const { user, token, updateUser } = useAuth();
  const { lang, setLang } = useUiLang();
  const [fullName, setFullName] = useState(user?.full_name ?? "");
  const [language, setLanguage] = useState<LangCode>((user?.preferred_language as LangCode) ?? "en");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  // LIVE-REPORTED GAP: unlike every other Full Name field in this app (Signup, Add/Edit Worker),
  // this one had no client-side required check at all -- a blank name only ever surfaced as a
  // generic banner error from the backend, if the backend even rejects it, rather than the same
  // inline "this field is required" pattern used everywhere else. Fixed to match.
  const [profileFieldErrors, setProfileFieldErrors] = useState<Record<string, boolean>>({});

  // Citizen-only -- see HomeLocationPicker.tsx's own docstring; workers/admins have no residence
  // to edit (a worker's `ward` is their operational area, a different concept -- see User.ward's
  // model docstring). Starts as `undefined`, meaning "unchanged" -- see handleSave below, which
  // only includes location in the PATCH at all once this citizen has actually touched the
  // picker, so a plain name/language save can never accidentally overwrite it via a stale
  // pre-filled value the citizen never looked at.
  const [location, setLocation] = useState<HomeLocationValue | undefined>(undefined);
  const [showEditLocation, setShowEditLocation] = useState(false);

  async function handleSave() {
    if (!token) return;
    if (!fullName.trim()) {
      setProfileFieldErrors({ fullName: true });
      return;
    }
    setProfileFieldErrors({});
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateMe(token, {
        full_name: fullName.trim(),
        preferred_language: language,
        ...(location && {
          ward: location.ward,
          state_id: location.home_state_id,
          district_id: location.home_district_id,
          ward_id: location.home_ward_id,
          locality_id: location.home_locality_id,
        }),
      });
      updateUser(updated);
      setLang(language); // instant — dashboards/TopBar/complaint list follow immediately, no reload
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "settings.error"));
    } finally {
      setSaving(false);
    }
  }

  // Collapsed by default -- own section, own fields, own submit action, deliberately separate
  // from the profile Save button above (changing a password is a materially different, more
  // sensitive action than editing a display name). Previously there was no way for a citizen to
  // change their own password at all -- see backend/routes/auth.py's change-password endpoint.
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmNewPassword, setConfirmNewPassword] = useState("");
  const [passwordFieldErrors, setPasswordFieldErrors] = useState<Record<string, boolean>>({});
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  // Same client-side echo of the server's rule as Signup.tsx -- see that file's own comment.
  const newPasswordTooWeak =
    newPassword.length > 0 &&
    (newPassword.length < 8 || !/[A-Za-z]/.test(newPassword) || !/\d/.test(newPassword) || !/[^A-Za-z0-9]/.test(newPassword));

  async function handleChangePassword() {
    if (!token) return;
    setPasswordError(null);
    setPasswordSuccess(false);

    const errors: Record<string, boolean> = {};
    if (!currentPassword) errors.currentPassword = true;
    if (!newPassword || newPasswordTooWeak) errors.newPassword = true;
    if (!confirmNewPassword || confirmNewPassword !== newPassword) errors.confirmNewPassword = true;
    setPasswordFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;

    setChangingPassword(true);
    try {
      await api.changePassword(token, { current_password: currentPassword, new_password: newPassword });
      // Backend revokes every OTHER refresh token on a successful change (see that endpoint's
      // own docstring) -- this session's own tokens are untouched, so no re-login needed here.
      setPasswordSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmNewPassword("");
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setChangingPassword(false);
    }
  }

  // Same collapsed-by-default pattern as change-password above -- own section, own fields, own
  // submit actions. Two sub-steps live in one section (send code, then verify it) rather than two
  // separate toggles, since they're really one flow. See backend/routes/auth.py's
  // send_email_verification/verify_email: the address isn't attached to the account (user.email)
  // until the code is confirmed, so a citizen can freely retry a mistyped address before that.
  const [showAddEmail, setShowAddEmail] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [emailCodeSent, setEmailCodeSent] = useState(false);
  const [emailFieldErrors, setEmailFieldErrors] = useState<Record<string, boolean>>({});
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailSuccess, setEmailSuccess] = useState(false);
  const [sendingEmailCode, setSendingEmailCode] = useState(false);
  const [verifyingEmail, setVerifyingEmail] = useState(false);

  async function handleSendEmailCode() {
    if (!token) return;
    setEmailError(null);
    if (!newEmail.trim()) {
      setEmailFieldErrors({ newEmail: true });
      return;
    }
    setEmailFieldErrors({});
    setSendingEmailCode(true);
    try {
      await api.sendEmailVerification(token, { email: newEmail.trim() });
      setEmailCodeSent(true);
    } catch (err) {
      setEmailError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setSendingEmailCode(false);
    }
  }

  async function handleVerifyEmail() {
    if (!token) return;
    setEmailError(null);
    if (!emailCode.trim()) {
      setEmailFieldErrors({ emailCode: true });
      return;
    }
    setEmailFieldErrors({});
    setVerifyingEmail(true);
    try {
      const updated = await api.verifyEmail(token, { code: emailCode.trim() });
      updateUser(updated);
      setEmailSuccess(true);
      setShowAddEmail(false);
      setNewEmail("");
      setEmailCode("");
      setEmailCodeSent(false);
    } catch (err) {
      setEmailError(err instanceof ApiError ? err.message : t(lang, "common.somethingWrong"));
    } finally {
      setVerifyingEmail(false);
    }
  }

  const modalRef = useModalA11y(onClose);

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={modalRef}
        className="modal"
        // Wider than the base .modal's 420px default (see TopBar.css) -- this modal grew a real
        // 4-level cascading location picker (see HomeLocationPicker below) that reads as cramped,
        // wrapping-heavy, and easy to misread at the base width; every other field here still
        // reads comfortably wider too.
        //
        // LIVE-REPORTED: plain overflow-y:auto on this rounded-corner box (the base .modal
        // class's own default) let the browser's native scrollbar sit flush against the right
        // edge, which visually squares off the top/bottom-right corners even though the CSS
        // radius is still applied on all four -- same bug, same fix already used by
        // SummaryModal.tsx/ReportModal.tsx: padding:0 + overflow:hidden here, with scrolling
        // moved to an inner wrapper below.
        style={{ maxWidth: 560, padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="jm-modal-title"
        tabIndex={-1}
      >
        <div className="modal-head" style={{ margin: 0, padding: "26px 26px 0" }}>
          <h3 className="display" id="jm-modal-title">{t(lang, "settings.title")}</h3>
          <button className="x" aria-label={t(lang, "common.close")} onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={{ overflowY: "auto", padding: "18px 26px 22px" }}>
        {error && <div className="banner-error">{error}</div>}

        <div className={`field ${profileFieldErrors.fullName ? "has-error" : ""}`}>
          <label htmlFor="settings-name">
            {t(lang, "settings.fullName")}
            <span className="field-required-mark" aria-hidden="true">*</span>
          </label>
          <input
            id="settings-name"
            type="text"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            aria-invalid={profileFieldErrors.fullName || undefined}
            aria-describedby={profileFieldErrors.fullName ? "settings-name-error" : undefined}
          />
          {profileFieldErrors.fullName && (
            <div className="field-error" id="settings-name-error">{t(lang, "common.fieldRequired")}</div>
          )}
        </div>
        <div className="field">
          <label htmlFor="settings-phone">{t(lang, "settings.phone")}</label>
          <input id="settings-phone" type="tel" value={user?.phone ?? ""} disabled />
        </div>
        {user?.email && user.email_verified && (
          <div className="field">
            <label htmlFor="settings-email">{t(lang, "auth.email.sectionTitle")}</label>
            <input id="settings-email" type="email" value={`${user.email} (${t(lang, "auth.email.verified")})`} disabled />
          </div>
        )}
        {user?.role === "citizen" && (
          <div className="field">
            <label htmlFor="settings-ward">{t(lang, "citizen.ward")}</label>
            {!showEditLocation ? (
              <>
                <input id="settings-ward" type="text" value={user?.ward || t(lang, "area.noWardSet")} disabled />
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  style={{ marginTop: 8 }}
                  onClick={() => setShowEditLocation(true)}
                >
                  {t(lang, "settings.editLocation")}
                </button>
              </>
            ) : (
              <HomeLocationPicker
                lang={lang}
                onChange={setLocation}
                initial={{
                  stateId: user?.state_id ?? undefined,
                  districtId: user?.district_id ?? undefined,
                  wardId: user?.ward_id ?? undefined,
                  localityId: user?.locality_id ?? undefined,
                }}
              />
            )}
          </div>
        )}
        <div className="field">
          <label id="settings-language-label">{t(lang, "settings.preferredLanguage")}</label>
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
        <p style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 10 }}>
          {t(lang, "settings.languageHelp")}
        </p>

        <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "16px 0" }} />

        {!showChangePassword ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowChangePassword(true)}>
            {t(lang, "auth.changePassword.title")}
          </button>
        ) : (
          <div>
            <h4 style={{ margin: "0 0 10px" }}>{t(lang, "auth.changePassword.title")}</h4>
            {passwordError && <div className="banner-error">{passwordError}</div>}
            {passwordSuccess && <div className="banner-success">{t(lang, "auth.changePassword.success")}</div>}
            <div className={`field ${passwordFieldErrors.currentPassword ? "has-error" : ""}`}>
              <label htmlFor="settings-current-password">
                {t(lang, "auth.changePassword.current")}
                <span className="field-required-mark" aria-hidden="true">*</span>
              </label>
              <PasswordInput
                lang={lang}
                id="settings-current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                aria-invalid={passwordFieldErrors.currentPassword || undefined}
              />
              {passwordFieldErrors.currentPassword && (
                <div className="field-error">{t(lang, "common.fieldRequired")}</div>
              )}
            </div>
            <div className={`field ${passwordFieldErrors.newPassword ? "has-error" : ""}`}>
              <label htmlFor="settings-new-password">
                {t(lang, "auth.changePassword.new")}
                <span className="field-required-mark" aria-hidden="true">*</span>
              </label>
              <PasswordInput
                lang={lang}
                id="settings-new-password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                aria-invalid={passwordFieldErrors.newPassword || undefined}
              />
              {passwordFieldErrors.newPassword ? (
                <div className="field-error">{t(lang, newPassword ? "auth.field.passwordWeak" : "common.fieldRequired")}</div>
              ) : (
                <div className="field-hint">{t(lang, "auth.field.passwordHint")}</div>
              )}
            </div>
            <div className={`field ${passwordFieldErrors.confirmNewPassword ? "has-error" : ""}`}>
              <label htmlFor="settings-confirm-new-password">
                {t(lang, "auth.changePassword.confirmNew")}
                <span className="field-required-mark" aria-hidden="true">*</span>
              </label>
              <PasswordInput
                lang={lang}
                id="settings-confirm-new-password"
                value={confirmNewPassword}
                onChange={(e) => setConfirmNewPassword(e.target.value)}
                aria-invalid={passwordFieldErrors.confirmNewPassword || undefined}
              />
              {passwordFieldErrors.confirmNewPassword && (
                <div className="field-error">
                  {t(lang, confirmNewPassword ? "auth.field.passwordMismatch" : "common.fieldRequired")}
                </div>
              )}
            </div>
            <button type="button" className="btn btn-primary btn-sm" onClick={handleChangePassword} disabled={changingPassword}>
              {changingPassword ? t(lang, "settings.saving") : t(lang, "auth.changePassword.button")}
            </button>
          </div>
        )}

        {!(user?.email && user.email_verified) && (
          <>
            <hr style={{ border: "none", borderTop: "1px solid var(--line)", margin: "16px 0" }} />
            {!showAddEmail ? (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setShowAddEmail(true)}>
                {t(lang, "auth.email.sectionTitle")}
              </button>
            ) : (
              <div>
                <h4 style={{ margin: "0 0 10px" }}>{t(lang, "auth.email.sectionTitle")}</h4>
                {emailError && <div className="banner-error">{emailError}</div>}
                {emailSuccess && <div className="banner-success">{t(lang, "auth.email.success")}</div>}
                <div className={`field ${emailFieldErrors.newEmail ? "has-error" : ""}`}>
                  <label htmlFor="settings-new-email">
                    {t(lang, "auth.email.label")}
                    <span className="field-required-mark" aria-hidden="true">*</span>
                  </label>
                  <input
                    id="settings-new-email"
                    type="email"
                    value={newEmail}
                    onChange={(e) => setNewEmail(e.target.value)}
                    disabled={emailCodeSent}
                    aria-invalid={emailFieldErrors.newEmail || undefined}
                  />
                  {emailFieldErrors.newEmail && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
                </div>
                {!emailCodeSent ? (
                  <button type="button" className="btn btn-primary btn-sm" onClick={handleSendEmailCode} disabled={sendingEmailCode}>
                    {sendingEmailCode ? t(lang, "settings.saving") : t(lang, "auth.email.sendCode")}
                  </button>
                ) : (
                  <>
                    <div className="field-hint">{t(lang, "auth.email.sent")}</div>
                    <div className={`field ${emailFieldErrors.emailCode ? "has-error" : ""}`}>
                      <label htmlFor="settings-email-code">
                        {t(lang, "auth.field.otpCode")}
                        <span className="field-required-mark" aria-hidden="true">*</span>
                      </label>
                      <input
                        id="settings-email-code"
                        type="text"
                        inputMode="numeric"
                        value={emailCode}
                        onChange={(e) => setEmailCode(e.target.value)}
                        aria-invalid={emailFieldErrors.emailCode || undefined}
                      />
                      {emailFieldErrors.emailCode && <div className="field-error">{t(lang, "common.fieldRequired")}</div>}
                    </div>
                    <button type="button" className="btn btn-primary btn-sm" onClick={handleVerifyEmail} disabled={verifyingEmail}>
                      {verifyingEmail ? t(lang, "settings.saving") : t(lang, "auth.email.verify")}
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={handleSendEmailCode} disabled={sendingEmailCode}>
                      {t(lang, "auth.email.resend")}
                    </button>
                  </>
                )}
              </div>
            )}
          </>
        )}

        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onLogout}>
            {t(lang, "settings.logout")}
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? t(lang, "settings.saving") : t(lang, "settings.save")}
          </button>
        </div>
        </div>
      </div>
    </div>
  );
}
