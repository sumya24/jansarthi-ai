import { useEffect, useRef, useState } from "react";
import Mascot, { type MascotState } from "./Mascot";
import MultiPhotoUpload from "./MultiPhotoUpload";
import { useUiLang } from "../lib/uiLang";
import { useAuth } from "../lib/auth";
import { t, toLangCode, SUPPORTED_LANGUAGES } from "../lib/i18n";
import { api, ApiError } from "../lib/api";
import { useAudioRecorder } from "../lib/useAudioRecorder";
import { useSpeechToText } from "../lib/useSpeechToText";
import type { AskJanMitraConversationTurn, AskVoiceResponse } from "../lib/ragTypes";
import "./VoiceAssistantOverlay.css";

type VoicePhase = "idle" | "listening" | "processing" | "speaking" | "error";

/**
 * "Mic 2" -- the voice-to-voice conversation assistant. A genuinely different control from Mic 1
 * (useSpeechToText.ts, which just fills the text input for manual editing/sending): this opens a
 * dedicated overlay, captures a full spoken turn via the SAME chunked-recording hook the
 * complaint-creation voice flow already uses (useAudioRecorder.ts), sends it to the real
 * POST /ask-janmitra/voice backend, and plays back the AI's real synthesized speech.
 *
 * Turn-based by design (not real-time interruption) -- see the implementation plan for why: this
 * stack has no streaming ASR/persistent channel, so real barge-in isn't feasible without a real
 * architecture change. The citizen explicitly stops their turn; the AI's full spoken response
 * plays before the next turn can start. Mute stops local playback only, never the backend call.
 *
 * The mascot's "speaking" state (Mascot.tsx's own `.mascot-speaking` pulse) is applied ONLY
 * while the <audio> element is actually firing `playing` -- removed again on pause/ended/error
 * (see the `audioPlaying` state below, set only from those real events) -- so it never appears
 * to speak without real audio playing, and never for a fixed/guessed duration.
 *
 * Live-reported bug: this overlay used to keep its own conversation history, seeded empty every
 * time it opened and never shared with the main Ask Sarthi chat page in either direction -- a
 * voice conversation was invisible in the text transcript, and switching back to typing afterward
 * lost all context from what was just said out loud. `initialHistory`/`onTurnComplete` close that
 * gap: this overlay now STARTS from whatever the main page has already discussed (so it can
 * recover a category/location the citizen already gave in text, exactly like a fresh text turn
 * would), and reports each of its own turns back up to the parent (so closing the overlay shows
 * the voice exchange in the same transcript, and a later typed message carries it as context) --
 * one shared conversation, not two disconnected ones.
 */
