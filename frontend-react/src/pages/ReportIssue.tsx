import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams, Link } from "react-router-dom";
import TopBar from "../components/TopBar";
import MicWaveform from "../components/MicWaveform";
import MultiPhotoUpload from "../components/MultiPhotoUpload";
import LocationPicker, { type LocationValue } from "../components/LocationPicker";
import { useAuth } from "../lib/auth";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api, ApiError, type Complaint } from "../lib/api";
import { useAudioRecorder } from "../lib/useAudioRecorder";
import { useSpeechToText } from "../lib/useSpeechToText";
import { useToast } from "../lib/toast";
import { SERVICE_CATEGORY_DEFS, guessServiceCategory, type ServiceCategoryDef } from "../lib/serviceCategories";
import type { ServiceCategory } from "../lib/ragTypes";
import { filterWardsToOwnCity, findWardMatch } from "../lib/wardFilter";

type Step = "location" | "description" | "media" | "ai" | "preview";
const STEPS: Step[] = ["location", "description", "media", "ai", "preview"];

/**
 * Smart Complaint Creation wizard (P0, Task 2). Location -> Description -> Voice/Photo ->
 * AI Understanding -> Preview/Confirmation -> Submit -> Success.
 *
 * "AI Understanding" runs a real 3-layer category classification, in order, each one only
 * consulted if the one before it couldn't confidently answer: (1) a real Sarvam model call
 * (POST /complaints/classify-category, see backend/services/complaint_category_service.py),
 * (2) a client-side keyword match (lib/serviceCategories.ts#guessServiceCategory) if the model
 * layer isn't configured, fails, times out, or is itself unsure, and (3) the citizen picking a
 * category themselves in the dropdown below if neither of the first two found anything -- civic
 * complaints say too many different things in too many different ways for any fixed classifier
 * to promise full coverage, so a human always has the final say. See classifyIntoStep() below
 * for exactly how the three chain together, and categorySource for which one actually produced
 * the current pick (purely for the "AI-suggested" vs "best guess from keywords" badge text --
 * never sent to the backend). Every other piece of data in this flow — ward, description,
 * photo, and the complaint returned after submit — is real, sent through the existing
 * api.createComplaint exactly as CitizenDashboard already did before this wizard replaced its
 * inline form.
 */
