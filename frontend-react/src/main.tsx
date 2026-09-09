import * as Sentry from "@sentry/react";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./styles/global.css";
import App from "./App.tsx";
import { AuthProvider } from "./lib/auth";
import { UiLangProvider } from "./lib/uiLang";
import { ThemeProvider } from "./lib/theme";
import { ToastProvider } from "./lib/toast";
import { VoiceModeProvider } from "./lib/voiceMode";
import CrashFallback from "./components/CrashFallback";

// Error monitoring -- a no-op when VITE_SENTRY_DSN is unset (the default), matching this app's
// existing "off unless explicitly configured" pattern for optional external services (see
// backend/main.py's init_error_monitoring() for the backend half of this same feature). Once
// set, an unhandled error anywhere in the React tree is automatically reported in real time --
// see docs/ERROR_MONITORING.md for how to get a DSN.
if (import.meta.env.VITE_SENTRY_DSN) {
  Sentry.init({
    dsn: import.meta.env.VITE_SENTRY_DSN,
    environment: import.meta.env.VITE_SENTRY_ENVIRONMENT || "development",
    // 0 by default -- this is an error-alerting feature first, not a performance profiler; see
    // the same reasoning in backend/config.py's SENTRY_TRACES_SAMPLE_RATE.
    tracesSampleRate: Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE || 0),
    // Complaint text/phone numbers can appear in component props/state -- default off, set
    // explicitly so a future SDK version change can't silently start attaching extra request/
    // user context.
    sendDefaultPii: false,
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Sentry.ErrorBoundary fallback={<CrashFallback />}>
      <ThemeProvider>
        <BrowserRouter>
          <UiLangProvider>
            <AuthProvider>
              <ToastProvider>
                <VoiceModeProvider>
                  <App />
                </VoiceModeProvider>
              </ToastProvider>
            </AuthProvider>
          </UiLangProvider>
        </BrowserRouter>
      </ThemeProvider>
    </Sentry.ErrorBoundary>
  </StrictMode>
);
