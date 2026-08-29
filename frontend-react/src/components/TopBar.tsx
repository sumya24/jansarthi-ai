import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import SettingsModal from "./SettingsModal";
import ThemeToggle from "./ThemeToggle";
import NotificationBell from "./NotificationBell";
import NavDrawer from "./NavDrawer";
import AskJanMitraWidget from "./AskJanMitraWidget";
import "./TopBar.css";

// "JanSarthi AI" is the product name, kept as-is in every language per the brand guide —
// only the subtitle and role label translate.
const ROLE_KEY: Record<string, string> = { citizen: "role.citizen", worker: "role.worker", admin: "role.admin" };

export default function TopBar() {
  const { user, logout } = useAuth();
  const { lang } = useUiLang();
  const navigate = useNavigate();
  const [showSettings, setShowSettings] = useState(false);

  function handleLogout() {
    // replace: true -- without it, the authenticated page stayed in history underneath /welcome,
    // so Back right after logging out briefly tried to re-open it before ProtectedRoute's own
    // (already-cleared-user) bounce kicked in, instead of just leaving the app cleanly.
    navigate("/welcome", { replace: true });
    logout();
  }

  if (!user) return null;

  return (
    <>
      {/* Visually hidden until keyboard-focused (see .skip-link in global.css) -- lets a
          keyboard user jump straight past the header/nav-drawer-toggle/notification bell/theme
          toggle/settings button to the page's own content, instead of tabbing through all of
          them on every single page. #main-content is set on every Citizen/Worker/Admin page's
          outer .page/.page-admin wrapper. */}
      <a href="#main-content" className="skip-link">
        {t(lang, "topbar.skipToContent")}
      </a>
      <div className="topbar">
        <div className="topbar-left">
          {/* The one page/tab navigation drawer, role-filtered -- see NavDrawer.tsx. Renders a
              hamburger button here plus an off-canvas overlay panel (position:fixed, so its DOM
              position doesn't affect where the panel appears). Deliberately does NOT contain
              notifications/profile/settings/logout -- those stay right here in the header. */}
          <NavDrawer />
          <div className="brand">
            <img src="/brand/logo-mark.png" alt="JanSarthi AI" className="brand-mark" />
            <div>
              <div className="brand-word display">JanSarthi AI</div>
              <div className="brand-tag">{t(lang, "topbar.subtitle")}</div>
            </div>
          </div>
        </div>
        <div className="whoami">
          <div className="avatar">{user.full_name.charAt(0).toUpperCase()}</div>
          <div>
            <div>{user.full_name}</div>
            <span className="role-pill">{t(lang, ROLE_KEY[user.role])}</span>
          </div>
          {/* Workers get assignment/update notifications; admins get AI-monitoring alerts (see
              AppNotification's docstring in lib/api.ts); citizens now get their own complaint's
              accepted/started/resolved notifications (see backend/routes/complaints.py). Every
              role gets the same bell now. */}
          <NotificationBell />
          <ThemeToggle className="icon-btn" />
          <button className="icon-btn" aria-label={t(lang, "topbar.settings")} onClick={() => setShowSettings(true)}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
              <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" stroke="currentColor" strokeWidth="1.8" />
              <path
                d="M19.4 13.5a7.6 7.6 0 0 0 0-3l2-1.5-2-3.5-2.4.8a7.6 7.6 0 0 0-2.6-1.5L14 2h-4l-.4 2.3a7.6 7.6 0 0 0-2.6 1.5l-2.4-.8-2 3.5 2 1.5a7.6 7.6 0 0 0 0 3l-2 1.5 2 3.5 2.4-.8a7.6 7.6 0 0 0 2.6 1.5L10 22h4l.4-2.3a7.6 7.6 0 0 0 2.6-1.5l2.4.8 2-3.5-2-1.5Z"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </div>
      {/* Citizen-only floating helper -- unrelated to page navigation, kept out of NavDrawer
          (which is now shared across all three roles) and rendered here instead, same
          role-gated pattern the rest of this header already uses. */}
      {user.role === "citizen" && <AskJanMitraWidget />}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} onLogout={handleLogout} />}
    </>
  );
}
