import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import SourceCard from "../components/SourceCard";
import Mascot, { type MascotState } from "../components/Mascot";
import MicWaveform from "../components/MicWaveform";
import MultiPhotoUpload from "../components/MultiPhotoUpload";
import VoiceAssistantOverlay from "../components/VoiceAssistantOverlay";
import LocationPicker, { type LocationValue } from "../components/LocationPicker";
import { useUiLang } from "../lib/uiLang";
import { useAuth } from "../lib/auth";
import { t, toLangCode } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import { useSpeechToText } from "../lib/useSpeechToText";
import type { AskJanMitraResponse, AskJanMitraConversationTurn, PhotoEvidenceRef } from "../lib/ragTypes";

const SUGGESTED_KEYS = ["waterLeak", "pothole", "garbage", "streetlight"] as const;

/** One turn in the visible chat log. `history` sent to the backend (AskJanMitraConversationTurn[])
 * is always derived from this array (role + text only) rather than kept as a second, parallel
 * list -- one source of truth for what the citizen sees AND what gets resent as context. */
interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  text: string;
  /** Wall-clock epoch ms this message was created -- a user message's own send time, or an
   * assistant message's own arrival time. Shown under every bubble (see the render below) as a
   * local clock time (e.g. "9:00 AM"), the same always-visible timestamp any ordinary chat app
   * shows -- live-reported request: a HOVER-only tooltip wasn't discoverable/visible enough on
   * its own. Optional only so a conversation restored from BEFORE this field existed degrades
   * gracefully (no timestamp shown) instead of rendering a broken date -- every new message always
   * sets it. */
  timestamp?: number;
  /** User messages only -- an object URL for the attached photo, so the citizen sees exactly
   * what they sent. Revoked on unmount (see the cleanup effect below), not on every render.
   * Tied to THIS page load's memory -- never persisted (see loadChatHistory/saveChatHistory) --
   * `photoRef` below is what makes the same photo survive a reload. */
  imagePreview?: string;
  /** User messages only -- the REAL, server-saved reference for this turn's photo (backend
   * already wrote it to disk the moment it was uploaded -- see PhotoEvidenceRef's own docstring),
   * filled in once the paired response comes back (see runQuery's success branch). Unlike
   * `imagePreview`, this is a small serializable object, not a blob -- it DOES round-trip through
   * storage (see PersistedChatMessage), so the photo itself keeps showing (via `api.photoUrl`)
   * after a reload, instead of only ever existing for the tab that sent it. LIVE-REPORTED BUG this
   * closes: a citizen's own attached photo vanished (leaving a broken-image icon, then -- once
   * `imagePreview` was correctly excluded from storage -- nothing at all) the moment the page
   * reloaded, even though the file was safely on disk the whole time. */
  photoRef?: PhotoEvidenceRef;
  /** User messages only -- LIVE-REPORTED BUG: clicking a translated quick-reply button (see
   * `_localize_options`'s own docstring for why the button always SENDS its canonical English
   * value) displayed that same canonical English text in the citizen's own bubble too -- reading
   * as if they'd typed English mid-Hindi-conversation, even though they only ever saw and clicked
   * a Hindi label. Set (by `handleFollowUpOption`) whenever `text` shows a translated label that
   * differs from the canonical value actually sent -- `historyForRequest` echoes THIS value, not
   * `text`, as this turn's conversation-history content, so the canonical English keyword the
   * backend's confirm/cancel/category detection already recognizes still round-trips correctly on
   * a LATER turn, while the citizen only ever sees their own language on screen. Absent (falls
   * back to `text`) for an ordinary typed message, where the two are identical anyway. */
  historyContent?: string;
  /** Assistant messages only -- the full backend response, so sources/follow-up/complaint
   * outcome can render without a second shape to keep in sync with `text`. */
  response?: AskJanMitraResponse;
  /** Assistant messages only -- the user question this response is answering, captured at
   * creation time. Needed so a follow-up option clicked later (e.g. "Use current location")
   * can resend the SAME original question with added location info, exactly like the previous
   * single-turn UI did with its one top-level `asked` variable -- just now per-message instead
   * of global, since multiple turns are visible at once. */
  originalQuestion?: string;
  /** Assistant messages only -- wall-clock milliseconds from the moment this turn's request was
   * sent to the moment its response (success, error, or stop) came back. Shown as a hover tooltip
   * on the bubble (see the render below) -- live-reported request, useful for noticing a
   * particular turn was unusually slow (e.g. photo captioning) without needing to check the
   * backend logs. */
  durationMs?: number;
  /** Set when this turn's request failed -- `text` is the error message in that case, and
   * `retry` (bound to this exact question/options at send time) re-issues the same request. */
  isError?: boolean;
  /** Set when the citizen deliberately cancelled this turn via the Stop button (see runQuery's
   * AbortController) -- distinct from `isError`: this isn't a failure, so it gets its own neutral
   * (not red) styling, though `retry` still works the same way for it. */
  isStopped?: boolean;
  retry?: () => void;
}

/**
 * Ask Sarthi -- a continuous, ChatGPT-style conversation with Sarthi, not a single-question
 * form (see git history for the earlier form-shaped version deliberately replaced here). Same
 * real backend, same real Mic 1 (useSpeechToText.ts)/Mic 2 (VoiceAssistantOverlay.tsx)/image
 * attach (MultiPhotoUpload.tsx)/mascot (Mascot.tsx) as before -- this file only changes how the
 * conversation is *laid out*, not what powers it. `conversationHistory` is resent with every
 * request (the API is stateless server-side, see backend/schemas/ask_janmitra.py's
 * ConversationTurn docstring) so a follow-up ("street light not working" after already having
 * said "I'm in Mohali") doesn't make the citizen repeat their location -- now derived from the
 * full visible `messages` transcript instead of a separate parallel list.
 *
 * Split into two exports: `AskJanMitraContent` is the actual chat UI (message list + composer)
 * with no assumptions about what wraps it -- its root fills whatever height its parent flex
 * container provides, which is what lets the exact same component work both as the standalone
 * page below (a fixed-height flex column under TopBar) and inside AskJanMitraWidget.tsx's
 * slide-out panel (already its own flex column) without needing to know which one it's in.
 */

