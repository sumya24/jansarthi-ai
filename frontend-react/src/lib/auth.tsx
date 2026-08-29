import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, setSessionHandlers, silentRefresh, type UserProfile } from "./api";
import { useUiLang } from "./uiLang";
import type { LangCode } from "./i18n";

interface AuthState {
  token: string | null;
  user: UserProfile | null;
  loading: boolean;
  /**
   * True from the moment logout() is called onward. ProtectedRoute checks
   * this to avoid firing its own redirect-to-/login: React Router can still
   * have the outgoing protected route mounted for a render or two after
   * navigate() already moved the URL elsewhere, and without this guard that
   * stale render's `!user` check races the real navigation and wins.
   */
  isLoggingOut: boolean;
  setSession: (accessToken: string, refreshToken: string, user: UserProfile) => void;
  updateUser: (user: UserProfile) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  // No persisted token to read on boot anymore -- the access token now lives only in React
  // state for the lifetime of this tab (see api.ts's own module docstring on this). A fresh page
  // load/hard refresh always starts from null here; the effect below re-derives a real session
  // from the httpOnly refresh_token cookie instead, exactly the same way api.ts's silentRefresh()
  // does mid-session on a 401 -- this is just that same recovery running once, proactively, at
  // boot, since there's no stored access token left to optimistically trust in the meantime.
  const [token, setToken] = useState<string | null>(null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  // The account's saved preferred_language is the source of truth for UI language once
  // logged in — synced into uiLang below so dashboards/TopBar/complaint display all follow it
  // automatically, with no separate action needed after logging in or loading a session.
  const { setLang } = useUiLang();

  useEffect(() => {
    if (token) {
      api
        .me(token)
        .then((profile) => {
          setUser(profile);
          setLang(profile.preferred_language as LangCode);
        })
        .catch(() => {
          // Token is invalid/expired AND api.ts's own silent-refresh-on-401 (triggered by this
          // exact api.me() call, since it passes a token) already failed too -- there's genuinely
          // no session left; api.ts's onSessionExpired handler below already reflects that, this
          // just clears the remaining React state to match.
          setToken(null);
          setUser(null);
        })
        .finally(() => setLoading(false));
      return;
    }
    if (isLoggingOut) {
      // token just became null BECAUSE logout() called setToken(null), not because this is a
      // fresh boot with nothing to restore yet -- without this guard, this effect re-fired on
      // that exact state change and raced its own logout() call: api.refresh() would reach the
      // server (and very plausibly succeed, since the refresh_token cookie logout() is in the
      // middle of revoking/clearing was still valid a moment earlier) and silently re-establish a
      // brand new session instants after the citizen just chose to leave -- a real, confirmed
      // race (caught by a fresh Playwright cookie check right after logout still finding the
      // cookies present).
      setLoading(false);
      return;
    }
    // No in-memory access token, and not mid-logout -- try the httpOnly refresh_token cookie
    // before concluding there's no session at all. Goes through the SAME deduplicated
    // silentRefresh() mid-session 401s already use (never the plain api.refresh() call) --
    // React StrictMode double-invokes every effect in development, and without shared dedup the
    // two near-simultaneous /auth/refresh calls that produced raced the refresh token's own
    // rotation: the second call could present the exact token the first one had *just* rotated
    // away, which reuse detection (correctly, by design) treats as compromise and revokes the
    // WHOLE session -- a real, confirmed bug behind "sometimes logged out right after a
    // refresh," not normal token expiry. silentRefresh()'s own onSessionRefreshed/onSessionExpired
    // handlers (registered below) already update token/user/lang; this effect only tracks loading.
    silentRefresh().finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, isLoggingOut]);

  // Registered once -- lets api.ts's module-level silentRefresh() (fired from ANY page's API
  // call, not just this provider's own effect above) keep this context's `token`/`user` in sync
  // with whatever session change it saw happen (the actual tokens now live only in httpOnly
  // cookies the browser manages on its own -- there's no storage left here to keep in sync with).
  useEffect(() => {
    setSessionHandlers({
      onSessionRefreshed: (accessToken, _refreshToken, refreshedUser) => {
        setToken(accessToken);
        setUser(refreshedUser);
        // Also relied on for the boot-time restore path now (see the effect above) -- that path
        // used to set this manually itself, so this handler has to cover it too, not just the
        // mid-session 401 case it originally handled alone.
        setLang(refreshedUser.preferred_language as LangCode);
      },
      onSessionExpired: () => {
        setToken(null);
        setUser(null);
      },
    });
  }, []);

  function setSession(accessToken: string, _refreshToken: string, newUser: UserProfile) {
    // No storeSession() call anymore -- login/signup/refresh already set the real httpOnly
    // cookies server-side (see backend/deps.py's set_auth_cookies); accessToken is kept here only
    // as in-memory React state for this tab's lifetime, never persisted (see this file's own
    // AuthProvider docstring above for why). _refreshToken is accepted for backward compatibility
    // with every call site (Login.tsx/Signup.tsx) but deliberately unused now -- there's nothing
    // left for this module to do with it.
    setToken(accessToken);
    setUser(newUser);
    setLang(newUser.preferred_language as LangCode);
    setIsLoggingOut(false);
  }

  function updateUser(newUser: UserProfile) {
    setUser(newUser);
  }

  function logout() {
    setIsLoggingOut(true);
    // Real, server-side revocation now, not just a local clear -- see backend/routes/auth.py's
    // logout docstring. Best-effort: fires and forgets rather than awaiting, so a slow/failed
    // network call never blocks the citizen from actually leaving their session locally. No
    // refresh token to pass -- POST /auth/logout reads it from the httpOnly cookie instead, and
    // clears all three auth cookies in its response either way (see backend/deps.py's
    // clear_auth_cookies) -- a token that somehow fails to revoke server-side this way still
    // expires naturally within REFRESH_TOKEN_EXPIRE_DAYS regardless.
    api.logout().catch(() => {});
    setToken(null);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{ token, user, loading, isLoggingOut, setSession, updateUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
