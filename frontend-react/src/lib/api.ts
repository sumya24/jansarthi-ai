import type { AskSarthiConversationTurn, AskSarthiResponse, AskVoiceResponse, ServiceCategory } from "./ragTypes";

// Falls back to "" (same-origin, relative requests) when unset -- the production Docker build
// deliberately leaves VITE_API_URL unset so requests go through Caddy's same-origin reverse
// proxy (see deploy/Caddyfile) with no CORS hop. Local dev sets VITE_API_URL explicitly via
// .env (copied from .env.example) to reach the backend dev server on a different port.
const API_URL = import.meta.env.VITE_API_URL || "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// --- Session state (cookie-backed) + silent refresh -----------------------------------------
// The two auth tokens are no longer held anywhere this module can read them back from later --
// they live only in the httpOnly access_token/refresh_token cookies a real browser session
// carries automatically (see backend/deps.py's set_auth_cookies), which JS has no access to at
// all. That's the actual security property this change is for: previously both tokens sat in
// localStorage, readable by any successful XSS payload for as long as the browser kept them
// around; now there is nothing there to read. React (auth.tsx) still keeps the CURRENT access
// token in memory for the lifetime of the tab (needed to attach as an Authorization header,
// still accepted alongside the cookie -- see get_current_user), but that memory copy is never
// persisted anywhere and vanishes on a hard refresh, which is exactly why AuthProvider's own boot
// sequence calls silentRefresh() unconditionally on mount to re-derive it from the cookie instead
// of ever reading it back out of storage.
let onSessionRefreshed: ((accessToken: string, refreshToken: string, user: UserProfile) => void) | null = null;
let onSessionExpired: (() => void) | null = null;

/** auth.tsx calls this once, on mount -- lets a silent refresh triggered by SOME OTHER page's API
 * call (not just the boot-time check) still update the shared AuthContext's `token`/`user`. */
export function setSessionHandlers(handlers: {
  onSessionRefreshed: (accessToken: string, refreshToken: string, user: UserProfile) => void;
  onSessionExpired: () => void;
}): void {
  onSessionRefreshed = handlers.onSessionRefreshed;
  onSessionExpired = handlers.onSessionExpired;
}

/** Reads the one cookie that's deliberately NOT httpOnly -- see backend/deps.py's
 * set_auth_cookies docstring for why the CSRF token specifically has to be JS-readable while the
 * two auth tokens next to it never are. */
function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|; )csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

// Exactly one refresh attempt in flight at a time, shared by every caller -- refresh tokens
// rotate on use (see backend/services/auth_service.py's rotate_refresh_token), so two concurrent
// 401s naively each calling POST /auth/refresh at the same moment would have the second one
// legitimately fail as "already rotated" (indistinguishable from real reuse/theft to the server).
// Coalescing into one shared promise means every concurrent 401 waits on the same single refresh
// instead of racing each other.
let refreshInFlight: Promise<{ access_token: string; refresh_token: string; user: UserProfile } | null> | null = null;

