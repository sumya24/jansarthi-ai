import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import type { ProxyOptions } from 'vite'
import type { IncomingMessage } from 'http'

// Real browser navigations (typing the URL, a hard refresh) send "Accept: text/html" on the very
// first request for the page; a fetch()/XHR call the already-booted app makes for JSON data never
// does. Some of these proxy prefixes are ALSO real frontend page routes with the identical path
// (e.g. "/admin", "/admin/workers", "/admin/ai-monitoring" are simultaneously backend API
// endpoints AND React Router pages) -- without this check, a hard refresh on one of those pages
// got sent straight to the backend instead of to Vite's own index.html, so the SPA never even
// booted and the browser showed the backend's raw JSON (a 404, or a 429 under load) instead of
// the app. Returning the request's own URL from `bypass` tells Vite to handle it normally
// (falling through to the SPA's index.html for an unmatched path) instead of proxying it.
//
// LIVE-REPORTED gap in the Accept-only version above: a tab left idle a long time (overnight,
// Chrome's own memory-saver discarding a background tab) can come back and reload with an Accept
// header that doesn't reliably carry "text/html" the way a freshly-typed URL or manual hard
// refresh does -- that one slipped through and showed raw JSON again. `Sec-Fetch-Mode` is the
// header actually built for this distinction: browsers set it to "navigate" ONLY for a real
// top-level page load, page reload included, and JS can never override it (unlike Accept, which
// in principle a request could construct differently) -- checked first, with the Accept check
// kept as a second pass for the rare non-browser client that omits Sec-Fetch-Mode entirely.
function bypassRealPageLoads(req: IncomingMessage): string | undefined {
  if (req.headers['sec-fetch-mode'] === 'navigate') return req.url;
  if (req.headers.accept?.includes('text/html')) return req.url;
  return undefined;
}

function proxyTo(target: string): ProxyOptions {
  return { target, bypass: bypassRealPageLoads };
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Proxies every backend route prefix to the dev API server, so the frontend's own fetch
    // calls (VITE_API_URL left unset -- see .env.example and lib/api.ts's own comment) are
    // same-origin from the browser's point of view, exactly like production's Caddy reverse
    // proxy (see deploy/Caddyfile) -- no CORS hop in either environment.
    //
    // This became load-bearing, not just tidy, once auth switched to httpOnly cookies: the
    // frontend dev server binds to "localhost:5173" while the backend was being called at
    // "127.0.0.1:8000" (VITE_API_URL) -- a hostname and a bare IP address are different "sites"
    // to a browser's SameSite cookie enforcement even though they're the same machine, so a
    // cookie set at :8000 was silently never sent back on the next request to :5173. Proxying
    // keeps the backend reachable at 127.0.0.1 (whatever made that the working address here
    // still holds) while removing the cross-site boundary entirely.
    proxy: {
      "/auth": proxyTo("http://127.0.0.1:8000"),
      "/admin": proxyTo("http://127.0.0.1:8000"),
      "/complaints": proxyTo("http://127.0.0.1:8000"),
      "/locations": proxyTo("http://127.0.0.1:8000"),
      "/notifications": proxyTo("http://127.0.0.1:8000"),
      "/ask-janmitra": proxyTo("http://127.0.0.1:8000"),
      "/uploads": proxyTo("http://127.0.0.1:8000"),
      "/health": proxyTo("http://127.0.0.1:8000"),
    },
  },
})
