import { useCallback, useRef, useState } from "react";

export type LiveVoicePhase = "idle" | "connecting" | "listening" | "processing" | "speaking" | "error";

export interface LiveVoiceMessage {
  role: "user" | "assistant";
  content: string;
  /** Wall-clock epoch ms this turn was sent (user) or arrived (assistant) -- same convention as
   * the main chat's own ChatMessage.timestamp (AskSarthi.tsx), shown the same way in
   * LiveVoiceOverlay.tsx. LIVE-REPORTED REQUEST: a longer conversation needs this the same way
   * the main chat already does -- it was missing entirely in Live mode's first version. */
  timestamp: number;
  /** Assistant turns only -- wall-clock ms from `turn_started` to this answer arriving, same
   * "how long did that take" signal the main chat's own `durationMs` already gives. */
  durationMs?: number;
}

export interface LiveVoiceSessionState {
  phase: LiveVoicePhase;
  messages: LiveVoiceMessage[];
  partialTranscript: string;
  error: string | null;
  /** 0..1 amplitude, updated continuously -- mic input while `listening`, TTS playback while
   * `speaking` -- for a real waveform/orb visualization to react to (see LiveVoiceOverlay.tsx). */
  audioLevel: number;
  start: () => Promise<boolean>;
  stop: () => void;
}

// Matches sarvam_realtime_client.py's configured `encoding="linear16"`/`sample_rate="16000"` --
// changing either side without the other silently corrupts every transcription.
const TARGET_SAMPLE_RATE = 16000;
// ScriptProcessorNode is deprecated in favor of AudioWorkletNode, but needs no separate worklet
// module file/async addModule() step to set up -- a reasonable v1 trade-off for a new, opt-in
// feature (see this app's "Live" vs "Classic" voice mode split); revisit if ScriptProcessorNode
// support is ever actually dropped, not preemptively.
const PROCESSOR_BUFFER_SIZE = 4096;