export default function ReportIssue() {
  const { token, user } = useAuth();
  const { lang } = useUiLang();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const recorder = useAudioRecorder();
  const speech = useSpeechToText(lang);

  const preselected = params.get("service") as ServiceCategory | null;

  const [step, setStep] = useState<Step>("location");
  const [location, setLocation] = useState<LocationValue>({ ward: "", coords: null, locality: "" });
  const [wards, setWards] = useState<string[]>([]);
  const [text, setText] = useState("");
  // True once the citizen has typed over what the mic filled in -- at that point their own
  // correction is what should be submitted, not a fresh server-side transcription of the raw
  // audio that produced it. Set only by the textarea's own onChange (a real keystroke), never
  // by the live-transcript sync effect below, so it's a reliable "a human actually edited this"
  // signal, same idea as AskJanMitra.tsx's questionFromVoice.
  const [textEditedManually, setTextEditedManually] = useState(false);
  const [photos, setPhotos] = useState<File[]>([]);
  const [category, setCategory] = useState<ServiceCategoryDef | null>(
    SERVICE_CATEGORY_DEFS.find((d) => d.id === preselected) ?? null
  );
  // Which of the wizard's 3 fallback layers actually produced `category` -- null for a
  // preselected category (skipped classification entirely) or when nothing matched at all
  // (manual picker is the only option left). Purely for the "AI-suggested" vs "best guess from
  // keywords" badge text below; never sent to the backend.
  const [categorySource, setCategorySource] = useState<"ai" | "keyword" | null>(null);
  const [aiRunning, setAiRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<Complaint | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .listWards(token)
      .then((all) => {
        // Scoped to the citizen's own city (see lib/wardFilter.ts) -- with 18 cities now real
        // (see the location-expansion work), the old flat list had grown to 50+ wards nationwide,
        // making a citizen's own city genuinely hard to find. An unknown/unparseable city falls
        // back to the full list unchanged; a KNOWN city with nothing under it yet correctly comes
        // back empty, which LocationPicker.tsx's own existing fallback turns into a plain
        // free-text box instead of a misleading list of unrelated cities' wards.
        const scoped = filterWardsToOwnCity(all, user?.ward);
        setWards(scoped);
        // LIVE-REPORTED BUG: pre-filling from `user.ward` directly (before this list has even
        // loaded, or without checking it's actually one of the real options) let a native
        // <select> silently default to showing its FIRST option whenever the citizen's own
        // registered ward wasn't one of the real choices here -- a citizen whose profile ward has
        // since changed to somewhere with no worker yet (a real, current case, not hypothetical)
        // saw a WRONG ward pre-selected, one they never picked, with no visual hint anything was
        // off. Only pre-fill once this real list has loaded AND findWardMatch has confirmed it
        // actually contains the citizen's ward -- a verified match, not a guess -- so an unmatched
        // profile ward correctly leaves the picker on its own placeholder instead of a misleading
        // default.
        //
        // Uses findWardMatch (city-aware, via the same prefix logic filterWardsToOwnCity uses to
        // scope this list), not a plain `scoped.includes(user.ward)`: a citizen whose registered
        // city came from the Settings cascade (a district name, e.g. "...Bengaluru Urban") already
        // gets correctly SCOPED to their city's real wards (all suffixed with the ULB name, e.g.
        // "...Bengaluru") by filterWardsToOwnCity's own prefix matching -- but an exact-string
        // `.includes()` check here would then fail to find their own ward in that (correctly
        // scoped) list, leaving the field blank even though their real ward is right there under a
        // differently-suffixed city name. findWardMatch returns the scoped list's own exact string
        // (the only value a <select>'s <option> actually holds), never `user.ward` itself.
        const match = findWardMatch(scoped, user?.ward);
        if (match) {
          setLocation((prev) => (prev.ward ? prev : { ...prev, ward: match }));
        }
      })
      .catch(() => setWards([]));
  }, [token, user?.ward]);

  const descriptionTextareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = descriptionTextareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [text]);

  // Fills the same box a typed description would use, live, exactly like Ask Sarthi's own mic --
  // see AskJanMitra.tsx's identical effect. Goes through setText directly rather than the
  // textarea's onChange, so a real keystroke (which does go through onChange) is the only thing
  // that ever sets textEditedManually.
  useEffect(() => {
    if (recorder.isRecording && speech.transcript) {
      setText(speech.transcript);
    }
  }, [speech.transcript, recorder.isRecording]);

  const stepIndex = STEPS.indexOf(step);

  // Entering the AI step: real model classification first, falling back to the client-side
  // keyword match if the model isn't confident (missing key, network failure, timeout, or a
  // genuine "I don't know" -- see backend/services/complaint_category_service.py), and finally
  // to the manual picker below if neither layer found anything. A category already set (from
  // the service-card preselection, or a prior visit to this step) is never re-classified over.
  // `text` covers voice input too now (the live transcript, or a manual correction of it) -- if
  // recognition wasn't available/didn't catch anything, text stays empty and both layers are
  // skipped, going straight to the manual picker same as always.
  async function classifyIntoStep() {
    if (category) {
      setAiRunning(false);
      return;
    }

    // A floor under the loading state so a fast/cached response never flashes the spinner for
    // an instant and reads as broken -- raced against the real classification work below, not
    // stacked after it, so a genuinely slow model call never waits any LONGER than it already is.
    const minDelay = new Promise((resolve) => window.setTimeout(resolve, 500));

    let resolved: ServiceCategoryDef | null = null;
    let source: "ai" | "keyword" | null = null;

    if (text.trim() && token) {
      try {
        const { category: aiCategory } = await api.classifyComplaintCategory(token, text.trim());
        if (aiCategory) {
          const match = SERVICE_CATEGORY_DEFS.find((d) => d.id === aiCategory);
          if (match) {
            resolved = match;
            source = "ai";
          }
        }
      } catch {
        // Best-effort -- falls through to the keyword layer below exactly as if this call had
        // never been made.
      }
    }

    if (!resolved) {
      const guessed = guessServiceCategory(text);
      if (guessed) {
        resolved = guessed;
        source = "keyword";
      }
    }

    await minDelay;
    setCategory(resolved);
    setCategorySource(source);
    setAiRunning(false);
  }

  function goNext() {
    setError(null);
    if (step === "location" && wards.length > 0 && !location.ward) {
      setError(t(lang, "citizen.errNoWard"));
      return;
    }
    if (step === "description" && !text.trim() && recorder.audioSegments.length === 0) {
      setError(t(lang, "citizen.errNoText"));
      return;
    }
    if (step === "media") {
      setStep("ai");
      setAiRunning(true);
      classifyIntoStep();
      return;
    }
    const next = STEPS[stepIndex + 1];
    if (next) setStep(next);
  }

  function goBack() {
    setError(null);
    const prev = STEPS[stepIndex - 1];
    if (prev) setStep(prev);
  }

  async function handleSubmit() {
    if (!token) return;
    setSubmitting(true);
    setError(null);
    const form = new FormData();
    form.append("language", lang);
    // A real recorded take, not manually corrected afterward, goes through the more accurate
    // server-side transcription (see complaint_agent.py) instead of the browser's own live
    // preview. Any other case -- typed from the start, or the live transcript got edited -- the
    // box's own text is what the citizen actually meant to say, so that's what's sent.
    if (recorder.audioSegments.length > 0 && !textEditedManually) {
      recorder.audioSegments.forEach((segment, i) => {
        const extension = segment.type.includes("mp4") ? "m4a" : segment.type.includes("ogg") ? "ogg" : "webm";
        form.append("audio", segment, `complaint_part${i + 1}.${extension}`);
      });
    } else {
      form.append("text", text.trim());
    }
    // LIVE-REPORTED GAP: `category` here is already the wizard's own resolved classification
    // (real model -> keyword match -> manual picker, see this file's own module docstring) -- it
    // used to only ever drive the "AI Understanding" preview card, never actually get sent along
    // with the rest of the submission, so Complaint.service_category stayed unset for every
    // form-filed complaint. `category.id` is a real ServiceCategory value (see
    // lib/serviceCategories.ts), validated again server-side.
    if (category) form.append("category", category.id);
    if (location.ward.trim()) form.append("ward", location.ward.trim());
    // Maps to the backend's own `address` field -- see LocationValue.locality's own docstring
    // (LocationPicker.tsx) for why this exists alongside the ward pick. Sent whenever the citizen
    // typed anything, independent of whether GPS coords are also present below -- an explicit
    // typed address always takes priority over a GPS reverse-geocode (see create_complaint's own
    // "not complaint.address" guard before overwriting it from resolved coordinates).
    if (location.locality.trim()) form.append("address", location.locality.trim());
    // Sent whenever "use current location" succeeded, regardless of whether a ward was also
    // picked — the backend independently resolves/stores both (see routes/complaints.py).
    if (location.coords) {
      form.append("latitude", String(location.coords.lat));
      form.append("longitude", String(location.coords.lng));
      form.append("accuracy", String(location.coords.accuracy));
    }
    for (const photo of photos) form.append("photos", photo);

    try {
      const complaint = await api.createComplaint(token, form);
      setSubmitted(complaint);
      toast.success(t(lang, "citizen.submitSuccess"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t(lang, "citizen.errSubmitFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  function formatSeconds(total: number): string {
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  if (submitted) {
    return (
      <div>
        <TopBar />
        <div className="page" id="main-content">
          <div className="surface-card wizard-panel enter" style={{ textAlign: "center", padding: "40px 24px" }}>
            <div className="success-icon" style={{ margin: "0 auto 16px", width: 56, height: 56, borderRadius: "50%", background: "var(--status-resolved-bg)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <path d="M4 12.5 9.5 18 20 6" stroke="var(--status-resolved)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
            <h2 className="display" style={{ margin: "0 0 8px" }}>{t(lang, "wizard.success.title")}</h2>
            <p style={{ color: "var(--ink-2)", marginBottom: 6 }}>{t(lang, "wizard.success.body")}</p>
            <div className="mono" style={{ fontSize: 18, fontWeight: 700, margin: "16px 0 24px" }}>
              JM-{String(submitted.id).padStart(5, "0")}
            </div>
            <div style={{ display: "flex", gap: 10, justifyContent: "center", flexWrap: "wrap" }}>
              <Link to="/citizen/complaints" className="btn btn-primary">
                {t(lang, "wizard.success.track")}
              </Link>
              <Link to="/citizen" className="btn btn-ghost">
                {t(lang, "wizard.success.home")}
              </Link>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <TopBar />
      <div className="page" id="main-content">
        <div className="page-head">
          <div>
            <h1 className="page-title display">{t(lang, "wizard.title")}</h1>
            <p className="page-sub">{t(lang, "wizard.subtitle")}</p>
          </div>
        </div>

        <div className="wizard-steps" aria-hidden="true">
          {STEPS.map((s, i) => (
            <div key={s} className={`wizard-step-dot ${i < stepIndex ? "done" : i === stepIndex ? "active" : ""}`} />
          ))}
        </div>

        {error && <div className="banner-error">{error}</div>}

        <div className="surface-card wizard-panel enter">
          {step === "location" && (
            <>
              <h2>{t(lang, "wizard.location.title")}</h2>
              <p className="wizard-hint">{t(lang, "wizard.location.hint")}</p>
              <LocationPicker value={location} onChange={setLocation} wards={wards} />
            </>
          )}

          {step === "description" && (
            <>
              <h2>{t(lang, "wizard.description.title")}</h2>
              <p className="wizard-hint">{t(lang, "wizard.description.hint")}</p>

              <div className="ask-chat-composer" style={{ margin: 0, padding: 0, border: "none", background: "none" }}>
                {recorder.error && <p className="ask-chat-composer-error">{recorder.error}</p>}

                <div className="ask-chat-composer-row">
                  <label htmlFor="complaint-text" className="sr-only">{t(lang, "citizen.describe")}</label>
                  {/* One textarea, always editable -- exactly like Ask Sarthi's own bar. Voice
                      fills it live while recording (see the effect above); typing over that (or
                      typing from a blank box) is just as valid, no separate "switch to typing"
                      control needed. */}
                  <textarea
                    ref={descriptionTextareaRef}
                    id="complaint-text"
                    rows={1}
                    value={text}
                    onChange={(e) => {
                      setText(e.target.value);
                      setTextEditedManually(true);
                    }}
                    placeholder={t(lang, "citizen.textPlaceholder")}
                    className="ask-chat-textarea"
                  />

                  {recorder.isRecording && (
                    <span className="mono" style={{ fontSize: 12, color: "var(--status-critical)", flexShrink: 0 }}>
                      {formatSeconds(recorder.seconds)}
                    </span>
                  )}

                  <button
                    type="button"
                    className={`ask-chat-icon-btn ask-chat-mic1-btn${recorder.isRecording ? " active" : ""}`}
                    onClick={() => {
                      if (recorder.isRecording) {
                        recorder.stop();
                        speech.stop();
                        return;
                      }
                      recorder.start();
                      if (speech.supported) speech.start();
                    }}
                    aria-label={t(lang, recorder.isRecording ? "citizen.stopRecording" : "citizen.startRecording")}
                    aria-pressed={recorder.isRecording}
                    title={t(lang, recorder.isRecording ? "citizen.stopRecording" : "citizen.startRecording")}
                  >
                    {recorder.isRecording ? (
                      <MicWaveform />
                    ) : (
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                        <rect x="9" y="3" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.8" />
                        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                      </svg>
                    )}
                  </button>

                  {/* Same slot Ask Sarthi's own bar gives its "Mic 2" voice-assistant button --
                      a refresh here instead, clearing the current take/text and starting clean,
                      since a second voice-to-voice conversation opener doesn't belong on a
                      single-field wizard step. */}
                  <button
                    type="button"
                    className="ask-chat-icon-btn"
                    onClick={() => {
                      recorder.stop();
                      speech.stop();
                      recorder.reset();
                      speech.reset();
                      setText("");
                      setTextEditedManually(false);
                    }}
                    aria-label={t(lang, "citizen.recordAgain")}
                    title={t(lang, "citizen.recordAgain")}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                      <path d="M20 12a8 8 0 1 1-2.34-5.66" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                      <path d="M20 4v5h-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>

                  <button
                    type="button"
                    className="ask-chat-send-btn"
                    onClick={goNext}
                    disabled={aiRunning}
                    aria-label={t(lang, "wizard.next")}
                    title={t(lang, "wizard.next")}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                      <path d="M4 12h15M13 6l6 6-6 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </button>
                </div>
              </div>
            </>
          )}

          {step === "media" && (
            <>
              <h2>{t(lang, "wizard.media.title")}</h2>
              <p className="wizard-hint">{t(lang, "wizard.media.hint")}</p>
              <MultiPhotoUpload photos={photos} onChange={setPhotos} />
            </>
          )}

          {step === "ai" && (
            <>
              <h2>{t(lang, "wizard.ai.title")}</h2>
              <p className="wizard-hint">{t(lang, "wizard.ai.hint")}</p>
              {aiRunning ? (
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "20px 0", color: "var(--ink-2)" }}>
                  <span className="ask-chat-thinking-dots" aria-hidden="true">
                    <span />
                    <span />
                    <span />
                  </span>
                  {t(lang, "wizard.ai.analyzing")}
                </div>
              ) : (
                <div className="surface-card" style={{ padding: 16 }}>
                  {categorySource && (
                    <div className="dev-badge" style={{ marginBottom: 10 }}>
                      {t(lang, categorySource === "ai" ? "wizard.ai.aiBadge" : "wizard.ai.keywordBadge")}
                    </div>
                  )}
                  {category ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <div className={`service-card-icon service-tile-${category.color}`} style={{ marginBottom: 0 }}>{category.icon}</div>
                      <div>
                        <div style={{ fontWeight: 700 }}>{t(lang, category.titleKey)}</div>
                        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{t(lang, "wizard.ai.matchedNote")}</div>
                      </div>
                    </div>
                  ) : (
                    <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>{t(lang, "wizard.ai.noMatch")}</p>
                  )}
                  <label style={{ display: "block", marginTop: 14, fontSize: 12, fontWeight: 700, color: "var(--ink-2)" }}>{t(lang, "wizard.ai.changeLabel")}</label>
                  <select
                    value={category?.id ?? ""}
                    onChange={(e) => {
                      // A manual pick from here on out -- no longer attributable to the AI or
                      // keyword layer, so the badge above stops claiming either one.
                      setCategorySource(null);
                      setCategory(SERVICE_CATEGORY_DEFS.find((d) => d.id === e.target.value) ?? null);
                    }}
                    style={{ marginTop: 6 }}
                  >
                    <option value="">{t(lang, "wizard.ai.notSure")}</option>
                    {SERVICE_CATEGORY_DEFS.map((d) => (
                      <option key={d.id} value={d.id}>
                        {t(lang, d.titleKey)}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </>
          )}

          {step === "preview" && (
            <>
              <h2>{t(lang, "wizard.preview.title")}</h2>
              <p className="wizard-hint">{t(lang, "wizard.preview.hint")}</p>
              <dl style={{ margin: 0 }}>
                <div className="wizard-summary-row">
                  <dt>{t(lang, "wizard.preview.service")}</dt>
                  <dd>{category ? t(lang, category.titleKey) : t(lang, "wizard.ai.notSure")}</dd>
                </div>
                <div className="wizard-summary-row">
                  <dt>{t(lang, "wizard.preview.location")}</dt>
                  <dd>
                    {location.ward || location.locality.trim()
                      ? [location.ward, location.locality.trim()].filter(Boolean).join(" — ")
                      : t(lang, "wizard.preview.noLocation")}
                  </dd>
                </div>
                <div className="wizard-summary-row">
                  <dt>{t(lang, "wizard.preview.description")}</dt>
                  <dd>{text.trim() || (recorder.audioSegments.length > 0 ? t(lang, "citizen.voiceRecorded") : "—")}</dd>
                </div>
                <div className="wizard-summary-row">
                  <dt>{t(lang, "wizard.preview.photo")}</dt>
                  <dd>{photos.length > 0 ? photos.map((p) => p.name).join(", ") : t(lang, "wizard.preview.noPhoto")}</dd>
                </div>
                <div className="wizard-summary-row">
                  <dt>{t(lang, "wizard.preview.priority")}</dt>
                  <dd>{t(lang, "wizard.preview.priorityStandard")}</dd>
                </div>
              </dl>
            </>
          )}

          <div className="wizard-nav">
            <button type="button" className="btn btn-ghost" onClick={stepIndex === 0 ? () => navigate("/citizen") : goBack} disabled={submitting}>
              {stepIndex === 0 ? t(lang, "common.cancel") : t(lang, "wizard.back")}
            </button>
            {step === "preview" ? (
              <button type="button" className="btn btn-primary" onClick={handleSubmit} disabled={submitting}>
                {submitting ? t(lang, "citizen.submitting") : t(lang, "citizen.submit")}
              </button>
            ) : step === "description" ? null : (
              /* The description step's own composer bar has its own round arrow button that
                 does the same thing -- a second "Next" here would just be a redundant copy of
                 the same action, right below it. */
              <button type="button" className="btn btn-primary" onClick={goNext} disabled={aiRunning}>
                {t(lang, "wizard.next")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
