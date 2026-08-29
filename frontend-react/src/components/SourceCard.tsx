import { t, type LangCode } from "../lib/i18n";
import { useUiLang } from "../lib/uiLang";
import type { GeographicScope, SourceRecord } from "../lib/ragTypes";

// LIVE-REPORTED BUG: `geographic_scope` is an internal categorical tag ("CITY"/"STATE"/...),
// never a place name -- it was rendered straight from the API response, so every non-English
// conversation still saw this one raw English word on the source card. `source_title`/
// `source_organization` are deliberately NOT run through this same treatment: those are real
// government document/body names (e.g. "Bruhat Bengaluru Mahanagara Palike (BBMP)"), which stay
// in their original form the same way an English-language citation wouldn't translate a proper
// noun either.
const SCOPE_LABEL_KEYS: Record<GeographicScope, string> = {
  NATIONAL: "ask.source.scope.national",
  STATE: "ask.source.scope.state",
  DISTRICT: "ask.source.scope.district",
  CITY: "ask.source.scope.city",
  MUNICIPALITY: "ask.source.scope.municipality",
  WARD: "ask.source.scope.ward",
};

/**
 * Renders one source backing an "Ask Sarthi" answer. Everything it shows comes from the
 * `source` prop — this component never hard-codes a government source or URL itself, so it
 * stays correct the moment the real RAG backend starts returning `SourceRecord`s (see
 * lib/ragTypes.ts and data/rag_knowledge_base/schema/source_record.schema.json, which this
 * prop shape mirrors exactly).
 *
 * verification_status drives the two presentations the spec calls for:
 *  - VERIFIED: labeled "Official source"; if source_url exists, a real outbound link to it.
 *  - SYNTHETIC: labeled "Synthetic / prototype data" — never a link, even if source_url were
 *    somehow set, because a synthetic record has no real page behind it to send someone to.
 * A missing URL on a verified source is handled gracefully (no broken/fake link rendered).
 *
 * `lang`: optional override for the labels on this card ("Official source", "View official
 * source →", ...) -- LIVE-REPORTED BUG: a source card rendered inside one specific Ask Sarthi
 * reply always used the account-wide UI toggle, not the language THAT reply actually came back
 * in (see i18n.ts's `toLangCode` docstring). Defaults to the toggle when omitted, so every other
 * caller of this component is unaffected.
 */
export default function SourceCard({ source, lang: langOverride }: { source: SourceRecord; lang?: LangCode }) {
  const { lang: uiLang } = useUiLang();
  const lang = langOverride ?? uiLang;
  const isVerified = source.verification_status === "VERIFIED";
  const hasLink = isVerified && !!source.source_url;

  return (
    <div className="source-card">
      <div className="source-card-head">
        <span className={`source-tag ${isVerified ? "verified" : "synthetic"}`}>
          {isVerified ? t(lang, "ask.source.official") : t(lang, "ask.source.synthetic")}
        </span>
        {source.geographic_scope && (
          <span className="source-scope">{t(lang, SCOPE_LABEL_KEYS[source.geographic_scope])}</span>
        )}
      </div>
      <div className="source-title">{source.source_title}</div>
      {source.source_organization && <div className="source-org">{source.source_organization}</div>}
      {hasLink && (
        <a href={source.source_url!} target="_blank" rel="noopener noreferrer" className="source-link">
          {t(lang, "ask.source.viewOfficial")}
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M7 17 17 7M9 7h8v8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </a>
      )}
      {!isVerified && <p className="source-note">{t(lang, "ask.source.syntheticNote")}</p>}
    </div>
  );
}
