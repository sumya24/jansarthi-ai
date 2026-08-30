import { useLayoutEffect, useRef, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t } from "../lib/i18n";
import ThemeToggle from "../components/ThemeToggle";
import Reveal from "../components/Reveal";
import "./Landing.css";

const STEP_ICONS = [
  // chat bubble + sparkle -- Ask Sarthi itself, introduced before any of the mechanics below
  <svg key="intro" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v7A2.5 2.5 0 0 1 17.5 16H10l-4 3.5V16H6.5A2.5 2.5 0 0 1 4 13.5v-7Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    <path d="M12 6.8l1 2.4 2.4 1-2.4 1-1 2.4-1-2.4-2.4-1 2.4-1 1-2.4Z" fill="currentColor" />
  </svg>,
  // mic
  <svg key="mic" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
    <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>,
  // sparkle / translate
  <svg key="translate" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M4 5h9M8 3v2M11 5c-.6 3.6-2.4 6-6 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M6 9c1 1.6 2.4 2.6 4 3.2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M14 21l1.8-4.6L20.4 14.6 16 12.8 14.2 8.4 12.4 12.8 8 14.6l4.6 1.8Z" fill="currentColor" />
  </svg>,
  // summary / confirm
  <svg key="confirm" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <rect x="5" y="3" width="14" height="18" rx="2" stroke="currentColor" strokeWidth="1.7" />
    <path d="M8 8h8M8 12h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    <path d="M8.5 16.5l1.8 1.8L14.5 15" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>,
  // check / worker
  <svg key="check" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeWidth="1.8" />
    <path d="M8 12.5l2.6 2.6L16.5 9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>,
  // loop / reassign
  <svg key="reassign" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M4 12a8 8 0 0 1 13.5-5.8M20 12a8 8 0 0 1-13.5 5.8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    <path d="M17.5 3v3.5H14M6.5 21v-3.5H10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
  </svg>,
  // camera / photo -- same glyph as the "Attach a photo" feature card below, reused rather than
  // invented, since this step is describing the same real capability.
  <svg key="photo" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <rect x="3" y="5" width="18" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.7" />
    <circle cx="9" cy="11" r="2.2" stroke="currentColor" strokeWidth="1.5" />
    <path d="M21 16l-5.5-5-4 4-2-1.5L3 18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>,
  // star / rate
  <svg key="rate" width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M12 3.5l2.6 5.4 5.9.7-4.3 4.1 1.1 5.9L12 16.7l-5.3 2.9 1.1-5.9-4.3-4.1 5.9-.7L12 3.5Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
  </svg>,
];

const FEATURE_ICONS = [
  <svg key="lang" width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z" stroke="currentColor" strokeWidth="1.7" /><path d="M3 12h18M12 3c2.4 2.4 3.6 5.7 3.6 9s-1.2 6.6-3.6 9c-2.4-2.4-3.6-5.7-3.6-9S9.6 5.4 12 3Z" stroke="currentColor" strokeWidth="1.5" /></svg>,
  <svg key="mic2" width="24" height="24" viewBox="0 0 24 24" fill="none"><rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.7" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /></svg>,
  <svg key="photo" width="24" height="24" viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.7" /><circle cx="9" cy="11" r="2.2" stroke="currentColor" strokeWidth="1.5" /><path d="M21 16l-5.5-5-4 4-2-1.5L3 18" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  <svg key="track" width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M4 19V5M4 19h16M8 15v-4M12.5 15V7M17 15v-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  // question / ask
  <svg key="qa" width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8A2.5 2.5 0 0 1 17.5 16H10l-4.5 4v-4H6.5A2.5 2.5 0 0 1 4 13.5v-8Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M9.6 8.4c.2-1 1.1-1.7 2.2-1.7 1.2 0 2.2.8 2.2 1.9 0 1.6-2.1 1.5-2.1 3.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /><circle cx="11.7" cy="13.8" r="0.15" fill="currentColor" stroke="currentColor" strokeWidth="1.4" /></svg>,
];