export function silentRefresh(): Promise<{ access_token: string; refresh_token: string; user: UserProfile } | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      // Empty body -- POST /auth/refresh reads the refresh token from the httpOnly cookie
      // (credentials: "include", added in _fetchJson) since this module has no JS-level access
      // to it to put in the body itself. No `token` option passed either, which is also exactly
      // why this call can never recursively trigger the 401-retry path below (that path only
      // ever fires when `options.token` was set).
      const result = await request<AuthResponse>("/auth/refresh", { method: "POST", body: {} });
      onSessionRefreshed?.(result.access_token, result.refresh_token, result.user);
      return result;
    } catch {
      // The refresh cookie itself is missing/invalid/expired/reused -- there is no session left.
      onSessionExpired?.();
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/** Shared core of request()/requestPaginated() below -- fetch + silent-refresh-and-retry-once on
 * a 401 + non-2xx error unwrapping, returning both the parsed body AND the raw Response so a
 * caller that needs a response header (see requestPaginated()'s X-Total-Count read) doesn't have
 * to re-implement this same auth/retry/error dance itself. */
async function _fetchJson(
  path: string,
  options: { method?: string; body?: unknown; token?: string | null; formData?: FormData; signal?: AbortSignal } = {}
): Promise<{ response: Response; data: unknown }> {
  const method = options.method || "GET";
  const headers: Record<string, string> = {};
  if (options.token) headers["Authorization"] = `Bearer ${options.token}`;
  // The CSRF double-submit check (backend/middleware.py's CSRFMiddleware) only ever looks at
  // this header for a mutating method, but reading it back on every request is cheap and one
  // less thing to get wrong per call site than gating it here too.
  if (method !== "GET" && method !== "HEAD") {
    const csrfToken = getCsrfToken();
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  }

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  let response: Response;
  try {
    // credentials: "include" -- required for the httpOnly auth cookies to actually travel with
    // the request at all, cross-origin (local dev: :5173 -> :8000) or not; without it, `fetch`
    // silently drops any cookie on a cross-origin request by default.
    //
    // LIVE-REPORTED BUG: cache: "no-store" -- several of these paths (/admin, /admin/workers,
    // /admin/ai-monitoring, ...) are DELIBERATELY both a backend API endpoint AND a React Router
    // page at the identical URL (see deploy/Caddyfile's own @backend matcher docstring). A real
    // top-level browser navigation to one of those page URLs (typing it, a bookmark, a hard
    // refresh) gets Caddy's SPA fallback (index.html) for that exact GET request -- correct,
    // intended behavior. But neither Caddy's response nor the browser's default fetch() cache
    // mode ("default") disables HTTP caching for that response, so the browser's own cache then
    // stores index.html under that URL. Moments later, when the mounted page's OWN JS calls
    // fetch() to that SAME URL to actually load its data, the default cache mode serves that
    // cached index.html straight back WITHOUT even hitting the network -- confirmed directly: a
    // fetch immediately after navigating to /admin/ai-monitoring returned the cached HTML, and
    // response.json() then threw a raw (non-ApiError) SyntaxError trying to parse it, which is
    // why this surfaced as a generic "Could not load AI monitoring data" error with the network
    // tab still showing a "200 OK" for the ORIGINAL (correctly-SPA) navigation request, not the
    // failing one. cache: "no-store" makes every API call in this app bypass the HTTP cache
    // entirely, so it always hits the network and always reaches the real backend response for
    // these colliding paths, regardless of what the browser cached for the page load itself.
    response = await fetch(`${API_URL}${path}`, {
      method, headers, body, signal: options.signal, credentials: "include", cache: "no-store",
    });
  } catch (err) {
    // A deliberate cancellation (see AskSarthi.tsx's stop-generation button) rejects `fetch`
    // with a DOMException named "AbortError" -- re-thrown as-is, not wrapped in ApiError, so the
    // caller can tell "the citizen chose to stop this" apart from "the network genuinely failed"
    // and skip showing an error bubble for the former.
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }

  // Only ever attempted for a call that carried a token in the first place -- login/signup/
  // refresh itself never pass `options.token`, so a 401 from any of those (wrong credentials, or
  // a genuinely dead refresh token) falls straight through to the normal error below instead of
  // looping back into another refresh attempt.
  if (response.status === 401 && options.token) {
    const refreshed = await silentRefresh();
    if (refreshed) {
      return _fetchJson(path, { ...options, token: refreshed.access_token });
    }
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      if (data?.detail) detail = data.detail;
    } catch {
      /* ignore non-JSON error bodies */
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return { response, data: undefined };
  return { response, data: await response.json() };
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; token?: string | null; formData?: FormData; signal?: AbortSignal } = {}
): Promise<T> {
  const { data } = await _fetchJson(path, options);
  return data as T;
}

/** Like request(), but for an endpoint whose real total row count (before any page/page_size
 * slicing) rides in the `X-Total-Count` response header rather than the JSON body itself -- see
 * backend/routes/complaints.py's `_paginate()` docstring. Falls back to the returned array's own
 * length when the header is absent (an unpaginated call -- i.e. page/page_size were never sent --
 * legitimately omits it, since the array already IS the whole result in that case). */
async function requestPaginated<T>(
  path: string,
  options: { method?: string; token?: string | null; signal?: AbortSignal } = {}
): Promise<{ items: T[]; total: number }> {
  const { response, data } = await _fetchJson(path, options);
  const items = (data as T[] | undefined) ?? [];
  const headerTotal = response.headers.get("X-Total-Count");
  return { items, total: headerTotal !== null ? Number(headerTotal) : items.length };
}

/** Fetch a binary (PDF) response, rather than JSON -- used only by downloadComplaintReport().
 * Auth still goes through the same Bearer header as everything else (no separate, weaker
 * authorization path for the download endpoint -- see backend/routes/complaints.py). Filename
 * comes from the server's Content-Disposition header, the same real value the backend derived
 * from the complaint's own display id -- never guessed or reconstructed client-side. Same silent-
 * refresh-then-retry-once behavior as request() above, for the same reason (a report download
 * shouldn't fail just because the access token happened to expire mid-session). */
async function requestBlob(path: string, token: string): Promise<{ blob: Blob; filename: string }> {
  async function attempt(withToken: string): Promise<Response> {
    try {
      // cache: "no-store" for the same reason as _fetchJson's own fetch() call -- /complaints*
      // is one of the path prefixes Caddy's @backend matcher covers, so this is subject to the
      // identical browser-HTTP-cache hazard even though report downloads aren't a bookmarked/
      // typed-URL destination in practice.
      return await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${withToken}` }, credentials: "include", cache: "no-store" });
    } catch {
      throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
    }
  }

  let response = await attempt(token);
  if (response.status === 401) {
    const refreshed = await silentRefresh();
    if (refreshed) response = await attempt(refreshed.access_token);
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const data = await response.json();
      if (data?.detail) detail = data.detail;
    } catch {
      /* ignore non-JSON error bodies */
    }
    throw new ApiError(response.status, detail);
  }
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/);
  const filename = match ? match[1] : "report.pdf";
  return { blob: await response.blob(), filename };
}

export interface UserProfile {
  id: number;
  full_name: string;
  phone: string;
  email: string | null;
  email_verified: boolean;
  role: "citizen" | "worker" | "admin";
  preferred_language: string;
  ward: string | null;
  // Structured counterpart of `ward` -- set for a citizen who's gone through the cascading
  // state/city/ward/area picker (signup, or editing it later in Settings), and for a worker whose
  // operational area an admin has picked via the same cascading picker (Edit Worker). Lets that
  // picker be pre-filled with the current selection instead of always starting blank -- see
  // HomeLocationPicker.tsx's `initial` prop.
  state_id: number | null;
  district_id: number | null;
  ward_id: number | null;
  locality_id: number | null;
  // Only meaningful for role="admin" -- gates the "Manage Admins" nav entry/page (see
  // backend/models.py's User.super_admin docstring). Always false for citizens/workers.
  super_admin: boolean;
}

// One selectable node at any level of the State/City/Ward/Area hierarchy (backend/routes/
// locations.py) -- same {id, name} shape at every level, so one generic picker step works for
// all four.
export interface LocationOption {
  id: number;
  name: string;
}

// See backend/routes/locations.py's ResolvedCoordinates -- every field is null if the reverse
// geocoder couldn't determine it (an honest "don't know", never a guess); deliberately no
// ward/locality field at all (OSM/Nominatim's ward-level coverage in India is unreliable).
export interface ResolvedCoordinates {
  formatted_address: string | null;
  city_name: string | null;
  district_name: string | null;
  state_name: string | null;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: UserProfile;
}

export type ComplaintStatus = "pending" | "assigned" | "accepted" | "in_progress" | "resolved";

export interface Complaint {
  id: number;
  citizen_id: string;
  original_text: string;
  original_language: string;
  translated_text: string;
  display_text: string;
  summary: string;
  display_summary: string;
  photo_path: string | null;
  status: ComplaintStatus;
  // LIVE-REPORTED GAP: this has always been classified at filing time (Ask Sarthi's own routing,
  // the Report an Issue wizard's 3-layer classifier) but was never persisted until now -- None
  // for a complaint filed before this field existed, or when classification itself was unsure.
  service_category: ServiceCategory | null;
  ward: string | null;
  address: string | null;
  // Human-readable names for whatever structured location could be resolved -- None at any
  // level that wasn't resolved (see backend's LocationResolver). Never a fabricated value.
  location_state: string | null;
  location_district: string | null;
  location_ulb: string | null;
  // The same resolution, as ids -- lets AssignWorkerModal default its hierarchical picker to
  // this complaint's own location without a name-based lookup. Same null-when-unresolved rule
  // as the name fields above.
  state_id: number | null;
  district_id: number | null;
  ward_id: number | null;
  assigned_worker_name: string | null;
  // Only populated once the assigned worker has accepted (or resolved) — see backend/routes/complaints.py.
  assigned_worker_phone: string | null;
  rejection_count: number;
  feedback_rating: number | null;
  feedback_comment: string | null;
  created_at: string;
}

// GET /complaints/area-summary -- deliberately a small, separate shape rather than reusing
// Complaint: no citizen_id, no assigned_worker_name/phone anywhere here, since this is a
// neighbor's complaint, not the caller's own (see backend's AreaSummaryResponse docstring).
export interface AreaComplaintSummary {
  id: number;
  status: ComplaintStatus;
  service_category: ServiceCategory | null;
  display_text: string;
  created_at: string;
  status_updated_at: string;
}

export interface AreaSummary {
  ward: string;
  pending_count: number;
  in_progress_count: number;
  resolved_count: number;
  complaints: AreaComplaintSummary[];
  // Total complaints matching the request's own `search` (or every complaint in the ward, with
  // no search) -- NOT necessarily complaints.length once page/pageSize are used, since that
  // array is only the current page. See backend's AreaSummaryResponse docstring.
  total: number;
}

// Worker complaint-resolution workflow -- see backend/routes/complaints.py's
// ComplaintUpdateResponse / StatusHistoryEntryResponse / ComplaintDetailResponse.
export type ComplaintUpdateType = "INITIAL_ASSESSMENT" | "PROGRESS_UPDATE" | "COMPLETION";

// Evidence upload phase -- see backend/models.py's ComplaintEvidence docstring. One row per
// uploaded file; a complaint/update can have zero to many, unlike the old single `photo_path`
// columns (kept alongside these, read-only, for rows written before this system existed).
export type EvidenceStage = "CITIZEN_COMPLAINT" | "INITIAL_ASSESSMENT" | "PROGRESS_UPDATE" | "COMPLETION";

export interface EvidenceFile {
  id: number;
  update_id: number | null;
  uploaded_by: number;
  uploader_role: "citizen" | "worker";
  file_name: string;
  file_path: string;
  file_type: string;
  file_size: number;
  stage: EvidenceStage;
  created_at: string;
}

export interface ComplaintUpdateEntry {
  id: number;
  update_type: ComplaintUpdateType;
  text: string;
  photo_path: string | null;
  worker_name: string | null;
  created_at: string;
  evidence: EvidenceFile[];
}

export interface StatusHistoryEntry {
  from_status: string | null;
  to_status: string;
  actor_role: "citizen" | "worker" | "system" | "admin";
  note: string | null;
  created_at: string;
}

// One worker's rejection of this complaint -- admin-only (see backend/routes/complaints.py's
// RejectionResponse/_to_detail_response docstrings). Always present on ComplaintDetail but only
// ever non-empty when the viewer is an admin; citizens and workers always get an empty array,
// enforced server-side, not just hidden here.
export interface ComplaintRejection {
  worker_name: string;
  reason: string | null;
  created_at: string;
}

export interface ComplaintDetail extends Complaint {
  updates: ComplaintUpdateEntry[];
  status_history: StatusHistoryEntry[];
  // Every evidence file across every stage -- group by `.stage` (and, within PROGRESS_UPDATE,
  // by `.update_id`) to show citizen/initial/progress/completion evidence separately.
  evidence: EvidenceFile[];
  rejections: ComplaintRejection[];
}

export interface ComplaintReport {
  complaint_id: number;
  display_id: string;
  service_summary: string;
  original_description: string;
  created_at: string;
  location_ward: string | null;
  location_state: string | null;
  location_district: string | null;
  location_ulb: string | null;
  location_address: string | null;
  assigned_worker_name: string | null;
  initial_assessment: string | null;
  initial_assessment_at: string | null;
  progress_updates: { text: string; photo_path: string | null; worker_name: string | null; created_at: string; evidence: string[] }[];
  completion_status: string | null;
  completion_evidence_photo: string | null;
  resolved_at: string | null;
  timeline: { from_status: string | null; to_status: string; actor_role: string; note: string | null; created_at: string }[];
  citizen_evidence: string[];
  initial_assessment_evidence: string[];
  completion_evidence: string[];
}

// In-app notifications -- see backend/models.py's Notification docstring for the authoritative
// list. NEW_ASSIGNMENT/REASSIGNED go to workers (assignment_service.py). COMPLAINT_ACCEPTED/
// STARTED/RESOLVED go to the citizen who filed it (routes/complaints.py). COMPLAINT_REJECTED and
// AI_ALERT are both broadcast to every admin (routes/complaints.py's reject_complaint(), and
// ai_request_log_repository.py's check_and_fire_alerts() for a sustained Ask Sarthi error-rate/
// latency problem -- see that function's own cooldown docstring). AI_ALERT is the one type that
// never carries a complaint_id, since it isn't about any single complaint (see NotificationBell.tsx).
export type NotificationType =
  | "NEW_ASSIGNMENT"
  | "REASSIGNED"
  | "COMPLAINT_ACCEPTED"
  | "COMPLAINT_STARTED"
  | "COMPLAINT_RESOLVED"
  | "COMPLAINT_REJECTED"
  | "AI_ALERT";

export interface AppNotification {
  id: number;
  type: NotificationType;
  title: string;
  message: string;
  complaint_id: number | null;
  created_at: string;
  read_at: string | null;
}

export interface NotificationList {
  notifications: AppNotification[];
  unread_count: number;
}

export interface WorkerSummary extends UserProfile {
  open_complaints: number;
  resolved_complaints: number;
}

export interface DeleteWorkerResult {
  deleted_worker_id: number;
  // How many of this worker's "assigned"/"accepted" complaints were reset to "pending" as a
  // result — see backend/routes/admin.py's delete_worker() docstring for why they're reset,
  // never deleted, along with the worker.
  reset_to_pending: number;
}

export interface DeleteComplaintResult {
  deleted_complaint_id: number;
}

export interface DeleteAiRequestResult {
  deleted_request_log_id: number;
}

export interface AssignComplaintResult {
  id: number;
  status: ComplaintStatus;
  assigned_worker_id: number | null;
  assigned_worker_name: string | null;
}

// AI Monitoring (Admin dashboard) -- see backend/routes/admin.py's /admin/ai-monitoring*
// endpoints and docs/ask_sarthi_langsmith_observability.md. Sourced entirely from the app's
// own database (AiRequestLog), never from LangSmith directly -- this keeps working even when
// LangSmith isn't configured; `trace_url` is the only field that ever comes from LangSmith
// config, and it's just a locally-built string, not a live LangSmith API call.
export interface LocationStatusCount {
  state: string;
  district: string;
  ward: string;
  status: string;
  total: number;
}

export interface ServiceStatusCount {
  service_category: string;
  status: string;
  total: number;
}

export interface DailyComplaintTrend {
  date: string;
  opened: number;
  resolved: number;
  accepted: number;
  rejected: number;
}

export interface DailyAiStat {
  date: string;
  request_count: number;
  average_latency_ms: number;
}

export interface ComplaintStatusCounts {
  pending: number;
  assigned: number;
  accepted: number;
  in_progress: number;
  resolved: number;
}

export interface AiMonitoringSummary {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  error_rate: number;
  average_latency_ms: number;
  rag_requests: number;
  complaint_requests: number;
  status_requests: number;
  out_of_scope_requests: number;
  clarification_requests: number;
  latency_alert_threshold_ms: number;
}

/** One row of the "Cost by model" panel -- see backend/routes/admin.py's ModelCostEntry and
 * tracing.get_model_cost_summary()'s own docstring for why this is a separate endpoint from
 * aiMonitoringSummary: Phoenix's own "Top models" dashboard widgets are hard-capped at showing
 * only 4 models at a time, which would silently hide the smaller-volume Gemini/local vision
 * models -- this always shows every real model Ask Sarthi calls. */
export interface ModelCostEntry {
  model_name: string;
  label: string;
  vendor: string;
  is_free: boolean;
  total_cost_inr: number;
  total_tokens: number;
  request_count: number;
}

export interface AiRequestLogEntry {
  id: number;
  request_id: string;
  intent: string | null;
  service_category: string | null;
  routed_to: string;
  success: boolean;
  error_type: string | null;
  latency_ms: number;
  created_at: string;
  trace_url: string | null;
  phoenix_trace_url: string | null;
  ai_cost_inr: number | null;
  ai_model_name: string | null;
  ai_total_tokens: number | null;
}

export const api = {
  // Email verification is mandatory at signup, but decoupled from the rest of the form behind
  // its own "Verify" button (see backend/routes/auth.py's module docstring): sendSignupEmailCode
  // + verifySignupEmailCode handle that round trip using only the email address, and hand back a
  // one-time email_verification_token that must be included in the final signup() call below.
  sendSignupEmailCode: (body: { email: string }) =>
    request<void>("/auth/signup/email/send-code", { method: "POST", body }),

  verifySignupEmailCode: (body: { email: string; code: string }) =>
    request<{ email_verification_token: string }>("/auth/signup/email/verify-code", { method: "POST", body }),

  // Creates the account directly, in one call -- but only succeeds if email_verification_token
  // proves verifySignupEmailCode above already succeeded for this email (see backend/routes/
  // auth.py's signup()). ward is mandatory at signup, but -- unlike the four home_*_id fields
  // below it -- editable again later via updateMe's own ward/state_id/.../locality_id group
  // (citizens genuinely move; see MeUpdateRequest's own docstring). The four home_*_id fields
  // here are optional and additive (see the same docstring) -- only the deepest one the
  // citizen's cascading picker actually reached needs to be sent.
  signup: (body: {
    full_name: string; phone: string; email: string; email_verification_token: string; password: string;
    preferred_language: string; ward: string;
    home_state_id?: number; home_district_id?: number; home_ward_id?: number; home_locality_id?: number;
  }) => request<AuthResponse>("/auth/signup", { method: "POST", body }),

  // identifier accepts either a phone number or a verified email -- see backend/routes/auth.py's
  // login() for the phone-shaped-vs-email-shaped detection.
  login: (body: { identifier: string; password: string }) =>
    request<AuthResponse>("/auth/login", { method: "POST", body }),

  me: (token: string) => request<UserProfile>("/auth/me", { token }),

  // ward/state_id/district_id/ward_id/locality_id are a single all-or-nothing group -- either
  // omit all of them (leaves the citizen's residence untouched), or send `ward` (required within
  // the group) plus whichever `..._id` is the deepest level their picker actually reached; see
  // backend/routes/auth.py's MeUpdateRequest docstring for why they can't be sent partially.
  updateMe: (
    token: string,
    body: {
      full_name?: string; preferred_language?: string;
      ward?: string; state_id?: number; district_id?: number; ward_id?: number; locality_id?: number;
    }
  ) => request<UserProfile>("/auth/me", { method: "PATCH", token, body }),

  // Send a 6-digit OTP to a candidate email; it isn't attached to the account until verifyEmail
  // below confirms it (see backend/routes/auth.py's send_email_verification docstring).
  sendEmailVerification: (token: string, body: { email: string }) =>
    request<void>("/auth/email/send-verification", { method: "POST", token, body }),

  verifyEmail: (token: string, body: { code: string }) =>
    request<UserProfile>("/auth/email/verify", { method: "POST", token, body }),

  // No token on either -- both are pre-login, self-service account recovery.
  forgotPassword: (body: { email: string }) =>
    request<void>("/auth/forgot-password", { method: "POST", body }),

  resetPassword: (body: { email: string; code: string; new_password: string }) =>
    request<void>("/auth/reset-password", { method: "POST", body }),

  // No `token` option on either -- /auth/refresh and /auth/logout both authenticate via the
  // refresh token, which for a real browser session rides along as the httpOnly refresh_token
  // cookie (see backend/routes/auth.py's RefreshRequest/LogoutRequest docstrings), never as a
  // Bearer access token. `refreshToken` here is optional and only for tests/manual API use --
  // the app itself never has one to pass, and normal silent-refresh-on-401 goes through the
  // internal silentRefresh() above, not this.
  refresh: (refreshToken?: string) =>
    request<AuthResponse>("/auth/refresh", { method: "POST", body: refreshToken ? { refresh_token: refreshToken } : {} }),

  logout: (refreshToken?: string) =>
    request<void>("/auth/logout", { method: "POST", body: refreshToken ? { refresh_token: refreshToken } : {} }),

  changePassword: (token: string, body: { current_password: string; new_password: string }) =>
    request<void>("/auth/change-password", { method: "POST", token, body }),

  // LIVE-REPORTED GAP: this used to always fetch every complaint the caller's role can see in
  // one response -- fine at demo scale, not fine once a citizen/worker/admin has hundreds. `page`/
  // `pageSize` are opt-in (see backend's `_paginate()` docstring): passed together, the backend
  // returns a real bounded slice plus an accurate `total` (read from `X-Total-Count`); omitted,
  // behavior is unchanged (every matching row, `total` falls back to that same array's length).
  listComplaints: (
    token: string,
    opts: {
      lang?: string; workerId?: number; status?: string; category?: string; search?: string; page?: number; pageSize?: number;
      dateFrom?: string; dateTo?: string;
    } = {}
  ) => {
    const params = new URLSearchParams();
    if (opts.lang) params.set("lang", opts.lang);
    if (opts.workerId !== undefined) params.set("worker_id", String(opts.workerId));
    if (opts.status) params.set("status", opts.status);
    if (opts.category) params.set("category", opts.category);
    if (opts.search) params.set("search", opts.search);
    if (opts.page !== undefined) params.set("page", String(opts.page));
    if (opts.pageSize !== undefined) params.set("page_size", String(opts.pageSize));
    if (opts.dateFrom) params.set("date_from", opts.dateFrom);
    if (opts.dateTo) params.set("date_to", opts.dateTo);
    const qs = params.toString();
    return requestPaginated<Complaint>(`/complaints${qs ? `?${qs}` : ""}`, { token });
  },

  // Deliberately no required token -- GET /complaints/wards is unauthenticated (see its own
  // docstring), since Signup needs this list before the citizen has one at all.
  listWards: (token?: string) => request<string[]>("/complaints/wards", token ? { token } : {}),

  // The optional State/City/Ward/Area cascading picker on Signup (backend/routes/locations.py) --
  // also unauthenticated for the same reason as listWards above. Each step is only ever called
  // after its parent was chosen, so an empty result means "no real data for this one yet" (see
  // that route module's docstring) -- the caller falls back to free text, it never means an error.
  listStates: () => request<LocationOption[]>("/locations/states", {}),
  listCitiesForState: (stateId: number) => request<LocationOption[]>(`/locations/states/${stateId}/cities`, {}),
  listWardsForCity: (districtId: number) => request<LocationOption[]>(`/locations/cities/${districtId}/wards`, {}),
  listLocalitiesForWard: (wardId: number) => request<LocationOption[]>(`/locations/wards/${wardId}/localities`, {}),

  // Resolves a ward's own display string (e.g. "Ward 3 — Indiranagar, Bengaluru", exactly what
  // listWards()'s entries look like) back to its structured Ward row, so a caller that only has
  // that string -- Report an Issue's ward dropdown -- can still reach listLocalitiesForWard above
  // it. Powers the Area/Address field's real-locality suggestions (see LocationPicker.tsx): once
  // a citizen picks a ward, this + listLocalitiesForWard fetch any real, already-known localities
  // for it, offered as autocomplete suggestions on that still-plain text field -- never a forced
  // choice, never fabricated, since a ward with nothing seeded under it (most of them, today)
  // resolves to `null` and the field stays exactly the free-text box it already was.
  resolveWard: (text: string) => request<LocationOption | null>(`/locations/wards/resolve?${new URLSearchParams({ text })}`, {}),

  // LIVE-REPORTED GAP: "Use current location" (LocationPicker.tsx) used to attach raw GPS coords
  // with only a generic "location detected" badge -- no indication of WHERE was actually
  // detected, since ward-level reverse geocoding only ever ran server-side, after final
  // submission. This calls that same resolver live, so the citizen can see and confirm an honest
  // human-readable address the moment location is detected, not after the fact.
  resolveCoordinates: (lat: number, lng: number) =>
    request<ResolvedCoordinates>(`/locations/resolve-coordinates?${new URLSearchParams({ lat: String(lat), lng: String(lng) })}`, {}),

  getAreaSummary: (
    token: string,
    opts: {
      lang?: string; status?: string; category?: string; search?: string; page?: number; pageSize?: number;
    } = {}
  ) => {
    const params = new URLSearchParams();
    if (opts.lang) params.set("lang", opts.lang);
    if (opts.status) params.set("status", opts.status);
    if (opts.category) params.set("category", opts.category);
    if (opts.search) params.set("search", opts.search);
    if (opts.page !== undefined) params.set("page", String(opts.page));
    if (opts.pageSize !== undefined) params.set("page_size", String(opts.pageSize));
    const qs = params.toString();
    return request<AreaSummary>(`/complaints/area-summary${qs ? `?${qs}` : ""}`, { token });
  },

  createComplaint: (token: string, form: FormData) =>
    request<Complaint>("/complaints", { method: "POST", token, formData: form }),

  // First layer of the Report an Issue wizard's 3-layer category classification (real model ->
  // client-side keyword match -> manual picker, see ReportIssue.tsx). `category` is null
  // whenever the model layer couldn't classify with confidence for ANY reason (not configured,
  // network failure, timeout, genuine uncertainty) -- the caller falls through to its own next
  // layer exactly as if this call was never made; never throws for a low-confidence result.
  classifyComplaintCategory: (token: string, text: string) =>
    request<{ category: ServiceCategory | null }>("/complaints/classify-category", {
      method: "POST",
      token,
      body: { text },
    }),

  // Full detail view (complaint + status timeline + worker-authored updates) -- the data source
  // for the worker task-detail page and the citizen-facing timeline alike. Same authorization
  // the list endpoint already applies (citizen owns it / worker is currently assigned / admin
  // sees everything), enforced server-side -- see backend/routes/complaints.py's
  // _get_visible_complaint.
  getComplaint: (token: string, id: number, lang?: string) =>
    request<ComplaintDetail>(`/complaints/${id}${lang ? `?lang=${lang}` : ""}`, { token }),

  acceptComplaint: (token: string, id: number) =>
    request<Complaint>(`/complaints/${id}/accept`, { method: "POST", token }),

  // `reason` is mandatory -- see RejectComplaintModal, which blocks submission with an empty
  // reason client-side; the backend independently re-validates this (never trust the frontend
  // alone for a hard rule -- see backend/routes/complaints.py's reject_complaint()).
  rejectComplaint: (token: string, id: number, reason: string) =>
    request<Complaint>(`/complaints/${id}/reject`, { method: "POST", token, body: { reason } }),

  // Mandatory initial assessment -- moves an accepted complaint to "in_progress". `photos` is
  // optional evidence (zero or more) -- see StartWorkModal. Now multipart/form-data (was JSON)
  // so it can carry files, matching /resolve's existing contract.
  startWork: (token: string, id: number, assessment: string, photos?: File[]) => {
    const form = new FormData();
    form.append("assessment", assessment);
    for (const photo of photos ?? []) form.append("photos", photo);
    return request<Complaint>(`/complaints/${id}/start`, { method: "POST", token, formData: form });
  },

  // Optional -- a worker may add zero, one, or many of these while a complaint is in_progress.
  // `photos` is optional evidence (zero or more), reusing the same upload path every other photo
  // in this app already uses.
  addProgressUpdate: (token: string, id: number, text: string, photos?: File[]) => {
    const form = new FormData();
    form.append("text", text);
    for (const photo of photos ?? []) form.append("photos", photo);
    return request<ComplaintUpdateEntry>(`/complaints/${id}/updates`, { method: "POST", token, formData: form });
  },

  // `completion_status` is mandatory (blocks the complaint moving to "resolved" without one --
  // see backend/routes/complaints.py's resolve_complaint() docstring for the commit-ordering
  // guarantee); `photos` is optional completion evidence (zero or more).
  resolveComplaint: (token: string, id: number, completionStatus: string, photos?: File[]) => {
    const form = new FormData();
    form.append("completion_status", completionStatus);
    for (const photo of photos ?? []) form.append("photos", photo);
    return request<Complaint>(`/complaints/${id}/resolve`, { method: "POST", token, formData: form });
  },

  submitFeedback: (token: string, id: number, body: { rating: number; comment?: string }) =>
    request<Complaint>(`/complaints/${id}/feedback`, { method: "POST", token, body }),

  // Resolution report -- both 404 (via the shared ApiError) until the complaint is actually
  // "resolved"; never a fake/partial report before then (see backend/routes/complaints.py).
  getComplaintReport: (token: string, id: number, lang?: string) =>
    request<ComplaintReport>(`/complaints/${id}/report${lang ? `?lang=${lang}` : ""}`, { token }),

  downloadComplaintReport: (token: string, id: number, lang?: string) =>
    requestBlob(`/complaints/${id}/report/download${lang ? `?lang=${lang}` : ""}`, token),

  // In-app notifications -- worker-only in practice today, see AppNotification's docstring.
  listNotifications: (token: string) => request<NotificationList>("/notifications", { token }),

  markNotificationRead: (token: string, id: number) =>
    request<AppNotification>(`/notifications/${id}/read`, { method: "POST", token }),

  // Same OTP round trip as sendSignupEmailCode/verifySignupEmailCode above, reusing the identical
  // backend proof-token mechanism (see backend/routes/admin.py's own send_worker_email_code/
  // verify_worker_email_code) -- admin-authenticated instead of public, since these are only ever
  // reached from inside the admin-only Add/Edit Worker forms (see EmailVerifyField.tsx).
  sendWorkerEmailCode: (token: string, email: string) =>
    request<void>("/admin/workers/email/send-code", { method: "POST", token, body: { email } }),

  verifyWorkerEmailCode: (token: string, email: string, code: string) =>
    request<{ email_verification_token: string }>("/admin/workers/email/verify-code", { method: "POST", token, body: { email, code } }),

  createWorker: (
    token: string,
    body: {
      full_name: string; phone: string; password: string; ward: string; preferred_language: string;
      // LIVE-REPORTED GAP: Add Worker was the one place still using a plain free-text ward box
      // when Edit Worker (updateWorker below) had already moved to the structured State/City/
      // Ward/Area picker. Additive, same as there -- `ward` stays required for the display
      // string, `ward_id`/`locality_id` are optional extras the backend uses to derive the full
      // state/district chain (see backend/routes/admin.py's CreateWorkerRequest docstring).
      ward_id?: number;
      locality_id?: number;
      email?: string;
      // Proof that verifyWorkerEmailCode above already succeeded for `email` -- required whenever
      // `email` is sent, same as signup() itself requires it (see backend/routes/admin.py's
      // CreateWorkerRequest docstring).
      email_verification_token?: string;
    }
  ) => request<UserProfile>("/admin/workers", { method: "POST", token, body }),

  // LIVE-REPORTED GAP: this used to always fetch every worker in one response, then filter/
  // paginate all of it client-side -- same gap the complaint dashboards had (see
  // backend/routes/admin.py's list_workers docstring). `search`/`page`/`pageSize` are opt-in and
  // additive: omitting page/pageSize returns every matching worker unchanged (so
  // AdminWorkerDetail.tsx's own unpaginated call keeps working). `totalOpenComplaints`/
  // `totalResolvedComplaints` are aggregate sums across EVERY worker (never affected by
  // search/page) -- back AdminWorkers.tsx's own two stat tiles.
  listWorkers: async (
    token: string,
    opts: { search?: string; page?: number; pageSize?: number; dateFrom?: string; dateTo?: string } = {}
  ): Promise<{ items: WorkerSummary[]; total: number; totalOpenComplaints: number; totalResolvedComplaints: number }> => {
    const params = new URLSearchParams();
    if (opts.search) params.set("search", opts.search);
    if (opts.page !== undefined) params.set("page", String(opts.page));
    if (opts.pageSize !== undefined) params.set("page_size", String(opts.pageSize));
    if (opts.dateFrom) params.set("date_from", opts.dateFrom);
    if (opts.dateTo) params.set("date_to", opts.dateTo);
    const qs = params.toString();
    const { response, data } = await _fetchJson(`/admin/workers${qs ? `?${qs}` : ""}`, { token });
    const items = (data as WorkerSummary[] | undefined) ?? [];
    const headerTotal = response.headers.get("X-Total-Count");
    return {
      items,
      total: headerTotal !== null ? Number(headerTotal) : items.length,
      totalOpenComplaints: Number(response.headers.get("X-Total-Open-Complaints") ?? "0"),
      totalResolvedComplaints: Number(response.headers.get("X-Total-Resolved-Complaints") ?? "0"),
    };
  },

  updateWorker: (
    token: string,
    id: number,
    body: {
      full_name?: string; ward?: string; preferred_language?: string; ward_id?: number; locality_id?: number;
      // "" clears the worker's email; omitting the key leaves it untouched -- see
      // backend/routes/admin.py's UpdateWorkerRequest docstring. Re-sending the worker's own
      // current (already-verified) email back unchanged needs no token; only an actual change
      // does -- same docstring.
      email?: string;
      email_verification_token?: string;
    }
  ) => request<UserProfile>(`/admin/workers/${id}`, { method: "PATCH", token, body }),

  resetWorkerPassword: (token: string, id: number, newPassword: string) =>
    request<UserProfile>(`/admin/workers/${id}/reset-password`, { method: "POST", token, body: { new_password: newPassword } }),

  deleteWorker: (token: string, id: number) =>
    request<DeleteWorkerResult>(`/admin/workers/${id}`, { method: "DELETE", token }),

  // Super-admin-only admin account management (see backend/models.py's User.super_admin
  // docstring and backend/routes/admin.py's require_super_admin). Reuses sendWorkerEmailCode/
  // verifyWorkerEmailCode above for the optional email-proof step -- same underlying "prove this
  // address before attaching it to any new account" requirement as creating a worker, see that
  // endpoint's own docstring for why there's no separate admin-specific pair of OTP routes.
  createAdmin: (
    token: string,
    body: {
      full_name: string; phone: string; password: string; preferred_language: string;
      email?: string;
      email_verification_token?: string;
      super_admin?: boolean;
    }
  ) => request<UserProfile>("/admin/admins", { method: "POST", token, body }),

  // Same opt-in pagination contract as listWorkers above: omitting page/pageSize returns every
  // matching admin unchanged, a caller that opts in gets a real slice plus a real `total`.
  updateAdmin: (
    token: string,
    id: number,
    body: {
      full_name?: string; preferred_language?: string;
      // "" clears the admin's email; omitting the key leaves it untouched -- see
      // backend/routes/admin.py's UpdateAdminRequest docstring. Re-sending the admin's own
      // current (already-verified) email back unchanged needs no token; only an actual change
      // does -- same as updateWorker.
      email?: string;
      email_verification_token?: string;
      // Promotes/demotes -- omit to leave unchanged. The backend refuses `false` on the caller's
      // own account (see update_admin()'s own docstring).
      super_admin?: boolean;
    }
  ) => request<UserProfile>(`/admin/admins/${id}`, { method: "PATCH", token, body }),

  resetAdminPassword: (token: string, id: number, newPassword: string) =>
    request<UserProfile>(`/admin/admins/${id}/reset-password`, { method: "POST", token, body: { new_password: newPassword } }),

  listAdmins: async (
    token: string,
    opts: { search?: string; page?: number; pageSize?: number } = {}
  ): Promise<{ items: UserProfile[]; total: number }> => {
    const params = new URLSearchParams();
    if (opts.search) params.set("search", opts.search);
    if (opts.page !== undefined) params.set("page", String(opts.page));
    if (opts.pageSize !== undefined) params.set("page_size", String(opts.pageSize));
    const qs = params.toString();
    const { response, data } = await _fetchJson(`/admin/admins${qs ? `?${qs}` : ""}`, { token });
    const items = (data as UserProfile[] | undefined) ?? [];
    const headerTotal = response.headers.get("X-Total-Count");
    return { items, total: headerTotal !== null ? Number(headerTotal) : items.length };
  },

  deleteAdmin: (token: string, id: number) =>
    request<{ deleted_admin_id: number }>(`/admin/admins/${id}`, { method: "DELETE", token }),

  deleteComplaint: (token: string, id: number) =>
    request<DeleteComplaintResult>(`/admin/complaints/${id}`, { method: "DELETE", token }),

  deleteAiRequestLog: (token: string, id: number) =>
    request<DeleteAiRequestResult>(`/admin/ai-monitoring/requests/${id}`, { method: "DELETE", token }),

  assignComplaint: (token: string, complaintId: number, workerId: number) =>
    request<AssignComplaintResult>(`/admin/complaints/${complaintId}/assign`, {
      method: "POST",
      token,
      body: { worker_id: workerId },
    }),

  // Flat state/district/ward/status rows -- the one view only an admin's all-ward access makes
  // useful. The frontend builds the 3-level drill-down tree from these; a combo with zero
  // complaints is simply absent, not returned as a zero row.
  complaintsByLocation: (token: string) => request<LocationStatusCount[]>("/admin/complaints/by-location", { token }),

  // Feeds the zoom-drilldown service donut on both Worker and Admin dashboards -- role-scoped
  // server-side exactly like listComplaints (citizen/worker see only their own; admin sees
  // everything, or one worker's own via `workerId`), so this is NOT under /admin.
  complaintsByService: (token: string, workerId?: number) =>
    request<ServiceStatusCount[]>(`/complaints/by-service${workerId !== undefined ? `?worker_id=${workerId}` : ""}`, { token }),

  // Real daily opened/resolved counts (see backend/routes/complaints.py's complaints_trend
  // docstring for why "resolved" is sourced from ComplaintStatusHistory, not Complaint.status).
  complaintsTrend: (token: string, opts: { workerId?: number; days?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.workerId !== undefined) params.set("worker_id", String(opts.workerId));
    if (opts.days !== undefined) params.set("days", String(opts.days));
    const qs = params.toString();
    return request<DailyComplaintTrend[]>(`/complaints/trend${qs ? `?${qs}` : ""}`, { token });
  },

  // One request for all five status counts (stat tiles + filter-chip badges) -- see
  // backend/routes/admin.py's ComplaintStatusCounts docstring for why this replaced five
  // separate per-status requests.
  complaintStatusCounts: (token: string) => request<ComplaintStatusCounts>("/admin/complaints/status-counts", { token }),

  aiMonitoringSummary: (token: string) => request<AiMonitoringSummary>("/admin/ai-monitoring", { token }),
  aiMonitoringModelCosts: (token: string) => request<ModelCostEntry[]>("/admin/ai-monitoring/model-costs", { token }),

  // Per-day request volume + avg latency -- feeds the Admin dashboard's AI health trend chart
  // (distinct from aiMonitoringSummary's single running total).
  aiMonitoringDaily: (token: string) => request<DailyAiStat[]>("/admin/ai-monitoring/daily", { token }),

  aiMonitoringRequests: (token: string, limit = 20) =>
    request<AiRequestLogEntry[]>(`/admin/ai-monitoring/requests?limit=${limit}`, { token }),

  // Real, server-side pagination (page/page_size) for the AI Monitoring page's "Recent Requests"
  // table -- total (before slicing) rides in the X-Total-Count header, same convention as
  // listComplaints() below.
  aiMonitoringRequestsPage: (
    token: string,
    page: number,
    pageSize: number,
    search?: string,
    dateFrom?: string,
    dateTo?: string
  ) => {
    const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (search) qs.set("search", search);
    if (dateFrom) qs.set("date_from", dateFrom);
    if (dateTo) qs.set("date_to", dateTo);
    return requestPaginated<AiRequestLogEntry>(`/admin/ai-monitoring/requests?${qs}`, { token });
  },

  askSarthi: (
    token: string,
    body: {
      question: string;
      language: string;
      latitude?: number | null;
      longitude?: number | null;
      location_text?: string | null;
      conversation_history?: AskSarthiConversationTurn[];
      // Purely an observability signal (see backend/services/observability/tracing.py) -- groups
      // every turn of one chat into the same Phoenix "session" instead of unrelated traces. Never
      // read back by the app, never changes routing/behavior.
      conversation_id?: string;
      // True when `question` came from Mic 1 rather than typing -- an observability signal only
      // (see backend/schemas/ask_sarthi.py's AskSarthiRequest.was_voice_input); never changes
      // routing/behavior.
      was_voice_input?: boolean;
    },
    signal?: AbortSignal
  ) => request<AskSarthiResponse>("/ask-sarthi", { method: "POST", token, body, signal }),

  // Same request as askSarthi(), plus one attached photo -- multipart because it carries a
  // file (see backend/routes/ask_sarthi.py's POST /ask-sarthi/image). conversation_history
  // is JSON-encoded into its own form field since multipart can't nest structured values, same
  // shape the JSON endpoint already validates.
  askSarthiWithImage: (
    token: string,
    body: {
      question: string;
      language: string;
      latitude?: number | null;
      longitude?: number | null;
      location_text?: string | null;
      conversation_history?: AskSarthiConversationTurn[];
      conversation_id?: string;
      image: File;
      was_voice_input?: boolean;
    },
    signal?: AbortSignal
  ) => {
    const form = new FormData();
    form.append("question", body.question);
    form.append("language", body.language);
    if (body.latitude != null) form.append("latitude", String(body.latitude));
    if (body.longitude != null) form.append("longitude", String(body.longitude));
    if (body.location_text) form.append("location_text", body.location_text);
    form.append("conversation_history", JSON.stringify(body.conversation_history ?? []));
    if (body.conversation_id) form.append("conversation_id", body.conversation_id);
    if (body.was_voice_input) form.append("was_voice_input", "true");
    form.append("image", body.image);
    return request<AskSarthiResponse>("/ask-sarthi/image", { method: "POST", token, formData: form, signal });
  },

  // The voice-to-voice assistant turn ("Mic 2") -- one or more recorded audio segments (see
  // lib/useAudioRecorder.ts, the same chunked-recording hook the complaint-creation voice flow
  // already uses) in, a real transcript + real spoken answer out. `image` is optional (a
  // combined voice+image turn), matching askSarthiWithImage()'s same file-attach shape.
  askSarthiVoice: (
    token: string,
    body: {
      language: string;
      latitude?: number | null;
      longitude?: number | null;
      location_text?: string | null;
      conversation_history?: AskSarthiConversationTurn[];
      conversation_id?: string;
      audioSegments: Blob[];
      image?: File | null;
    }
  ) => {
    const form = new FormData();
    form.append("language", body.language);
    if (body.latitude != null) form.append("latitude", String(body.latitude));
    if (body.longitude != null) form.append("longitude", String(body.longitude));
    if (body.location_text) form.append("location_text", body.location_text);
    form.append("conversation_history", JSON.stringify(body.conversation_history ?? []));
    if (body.conversation_id) form.append("conversation_id", body.conversation_id);
    body.audioSegments.forEach((segment, i) => form.append("audio", segment, `segment_${i}.webm`));
    if (body.image) form.append("image", body.image);
    return request<AskVoiceResponse>("/ask-sarthi/voice", { method: "POST", token, formData: form });
  },

  photoUrl: (filename: string) => `${API_URL}/uploads/${filename}`,
};
