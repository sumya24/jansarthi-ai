import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { AskSarthiContent, type AskSarthiHandle } from "../pages/AskSarthi";
import Mascot from "./Mascot";
import "./AskSarthiWidget.css";

/** Floating entry point for Ask Sarthi -- replaces the old nav tab with the common
 * floating-chat-widget pattern: a round button in the corner that periodically shows
 * a small greeting bubble, and opens a slide-out panel instead of navigating away. The panel
 * renders the exact same `AskSarthiContent` the standalone /citizen/ask page uses, so both
 * entry points share one real implementation.
 *
 * Hidden on /citizen/ask itself -- opening this panel while already on the dedicated page for
 * the same thing would just be redundant. */
export default function AskSarthiWidget() {
  const { lang } = useUiLang();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [showGreeting, setShowGreeting] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const contentRef = useRef<AskSarthiHandle>(null);

  // Periodic nudge: show the greeting bubble a little after load, then again every so often --
  // but only until the widget's actually been used once this session, so it doesn't keep
  // pestering someone who already knows what it does.
  useEffect(() => {
    if (everOpened) return;
    const initial = setTimeout(() => setShowGreeting(true), 1600);
    const repeat = setInterval(() => setShowGreeting(true), 9000);
    return () => {
      clearTimeout(initial);
      clearInterval(repeat);
    };
  }, [everOpened]);

  useEffect(() => {
    if (!showGreeting) return;
    const hide = setTimeout(() => setShowGreeting(false), 4000);
    return () => clearTimeout(hide);
  }, [showGreeting]);

  // The anchor is position: fixed in the bottom-right corner of the viewport, so the greeting
  // bubble renders on top of whatever real page content happens to be sitting there -- on a
  // long page (e.g. a resolved complaint's Resolution Report card) that's often actual text the
  // citizen is trying to read, not empty background, and the bubble's opaque surface genuinely
  // hides it underneath for the ~4s it's up. Rather than only firing near load (still possible to
  // scroll down within 1.6s) or shrinking the bubble (it's already a compact 240px max-width),
  // suppress it once the page itself has scrolled any real distance -- by then the citizen is
  // reading something, not looking at an empty first screen, and that's exactly when covering
  // content is the wrong tradeoff for an unprompted nudge.
  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 120);
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Close on Escape, same as any other overlay/drawer.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (location.pathname.startsWith("/citizen/ask")) return null;

  function handleToggle() {
    setOpen((prev) => !prev);
    setEverOpened(true);
    setShowGreeting(false);
  }

  return (
    <>
      <div className={`ask-widget-backdrop ${open ? "open" : ""}`} onClick={() => setOpen(false)} aria-hidden="true" />

      <div className={`ask-widget-panel ${open ? "open" : ""}`} role="dialog" aria-modal="true" aria-label={t(lang, "nav.askJanmitra")}>
        <div className="ask-widget-panel-head">
          {/* Same row as the close button, not its own stacked bar underneath -- see
              AskSarthiHandle/hideNewChatBar's docstring in AskSarthi.tsx for the "two
              separate corner toolbars" problem this replaces. Always shown once the panel's ever
              been opened, even on an empty conversation -- clicking it then is just a harmless
              no-op, and a control that doesn't pop in/out as messages arrive is more predictable
              in a compact panel than one that does. */}
          {everOpened && (
            <button type="button" className="ask-widget-newchat-btn" onClick={() => contentRef.current?.newChat()}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              {t(lang, "ask.newChat")}
            </button>
          )}
          <button type="button" className="ask-widget-close" onClick={() => setOpen(false)} aria-label={t(lang, "common.close")}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        {/* Stays mounted once opened (rather than unmounting on close) so it doesn't visibly
            pop out from under the still-sliding panel -- it's just off-screen via the panel's
            own transform when closed. hideNewChatBar=true: this panel's head row above already
            carries New Chat, so the content itself renders no second, internal one. */}
        {everOpened && <AskSarthiContent ref={contentRef} hideNewChatBar />}
      </div>

      {/* Hidden while the panel is open, not just visually behind it -- its z-index (96) sits
          above the panel's (95) so the "Complaint submitted"-style toast layer can still surface
          over an open panel, which put the FAB's own hit area above the panel's bottom-right
          corner too. The chat composer's send button now lives in exactly that corner (a fixed
          persistent composer, unlike the old form layout), so leaving the FAB visible+clickable
          there blocked it. Standard behavior for a launcher button once its own panel is open. */}
      <div className={`ask-widget-anchor${open ? " ask-widget-anchor-hidden" : ""}`}>
        <div className={`ask-widget-greeting ${showGreeting && !open && !scrolled ? "visible" : ""}`}>
          <Mascot state="greeting" size={20} />
          {t(lang, "ask.widget.greeting")}
        </div>
        {/* Always idle here -- opening the panel isn't the same thing as the mic actively
            recording (see Mascot.tsx's docstring); "listening" only ever appears inside
            AskSarthiContent, driven by useSpeechToText's real status. */}
        <button type="button" className="ask-widget-fab" onClick={handleToggle} aria-label={t(lang, "nav.askJanmitra")} aria-haspopup="dialog" aria-expanded={open}>
          <Mascot state="idle" size={58} />
        </button>
      </div>
    </>
  );
}
