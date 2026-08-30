import { useCallback, useEffect, useRef, useState } from "react";
import type { LangCode } from "./i18n";

/**
 * Browser-native speech-to-text for the Ask Sarthi chat, via the Web Speech API
 * (SpeechRecognition). This is deliberately NOT the Sarvam STT pipeline that
 * ReportIssue.tsx's voice recording uses (see useAudioRecorder.ts) -- that pipeline only exists
 * on the complaint-creation endpoint (POST /complaints accepts multipart `audio` segments,
 * transcribed server-side); POST /ask-sarthi has no audio field at all (see
 * backend/schemas/ask_sarthi.py), so there is no way to route chat speech through it.
 *
 * This hook instead runs recognition entirely client-side in the browser and produces a plain
 * text transcript, which the caller drops into the same editable text input used for typed
 * questions -- so from that point on it's just a normal (already-existing, already-real)
 * /ask-sarthi text request. No backend change, no new capability invented.
 *
 * Not supported in every browser (notably Firefox) -- `supported` is checked once so the
 * caller can render nothing at all rather than a dead button.
 */

interface MinimalSpeechRecognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  onresult: ((event: SpeechRecognitionResultEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
}

interface SpeechRecognitionResultEvent extends Event {
  resultIndex: number;
  results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }>;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
}

type SpeechRecognitionCtor = new () => MinimalSpeechRecognition;

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: SpeechRecognitionCtor; webkitSpeechRecognition?: SpeechRecognitionCtor };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

// BCP-47 tags for each supported UI language, for SpeechRecognition's `lang` property -- the
// browser's own recognizer does the actual language-specific work, this just tells it which.
const RECOGNITION_LANG: Record<LangCode, string> = {
  en: "en-IN",
  hi: "hi-IN",
  mr: "mr-IN",
  or: "or-IN",
  gu: "gu-IN",
  bn: "bn-IN",
};

export type SpeechToTextStatus = "idle" | "permission" | "recording" | "error";

export interface SpeechToTextState {
  supported: boolean;
  status: SpeechToTextStatus;
  transcript: string;
  /** An i18n KEY (e.g. "ask.voice.noSpeech"), not a literal message -- unlike
   * useAudioRecorder.ts's plain English error strings, this hook's caller is already inside
   * the fully-localized Ask Sarthi UI, so the key is resolved via t(lang, error) at render
   * time instead of hardcoding English here. */
  error: string | null;
  start: () => void;
  stop: () => void;
  reset: () => void;
}

export function useSpeechToText(lang: LangCode): SpeechToTextState {
  const Ctor = getSpeechRecognitionCtor();
  const supported = Ctor !== null;

  const [status, setStatus] = useState<SpeechToTextStatus>("idle");
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<MinimalSpeechRecognition | null>(null);
  const finalTranscriptRef = useRef("");

  useEffect(() => {
    return () => {
      recognitionRef.current?.stop();
    };
  }, []);

  const reset = useCallback(() => {
    finalTranscriptRef.current = "";
    setTranscript("");
    setError(null);
    setStatus("idle");
  }, []);

  const start = useCallback(() => {
    if (!Ctor) return;
    setError(null);
    finalTranscriptRef.current = "";
    setTranscript("");
    setStatus("permission");

    const recognition = new Ctor();
    recognition.lang = RECOGNITION_LANG[lang] || "en-IN";
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event) => {
      setStatus("recording");
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text = result[0]?.transcript ?? "";
        if (result.isFinal) finalTranscriptRef.current += text;
        else interim += text;
      }
      setTranscript((finalTranscriptRef.current + " " + interim).trim());
    };

    recognition.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "permission-denied") {
        setError("ask.voice.permissionDenied");
      } else if (event.error === "no-speech") {
        setError("ask.voice.noSpeech");
      } else {
        setError("ask.voice.genericError");
      }
      setStatus("error");
    };

    recognition.onend = () => {
      setStatus((prev) => (prev === "error" ? prev : "idle"));
    };

    recognitionRef.current = recognition;
    recognition.start();
    setStatus("recording");
  }, [Ctor, lang]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    setStatus("idle");
  }, []);

  return { supported, status, transcript, error, start, stop, reset };
}
