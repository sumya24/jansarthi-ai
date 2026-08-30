/** Sarthi's mascot -- the actual character the user supplied (cropped from their own
 * reference character sheet: "ChatGPT Image Aug 10, 2026, 06_20_08 PM.png", the green-vest/
 * white-cap "जनमित्र" branded municipal assistant), not a hand-drawn placeholder. Background
 * removed (flood-filled from the image edges, so only the connected white backdrop goes
 * transparent -- the character's own white clothing stays intact) and each pose downscaled to
 * a sensible on-screen size (public/mascot/*.png, ~130-180KB each, still well under the "don't
 * load huge character assets" guidance).
 *
 * Two fixes applied directly to the 5 PNGs (not something this component's code can express):
 * the edge-only flood fill above never reached the small enclosed gap between the legs on every
 * standing pose -- not connected to the image's outer border, so it kept its original opaque
 * off-white backdrop color instead of going transparent, a visible pale smudge between the feet.
 * Fixed with a second, tightly-bounded pass scoped to just that pocket per pose. Separately,
 * each source crop was only ~240px tall, under-resolved for common 2-3x device-pixel-ratio
 * screens even at this component's largest real usage (WelcomeMascot's size=130 in
 * AskSarthi.tsx) well before any user pinch-zoom -- upscaled 2x via Lanczos resampling for
 * real headroom. Aspect ratios (and so the ASPECT table below) are unchanged -- both dimensions
 * scaled by the same factor.
 *
 * Deliberately does NOT use the sheet's own baked-in speech-bubble poses (e.g. "नमस्कार! मैं
 * जनमित्र AI आपका स्वागत करता हूँ") -- this app supports 6 languages via i18n.ts, and a phrase
 * baked into a raster image would only ever render in whichever language it happened to be
 * generated in, regardless of the citizen's actual selected language. All 5 crops here are the
 * character ALONE; any accompanying text (the greeting bubble, etc.) stays real, translated UI
 * text rendered next to the image, same as before.
 *
 * Each state is real application state, never a proxy -- see AskSarthiWidget.tsx,
 * AskSarthi.tsx's AskSarthiContent, and VoiceAssistantOverlay.tsx for exactly what drives
 * each one ("speaking" specifically comes from the real HTMLAudioElement's playing/pause/ended
 * events during TTS playback, not a timer). There is deliberately no "error" state: an error is
 * already communicated by the existing text error banner, so the mascot just stays idle rather
 * than performing an emotion this state set doesn't have.
 *
 * Every state gets its own CSS animation (loop or one-shot; see AskSarthiWidget.css, the sole
 * home for `.mascot-*` animation rules regardless of which component renders <Mascot>) so the
 * character reads as alive rather than a static photo swap -- transform/filter only (GPU-
 * friendly), nothing that fakes lip-sync by distorting the whole image, and every animation is
 * disabled under prefers-reduced-motion (the state itself still visibly changes via the pose
 * swap; only the extra motion drops).
 */

export type MascotState = "idle" | "greeting" | "thinking" | "listening" | "speaking" | "success";

// Each source crop has its own natural aspect ratio (a person, not a square icon) -- rather than
// stretch/pad every state to a fixed square, `size` below controls height and each image keeps
// its own width via these ratios, sampled directly from the actual asset dimensions.
const ASPECT: Record<MascotState, number> = {
  idle: 189 / 240,
  greeting: 126 / 240,
  thinking: 92 / 240,
  listening: 108 / 240,
  speaking: 126 / 240,
  success: 142 / 240,
};

// "speaking" deliberately has no asset of its own -- the approved character sheet only ever had
// 5 crops (see this file's module docstring), and the brief for this state is explicit: never
// invent new character art. It reuses "greeting"'s image (open smile, mid-gesture -- the closest
// existing pose to "actively talking", clearly more so than idle's calm folded-hands look). The
// two states stay visually and behaviorally distinct through their own CSS animation class
// (mascot-greeting: one-shot wave, plays once; mascot-speaking: continuous pulse, gated on real
// TTS <audio> playback -- see AskSarthiWidget.css and VoiceAssistantOverlay.tsx) even though
// they share a picture.
const IMAGE_FOR_STATE: Record<MascotState, Exclude<MascotState, "speaking">> = {
  idle: "idle",
  greeting: "greeting",
  thinking: "thinking",
  listening: "listening",
  speaking: "greeting",
  success: "success",
};

export default function Mascot({ state, size = 40 }: { state: MascotState; size?: number }) {
  return (
    <img
      src={`/mascot/mascot-${IMAGE_FOR_STATE[state]}.png`}
      alt=""
      className={`mascot mascot-${state}`}
      style={{ height: size, width: size * ASPECT[state], display: "block", objectFit: "contain" }}
    />
  );
}
