import { useEffect, useRef, useState } from "react";
import { api, type LocationOption } from "../lib/api";
import { t, type LangCode } from "../lib/i18n";
import { localizeStateName } from "../lib/locationNames";

export interface HomeLocationValue {
  ward: string;
  home_state_id?: number;
  home_district_id?: number;
  home_ward_id?: number;
  home_locality_id?: number;
}

/** The single, cascading State -> City -> Ward -> Area location picker on Signup (see
 * backend/routes/locations.py) -- this IS the "Area / ward" field now (there used to be two
 * separate location sections on this form, a real, reported point of confusion; this replaces
 * both with one). `ward`, the value that still backs worker matching/"My Area"/everything that
 * already reads `User.ward` unchanged, is composed here from whichever levels were actually
 * resolved -- see composeWard() below -- using the exact "{ward} — {locality}, {city}" format
 * real seeded worker wards already use, so a citizen who picks a real Pune/Kanpur/etc. ward ends
 * up with a string that matches real workers, not an approximation.
 *
 * Real data only: the State dropdown itself only lists states we actually have data for on
 * either side (backend/routes/locations.py's _COVERED_STATE_CODES); everything below that is
 * real data too, so any step whose parent has no children falls back to free text rather than
 * inventing options -- and once ANY level is free text, every level below it is free text too
 * (there's no structured id left to query children from), never disabled.
 *
 * A citizen whose state isn't in the list picks "Other / not listed" instead, which behaves like
 * every other free-text fallback here: city/ward/area unlock immediately as text fields (no
 * fetch to wait on, since there's no state id to query children from). Unlike a real state pick,
 * home_state_id has nowhere to point in this case (it's a strict FK, see models.py), so it's left
 * undefined and the typed state name rides along in the `ward` string instead -- see
 * composeWard() -- rather than being silently dropped.
 *
 * All 4 rows are always mounted (never conditionally added/removed) -- a step that isn't
 * reachable yet is shown disabled/greyed out rather than hidden, so the box's height never grows
 * as the citizen picks each level (a second real, reported layout-shift bug this also fixes). */