// Each step's accent pulled straight from the app's real palette (status/service tokens
// already used elsewhere for waste/water/roads/streetlight, plus the adaptive brand accent) --
// not invented colors, so the flow reads as "our brand," not a copy of someone else's demo.
// "You confirm" reuses --status-pending -- a real status color already used elsewhere in the app
// for "waiting on a decision," which is exactly the moment this step represents. "Meet Ask
// Sarthi" bookends the flow with the same adaptive brand accent "Track it, then rate it" ends
// on -- introduces and closes on the app's own color, not a random pick to fill the extra slot.
// "Photos, every step of the way" reuses --status-progress -- the same blue "Speak or type"
// (the other non-service-specific step) already uses, rather than a 7th invented color.
const STEP_ACCENT = [
  "var(--accent-fg)",
  "var(--status-progress)",
  "var(--service-streetlight)",
  "var(--status-pending)",
  "var(--service-waste)",
  "var(--service-roads)",
  "var(--status-progress)",
  "var(--accent-fg)",
];
const STEP_ACCENT_BG = [
  "color-mix(in srgb, var(--accent-fg) 16%, transparent)",
  "var(--status-progress-bg)",
  "var(--service-streetlight-bg)",
  "var(--status-pending-bg)",
  "var(--service-waste-bg)",
  "var(--service-roads-bg)",
  "var(--status-progress-bg)",
  "color-mix(in srgb, var(--accent-fg) 16%, transparent)",
];

// Same brand palette as the flow section above, just a 4-color slice of it (purple/blue/green/
// orange) instead of a straight rotation from the top -- these four features aren't a sequence
// like the flow steps, so there's no "first color" reason to start from blue. The 5th (asking a
// question) isn't tied to one specific civic category the way the other four are -- it spans all
// of them -- so it gets the adaptive brand accent instead of borrowing a service color that would
// misleadingly imply it's specific to that one category. Same treatment the flow trunk/branches
// above use for their own non-categorized steps.
const FEATURE_ACCENT = [
  "var(--service-streetlight)",
  "var(--status-progress)",
  "var(--service-waste)",
  "var(--service-roads)",
  "var(--accent-fg)",
];
const FEATURE_ACCENT_BG = [
  "var(--service-streetlight-bg)",
  "var(--status-progress-bg)",
  "var(--service-waste-bg)",
  "var(--service-roads-bg)",
  "color-mix(in srgb, var(--accent-fg) 16%, transparent)",
];

// Must match .flow-line-glow's keyframes/duration in Landing.css exactly (flow-glow-travel,
// top: 2% -> 98% over 6000ms linear) -- these drive the node-glow delay math below, which needs
// to reason about the same timeline the CSS animation is actually running.
const FLOW_GLOW_DURATION_MS = 6000;
const FLOW_GLOW_START_PCT = 2;
const FLOW_GLOW_END_PCT = 98;
// LIVE-REPORTED: the node's own flash (.flow-node's flow-node-glow keyframe, Landing.css) peaks
// at 93% of ITS OWN cycle, not at 0% -- so setting animation-delay to the exact moment the
// traveling light reaches a node made that node's flash fire almost a full 6s late (delay + 93%
// of another cycle later), completely out of sync with the light passing it. Subtracting that
// same 93%-of-cycle offset here makes the delay land 93% of a cycle EARLIER instead (a negative
// animation-delay is valid CSS -- it just starts the animation already partway through), so the
// node's peak now actually coincides with the light reaching it.
const FLOW_NODE_GLOW_PEAK_PCT = 93;

