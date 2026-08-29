import { useNavigate } from "react-router-dom";
import { useUiLang } from "../lib/uiLang";
import { SUPPORTED_LANGUAGES, t, type LangCode } from "../lib/i18n";
import "./LanguageGate.css";

// Derived from SUPPORTED_LANGUAGES (the one place languages are defined) rather than a second,
// separately-maintained list — adding a language means editing i18n.ts only, nowhere else.
const OPTIONS = (Object.keys(SUPPORTED_LANGUAGES) as LangCode[]).map((code) => ({
  code,
  native: SUPPORTED_LANGUAGES[code].name,
  gloss: SUPPORTED_LANGUAGES[code].gloss,
}));

export default function LanguageGate() {
  const { lang, setLang } = useUiLang();
  const navigate = useNavigate();

  function choose(code: LangCode) {
    setLang(code);
    // replace: true -- a one-way gate, not a page worth returning to via Back.
    navigate("/welcome", { replace: true });
  }

  return (
    <div className="gate">
      <div className="gate-card">
        <img src="/brand/logo-mark.png" alt="JanSarthi AI" className="gate-seal" />
        <h1 className="gate-title display">
          {OPTIONS.map((opt) => (
            <span key={opt.code} className={`gate-title-line ${opt.code !== "en" ? "indic" : ""}`}>
              {t(opt.code, "gate.title")}
            </span>
          ))}
        </h1>
        <p className="gate-sub">{t(lang, "gate.changeAnytime")}</p>
        <div className="gate-options">
          {OPTIONS.map((opt) => (
            <button key={opt.code} className="gate-opt" onClick={() => choose(opt.code)}>
              <span className="gate-opt-label">
                <span className={`native ${opt.code !== "en" ? "indic" : ""}`}>{opt.native}</span>
                {opt.code !== "en" && <span className="gloss">{opt.gloss}</span>}
              </span>
              <span className="arrow">→</span>
            </button>
          ))}
        </div>
        <p className="gate-note">
          {t(lang, "gate.privacyNote")}
        </p>
      </div>
    </div>
  );
}
