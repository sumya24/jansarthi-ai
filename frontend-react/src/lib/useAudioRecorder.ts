import { useCallback, useEffect, useRef, useState } from "react";

/** Picks a MIME type MediaRecorder on this browser will actually accept. */
function pickMimeType(): string | undefined {
  const candidates = ["audio/webm", "audio/mp4", "audio/ogg"];
  for (const type of candidates) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported?.(type)) return type;
  }
  return undefined;
}

// Sarvam's speech-to-text endpoint hard-rejects any single request over 30 seconds of audio
// (confirmed live -- see docs/ai_pipeline_limits.md), so a recording longer than that would
// otherwise just fail outright. Instead of capping what a citizen can say, the recorder stops
// and immediately restarts itself every SEGMENT_SECONDS -- each restart produces a new, fully
// valid, independent audio file (this is more reliable across browsers than trying to slice one
// continuous file after the fact). 28s, not 30s, leaves a small safety margin. The backend
// transcribes each segment independently and stitches the text back together.
const SEGMENT_SECONDS = 28;

// A generous ceiling on total recording length (10 segments ~= 4-5 minutes), so a citizen who
// forgets a recording is running doesn't rack up an unbounded number of STT calls by accident.
const MAX_SEGMENTS = 10;

export interface AudioRecorderState {
  isRecording: boolean;
  seconds: number;
  /** Ordered list of ≤28s audio segments recorded so far -- this is what gets submitted. */
  audioSegments: Blob[];
  /** Playback preview URL. Only set when there's exactly one segment; multi-segment
   * recordings still submit and transcribe correctly, they just don't get a gapless local
   * preview (stitching multiple independently-finalized files into one playable preview
   * isn't reliable across browsers, so this deliberately doesn't attempt it). */
  audioUrl: string | null;
  error: string | null;
  /** Resolves `true` once recording has genuinely started (the mic stream is open AND the real
   * MediaRecorder is running), `false` if it failed for any reason (see `error` for why). A
   * caller that flips its own UI to a "listening"/"speak now" state must wait for `true` first --
   * see VoiceAssistantOverlay.tsx's own handleMicTap for the live-reported bug this return value
   * exists to let callers avoid: showing "listening" before this genuinely resolved raced the
   * citizen's own first words against the mic still initializing, silently losing them. */
  start: () => Promise<boolean>;
  stop: () => void;
  reset: () => void;
}

/** Records a voice note from the user's microphone via MediaRecorder, in ≤28s segments. */
export function useAudioRecorder(): AudioRecorderState {
  const [isRecording, setIsRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [audioSegments, setAudioSegments] = useState<Blob[]>([]);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const segmentsRef = useRef<Blob[]>([]);
  const mimeTypeRef = useRef<string | undefined>(undefined);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const stopStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  useEffect(() => stopStream, [stopStream]);

  const reset = useCallback(() => {
    segmentsRef.current = [];
    setAudioSegments([]);
    setAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setSeconds(0);
    setError(null);
  }, []);

  /** Starts a new MediaRecorder on the already-open stream -- used both for the very first
   * segment and every ~28s restart. Does not touch the stream itself, so recording continues
   * uninterrupted from the citizen's point of view.
   *
   * Each recorder gets its own private `chunks` array, captured by closure rather than kept
   * in a shared ref: the old recorder's final `ondataavailable`/`onstop` fire *asynchronously*
   * after `.stop()`, so a shared array reset here for the new segment could race with the old
   * segment's still-pending final chunk and corrupt both segments. Per-instance closures make
   * that race impossible instead of just unlikely. */
  const startSegmentRecorder = useCallback(() => {
    if (!streamRef.current) return;
    const chunks: Blob[] = [];
    const recorder = new MediaRecorder(streamRef.current, mimeTypeRef.current ? { mimeType: mimeTypeRef.current } : undefined);
    recorderRef.current = recorder;

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunks.push(e.data);
    };
    recorder.onstop = () => {
      const blob = new Blob(chunks, { type: mimeTypeRef.current || "audio/webm" });
      segmentsRef.current = [...segmentsRef.current, blob];
      setAudioSegments(segmentsRef.current);
    };

    recorder.start();
  }, []);

  const start = useCallback(async () => {
    setError(null);
    reset();

    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("Voice recording isn't supported in this browser. Please type your complaint instead.");
      return false;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      mimeTypeRef.current = pickMimeType();

      startSegmentRecorder();
      setIsRecording(true);
      setSeconds(0);
      timerRef.current = setInterval(() => {
        setSeconds((s) => {
          const next = s + 1;
          if (next % SEGMENT_SECONDS === 0) {
            if (segmentsRef.current.length + 1 >= MAX_SEGMENTS) {
              // Hit the safety ceiling: finish this segment and stop for real, same as the
              // citizen tapping "Stop" themselves.
              stopTimer();
              setIsRecording(false);
              if (recorderRef.current && recorderRef.current.state !== "inactive") recorderRef.current.stop();
              stopStream();
            } else {
              // Rotate to a fresh segment without ending the recording or the mic stream.
              recorderRef.current?.stop();
              startSegmentRecorder();
            }
          }
          return next;
        });
      }, 1000);
      return true;
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        setError("Microphone access was denied. Please allow microphone access, or type your complaint instead.");
      } else if (name === "NotFoundError") {
        setError("No microphone was found on this device. Please type your complaint instead.");
      } else {
        setError("Couldn't start recording. Please type your complaint instead.");
      }
      return false;
    }
  }, [reset, startSegmentRecorder, stopStream, stopTimer]);

  const stop = useCallback(() => {
    stopTimer();
    setIsRecording(false);
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    stopStream();
  }, [stopTimer, stopStream]);

  // Preview URL: only meaningful (and only attempted) for a single-segment recording -- see
  // the audioUrl doc comment above for why multi-segment recordings skip this.
  useEffect(() => {
    if (isRecording) return;
    setAudioUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return audioSegments.length === 1 ? URL.createObjectURL(audioSegments[0]) : null;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioSegments, isRecording]);

  return { isRecording, seconds, audioSegments, audioUrl, error, start, stop, reset };
}
