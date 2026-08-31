import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t, type LangCode } from "../lib/i18n";

// Small icons drawn in this app's own stroke language (see ServiceIcons.tsx: viewBox 0 0 24 24,
// ~1.6-1.8px currentColor stroke, rounded caps/joins) rather than pulled from an icon library.
function HomeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M4 11 12 4l8 7" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M6 9.5V20h12V9.5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 20v-5.5h4V20" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ReportIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.7" />
      <path d="M12 8v8M8 12h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function ComplaintsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path
        d="M9 3.5h6a1 1 0 0 1 1 1V5h1.5A1.5 1.5 0 0 1 19 6.5v13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19.5v-13A1.5 1.5 0 0 1 6.5 5H8v-.5a1 1 0 0 1 1-1Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M8.5 11h7M8.5 14.5h7M8.5 18h4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function AskIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M4 12a8 8 0 1 1 3.3 6.5L4 20l1.2-3.7A7.96 7.96 0 0 1 4 12Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M8.2 10.8h7.6M8.2 13.8h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function AreaIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M12 21s6.5-5.9 6.5-11A6.5 6.5 0 0 0 5.5 10c0 5.1 6.5 11 6.5 11Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <circle cx="12" cy="10" r="2.2" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function QueueIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M12 3.5 3.5 8l8.5 4.5L20.5 8 12 3.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M3.5 12 12 16.5 20.5 12M3.5 16 12 20.5 20.5 16" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function DashboardIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <rect x="4" y="4" width="7" height="7" rx="1.4" stroke="currentColor" strokeWidth="1.6" />
      <rect x="13" y="4" width="7" height="7" rx="1.4" stroke="currentColor" strokeWidth="1.6" />
      <rect x="4" y="13" width="7" height="7" rx="1.4" stroke="currentColor" strokeWidth="1.6" />
      <rect x="13" y="13" width="7" height="7" rx="1.4" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function WorkersIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <circle cx="9" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.6" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="17.5" cy="9" r="2.4" stroke="currentColor" strokeWidth="1.5" />
      <path d="M15 20a4.6 4.6 0 0 1 7.5-3.6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function AiMonitoringIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M3 12h4l2-6 4 12 2-6h6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// A shield -- distinct from WorkersIcon's two-person glyph, reads as "access/permissions" rather
// than "a group of people," matching what this nav item actually gates (super-admin-only account
// management), not just another staff-roster page.
function AdminsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
      <path d="M12 3.5 5 6v5.5c0 4.6 3 7.9 7 9 4-1.1 7-4.4 7-9V6l-7-2.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M9 12l2 2 4-4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

interface NavItem {
  to: string;
  end?: boolean;
  label: string;
  icon: ReactNode;
}

// Maps directly to the existing top-level routes in App.tsx -- no invented pages. Detail routes
// (/citizen/complaints/:id, /worker/complaints/:id, /admin/workers/:id) are reached by clicking
// into a list, not from nav, so they're deliberately not here.
//
// `superAdmin` (only meaningful when role === "admin") adds /admin/admins -- mirrors
// AdminDashboard.tsx's own `user?.super_admin` gate on its "Manage Admins" button, so both
// entry points into that page agree on who sees it.
function getNavItems(role: string, lang: LangCode, superAdmin: boolean): NavItem[] {
  if (role === "citizen") {
    return [
      { to: "/citizen", end: true, label: t(lang, "nav.home"), icon: <HomeIcon /> },
      { to: "/citizen/report", label: t(lang, "home.hero.reportCta"), icon: <ReportIcon /> },
      { to: "/citizen/complaints", label: t(lang, "nav.myComplaints"), icon: <ComplaintsIcon /> },
      { to: "/citizen/ask", label: t(lang, "nav.askJanmitra"), icon: <AskIcon /> },
      { to: "/citizen/area", label: t(lang, "nav.myArea"), icon: <AreaIcon /> },
    ];
  }
  if (role === "worker") {
    // /worker is the only worker-facing top-level page today -- WorkerComplaintDetail is a
    // detail view reached by clicking a queue card, not a nav destination.
    return [{ to: "/worker", end: true, label: t(lang, "worker.title"), icon: <QueueIcon /> }];
  }
  if (role === "admin") {
    return [
      { to: "/admin", end: true, label: t(lang, "admin.title"), icon: <DashboardIcon /> },
      { to: "/admin/workers", label: t(lang, "nav.workers"), icon: <WorkersIcon /> },
      ...(superAdmin ? [{ to: "/admin/admins", label: t(lang, "nav.admins"), icon: <AdminsIcon /> }] : []),
      { to: "/admin/ai-monitoring", label: t(lang, "nav.aiMonitoring"), icon: <AiMonitoringIcon /> },
    ];
  }
  return [];
}

/** The app's one page/tab navigation drawer -- hidden by default behind a hamburger button in
 * TopBar, sliding in from the left as an overlay (backdrop + fixed panel) rather than pushing or
 * shrinking page content, same concept on desktop and mobile. Closes on backdrop click, Escape,
 * or picking a nav item. Deliberately does NOT contain notifications/profile/settings/logout --
 * those already live in the header (see TopBar.tsx) and stay there untouched; this is only the
 * existing page/tab navigation, filtered by `user.role` the same way ProtectedRoute already
 * gates routes. Rendered once from TopBar.tsx, not per-page. */
export default function NavDrawer() {
  const { user } = useAuth();
  const { lang } = useUiLang();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (!user) return null;
  const items = getNavItems(user.role, lang, user.super_admin);
  if (items.length === 0) return null;

  return (
    <>
      <button
        type="button"
        className="nav-drawer-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={t(lang, open ? "nav.closeMenu" : "nav.openMenu")}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
          <path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
      <div className={`nav-drawer-backdrop ${open ? "open" : ""}`} onClick={() => setOpen(false)} aria-hidden="true" />
      <nav className={`nav-drawer ${open ? "open" : ""}`} aria-label={t(lang, "nav.appNavigation")}>
        <div className="nav-drawer-head">
          <span className="nav-drawer-title">{t(lang, "nav.appNavigation")}</span>
          <button type="button" className="nav-drawer-close" onClick={() => setOpen(false)} aria-label={t(lang, "nav.closeMenu")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
              <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {items.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => (isActive ? "active" : "")}
            onClick={() => setOpen(false)}
          >
            {item.icon}
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </>
  );
}