/** Crossfades between two mascot poses instead of hard-swapping the <img src>, which is what read
 * as "fake"/slideshow-like -- two static poses popping in and out with no transition at all. Both
 * poses are real, already-existing Mascot states (no invented pose); this only smooths *how* the
 * welcome screen moves between them. Scoped to this one welcome-screen usage rather than changing
 * Mascot.tsx itself, since every other consumer (message avatars, the widget FAB, VoiceAssistantOverlay)
 * only ever renders one state at a time with no complaint about the transition. */
function WelcomeMascot({ state, size }: { state: MascotState; size: number }) {
  const [current, setCurrent] = useState(state);
  const [outgoing, setOutgoing] = useState<MascotState | null>(null);
  const lastState = useRef(state);

  useEffect(() => {
    if (state === lastState.current) return;
    setOutgoing(lastState.current);
    setCurrent(state);
    lastState.current = state;
    const timeout = window.setTimeout(() => setOutgoing(null), 500);
    return () => window.clearTimeout(timeout);
  }, [state]);

  return (
    <div className="ask-chat-welcome-mascot" style={{ height: size, width: size }}>
      {outgoing && (
        <div key={`out-${outgoing}`} className="ask-chat-welcome-mascot-layer ask-chat-welcome-mascot-out">
          <Mascot state={outgoing} size={size} />
        </div>
      )}
      <div key={`in-${current}`} className="ask-chat-welcome-mascot-layer ask-chat-welcome-mascot-in">
        <Mascot state={current} size={size} />
      </div>
    </div>
  );
}

/** Chat persistence -- survives a page refresh/tab close, matching every other production chat
 * UI (ChatGPT, WhatsApp Web, etc.) instead of the previous "gone the moment you reload" state.
 * localStorage (not sessionStorage) so it also survives fully closing and reopening the browser,
 * and keyed per logged-in citizen (`user.id`) so a shared/kiosk browser -- a real scenario for a
 * civic-services app -- never bleeds one citizen's conversation into the next citizen who logs in
 * on it. Versioned key (`v1`) so a future change to ChatMessage's shape can't crash on old,
 * incompatible stored data -- corrupted/unexpected JSON is just treated as "no history", never
 * thrown.
 *
 * Two ChatMessage fields deliberately do NOT round-trip through storage (see PersistedChatMessage
 * below):
 *  - `imagePreview` is a `URL.createObjectURL()` blob URL tied to this page load's memory -- it's
 *    already invalid the instant the page reloads, so persisting it would just leave a broken
 *    <img> behind. LIVE-REPORTED FOLLOW-UP: excluding it correctly stopped the broken icon, but
 *    also meant a citizen's own attached photo simply vanished after a reload -- even though the
 *    real file was safely saved to disk the whole time (see PhotoEvidenceRef's own docstring).
 *    `photoRef` (below) is the fix: the REAL, server-backed reference to that same file, filled in
 *    once the response confirms it (see runQuery's success branch) -- it DOES round-trip through
 *    storage, so the render below can fall back to `api.photoUrl(photoRef.filename)` once the
 *    ephemeral blob is gone, and the photo keeps showing indefinitely, exactly like ChatGPT/
 *    Claude's own attached-image history.
 *  - `retry` is a live closure -- can't survive serialization. A restored error turn still shows
 *    its message, just without a working "Try again" button (the existing render already checks
 *    `msg.retry` before showing that button, so restoring it as `undefined` needs no extra guard).
 */
type PersistedChatMessage = Pick<ChatMessage, "id" | "role" | "text" | "response" | "originalQuestion" | "isError" | "isStopped" | "durationMs" | "timestamp" | "photoRef" | "historyContent">;

const CHAT_HISTORY_VERSION = "v1";
const CHAT_HISTORY_MAX_MESSAGES = 200;

function chatHistoryKey(userId: number) {
  return `janmitra.askChatHistory.${CHAT_HISTORY_VERSION}.${userId}`;
}