function wsUrl(language: string): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/ask-sarthi/voice/live?language=${encodeURIComponent(language)}`;
}

/** Downsamples a Float32 PCM buffer from `fromRate` to `TARGET_SAMPLE_RATE` via simple linear
 * interpolation -- good enough for speech (not archival-quality resampling), and cheap enough to
 * run on every ScriptProcessorNode callback without audible glitching. A no-op copy when the
 * AudioContext already runs at the target rate, which most browsers grant when asked (see
 * `start()` below). */
function downsampleTo16kHz(input: Float32Array, fromRate: number): Float32Array {
  if (fromRate === TARGET_SAMPLE_RATE) return input;
  const ratio = fromRate / TARGET_SAMPLE_RATE;
  const outLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    output[i] = input[Math.floor(i * ratio)];
  }
  return output;
}

function floatTo16BitPCM(input: Float32Array): ArrayBuffer {
  const buffer = new ArrayBuffer(input.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < input.length; i++) {
    const clamped = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(i * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return buffer;
}

function rms(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

function base64ToArrayBuffer(base64: string): ArrayBuffer {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

/** Continuous, always-listening voice session against `/ask-sarthi/voice/live` -- the "Live"
 * counterpart to useAudioRecorder.ts's tap-to-record flow. No start/stop-recording gesture: once
 * `start()` resolves, mic audio streams continuously until Sarvam's own VAD (server-side) detects
 * an utterance and the turn runs; the mic is muted here (never sent) while a turn's TTS audio is
 * still playing, so the assistant never hears/transcribes its own voice -- see this feature's own
 * scope note (backend/routes/ask_sarthi.py) on why full barge-in isn't attempted. */
export function useLiveVoiceSession(language: string): LiveVoiceSessionState {
  const [phase, setPhase] = useState<LiveVoicePhase>("idle");
  const [messages, setMessages] = useState<LiveVoiceMessage[]>([]);
  const [partialTranscript, setPartialTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const mutedRef = useRef(false); // true while a turn's audio is playing -- see module docstring
  const playbackTimeRef = useRef(0); // AudioContext.currentTime cursor for gapless chunk queueing
  const phaseRef = useRef<LiveVoicePhase>("idle");
  const turnStartedAtRef = useRef(0); // performance.now() at "turn_started" -- for the assistant turn's own durationMs

  function setPhaseBoth(next: LiveVoicePhase) {
    phaseRef.current = next;
    setPhase(next);
  }

  const stop = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    processorRef.current?.disconnect();
    processorRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    audioContextRef.current?.close();
    audioContextRef.current = null;
    setPhaseBoth("idle");
    setPartialTranscript("");
  }, []);

  const playAudioChunk = useCallback((base64: string) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;
    ctx.decodeAudioData(base64ToArrayBuffer(base64).slice(0)).then((buffer) => {
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      const channel = buffer.getChannelData(0);
      setAudioLevel(rms(channel));
      const startAt = Math.max(ctx.currentTime, playbackTimeRef.current);
      source.start(startAt);
      playbackTimeRef.current = startAt + buffer.duration;
    }).catch(() => {
      // A single malformed/undecoded chunk must never break the rest of the turn's playback --
      // the text answer already reached the citizen via the "answer" message regardless.
    });
  }, []);

  const start = useCallback(async (): Promise<boolean> => {
    setError(null);
    setMessages([]);
    setPhaseBoth("connecting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      streamRef.current = stream;
      const audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      audioContextRef.current = audioContext;
      playbackTimeRef.current = audioContext.currentTime;

      const ws = new WebSocket(wsUrl(language));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data);
        switch (message.type) {
          case "partial_transcript":
            setPartialTranscript(message.text);
            break;
          case "turn_started":
            setPartialTranscript("");
            setPhaseBoth("processing");
            turnStartedAtRef.current = performance.now();
            setMessages((prev) => [...prev, { role: "user", content: message.transcript, timestamp: Date.now() }]);
            break;
          case "answer":
            setMessages((prev) => [...prev, {
              role: "assistant", content: message.answer, timestamp: Date.now(),
              durationMs: performance.now() - turnStartedAtRef.current,
            }]);
            break;
          case "audio_chunk":
            mutedRef.current = true;
            setPhaseBoth("speaking");
            playAudioChunk(message.audio_base64);
            break;
          case "turn_complete":
            mutedRef.current = false;
            setPhaseBoth("listening");
            break;
          case "error":
            setError(message.detail || "Something went wrong.");
            break;
        }
      };
      ws.onerror = () => setError("Connection to Ask Sarthi was lost.");
      ws.onclose = () => {
        if (phaseRef.current !== "idle") setPhaseBoth("idle");
      };

      await new Promise<void>((resolve, reject) => {
        ws.addEventListener("open", () => resolve(), { once: true });
        ws.addEventListener("error", () => reject(new Error("Could not connect.")), { once: true });
      });

      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(PROCESSOR_BUFFER_SIZE, 1, 1);
      processorRef.current = processor;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        if (mutedRef.current || ws.readyState !== WebSocket.OPEN) return;
        setAudioLevel(rms(input));
        const downsampled = downsampleTo16kHz(input, audioContext.sampleRate);
        ws.send(floatTo16BitPCM(downsampled));
      };
      source.connect(processor);
      // A ScriptProcessorNode only fires while connected into the graph's destination, even
      // though this app never wants to actually HEAR its own mic input -- a silent (zero-gain)
      // node keeps the callback running without looping the citizen's own voice back to them.
      const silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      processor.connect(silentGain);
      silentGain.connect(audioContext.destination);

      setPhaseBoth("listening");
      return true;
    } catch {
      setError("Couldn't access your microphone, or couldn't reach Ask Sarthi.");
      setPhaseBoth("error");
      stop();
      return false;
    }
  }, [language, playAudioChunk, stop]);

  return { phase, messages, partialTranscript, error, audioLevel, start, stop };
}
