import { createContext, useContext, useState, type ReactNode } from "react";

const VOICE_MODE_KEY = "janmitra.voiceMode";

export type VoiceMode = "classic" | "live";

interface VoiceModeState {
  voiceMode: VoiceMode;
  setVoiceMode: (mode: VoiceMode) => void;
}

const VoiceModeContext = createContext<VoiceModeState | null>(null);

// A per-device UI preference (like uiLang.tsx/theme.tsx), not an account setting -- "Live" is a
// new, opt-in experimental mode (see LiveVoiceOverlay.tsx), not something that needs to follow a
// citizen across devices the way their account language does.
export function VoiceModeProvider({ children }: { children: ReactNode }) {
  const [voiceMode, setVoiceModeState] = useState<VoiceMode>(
    () => (localStorage.getItem(VOICE_MODE_KEY) as VoiceMode) || "classic"
  );

  function setVoiceMode(mode: VoiceMode) {
    localStorage.setItem(VOICE_MODE_KEY, mode);
    setVoiceModeState(mode);
  }

  return <VoiceModeContext.Provider value={{ voiceMode, setVoiceMode }}>{children}</VoiceModeContext.Provider>;
}

export function useVoiceMode(): VoiceModeState {
  const ctx = useContext(VoiceModeContext);
  if (!ctx) throw new Error("useVoiceMode must be used within VoiceModeProvider");
  return ctx;
}