export default function VoiceAssistantOverlay({
  onClose,
  initialHistory,
  onTurnComplete,
  conversationId,
}: {
  onClose: () => void;
  initialHistory: AskJanMitraConversationTurn[];
  /** LIVE-REPORTED BUG, fixed alongside `initialHistory`'s own: the turn this reports back to the
   * main chat used to lose the attached photo (no thumbnail in the background transcript, even
   * though the request genuinely included one) and never showed how long the reply took (unlike
   * every typed/photo turn, which does) -- because this callback only ever passed `question`/
   * `response`, nothing about the image or real elapsed time. `durationMs` is measured here
   * (right around the actual `api.askJanMitraVoice` call, not a guess); `imagePreview` is a
   * `URL.createObjectURL()` blob of whatever was attached THIS turn, or `undefined` if nothing
   * was -- same shape/lifecycle as `AskJanMitra.tsx`'s own text/image submit path, which is also
   * where this blob's cleanup (`imagePreviewUrlsRef`) lives, not here. */
  onTurnComplete: (question: string, response: AskVoiceResponse, durationMs: number, imagePreview?: string) => void;
  /** Observability only (see AskJanMitra.tsx's loadOrCreateConversationId docstring) -- passed
   * through so a voice turn groups into the SAME Phoenix session as the rest of this chat, not a
   * separate one, matching how `initialHistory`/`onTurnComplete` already keep this one shared
   * conversation instead of two disconnected ones. */
  conversationId: string;
}) {
  const { lang } = useUiLang();
  const { token } = useAuth();
  const recorder = useAudioRecorder();
  // LIVE-REPORTED REQUEST: while `recorder` above captures the real audio this turn is actually
  // decided from (unchanged), this is a SECOND, independent transcription running purely for a
  // live, on-screen caption while the citizen is still talking -- the same browser-native
  // technology Mic 1 already uses (see useSpeechToText.ts's own docstring), reused here only for
  // display. Deliberately Chrome/Edge-only, same limitation Mic 1 already has (`supported` below
  // gates all of it, so an unsupported browser just shows nothing extra -- silently, never a
  // broken control) -- covered live testing across Firefox before shipping. Its own `error` is
  // NEVER surfaced as a voice-turn error: this is a cosmetic preview, not the thing that actually
  // determines the complaint/answer, so its failure must never look like the real turn failed.
  const liveCaption = useSpeechToText(lang);

  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [responseText, setResponseText] = useState<string | null>(null);
  // LIVE-REPORTED BUG: the "Transcript"/"Response" labels below rendered in the account-wide UI
  // toggle (`lang`), not the language THIS turn actually came back in -- see i18n.ts's
  // `toLangCode` docstring for the full context. Set alongside `responseText` from the same real
  // `result.language`, so both labels follow the actual conversation, not a stale toggle.
  const [responseLanguage, setResponseLanguage] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [audioPlaying, setAudioPlaying] = useState(false);
  // Seeded from the main chat's own history (see this component's own docstring), not an empty
  // array -- every turn taken IN this overlay still just appends onto it locally, same as before.
  const [history, setHistory] = useState<AskJanMitraConversationTurn[]>(initialHistory);
  const [attachedImage, setAttachedImage] = useState<File[]>([]);

  const stopRequestedRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  // useAudioRecorder's audioSegments only reflects the final segment once its own async
  // MediaRecorder.onstop handler fires (see that hook's docstring) -- calling stop() does not
  // mean the last segment is ready yet, so the actual submit is deferred to here.
  useEffect(() => {
    if (!stopRequestedRef.current) return;
    if (recorder.isRecording) return;
    if (recorder.audioSegments.length === 0) return;
    stopRequestedRef.current = false;
    const segments = recorder.audioSegments;
    recorder.reset();
    void submitTurn(segments);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recorder.isRecording, recorder.audioSegments]);

  // Stop any in-flight recording/playback if the overlay unmounts uncleanly.
  useEffect(() => {
    return () => {
      audioRef.current?.pause();
      if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
      recorder.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleMicTap() {
    if (phase === "listening") {
      stopRequestedRef.current = true;
      setPhase("processing");
      recorder.stop();
      if (liveCaption.supported) liveCaption.stop();
      return;
    }
    if (phase === "idle" || phase === "error") {
      setError(null);
      setResponseText(null);
      setResponseLanguage(null);
      // LIVE-REPORTED BUG: `transcript` (the "YOU SAID" box) was missing from this reset, unlike
      // `responseText`/`responseLanguage` right above it -- so starting a SECOND turn in the same
      // overlay session kept showing the FIRST turn's transcript all the way through "Thinking..."
      // (this turn's own transcript genuinely doesn't exist yet until the backend responds), which
      // reads as "it didn't hear my new question at all" rather than "still processing your new
      // one". Cleared here for the same reason the two lines above already are.
      setTranscript(null);
      // LIVE-REPORTED BUG: `recorder.start()` is genuinely async -- it opens the mic stream, THEN
      // starts the real MediaRecorder. This used to flip to "listening" (which visually invites
      // the citizen to start talking) before awaiting that, so a citizen who spoke the instant
      // they saw "listening" had their first word(s) captured by nothing -- confirmed live: the
      // citizen said "tell me about this image", but the backend's own STT transcript (visible in
      // Phoenix's speech_to_text span) shows only "this image" ever arrived. Awaiting `start()`
      // first, and only flipping to "listening" on its real success, closes that gap -- the
      // indicator now can't appear before recording genuinely is.
      const started = await recorder.start();
      if (!started) return; // recorder.error is already set and rendered independently below
      setPhase("listening");
      if (liveCaption.supported) {
        liveCaption.reset();
        liveCaption.start();
      }
    }
  }

  async function submitTurn(segments: Blob[]) {
    if (!token) return;
    // Captured before the attempted image, in case decoding it as a preview URL ever throws --
    // this turn's real elapsed time shouldn't depend on that succeeding.
    const sentAt = performance.now();
    // Created now (this turn's image, if any, is about to be cleared on success either way) --
    // `undefined` when nothing was attached, matching AskJanMitra.tsx's own text/image submit path.
    const imagePreview = attachedImage[0] ? URL.createObjectURL(attachedImage[0]) : undefined;
    try {
      const result = await api.askJanMitraVoice(token, {
        language: lang,
        conversation_history: history,
        conversation_id: conversationId,
        audioSegments: segments,
        image: attachedImage[0] ?? null,
      });
      setTranscript(result.question);
      setResponseText(result.answer);
      setResponseLanguage(result.language);
      setHistory((prev) => [
        ...prev,
        { role: "user", content: result.question },
        // `complaint_workflow_state` echoed here too -- same gap, same fix as the main text chat
        // (see AskJanMitra.tsx's own historyForRequest comment): without it, a complaint filed or
        // cancelled via voice can't be recognized as a closed boundary on a later turn (voice OR
        // text, since this history is shared with the main chat -- see this component's own
        // docstring), letting a brand-new complaint silently reuse its stale ward/category.
        { role: "assistant", content: result.answer, complaint_workflow_state: result.complaint_workflow_state },
      ]);
      // Reports this turn up to the main chat page (see this component's own docstring) -- so
      // closing the overlay shows it in the same transcript, sources/follow-up options included,
      // now with the real elapsed time and the attached photo (if any) too -- see this prop's own
      // docstring for the live-reported gap this closes.
      onTurnComplete(result.question, result, performance.now() - sentAt, imagePreview);
      // Only clear on success -- a failed request keeps the attached photo so the citizen can
      // just retry, matching AskJanMitra.tsx's own text/image submit behavior.
      setAttachedImage([]);

      if (result.audio_base64) {
        playAudio(result.audio_base64, result.audio_format);
      } else {
        // TTS failed server-side -- a real, honest degrade: the text answer above is already
        // real, there's just no audio to play. Never fabricate/placeholder audio here.
        setPhase("idle");
      }
    } catch (err) {
      // The request never succeeded, so `imagePreview` (if created above) was never handed off
      // to the parent for its own cleanup tracking -- revoke it here instead, or it leaks for the
      // rest of this tab's life.
      if (imagePreview) URL.revokeObjectURL(imagePreview);
      setError(err instanceof ApiError ? err.message : t(lang, "ask.voiceAssistant.errorGeneric"));
      setPhase("error");
    }
  }

  function playAudio(audioBase64: string, format: string) {
    const byteChars = atob(audioBase64);
    const bytes = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
    const blob = new Blob([bytes], { type: `audio/${format}` });
    const url = URL.createObjectURL(blob);
    audioUrlRef.current = url;

    const audioEl = new Audio(url);
    audioEl.muted = muted;
    audioRef.current = audioEl;

    audioEl.onplaying = () => setAudioPlaying(true);
    audioEl.onpause = () => setAudioPlaying(false);
    audioEl.onended = () => {
      setAudioPlaying(false);
      setPhase("idle");
      URL.revokeObjectURL(url);
      audioUrlRef.current = null;
    };
    audioEl.onerror = () => {
      setAudioPlaying(false);
      setPhase("idle");
    };

    setPhase("speaking");
    audioEl.play().catch(() => {
      setAudioPlaying(false);
      setPhase("idle");
    });
  }

  function toggleMute() {
    setMuted((prev) => {
      const next = !prev;
      if (audioRef.current) audioRef.current.muted = next;
      return next;
    });
  }

  function handleEnd() {
    audioRef.current?.pause();
    recorder.stop();
    onClose();
  }

  const mascotState: MascotState =
    phase === "listening" ? "listening"
    : phase === "processing" ? "thinking"
    : audioPlaying ? "speaking"
    : "idle";

  return (
    <div className="voice-overlay-backdrop" onClick={handleEnd}>
      <div
        className="voice-overlay-panel"
        role="dialog"
        aria-modal="true"
        aria-label={t(lang, "ask.voiceAssistant.title")}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          className="voice-overlay-close"
          aria-label={t(lang, "ask.voiceAssistant.close")}
          onClick={handleEnd}
        >
          ✕
        </button>

        {/* The animated glow is purely decorative (aria-hidden) and keyed off `phase`, not
            `mascotState` -- phase has a real "error" state mascotState collapses into "idle",
            and the glow should visibly dim/redden on error rather than keep pulsing like
            nothing's wrong. */}
        <div className={`voice-orb voice-orb-${phase}`}>
          <div className="voice-orb-glow" aria-hidden="true" />
          <div className="voice-overlay-mascot">
            <Mascot state={mascotState} size={80} />
          </div>
        </div>

        <div className="voice-overlay-state" aria-live="polite">
          {t(lang, `ask.voiceAssistant.state.${phase}`)}
        </div>

        <div className="voice-overlay-language">{SUPPORTED_LANGUAGES[lang].name}</div>

        {/* Live caption -- a rough, on-screen preview of what's being said WHILE still talking
            (see `liveCaption`'s own docstring above for why this is a second, independent, purely
            cosmetic transcription). Only ever shown during `listening`, on a browser that
            supports it -- an unsupported browser (Firefox, Safari) simply never renders this,
            same as Mic 1's own graceful degradation. A calm placeholder holds the box's height
            steady before any words arrive, so the panel doesn't visibly jump the moment speech
            starts. */}
        {phase === "listening" && liveCaption.supported && (
          <div className="voice-overlay-live-caption" aria-live="polite">
            <p className={liveCaption.transcript ? "" : "voice-overlay-live-caption-placeholder"}>
              {liveCaption.transcript || t(lang, "ask.voiceAssistant.liveCaptionPlaceholder")}
              <span className="voice-overlay-live-caption-cursor" aria-hidden="true" />
            </p>
          </div>
        )}

        {/* Attaching a photo here is optional and only meaningful before/between turns -- a
            combined voice+image turn (see backend ask_janmitra_service.ask_voice()), reusing
            the exact same single-image attach control the text/image flow already uses. */}
        {(phase === "idle" || phase === "error") && (
          <MultiPhotoUpload photos={attachedImage} onChange={setAttachedImage} maxFiles={1} placeholderKey="ask.image.addLabel" />
        )}

        {transcript && (
          <div className="voice-overlay-transcript">
            <div className="voice-overlay-transcript-label">{t(toLangCode(responseLanguage), "ask.voiceAssistant.transcriptLabel")}</div>
            <p>{transcript}</p>
          </div>
        )}

        {responseText && (
          <div className="voice-overlay-transcript voice-overlay-response">
            <div className="voice-overlay-transcript-label">{t(toLangCode(responseLanguage), "ask.voiceAssistant.responseLabel")}</div>
            <p>{responseText}</p>
          </div>
        )}

        {error && <div className="banner-error">{error}</div>}
        {recorder.error && <div className="banner-error">{recorder.error}</div>}

        <div className="voice-overlay-controls">
          <button
            type="button"
            className="voice-overlay-mic"
            onClick={handleMicTap}
            disabled={phase === "processing" || phase === "speaking"}
            aria-pressed={phase === "listening"}
            aria-label={t(
              lang,
              phase === "listening" ? "ask.voiceAssistant.micControl.stop" : "ask.voiceAssistant.micControl.start"
            )}
          >
            {phase === "listening" ? (
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
                <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
              </svg>
            ) : (
              <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
                <rect x="9" y="3" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
                <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            )}
          </button>

          {phase === "listening" && <span className="voice-overlay-seconds mono">{recorder.seconds}s</span>}

          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={toggleMute}
            aria-pressed={muted}
            aria-label={t(lang, muted ? "ask.voiceAssistant.unmute" : "ask.voiceAssistant.mute")}
          >
            {muted ? "🔇" : "🔊"}
          </button>

          <button type="button" className="btn btn-ghost btn-sm" onClick={handleEnd}>
            {t(lang, "ask.voiceAssistant.endConversation")}
          </button>
        </div>

        {phase === "error" && (
          <button type="button" className="btn btn-primary btn-sm" onClick={() => setPhase("idle")}>
            {t(lang, "ask.voiceAssistant.tryAgain")}
          </button>
        )}

        <p className="voice-overlay-note">{t(lang, "ask.voiceAssistant.turnBasedNote")}</p>
      </div>
    </div>
  );
}