export default function Landing() {
  const { lang } = useUiLang();
  const featureKeys = ["languages", "voice", "photo", "tracking", "qa"] as const;
  const stepKeys = ["intro", "speak", "translate", "confirm", "resolve", "reassign", "photo", "rate"] as const;

  // Row heights are content-driven (card text length varies), so the nodes are NOT evenly
  // spaced along the line -- a naive "1/N of the duration apart" delay visibly desyncs from
  // where the traveling glow actually is. Measuring each node's real position and converting it
  // to a point on the same 2%-98%/6000ms timeline keeps the flash locked to the light itself,
  // regardless of how any given card happens to wrap at any width or in any language.
  const flowGridRef = useRef<HTMLDivElement | null>(null);

  // LIVE-REPORTED (round 2): setting a CSS custom property to drive animation-delay only ever
  // reliably synced the FIRST paint -- every LATER recompute (a web font finishing load and
  // reflowing text is a real, common trigger no one was clicking or resizing anything for) hit
  // the exact same "browsers don't dependably resync an already-playing animation's phase just
  // because animation-delay's computed value changed" problem again, just later -- which is
  // exactly the "sometimes it matches, sometimes a different circle glows" this was live-reported
  // as. Fixed properly this time: instead of hoping a delay value change resyncs a CSS animation
  // that's already running, directly read the traveling light's own live Animation.currentTime
  // (the Web Animations API -- a real clock, not a hopeful CSS mutation) and use it to compute +
  // directly SET each node's own Animation.currentTime to match, every single time this runs --
  // deterministic and self-correcting on every call, not just the first.
  useLayoutEffect(() => {
    const container = flowGridRef.current;
    if (!container) return;

    const resync = () => {
      const containerRect = container.getBoundingClientRect();
      if (containerRect.height === 0) return;
      const lineGlowAnim = container.querySelector(".flow-line-glow")?.getAnimations()[0];
      if (!lineGlowAnim || typeof lineGlowAnim.currentTime !== "number") return;
      const lineNowMs = lineGlowAnim.currentTime % FLOW_GLOW_DURATION_MS;
      const peakMs = (FLOW_NODE_GLOW_PEAK_PCT / 100) * FLOW_GLOW_DURATION_MS;

      container.querySelectorAll<HTMLElement>(".flow-node-col").forEach((col) => {
        const nodeAnim = col.querySelector(".flow-node")?.getAnimations()[0];
        if (!nodeAnim) return;
        const r = col.getBoundingClientRect();
        const centerY = r.top + r.height / 2 - containerRect.top;
        const pct = Math.min(FLOW_GLOW_END_PCT, Math.max(FLOW_GLOW_START_PCT, (centerY / containerRect.height) * 100));
        const arrivesAtMs = ((pct - FLOW_GLOW_START_PCT) / (FLOW_GLOW_END_PCT - FLOW_GLOW_START_PCT)) * FLOW_GLOW_DURATION_MS;
        // How long from right now until the light actually reaches this node, on the light's own
        // real clock (mod duration so it's always a positive forward-looking gap, never negative).
        const msUntilArrival = (((arrivesAtMs - lineNowMs) % FLOW_GLOW_DURATION_MS) + FLOW_GLOW_DURATION_MS) % FLOW_GLOW_DURATION_MS;
        // Set this node's OWN clock so its 93%-of-cycle peak lands exactly msUntilArrival from now.
        nodeAnim.currentTime = (((peakMs - msUntilArrival) % FLOW_GLOW_DURATION_MS) + FLOW_GLOW_DURATION_MS) % FLOW_GLOW_DURATION_MS;
      });
    };

    resync();
    const ro = new ResizeObserver(resync);
    ro.observe(container);
    window.addEventListener("resize", resync);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", resync);
    };
    // Re-sync on language change too: text length (and so row height) changes with it, and this
    // is a clearer, more explicit trigger than relying solely on ResizeObserver to catch every
    // reflow cause.
  }, [lang]);

  return (
    <div>
      <nav className="landingnav">
        <div className="landingnav-inner">
          <div className="landing-brand">
            <img src="/brand/logo-mark.png" alt="JanSarthi AI" className="landing-seal" />
            <div className="landing-brand-word display">JanSarthi AI</div>
          </div>
          <div className="navactions">
            <Link to="/" className="lang-badge">
              {SUPPORTED_LANGUAGES[lang].name}
            </Link>
            <ThemeToggle className="theme-toggle" />
            <Link to="/login" className="btn btn-ghost btn-sm">
              {t(lang, "landing.login")}
            </Link>
            <Link to="/signup" className="btn btn-primary btn-sm">
              {t(lang, "landing.signup")}
            </Link>
          </div>
        </div>
      </nav>

      <div className="hero-band">
        <div className="hero-band-bg" aria-hidden="true" />
        <div className="hero enter">
        <div className="hero-copy">
          <div className="eyebrow">{t(lang, "topbar.subtitle")}</div>
          <h1 className="display">{t(lang, "hero.headline")}</h1>
          <p className="lede">{t(lang, "hero.sub")}</p>
          <div className="hero-ctas">
            <Link to="/signup" className="btn btn-primary">
              {t(lang, "hero.cta.primary")}
            </Link>
            <Link to="/login" className="btn btn-ghost">
              {t(lang, "hero.cta.secondary")}
            </Link>
          </div>
        </div>
        <div className="hero-visual">
          <div className="hero-visual-halo" aria-hidden="true" />
          <svg viewBox="0 0 380 300" width="100%" style={{ display: "block", height: "auto" }}>
            <defs>
              {/* The same blue-to-green "AI flow" language used elsewhere on the site
                  (--ai-from/--ai-to) -- here it doubles as a literal cue: blue at the mic
                  phone's end, green at the resolved phone's end. userSpaceOnUse so both the
                  dashed line and the arrowhead share one continuous gradient instead of each
                  re-deriving its own blue-to-green blend across its own small bounding box. */}
              <linearGradient id="hero-arrow-gradient" gradientUnits="userSpaceOnUse" x1="138" y1="130" x2="236" y2="130">
                <stop offset="0%" stopColor="var(--ai-from)" />
                <stop offset="100%" stopColor="var(--ai-to)" />
              </linearGradient>
              {/* Reversed: green at the resolved phone's end, blue at the mic phone's end --
                  the loop-back arrow below reads right-to-left, so its gradient should too. */}
              <linearGradient id="hero-arrow-gradient-reverse" gradientUnits="userSpaceOnUse" x1="234" y1="210" x2="138" y2="210">
                <stop offset="0%" stopColor="var(--ai-to)" />
                <stop offset="100%" stopColor="var(--ai-from)" />
              </linearGradient>
            </defs>
            <rect x="18" y="40" width="110" height="220" rx="20" fill="var(--surface)" stroke="var(--line)" strokeWidth="2" />
            {/* Inner "screen" rect had no stroke at all, so its edge only ever showed up as a
                faint fill difference against the outer phone body -- give it a real, visible
                bezel to match. Same fix mirrored on the second phone below. */}
            <rect x="34" y="64" width="78" height="150" rx="8" fill="var(--paper)" stroke="var(--line)" strokeWidth="1.5" />
            {/* Two ways in: voice (mic) and text (chat bubble) side by side, pulsing in
                alternation (chat's animation-delay is exactly half the shared period) so the
                two visibly trade emphasis back and forth rather than both breathing in sync. */}
            {/* A CSS animation targeting `transform` fully replaces an SVG `transform` attribute
                on the same element rather than composing with it -- so this group's coordinates
                are hand-scaled/repositioned directly (not via a wrapping transform=""), or the
                mic-breathe animation below would silently discard the repositioning. */}
            <g className="hero-mic-pulse" style={{ transformOrigin: "54px 112px" }}>
              <circle cx="54" cy="112" r="15.84" fill="var(--status-progress-bg)" stroke="var(--status-progress)" strokeWidth="1.44" />
              <path d="M54 104.8a5.76 5.76 0 0 1 5.76 5.76v4.32a5.76 5.76 0 0 1-11.52 0v-4.32a5.76 5.76 0 0 1 5.76-5.76Z" fill="var(--status-progress)" />
              <path d="M46.08 116.32a7.92 7.92 0 0 0 15.84 0M54 124.24v5.04" stroke="var(--status-progress)" strokeWidth="1.58" fill="none" strokeLinecap="round" />
            </g>
            <g className="hero-chat-pulse" style={{ transformOrigin: "92px 109px" }}>
              <circle cx="92" cy="109" r="17" fill="var(--status-progress-bg)" stroke="var(--status-progress)" strokeWidth="2" />
              <path d="M81 101h22a6 6 0 0 1 6 6v4a6 6 0 0 1-6 6h-14l-8 7v-7h0a6 6 0 0 1-6-6v-4a6 6 0 0 1 6-6Z" fill="none" stroke="var(--status-progress)" strokeWidth="1.7" strokeLinejoin="round" />
              <circle cx="88" cy="109" r="1.3" fill="var(--status-progress)" />
              <circle cx="93" cy="109" r="1.3" fill="var(--status-progress)" />
              <circle cx="98" cy="109" r="1.3" fill="var(--status-progress)" />
            </g>
            {/* First line recolored blue (matching this phone's mic/chat accent) so it mirrors
                the second phone's colored-highlight-plus-line pattern instead of reading as two
                plain gray lines with nothing tying it back to this phone's own icons. */}
            <rect x="50" y="147" width="46" height="7" rx="3.5" fill="var(--status-progress)" opacity="0.85" />
            <rect x="58" y="160" width="30" height="7" rx="3.5" fill="var(--line)" />
            <rect x="252" y="40" width="110" height="220" rx="20" fill="var(--surface)" stroke="var(--line)" strokeWidth="2" />
            <rect x="268" y="64" width="78" height="150" rx="8" fill="var(--paper)" stroke="var(--line)" strokeWidth="1.5" />
            <rect x="280" y="86" width="54" height="8" rx="4" fill="var(--status-resolved)" opacity="0.85" />
            <rect x="280" y="102" width="40" height="8" rx="4" fill="var(--line)" />
            <rect x="280" y="118" width="48" height="8" rx="4" fill="var(--line)" />
            <g className="hero-check-pop" style={{ transformOrigin: "307px 160px" }}>
              <circle cx="307" cy="160" r="16" fill="var(--status-resolved-bg)" stroke="var(--status-resolved)" strokeWidth="2" />
              <path d="M300 160l5 5 9-10" stroke="var(--status-resolved)" strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </g>
            <path className="hero-flow-arrow" d="M138 130c34-6 58-6 96 0" stroke="url(#hero-arrow-gradient)" strokeWidth="2.5" fill="none" strokeDasharray="1 8" strokeLinecap="round" />
            <path d="M222 122l14 8-14 8" stroke="url(#hero-arrow-gradient)" strokeWidth="2.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            {/* The loop back: reassignment (if a worker declines) and feedback both send things
                the other way, so the illustration shouldn't read as strictly one-directional.
                Kept visually secondary (lower opacity, thinner) so it doesn't compete with the
                primary submit-to-resolved story above it. */}
            <g opacity="0.55">
              <path className="hero-flow-arrow" d="M234 210c-34 6-58 6-96 0" stroke="url(#hero-arrow-gradient-reverse)" strokeWidth="2" fill="none" strokeDasharray="1 8" strokeLinecap="round" />
              <path d="M152 202l-14 8 14 8" stroke="url(#hero-arrow-gradient-reverse)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </g>
          </svg>
        </div>
        </div>
      </div>

      <div className="landing-glow-region">
        <div className="landing-glow-region-bg" aria-hidden="true" />

      <section className="landing-section">
        <Reveal as="div" className="section-heading">
          <div className="eyebrow">{t(lang, "landing.howItWorks.eyebrow")}</div>
          <h2 className="display">{t(lang, "landing.howItWorks.title")}</h2>
        </Reveal>

        <div className="flow-grid" ref={flowGridRef}>
          <div className="flow-line" aria-hidden="true" />
          <div className="flow-line-glow" aria-hidden="true" />
          {stepKeys.map((key, i) => (
            <Reveal
              key={key}
              stagger={i}
              className={`flow-card surface-card ${i % 2 === 0 ? "flow-card-left" : "flow-card-right"}`}
              style={{ "--node-row": i + 1, "--node-color": STEP_ACCENT[i], "--node-bg": STEP_ACCENT_BG[i] } as CSSProperties}
            >
              <span className="flow-step-badge mono">{String(i + 1).padStart(2, "0")}</span>
              <h3>{t(lang, `landing.howItWorks.${key}.title`)}</h3>
              <p>{t(lang, `landing.howItWorks.${key}.body`)}</p>
            </Reveal>
          ))}
          {stepKeys.map((key, i) => (
            <div
              key={`node-${key}`}
              className="flow-node-col"
              style={{
                "--node-row": i + 1,
                "--node-color": STEP_ACCENT[i],
                "--node-bg": STEP_ACCENT_BG[i],
              } as CSSProperties}
              aria-hidden="true"
            >
              <div className="flow-node">{STEP_ICONS[i]}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section landing-section-alt">
        <Reveal as="div" className="section-heading">
          <div className="eyebrow">{t(lang, "landing.features.eyebrow")}</div>
          <h2 className="display">{t(lang, "landing.features.title")}</h2>
        </Reveal>
        <div className="features-grid">
          {featureKeys.map((key, i) => (
            <Reveal
              key={key}
              stagger={i}
              className="feature-card surface-card hoverable"
              style={{ "--feature-color": FEATURE_ACCENT[i], "--feature-bg": FEATURE_ACCENT_BG[i] } as CSSProperties}
            >
              <div className="feature-icon">{FEATURE_ICONS[i]}</div>
              <h3>{t(lang, `landing.features.${key}.title`)}</h3>
              <p>{t(lang, `landing.features.${key}.body`)}</p>
            </Reveal>
          ))}
        </div>
      </section>

      <Reveal as="section" className="landing-cta">
        <div className="landing-cta-content">
          <h2 className="display">{t(lang, "landing.finalCta.title")}</h2>
          <p>{t(lang, "landing.finalCta.body")}</p>
          <Link to="/signup" className="btn btn-primary landing-cta-btn">
            {t(lang, "hero.cta.primary")}
          </Link>
        </div>
      </Reveal>

      <footer className="landing-footer">
        <div className="landing-brand">
          <img src="/brand/logo-mark.png" alt="JanSarthi AI" className="landing-seal landing-seal-sm" />
          <div className="landing-brand-word display">JanSarthi AI</div>
        </div>
        <p>{t(lang, "landing.footer.tagline")}</p>
      </footer>
      </div>
    </div>
  );
}