function loadChatHistory(userId: number | undefined): ChatMessage[] {
  if (userId == null) return [];
  try {
    const raw = localStorage.getItem(chatHistoryKey(userId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // LIVE-REPORTED BUG: `saveChatHistory` was fixed to stop WRITING `imagePreview` (a
    // `URL.createObjectURL()` blob tied to the page load that created it -- see that function's
    // own docstring), but a citizen's browser may already hold an OLDER entry, saved before that
    // fix existed, that still has one -- and JSON.parse doesn't know the field is stale, so it
    // comes back as a real-looking string that then fails to load, rendering as the browser's
    // broken-image icon. Strip it defensively on every load, not just going forward, so a photo
    // preview NEVER survives past the page load that created it, regardless of when it was saved.
    return (parsed as ChatMessage[]).map(({ imagePreview: _imagePreview, retry: _retry, ...rest }) => rest);
  } catch {
    return [];
  }
}

function saveChatHistory(userId: number | undefined, messages: ChatMessage[]) {
  if (userId == null) return;
  const persisted: PersistedChatMessage[] = messages
    .slice(-CHAT_HISTORY_MAX_MESSAGES)
    .map(({ id, role, text, response, originalQuestion, isError, isStopped, durationMs, timestamp, photoRef, historyContent }) => ({
      id, role, text, response, originalQuestion, isError, isStopped, durationMs, timestamp, photoRef, historyContent,
    }));
  try {
    localStorage.setItem(chatHistoryKey(userId), JSON.stringify(persisted));
  } catch {
    // Storage full/unavailable (private browsing, quota) -- the conversation still works for this
    // tab, it just won't survive a reload. Not worth surfacing as a user-facing error for that.
  }
}

/** "2.3s" under a minute, "1m 12s" at/above one minute -- photo captioning on this deployment's
 * CPU-only vision model can genuinely take several minutes (see runQuery's own comment), so a
 * bare seconds count alone would read strangely for those replies. */
function formatDuration(ms: number): string {
  const totalSeconds = ms / 1000;
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}m ${seconds}s`;
}

/** Local clock time, e.g. "9:00 AM" -- the browser's own locale/24-hour preference, not a fixed
 * format, same as how a phone's own clock would show it. */
function formatClockTime(ms: number): string {
  return new Date(ms).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** Imperative surface for the one action a parent legitimately needs to trigger from outside --
 * see the `hideNewChatBar` prop below for why. Deliberately just one method, not a general
 * escape hatch: everything else about this chat stays fully encapsulated. */
export interface AskJanMitraHandle {
  newChat: () => void;
}

interface AskJanMitraContentProps {
  /** True only for AskJanMitraWidget.tsx's slide-out panel. The panel already has its own close
   * (X) button sitting in its own top-right corner (AskJanMitraWidget.tsx's `.ask-widget-panel-
   * head`); this component's own internal "New chat" bar is ALSO right-aligned at the very top,
   * so inside the widget the two stacked into two separate corner toolbars instead of one clean
   * row -- a real, reported "doesn't look clean" issue. When true, this component renders no
   * internal bar at all and the caller is expected to place its own "New chat" control (wired to
   * the `newChat` ref handle below) in the SAME row as its close button instead. The standalone
   * /citizen/ask page has no close button to share a row with, so it's left false there and keeps
   * the simple self-contained bar. */
  hideNewChatBar?: boolean;
}

export const AskJanMitraContent = forwardRef<AskJanMitraHandle, AskJanMitraContentProps>(function AskJanMitraContent(
  { hideNewChatBar },
  ref
) {
  const { lang } = useUiLang();
  const { token, user } = useAuth();
  const [question, setQuestion] = useState("");
  // Lazily hydrated from localStorage on first render -- by the time this component can ever
  // mount, `user` is already resolved and non-null (TopBar only renders the widget once
  // `user.role === "citizen"`, and the standalone /citizen/ask route sits behind ProtectedRoute,
  // which itself waits on `loading` and redirects if `!user`), so this reads the right citizen's
  // history immediately rather than starting empty and "popping in" a moment later.
  const [messages, setMessages] = useState<ChatMessage[]>(() => loadChatHistory(user?.id));
  const [loading, setLoading] = useState(false);
  // Real, worker-backed wards -- the SAME list/component ReportIssue.tsx's "Report an Issue"
  // wizard already uses (see LocationPicker.tsx's own docstring), reused here rather than a
  // hand-rolled button list so a citizen actually gets a real dropdown of serviceable areas, not
  // a generic "type something" box. Fetched once; this list changes rarely (only when an admin
  // adds/removes a worker), same assumption ReportIssue.tsx already makes.
  const [wards, setWards] = useState<string[]>([]);
  const [locationPickerValue, setLocationPickerValue] = useState<LocationValue>({ ward: "", coords: null });
  const speech = useSpeechToText(lang);
  const [showSuccess, setShowSuccess] = useState(false);
  const [attachedImage, setAttachedImage] = useState<File[]>([]);
  const [showAttach, setShowAttach] = useState(false);
  const [voiceOverlayOpen, setVoiceOverlayOpen] = useState(false);
  // True while `question`'s current text came from Mic 1 rather than typing -- sent as
  // `was_voice_input` so the backend's LangSmith metadata can distinguish "TEXT"/"IMAGE" from
  // "STT"/"IMAGE_STT" (see ask_janmitra_service.py). Purely an observability signal; never
  // changes what gets asked or how it's routed.
  const [questionFromVoice, setQuestionFromVoice] = useState(false);

  // Starts from the highest id already present in restored history, not 0 -- otherwise the very
  // first new message after a reload would reuse an id already on screen (React key collision,
  // and the two messages' state would get tangled together).
  const nextIdRef = useRef<number | undefined>(undefined);
  if (nextIdRef.current === undefined) {
    nextIdRef.current = messages.reduce((max, m) => Math.max(max, m.id), 0);
  }
  const imagePreviewUrlsRef = useRef<string[]>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // The in-flight request's own cancellation handle, so the citizen can stop waiting on a slow
  // reply (photo captioning in particular can take several minutes on this deployment's CPU-only
  // vision model -- see MultiPhotoUpload's caller) instead of being stuck until it resolves on its
  // own, the same "Stop" affordance ChatGPT/Claude-style composers offer. Only ever one request in
  // flight at a time (the composer disables further submits while `loading`), so a single ref is
  // enough -- no need for a per-request map.
  const abortControllerRef = useRef<AbortController | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function nextId() {
    nextIdRef.current = (nextIdRef.current ?? 0) + 1;
    return nextIdRef.current;
  }

  // Persist on every change -- a real chat, not a draft, so there's no explicit "save" moment to
  // wait for; each new turn (or a New Chat reset) should already be safe by the time it renders.
  useEffect(() => {
    saveChatHistory(user?.id, messages);
  }, [messages, user?.id]);

  // "New chat" -- the explicit, discoverable way to start fresh now that history survives a
  // reload (without this, closing the composer's only exit from an old conversation is gone).
  // Revokes any still-live blob URLs from THIS session's own attachments the same way the unmount
  // cleanup effect below does; revoking one twice is a harmless no-op, so no bookkeeping needed to
  // avoid double-revoking on eventual unmount.
  function handleNewChat() {
    messages.forEach((m) => {
      if (m.imagePreview) URL.revokeObjectURL(m.imagePreview);
    });
    setMessages([]);
    setQuestion("");
    setAttachedImage([]);
    setShowAttach(false);
  }

  // The only thing AskJanMitraWidget.tsx needs to reach in from outside -- see
  // AskJanMitraHandle's docstring above.
  useImperativeHandle(ref, () => ({ newChat: handleNewChat }));

  // Live transcript -> the same editable composer text typed questions use, so by the time the
  // citizen hits Send it's an ordinary text request (see useSpeechToText.ts's docstring on why
  // this is the only way to route chat speech at all -- /ask-janmitra has no audio field).
  useEffect(() => {
    if (speech.status === "recording" && speech.transcript) {
      setQuestion(speech.transcript);
      setQuestionFromVoice(true);
    }
  }, [speech.transcript, speech.status]);

  // Brief "success" mascot flash on a genuinely new answer, not on every render.
  useEffect(() => {
    if (loading) return;
    const last = messages[messages.length - 1];
    if (!last || last.role !== "assistant" || last.isError || last.isStopped) return;
    setShowSuccess(true);
    const timer = setTimeout(() => setShowSuccess(false), 1200);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, loading]);

  // Auto-scroll to the latest turn (or the thinking indicator) -- a real conversation, so the
  // newest message is always what's in view, same as any chat app.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, loading]);

  // Composer grows with content up to a cap, then scrolls internally -- never pushes the send
  // row off-screen on a long paste.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [question]);

  useEffect(() => {
    const urls = imagePreviewUrlsRef.current;
    return () => {
      urls.forEach((url) => URL.revokeObjectURL(url));
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    api.listWards(token).then(setWards).catch(() => setWards([]));
  }, [token]);

  // One mascot, real state in -> real expression out. No invented "error" expression: an error
  // already has its own text bubble, so the mascot just stays idle rather than performing an
  // emotion this 5-state set doesn't have.
  const mascotState: MascotState =
    speech.status === "recording" ? "listening" : loading ? "thinking" : showSuccess ? "success" : "idle";

  // Ambient wave<->namaste loop, but ONLY for the pre-conversation welcome screen and ONLY while
  // nothing real is actually happening (mascotState is genuinely "idle") -- ask.widget.greeting's
  // bubble greeting stays a true one-shot per its own docstring; this is a separate, explicitly
  // requested exception scoped to the empty state, done by re-applying the .mascot-greeting class
  // on an interval rather than changing the underlying one-shot wave animation itself.
  const [welcomeWave, setWelcomeWave] = useState(false);
  useEffect(() => {
    if (messages.length > 0 || mascotState !== "idle") return;
    const interval = setInterval(() => setWelcomeWave((w) => !w), 2600);
    return () => clearInterval(interval);
  }, [messages.length, mascotState]);
  const welcomeMascotState: MascotState = mascotState === "idle" && welcomeWave ? "greeting" : mascotState;

  async function runQuery(
    q: string,
    opts: { locationText?: string; lat?: number; lng?: number; displayText?: string; historyText?: string } = {}
  ) {
    if (!token) return;
    const trimmed = q.trim();
    const imageToSend = attachedImage[0];
    if (!trimmed && !imageToSend) return;

    // Live-reported bug: submitting a message while Mic 1 was still actively listening (e.g. the
    // citizen dictated a question, then pressed Enter/Send instead of tapping the mic again to
    // stop it first) left the browser's SpeechRecognition session running in the background for
    // the rest of the turn -- visibly, via Chrome's own tab-level "mic in use" recording
    // indicator, well past the point the citizen had already sent their message and moved on.
    // Every submission path (typed+Enter, typed+Send click, a quick-reply chip, the location
    // picker) funnels through this one shared function, so stopping it here -- once, the moment a
    // message actually goes out -- covers all of them, matching the same "sending your message
    // ends dictation" expectation ChatGPT-style voice-to-text composers already set.
    if (speech.status === "recording") speech.stop();

    // Everything already on screen, in order -- exactly what the backend should treat as prior
    // context for this new turn (see the class docstring on why this replaces a separate list).
    // LIVE-REPORTED REQUEST: `photo_evidence` (assistant turns only) is echoed straight back from
    // `m.response` -- see backend/services/orchestration/nodes.py's
    // `_recover_photo_evidence_from_history` for why: a photo attached on an earlier turn needs
    // this reference to still reach a complaint confirmed several messages later.
    //
    // LIVE-REPORTED BUG: `complaint_workflow_state` (assistant turns only) was never echoed here
    // at all -- this field has existed on the backend from the start specifically so a FILED/
    // CANCELLED complaint's own turn (and a pending confirmation prompt) can be recognized as
    // DATA, not by re-parsing `answer`'s own (possibly translated) text (see ragTypes.ts's own
    // docstring). Without it, every one of those checks silently fell back to matching fixed
    // ENGLISH substrings that a Hindi/Marathi/Odia/Gujarati/Bengali conversation never produces --
    // a complaint filed in one city, then a brand-new complaint described in a DIFFERENT city
    // right after (same chat, no "New chat" click), silently reused the FIRST one's already-closed
    // ward/category instead of resolving fresh.
    const historyForRequest: AskJanMitraConversationTurn[] = messages.map((m) => ({
      role: m.role,
      content: m.historyContent ?? m.text,
      photo_evidence: m.role === "assistant" ? m.response?.photo_evidence ?? undefined : undefined,
      complaint_workflow_state: m.role === "assistant" ? m.response?.complaint_workflow_state ?? undefined : undefined,
    }));

    let imagePreview: string | undefined;
    if (imageToSend) {
      imagePreview = URL.createObjectURL(imageToSend);
      imagePreviewUrlsRef.current.push(imagePreview);
    }
    // `opts.displayText`, when given, is what the citizen actually just DID (e.g. "Ward 22 —
    // Kothrud, Pune" from the location picker, or a picked city name for an ambiguous-location
    // reply) -- shown in the chat bubble AND sent as this turn's conversation-history content,
    // instead of silently resending `trimmed` (the ORIGINAL complaint question, needed as the
    // real `question` field for the backend) as if the citizen had typed it again. Real, reported
    // bug this closes: "पानी के रिसाव की शिकायत कैसे करें?" appeared twice in a row after picking
    // a ward from the dropdown, reading as if the citizen had retyped their own question, when
    // they'd actually just answered "where". `historyForRequest` above is derived from `messages`
    // (the visible transcript, see this component's own docstring on why there's no second,
    // parallel history list) -- showing the real answer here also makes conversation_history read
    // (and resolve, e.g. via nodes.py's own conversation-history location fallback) correctly on
    // any LATER turn, rather than re-showing the original question a second time in history too.
    // Captured (not inlined) so the success branch below can find this EXACT user turn again once
    // the response confirms the photo was saved, and attach its durable `photoRef` -- see
    // ChatMessage.photoRef's own docstring.
    const userMessageId = nextId();
    setMessages((prev) => [
      ...prev,
      {
        id: userMessageId, role: "user", text: opts.displayText ?? trimmed,
        historyContent: opts.historyText, imagePreview, timestamp: Date.now(),
      },
    ]);
    setQuestion("");
    setLocationPickerValue({ ward: "", coords: null });
    // LIVE-REPORTED BUG: this used to only clear on a SUCCESSFUL response (see the try block
    // below), on purpose -- so a failed request left the photo attached for an easy retry. But
    // photo captioning can take several minutes (see backend/services/vision_service.py's own
    // moondream2 model -- CPU-only inference, no fast path), so the attached-photo thumbnail sat
    // in the composer, still looking "not yet sent", for that whole wait -- confusing, since the
    // photo genuinely was already sent and is sitting in the chat transcript above. Same fix as
    // Mic 1's own "sending ends dictation" behavior: clear the moment the message actually goes
    // out, not only once a response comes back. The retry-convenience this trades away is real
    // but minor (re-attaching one photo after a failure) next to several minutes of a misleading
    // "still attached" thumbnail on the (much more common) success path.
    setAttachedImage([]);
    setShowAttach(false);
    setLoading(true);
    const controller = new AbortController();
    abortControllerRef.current = controller;
    // LIVE-REPORTED REQUEST: wall-clock time for THIS turn specifically -- shown as a hover
    // tooltip on the reply (see the render below), separate from the "Thinking..." indicator,
    // which only shows a request is in flight, not how long it actually took once it's done.
    const sentAt = performance.now();

    try {
      const result = imageToSend
        ? await api.askJanMitraWithImage(
            token,
            {
              question: trimmed,
              language: lang,
              latitude: opts.lat,
              longitude: opts.lng,
              location_text: opts.locationText,
              conversation_history: historyForRequest,
              image: imageToSend,
              was_voice_input: questionFromVoice,
            },
            controller.signal
          )
        : await api.askJanMitra(
            token,
            {
              question: trimmed,
              language: lang,
              latitude: opts.lat,
              longitude: opts.lng,
              location_text: opts.locationText,
              conversation_history: historyForRequest,
              was_voice_input: questionFromVoice,
            },
            controller.signal
          );
      setMessages((prev) => [
        // Backfill THIS turn's own user message with the real, durable `photoRef` the response
        // just confirmed was saved -- `imagePreview` (the blob) still renders it for the rest of
        // this tab's life, but only `photoRef` survives a reload (see its own docstring).
        ...(imageToSend && result.photo_evidence
          ? prev.map((m) => (m.id === userMessageId ? { ...m, photoRef: result.photo_evidence ?? undefined } : m))
          : prev),
        {
          id: nextId(), role: "assistant", text: result.answer, response: result, originalQuestion: trimmed,
          durationMs: performance.now() - sentAt, timestamp: Date.now(),
        },
      ]);
    } catch (err) {
      // LIVE-REPORTED BUG: a deliberate Stop click used to just vanish the "Thinking..." indicator
      // with nothing in its place -- correct in that it isn't an error, but looked like the
      // message had gone nowhere. A neutral (not red) "Stopped" note instead, same
      // ChatGPT/Claude-style interrupted-response acknowledgment, with the same retry affordance
      // an error gets (re-asking the identical question is exactly what a citizen who stopped a
      // slow reply would want next).
      if (err instanceof DOMException && err.name === "AbortError") {
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "assistant", text: t(lang, "ask.stopped"), isStopped: true, retry: () => runQuery(trimmed, opts), timestamp: Date.now() },
        ]);
        return;
      }
      const message = err instanceof ApiError ? err.message : t(lang, "ask.error");
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: "assistant", text: message, isError: true, retry: () => runQuery(trimmed, opts), timestamp: Date.now() },
      ]);
    } finally {
      abortControllerRef.current = null;
      setLoading(false);
      setQuestionFromVoice(false);
    }
  }

  function handleSubmit(e?: FormEvent) {
    e?.preventDefault();
    // Ground this in real conversation state, not a guess at the typed text's shape: if the
    // AI's last message was SPECIFICALLY asking for a location (either the plain "what is the
    // location?" shape -- follow_up_options includes "Use current location" -- or the ambiguous
    // "which city, X or Y?" shape -- location.is_ambiguous), treat this reply as the answer to
    // THAT question rather than a brand-new one. Real, reported problem this closes: a citizen
    // typing a real city name (e.g. "Kolhapur") directly into the composer, instead of using the
    // "Select location" button, got the identical location question back forever -- their answer
    // was sent as a new `question` with no `location_text`, so it was never even considered a
    // location. Every OTHER follow-up shape (category, status, unclear, image-no-text) has a
    // distinct options shape that does NOT match this check, so this can't misfire on those --
    // e.g. it won't treat a reply to "what issue would you like to report?" as a location.
    const lastMsg = messages[messages.length - 1];
    const lastAskedForLocation =
      lastMsg?.role === "assistant" &&
      lastMsg.response?.follow_up_required &&
      (lastMsg.response.follow_up_options.includes("Use current location") || lastMsg.response.location?.is_ambiguous);
    if (lastAskedForLocation && lastMsg.originalQuestion && question.trim() && attachedImage.length === 0) {
      // Same displayText fix as handleLocationPickerSubmit below -- what's typed here (e.g.
      // "Kolhapur") IS already the real answer, so show it, not the original question again.
      runQuery(lastMsg.originalQuestion, { locationText: question.trim(), displayText: question.trim() });
      return;
    }
    runQuery(question);
  }

  // ChatGPT/Claude-style cancellation for a slow in-flight reply (photo captioning especially --
  // see runQuery's own comment on the CPU-only vision model). Aborting the fetch itself is enough:
  // runQuery's catch block recognizes the resulting AbortError and skips showing an error bubble,
  // and its `finally` already resets `loading`/the controller ref -- this just triggers that.
  function stopGeneration() {
    abortControllerRef.current?.abort();
  }

  function handleComposerKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!loading && (question.trim() || attachedImage.length > 0)) handleSubmit();
    }
  }

  // Only ever reached for `location_ambiguous`'s real city-name options now (e.g. "Patiala" vs
  // "Sahibzada Ajit Singh Nagar (Mohali)", see clarification_flow_node) -- the plain "which
  // location?" case (`_LOCATION_CLARIFICATION_OPTIONS`) is handled entirely by the real
  // `LocationPicker` below instead of this generic button-label resend, so a citizen actually
  // gets a real dropdown of serviceable wards (see `handleLocationPickerSubmit`), not a button
  // whose own label text ("Select location") used to get sent as if it were a place name.
  function handleFollowUpOption(msg: ChatMessage, option: string) {
    if (!msg.originalQuestion) return;
    // This one handler renders the buttons for THREE different clarification shapes (see the
    // JSX above -- "location_ambiguous's real city-name candidates... OR the category options"),
    // but only the ambiguous-LOCATION case ("Which city -- Mumbai, Nagpur?") is actually a
    // location answer. Live-reported bug this fixes: clicking a CATEGORY option (e.g. "Garbage",
    // from _CATEGORY_CLARIFICATION_OPTIONS) unconditionally resent the ORIGINAL question with the
    // clicked label as `locationText` -- which the backend correctly can't resolve as a location,
    // so it just re-asked the identical "What issue would you like to report?" question again, no
    // matter which category button was clicked. Same problem for the intent-ambiguous
    // clarification's "Report a problem"/"What is the procedure?" options. Only
    // `location.is_ambiguous` genuinely means "the clicked option IS a place name" -- every other
    // follow_up_options case means "the clicked option IS the citizen's actual reply text" (a
    // category name, or a report-vs-info choice), which must be sent as the real `question`, not
    // resent alongside the stale original one.
    if (msg.response?.location?.is_ambiguous) {
      // Real city names, never translated (see `_localize_options`'s own docstring on why the
      // dynamic ambiguous-location candidates are deliberately left as-is) -- display and
      // history content are already identical here, nothing to split.
      runQuery(msg.originalQuestion, { locationText: option, displayText: option });
      return;
    }
    // LIVE-REPORTED BUG: shows the label the citizen actually saw and clicked (their own language)
    // in their own chat bubble, while still sending/recording the canonical English `option` as
    // both this request's `question` and this turn's conversation-history content -- see
    // ChatMessage.historyContent's own docstring for why the two must differ safely rather than
    // showing raw English mid-conversation.
    const optionIndex = msg.response?.follow_up_options.indexOf(option) ?? -1;
    const label = optionIndex >= 0 ? msg.response?.follow_up_options_labels?.[optionIndex] : undefined;
    runQuery(option, { displayText: label ?? option, historyText: option });
  }

  function handleLocationPickerSubmit(msg: ChatMessage) {
    if (!msg.originalQuestion || !locationPickerValue.ward.trim()) return;
    const ward = locationPickerValue.ward.trim();
    runQuery(msg.originalQuestion, { locationText: ward, displayText: ward });
  }

  function askSuggested(key: (typeof SUGGESTED_KEYS)[number]) {
    runQuery(t(lang, `ask.suggested.${key}`));
  }

  const lastMessageId = messages.length > 0 ? messages[messages.length - 1].id : null;

  return (
    <div className="ask-chat-shell">
      {!hideNewChatBar && messages.length > 0 && (
        <div className="ask-chat-topbar">
          <button type="button" className="ask-chat-newchat-btn" onClick={handleNewChat}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            {t(lang, "ask.newChat")}
          </button>
        </div>
      )}
      <div className="ask-chat-messages">
        {messages.length === 0 && (
          <div className="ask-chat-empty">
            <WelcomeMascot state={welcomeMascotState} size={130} />
            <h1 className="ask-chat-empty-title">{t(lang, "ask.title")}</h1>
            <p className="ask-chat-empty-sub">{t(lang, "ask.subtitle")}</p>
            <div className="ask-suggestions ask-suggestions-center">
              {SUGGESTED_KEYS.map((key) => (
                <button key={key} type="button" className="ask-suggestion" onClick={() => askSuggested(key)}>
                  {t(lang, `ask.suggested.${key}`)}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id} className={`ask-chat-row ask-chat-row-${msg.role}`}>
            {msg.role === "assistant" && (
              <div className="ask-chat-avatar">
                <Mascot state="idle" size={72} />
              </div>
            )}

            <div
              className={`ask-chat-bubble${msg.isError ? " ask-chat-bubble-error" : ""}${msg.isStopped ? " ask-chat-bubble-stopped" : ""}`}
            >
              {/* `imagePreview` (this tab's own blob) first -- instant, no round-trip needed;
                  `photoRef` (the real saved file) once that's all a reload has left -- see
                  ChatMessage.photoRef's own docstring for why both exist. */}
              {(msg.imagePreview || msg.photoRef) && (
                <img
                  src={msg.imagePreview || api.photoUrl(msg.photoRef!.filename)}
                  alt={t(lang, "photo.previewAlt")}
                  className="ask-chat-image"
                />
              )}
              <p className="ask-chat-text">{msg.text}</p>
              {/* LIVE-REPORTED REQUEST: an always-visible clock time under every bubble -- when
                  this was SENT (user turn) or RECEIVED (assistant turn) -- not just a hover-only
                  tooltip, which wasn't discoverable enough on its own. Assistant turns also show
                  how long that specific reply took, since a slow one (photo captioning especially)
                  is exactly what's useful to notice at a glance. */}
              {msg.timestamp != null && (
                <div className="ask-chat-timestamp">
                  {formatClockTime(msg.timestamp)}
                  {msg.role === "assistant" && msg.durationMs != null && ` · ${formatDuration(msg.durationMs)}`}
                </div>
              )}

              {(msg.isError || msg.isStopped) && msg.retry && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={msg.retry} style={{ marginTop: 8 }}>
                  {t(lang, "ask.voiceAssistant.tryAgain")}
                </button>
              )}

              {msg.response && (
                <>
                  {msg.response.complaint_id != null && (
                    <div className="ask-chat-complaint-note">
                      <span className="ai-dot active" />
                      {t(toLangCode(msg.response.language), "citizen.submitSuccess")}
                      <Link to="/citizen/complaints" className="ask-chat-complaint-link">
                        {t(toLangCode(msg.response.language), "ask.action.track")}
                      </Link>
                    </div>
                  )}

                  {msg.response.follow_up_required && msg.id === lastMessageId && !loading && (
                    <div className="ask-followup">
                      {/* LIVE-REPORTED BUG: this used to be gated on `follow_up_question` alone,
                          which unclear_flow_node/status_flow_node's "which complaint?" question
                          both set to the full answer text with NO real options to introduce (see
                          the UNCLEAR branch below, which already deliberately renders no buttons
                          for this exact reason) -- "Please clarify:" showed above nothing at all,
                          e.g. after a plain "Solve 25 * 4" got the honest "I'm not sure I
                          understood that" reply. Gated on `follow_up_options.length > 0` instead,
                          matching the actual condition that decides whether anything renders
                          below this label. */}
                      {msg.response.follow_up_options.length > 0 && (
                        <div className="ask-sources-label">{t(toLangCode(msg.response.language), "ask.followUp.label")}</div>
                      )}
                      {msg.response.follow_up_options.includes("Use current location") && !msg.response.location?.is_ambiguous ? (
                        // The plain "what is the location?" case (`_LOCATION_CLARIFICATION_
                        // OPTIONS`, see nodes.py) -- a real `LocationPicker` (GPS, or a dropdown
                        // of the actual currently-staffed wards), the SAME component ReportIssue.
                        // tsx's "Report an Issue" wizard already uses, instead of a hand-rolled
                        // button list. Closes a real, reported gap: the raw backend option labels
                        // ("Enter location"/"Select location") both used to open the identical
                        // free-text box -- two buttons that looked like a choice but weren't one
                        // -- when "Select location" was always meant to be a real dropdown (see
                        // LocationPicker.tsx's own "Choose your ward or area from a list" hint,
                        // which only this component actually delivers on).
                        <div style={{ marginTop: 8 }}>
                          <LocationPicker value={locationPickerValue} onChange={setLocationPickerValue} wards={wards} />
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            style={{ marginTop: 10 }}
                            disabled={!locationPickerValue.ward.trim()}
                            onClick={() => handleLocationPickerSubmit(msg)}
                          >
                            {t(toLangCode(msg.response.language), "ask.submit")}
                          </button>
                        </div>
                      ) : msg.response.follow_up_options.length > 0 ? (
                        // location_ambiguous's real city-name candidates (e.g. "Patiala" vs
                        // "Sahibzada Ajit Singh Nagar (Mohali)") or the category options -- a
                        // short, fixed, already-meaningful list, where a plain button per option
                        // is the correct UI (not a full ward-picker).
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                          {/* `labels`, not `msg.response.follow_up_options_labels` inline below --
                              TS narrowing of `msg.response` (from the `{msg.response && (...)}`
                              wrapper above) doesn't survive into this .map() closure. */}
                          {(() => {
                            const labels = msg.response.follow_up_options_labels;
                            return msg.response.follow_up_options.map((opt, i) => (
                              // Displays the translated label but ALWAYS clicks through with the
                              // canonical `opt` -- clicking must keep sending exactly the English
                              // text the backend's confirm/cancel/category detection already
                              // recognizes, never the (possibly slightly different) translator
                              // output, or a citizen's clear "yes" could silently fail to register.
                              <button key={opt} type="button" className="btn btn-ghost btn-sm" onClick={() => handleFollowUpOption(msg, opt)}>
                                {labels?.[i] ?? opt}
                              </button>
                            ));
                          })()}
                        </div>
                      ) : msg.response.intent === "TYPE_C_STATUS" ? (
                        // status_flow_node deliberately leaves follow_up_options empty here --
                        // there's no fixed list of complaint numbers to offer, it wants a free-
                        // typed one (the citizen can just type it in the composer as normal).
                        // This used to fall through to the location-picker branch below, which
                        // made a "what's your complaint number?" question show "Use current
                        // location"/GPS buttons -- a real, reported bug. A link to the complaints
                        // list matches the answer text's own "...or check your complaints list."
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                          <Link to="/citizen/complaints" className="btn btn-ghost btn-sm">
                            {t(toLangCode(msg.response.language), "ask.action.track")}
                          </Link>
                        </div>
                      ) : msg.response.intent === "UNCLEAR" ? (
                        // unclear_flow_node ALSO leaves follow_up_options empty (see nodes.py) --
                        // the same bug the TYPE_C_STATUS branch above fixes, just a second real
                        // occurrence caught later (a plain "hello" reproduced it live: the answer
                        // correctly said "I didn't understand", but the UI still offered a "Use
                        // current location" button underneath it, which makes no sense for a
                        // greeting). There's no single right action to offer here -- the answer
                        // text itself already says what to do ("What would you like help with?"),
                        // so the citizen just types their real question in the composer. No
                        // buttons is the honest UI for that, not a location picker.
                        null
                      ) : (
                        // Defensive fallback only, for a genuinely unknown future case -- NOT a
                        // location picker (see above: no real backend path with empty options has
                        // ever actually meant "needs a location" -- every real location
                        // clarification already populates follow_up_options via
                        // clarification_flow_node's four branches). Rendering nothing is the safe
                        // default; a specific new empty-options case should get its own branch
                        // above, the same way TYPE_C_STATUS/UNCLEAR did, not a guessed-at button.
                        null
                      )}
                    </div>
                  )}

                  {msg.response.sources.length > 0 && (() => {
                    // Captured once here, not inline in the .map() below -- TS narrowing of
                    // `msg.response` (from the `{msg.response && (...)}` wrapper above) doesn't
                    // survive into that nested closure.
                    const responseLang = toLangCode(msg.response.language);
                    return (
                      <>
                        <div className="ask-sources-label">{t(responseLang, "ask.sourcesLabel")}</div>
                        {msg.response.sources.map((source) => (
                          <SourceCard key={source.source_id} source={source} lang={responseLang} />
                        ))}
                      </>
                    );
                  })()}

                  {!msg.response.follow_up_required && (
                    <div className="ask-quick-actions">
                      <Link to="/citizen/report" className="btn btn-ghost btn-sm">
                        {t(toLangCode(msg.response.language), "ask.action.report")}
                      </Link>
                      <Link to="/citizen/complaints" className="btn btn-ghost btn-sm">
                        {t(toLangCode(msg.response.language), "ask.action.track")}
                      </Link>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="ask-chat-row ask-chat-row-assistant">
            <div className="ask-chat-avatar">
              <Mascot state="thinking" size={72} />
            </div>
            <div className="ask-chat-bubble ask-chat-thinking" aria-live="polite">
              <span className="ask-chat-thinking-dots" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              {t(lang, "ask.loading")}
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="ask-chat-composer">
        {(showAttach || attachedImage.length > 0) && (
          <div className="ask-chat-attach-panel">
            <MultiPhotoUpload photos={attachedImage} onChange={setAttachedImage} maxFiles={1} placeholderKey="ask.image.addLabel" />
          </div>
        )}

        {speech.supported && speech.error && (
          <p className="ask-chat-composer-error">{t(lang, speech.error)}</p>
        )}

        <div className="ask-chat-composer-row">
          <button
            type="button"
            className={`ask-chat-icon-btn${showAttach || attachedImage.length > 0 ? " active" : ""}`}
            onClick={() => setShowAttach((s) => !s)}
            disabled={loading}
            aria-label={t(lang, "ask.image.addLabel")}
            title={t(lang, "ask.image.addLabel")}
            aria-pressed={showAttach || attachedImage.length > 0}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>

          <textarea
            ref={textareaRef}
            value={question}
            onChange={(e) => {
              setQuestion(e.target.value);
              // A manual edit after Mic 1 filled the box means the citizen is typing now --
              // the request should honestly report "TEXT", not "STT".
              setQuestionFromVoice(false);
            }}
            onKeyDown={handleComposerKeyDown}
            placeholder={t(lang, "ask.inputPlaceholder")}
            aria-label={t(lang, "ask.inputPlaceholder")}
            className="ask-chat-textarea"
            rows={1}
            disabled={loading}
          />

          {/* No button at all when the browser doesn't expose SpeechRecognition (e.g. Firefox) --
              true graceful absence, not a disabled ghost control. */}
          {speech.supported && (
            <button
              type="button"
              className={`ask-chat-icon-btn ask-chat-mic1-btn${speech.status === "recording" ? " active" : ""}`}
              onClick={() => (speech.status === "recording" ? speech.stop() : speech.start())}
              disabled={loading}
              aria-label={t(lang, speech.status === "recording" ? "ask.voice.stop" : "ask.voice.micLabel")}
              aria-pressed={speech.status === "recording"}
              title={t(lang, speech.status === "recording" ? "ask.voice.stop" : "ask.voice.micLabel")}
            >
              {speech.status === "recording" ? (
                <MicWaveform />
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <rect x="9" y="3" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              )}
            </button>
          )}

          {/* "Mic 2" -- a genuinely separate control from the mic above (Mic 1, which just fills
              the composer for manual editing/sending). This one opens a dedicated
              spoken-conversation overlay instead -- see VoiceAssistantOverlay.tsx's docstring
              for why the two are deliberately not the same button/hook. A waveform, not
              headphones -- headphones read as "listen to audio," not "start a live back-and-forth
              voice conversation," and sat too close to Mic 1's own capsule-mic glyph to read as a
              clearly different action at a glance. */}
          <button
            type="button"
            className="ask-chat-icon-btn"
            onClick={() => setVoiceOverlayOpen(true)}
            disabled={loading}
            aria-label={t(lang, "ask.voiceAssistant.openLabel")}
            title={t(lang, "ask.voiceAssistant.openLabel")}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M4 10v4M8 6v12M12 3v18M16 6v12M20 10v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
          </button>

          {/* LIVE-REPORTED REQUEST: a reply can take several minutes (photo captioning on this
              deployment's CPU-only vision model, see runQuery's own comment) -- while `loading`,
              this becomes a real, clickable Stop button (ChatGPT/Claude-style) instead of a
              disabled spinner the citizen can only wait out. `type="button"` (not "submit") so
              clicking it can never re-trigger a form submit. */}
          {loading ? (
            <button
              type="button"
              className="ask-chat-send-btn stop"
              onClick={stopGeneration}
              aria-label={t(lang, "ask.stop")}
              title={t(lang, "ask.stop")}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="5" y="5" width="14" height="14" rx="2" />
              </svg>
            </button>
          ) : (
            <button
              type="submit"
              className="ask-chat-send-btn"
              disabled={!question.trim() && attachedImage.length === 0}
              aria-label={t(lang, "ask.submit")}
              title={t(lang, "ask.submit")}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                <path d="M4 12h15M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </div>
      </form>

      {/* Live-reported bug: the overlay used to keep its own, completely separate conversation
          history -- a voice exchange never appeared in this transcript, and this transcript's
          own context was invisible to the overlay. `initialHistory` seeds the overlay with
          exactly what `runQuery` itself would send as this turn's own history (same derivation,
          same source of truth); `onTurnComplete` appends the overlay's own turn back into
          `messages` the moment it finishes, so closing the overlay shows it here too, sources/
          follow-up options included, exactly like a typed turn would. */}
      {voiceOverlayOpen && (
        <VoiceAssistantOverlay
          onClose={() => setVoiceOverlayOpen(false)}
          initialHistory={messages.map((m) => ({ role: m.role, content: m.text }))}
          onTurnComplete={(question, response) => {
            // Both stamped with "now" -- the overlay only reports the finished turn, not the
            // citizen's own original send time inside it, so this is the closest approximation
            // available (the two turns did happen within the same round-trip either way).
            const now = Date.now();
            setMessages((prev) => [
              ...prev,
              { id: nextId(), role: "user", text: question, timestamp: now },
              { id: nextId(), role: "assistant", text: response.answer, response, timestamp: now },
            ]);
          }}
        />
      )}
    </div>
  );
});

/** The standalone full-page route (/citizen/ask) -- TopBar + the same chat content the floating
 * widget renders, so a direct link/bookmark/browser-back still lands somewhere real. Gives
 * AskJanMitraContent a fixed, viewport-relative height to fill (100dvh minus TopBar) so its
 * composer stays anchored near the bottom instead of just trailing off at the end of a long page. */
export default function AskJanMitra() {
  return (
    <div className="ask-page-viewport">
      <TopBar />
      <div className="page ask-page">
        <AskJanMitraContent />
      </div>
    </div>
  );
}