const OTHER_STATE = "other";
export default function HomeLocationPicker({
  lang, onChange, hasError, initial,
}: {
  lang: LangCode; onChange: (value: HomeLocationValue) => void; hasError?: boolean;
  // Pre-fills the cascade from an EXISTING selection (editing a citizen's saved location in
  // Settings) instead of always starting blank (signup, where there's nothing to pre-fill yet).
  // Only the deepest id needs to be given, same convention `onChange` itself already uses --
  // each shallower level's dropdown is fetched and set automatically as the chain below it
  // resolves. Read once, on mount, via useRef below -- deliberately NOT reactive to `initial`
  // changing later (e.g. a parent re-render after its own unrelated state update), since that
  // would silently stomp on whatever the citizen has already picked mid-edit.
  initial?: { stateId?: number; districtId?: number; wardId?: number; localityId?: number };
}) {
  const [states, setStates] = useState<LocationOption[]>([]);
  const [stateId, setStateId] = useState<number | "">("");
  const [statesLoaded, setStatesLoaded] = useState(false);
  const [stateOther, setStateOther] = useState(false);
  const [stateText, setStateText] = useState("");

  const [cities, setCities] = useState<LocationOption[]>([]);
  const [cityId, setCityId] = useState<number | "">("");
  const [cityText, setCityText] = useState("");
  const [citiesLoaded, setCitiesLoaded] = useState(false);

  const [wardOptions, setWardOptions] = useState<LocationOption[]>([]);
  const [wardId, setWardId] = useState<number | "">("");
  const [wardText, setWardText] = useState("");
  const [wardsLoaded, setWardsLoaded] = useState(false);

  const [localities, setLocalities] = useState<LocationOption[]>([]);
  const [localityId, setLocalityId] = useState<number | "">("");
  const [localityText, setLocalityText] = useState("");
  const [localitiesLoaded, setLocalitiesLoaded] = useState(false);

  // Captured once, on mount -- see the `initial` prop's own comment above for why this
  // deliberately doesn't track later prop changes.
  const initialRef = useRef(initial).current;

  useEffect(() => {
    api.listStates().then((s) => { setStates(s); setStatesLoaded(true); }).catch(() => setStatesLoaded(true));
  }, []);

  // Pre-fill cascade: each stage below fires once its own prerequisite data has loaded, and in
  // turn kicks off the fetch the next stage down needs -- the exact same one-level-at-a-time
  // fetch chain a real citizen clicking through the dropdowns would trigger, just driven by
  // `initialRef` instead of onChange handlers. Guards on `stateId === ""` etc. so this never
  // fires again after the citizen (or an earlier run of this same effect) has already set that
  // level -- each stage runs at most once.
  useEffect(() => {
    if (!statesLoaded || !initialRef?.stateId || stateId !== "") return;
    if (!states.some((s) => s.id === initialRef.stateId)) return; // state no longer exists/listed
    setStateId(initialRef.stateId);
    api.listCitiesForState(initialRef.stateId).then((c) => { setCities(c); setCitiesLoaded(true); }).catch(() => setCitiesLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statesLoaded, states]);

  useEffect(() => {
    if (!citiesLoaded || !initialRef?.districtId || cityId !== "") return;
    if (!cities.some((c) => c.id === initialRef.districtId)) return;
    setCityId(initialRef.districtId);
    api.listWardsForCity(initialRef.districtId).then((w) => { setWardOptions(w); setWardsLoaded(true); }).catch(() => setWardsLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citiesLoaded, cities]);

  useEffect(() => {
    if (!wardsLoaded || !initialRef?.wardId || wardId !== "") return;
    if (!wardOptions.some((w) => w.id === initialRef.wardId)) return;
    setWardId(initialRef.wardId);
    api.listLocalitiesForWard(initialRef.wardId).then((l) => { setLocalities(l); setLocalitiesLoaded(true); }).catch(() => setLocalitiesLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wardsLoaded, wardOptions]);

  useEffect(() => {
    if (!localitiesLoaded || !initialRef?.localityId || localityId !== "") return;
    if (!localities.some((l) => l.id === initialRef.localityId)) return;
    setLocalityId(initialRef.localityId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localitiesLoaded, localities]);

  const cityName = cityId !== "" ? cities.find((c) => c.id === cityId)?.name ?? "" : cityText.trim();
  const wardName = wardId !== "" ? wardOptions.find((w) => w.id === wardId)?.name ?? "" : wardText.trim();
  const localityName = localityId !== "" ? localities.find((l) => l.id === localityId)?.name ?? "" : localityText.trim();

  // Mirrors the exact format real seeded worker wards already use ("Ward 22 — Kothrud, Pune"),
  // degrading gracefully when a level wasn't reached -- never fabricates a level that wasn't
  // actually given.
  function composeWard(): string {
    if (!wardName) return "";
    const withLocality = localityName ? `${wardName} — ${localityName}` : wardName;
    const withCity = cityName ? `${withLocality}, ${cityName}` : withLocality;
    // Only the free-text "Other" state has nowhere else to be recorded (home_state_id can't
    // point at it); a real state pick is already implied by its city, exactly as before.
    const stateName = stateOther ? stateText.trim() : "";
    return stateName ? `${withCity}, ${stateName}` : withCity;
  }

  useEffect(() => {
    onChange({
      ward: composeWard(),
      home_state_id: stateId === "" ? undefined : stateId,
      home_district_id: cityId === "" ? undefined : cityId,
      home_ward_id: wardId === "" ? undefined : wardId,
      home_locality_id: localityId === "" ? undefined : localityId,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stateId, stateOther, stateText, cityId, cityText, wardId, wardText, localityId, localityText]);

  function resetBelowState() {
    setCityId(""); setCityText(""); setCities([]); setCitiesLoaded(false);
    setWardId(""); setWardText(""); setWardOptions([]); setWardsLoaded(false);
    setLocalityId(""); setLocalityText(""); setLocalities([]); setLocalitiesLoaded(false);
  }

  function resetBelowCity() {
    setWardId(""); setWardText(""); setWardOptions([]); setWardsLoaded(false);
    setLocalityId(""); setLocalityText(""); setLocalities([]); setLocalitiesLoaded(false);
  }

  function resetBelowWard() {
    setLocalityId(""); setLocalityText(""); setLocalities([]); setLocalitiesLoaded(false);
  }

  function handleStateChange(raw: string) {
    if (raw === OTHER_STATE) {
      setStateId("");
      setStateOther(true);
      setStateText("");
      resetBelowState();
      return;
    }
    const id = raw === "" ? "" : Number(raw);
    setStateId(id);
    setStateOther(false);
    setStateText("");
    resetBelowState();
    if (id !== "") {
      api.listCitiesForState(id).then((c) => { setCities(c); setCitiesLoaded(true); }).catch(() => setCitiesLoaded(true));
    }
  }

  function handleCityChange(raw: string) {
    const id = raw === "" ? "" : Number(raw);
    setCityId(id);
    resetBelowCity();
    if (id !== "") {
      api.listWardsForCity(id).then((w) => { setWardOptions(w); setWardsLoaded(true); }).catch(() => setWardsLoaded(true));
    }
  }

  function handleCityText(value: string) {
    setCityText(value);
    // Free-text city has no district id to query children from -- ward/area become directly
    // available as free text too, immediately, rather than staying disabled forever.
    resetBelowCity();
  }

  function handleWardChange(raw: string) {
    const id = raw === "" ? "" : Number(raw);
    setWardId(id);
    resetBelowWard();
    if (id !== "") {
      api.listLocalitiesForWard(id).then((l) => { setLocalities(l); setLocalitiesLoaded(true); }).catch(() => setLocalitiesLoaded(true));
    }
  }

  function handleWardText(value: string) {
    setWardText(value);
    resetBelowWard();
  }

  // A level is "resolved" once it has a real id OR non-empty free text -- either way, the next
  // level down can be worked on.
  const cityResolved = cityId !== "" || cityText.trim() !== "";
  const wardResolved = wardId !== "" || wardText.trim() !== "";

  // "Other" state has no id to fetch cities for -- city becomes free text immediately, same as
  // how a resolved-but-childless city/ward unlocks the level below it.
  const cityReady = stateOther || (stateId !== "" && citiesLoaded);
  // Ward becomes usable once the city is resolved -- via a real dropdown pick (then wait for its
  // wards to load) or via free text (no fetch needed, no gate to wait on).
  const wardReady = cityId !== "" ? wardsLoaded : cityResolved;
  const areaReady = wardId !== "" ? localitiesLoaded : wardResolved;

  return (
    <div className={`home-location-picker ${hasError ? "has-error" : ""}`}>
      <div className="home-location-heading">{t(lang, "signup.homeLocation.heading")}</div>
      <div className="home-location-subtitle">{t(lang, "signup.homeLocation.subtitle")}</div>

      <div className="field home-location-row">
        <label htmlFor="signup-home-state">
          {t(lang, "signup.homeLocation.state")}
          <span className="field-required-mark" aria-hidden="true" />
        </label>
        {!statesLoaded ? (
          <select id="signup-home-state" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.statePlaceholder")}</option>
          </select>
        ) : (
          <select
            id="signup-home-state"
            value={stateOther ? OTHER_STATE : stateId}
            onChange={(e) => handleStateChange(e.target.value)}
          >
            <option value="">{t(lang, "signup.homeLocation.statePlaceholder")}</option>
            {states.map((s) => (
              <option key={s.id} value={s.id}>{localizeStateName(s.name, lang)}</option>
            ))}
            <option value={OTHER_STATE}>{t(lang, "signup.homeLocation.stateOther")}</option>
          </select>
        )}
        {stateOther && (
          <input
            id="signup-home-state-text"
            type="text"
            value={stateText}
            onChange={(e) => setStateText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.stateTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row">
        <label htmlFor="signup-home-city">
          {t(lang, "signup.homeLocation.city")}
          <span className="field-required-mark" aria-hidden="true" />
        </label>
        {!cityReady ? (
          <select id="signup-home-city" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.cityPlaceholder")}</option>
          </select>
        ) : cities.length > 0 ? (
          <select id="signup-home-city" value={cityId} onChange={(e) => handleCityChange(e.target.value)}>
            <option value="">{t(lang, "signup.homeLocation.cityPlaceholder")}</option>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="signup-home-city"
            type="text"
            value={cityText}
            onChange={(e) => handleCityText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.cityTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row">
        <label htmlFor="signup-home-ward">
          {t(lang, "signup.homeLocation.ward")}
          <span className="field-required-mark" aria-hidden="true" />
        </label>
        {!wardReady ? (
          <select id="signup-home-ward" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.wardPlaceholder")}</option>
          </select>
        ) : wardOptions.length > 0 ? (
          <select id="signup-home-ward" value={wardId} onChange={(e) => handleWardChange(e.target.value)}>
            <option value="">{t(lang, "signup.homeLocation.wardPlaceholder")}</option>
            {wardOptions.map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="signup-home-ward"
            type="text"
            value={wardText}
            onChange={(e) => handleWardText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.wardTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row home-location-row-last">
        <label htmlFor="signup-home-area">
          {t(lang, "signup.homeLocation.area")}
          <span className="field-optional-mark">{t(lang, "signup.homeLocation.optional")}</span>
        </label>
        {!areaReady ? (
          <select id="signup-home-area" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.areaPlaceholder")}</option>
          </select>
        ) : localities.length > 0 ? (
          <select id="signup-home-area" value={localityId} onChange={(e) => setLocalityId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">{t(lang, "signup.homeLocation.areaPlaceholder")}</option>
            {localities.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="signup-home-area"
            type="text"
            value={localityText}
            onChange={(e) => setLocalityText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.areaTextPlaceholder")}
          />
        )}
      </div>
    </div>
  );
}
