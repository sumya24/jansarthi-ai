import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useUiLang } from "../lib/uiLang";
import { formatClockTime, formatDuration, t } from "../lib/i18n";
import { useLiveVoiceSession, type LiveVoicePhase } from "../lib/useLiveVoiceSession";
import type { AskSarthiConversationTurn } from "../lib/ragTypes";
import "./VoiceAssistantOverlay.css";
import "./LiveVoiceOverlay.css";

// "connecting" has no dedicated treatment of its own -- borrows "idle"'s quiet/slow pace (nothing
// to react to yet either way).
const ORB_CLASS_FOR_PHASE: Record<LiveVoicePhase, string> = {
  idle: "live-voice-orb-idle",
  connecting: "live-voice-orb-idle",
  listening: "live-voice-orb-listening",
  processing: "live-voice-orb-processing",
  speaking: "live-voice-orb-speaking",
  error: "live-voice-orb-error",
};

/**
 * "Live" voice mode -- the always-listening, low-latency sibling of VoiceAssistantOverlay.tsx
 * ("Classic"), picked via the VoiceModeProvider toggle (see AskSarthi.tsx). No tap-to-record: the
 * mic streams continuously to `/ask-sarthi/voice/live` the moment this overlay opens, and Sarvam's
 * own server-side VAD (see useLiveVoiceSession.ts) decides where each utterance ends.
 *
 * LIVE-REPORTED FEEDBACK: explicitly modeled on ChatGPT's own voice-mode layout (the user's own
 * reference) -- a full-viewport takeover (not a small inline panel), conversation history scrolled
 * above, one large centered orb as the sole "what is Sarthi doing" focal point, and a slim bottom
 * bar carrying just the phase label and a close control. Unlike Classic's modal, the history stays
 * genuinely visible/scrollable the whole time (the actual point of "Live" mode) rather than being
 * hidden behind the orb.
 *
 * SECOND ROUND OF LIVE-REPORTED FEEDBACK: the orb first reused Classic mode's `.voice-orb` glow
 * (a blurred, mostly-transparent radial gradient designed as a subtle accent BEHIND a mascot
 * character) stretched to 220px as this page's only focal element -- it read as an out-of-focus
 * smudge, not a real orb, and sat too low with too much dead space around it. `.live-voice-orb`
 * (LiveVoiceOverlay.css) is a purpose-built solid gradient sphere instead (crisp glossy fill +
 * an outer glow that's separate from the fill, not the whole shape), and `.live-voice-center`
 * uses `flex: 1` so it always centers within whatever space history/the bottom bar leave, instead
 * of getting pushed toward one edge.
 *
 * THIRD ROUND OF LIVE-REPORTED FEEDBACK: the page's own always-present floating "Ask Sarthi"
 * widget FAB (AskSarthiWidget.css, z-index 96 -- deliberately below every real modal in this
 * app's z-index scale) was visibly showing through the bottom corner despite this overlay's own
 * z-index 150. Root cause: this component rendered as a plain child in the normal React tree, so
 * its `position: fixed` was scoped to whatever stacking context its ANCESTORS happen to establish
 * (a well-known CSS trap -- a `transform`/`filter`/`opacity<1`/etc. on any ancestor confines a
 * fixed-position descendant's stacking to that ancestor's local context instead of the true
 * viewport), rather than actually competing at the top-level stacking context the widget FAB
 * lives in. Fixed by rendering via `createPortal(..., document.body)`, the standard way to
 * guarantee a full-screen takeover actually sits above everything else on the page, matching how
 * a real modal should be implemented (VoiceAssistantOverlay.tsx's own Classic modal has the same
 * theoretical gap -- not fixed here, since it wasn't reported live and this feature's own scope
 * is Live mode only).
 *
 * Also added a real header (this app's own name/mark, matching the reference's own "ChatGPT
 * Voice" label) and turned the bottom bar into a floating rounded pill (margin + shadow) instead
 * of a full-width bordered strip -- both closer to the reference's actual composition.
 *
 * Reports each completed turn back to the parent via `onTurnComplete` as it happens (not just
 * once on close) -- same "one shared conversation, not two disconnected ones" contract
 * VoiceAssistantOverlay.tsx's own `initialHistory`/`onTurnComplete` established, just applied
 * incrementally here since a live session can run many turns in one sitting.
 */
export default function LiveVoiceOverlay({
  onClose,
  initialHistory,
  onTurnComplete,
}: {
  onClose: () => void;
  initialHistory: AskSarthiConversationTurn[];
  onTurnComplete: (question: string, answer: string, durationMs?: number) => void;
}) {
  const { lang } = useUiLang();
  const session = useLiveVoiceSession(lang);
  const reportedCountRef = useRef(0);
  const startedRef = useRef(false);
  const historyEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    session.start();
    return () => session.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Reports each completed (user, assistant) pair up to the parent exactly once, as soon as it
  // appears in session.messages -- not on close, so the main chat's own transcript stays in sync
  // live, the same guarantee Classic mode's onTurnComplete already gives at end-of-turn.
  useEffect(() => {
    const pairsReady = Math.floor(session.messages.length / 2);
    for (let i = reportedCountRef.current; i < pairsReady; i++) {
      const userTurn = session.messages[i * 2];
      const assistantTurn = session.messages[i * 2 + 1];
      onTurnComplete(userTurn.content, assistantTurn.content, assistantTurn.durationMs);
    }
    reportedCountRef.current = pairsReady;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.messages]);

  useEffect(() => {
    historyEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [session.messages.length, session.partialTranscript]);

  const displayHistory: (AskSarthiConversationTurn & { timestamp?: number; durationMs?: number })[] = [
    ...initialHistory,
    ...session.messages,
  ];

  return createPortal(
    <div className="live-voice-fullscreen">
      <div className="live-voice-header">
        <span className="ai-dot active" aria-hidden="true" />
        {t(lang, "ask.liveVoice.header")}
      </div>

      <div className="live-voice-history" role="log" aria-live="polite">
        {displayHistory.length === 0 && !session.partialTranscript && (
          <div className="live-voice-empty">{t(lang, "ask.liveVoice.emptyHint")}</div>
        )}
        {displayHistory.map((turn, i) => (
          <div key={i} className={`live-voice-turn live-voice-turn-${turn.role}`}>
            {turn.content}
            {turn.timestamp != null && (
              <div className="ask-chat-timestamp">
                {formatClockTime(turn.timestamp, lang)}
                {turn.role === "assistant" && turn.durationMs != null && ` · ${formatDuration(turn.durationMs)}`}
              </div>
            )}
          </div>
        ))}
        {session.partialTranscript && (
          <div className="live-voice-turn live-voice-turn-user live-voice-turn-partial">{session.partialTranscript}</div>
        )}
        <div ref={historyEndRef} />
      </div>

      <div className="live-voice-center">
        <div className={`live-voice-orb ${ORB_CLASS_FOR_PHASE[session.phase]}`} />
      </div>

      <div className="live-voice-bottom-pill">
        {session.error ? (
          <div className="live-voice-error">{session.error}</div>
        ) : (
          <div className="voice-overlay-state">{t(lang, `ask.liveVoice.state.${session.phase}`)}</div>
        )}
        <button type="button" className="live-voice-close" onClick={onClose} aria-label={t(lang, "ask.voiceAssistant.close")}>
          ×
        </button>
      </div>
    </div>,
    document.body
  );
}
