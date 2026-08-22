import { useEffect, useRef, useState } from "react";
import { api, type LocationOption } from "../lib/api";
import { t, type LangCode } from "../lib/i18n";

export interface WorkerLocationValue {
  ward: string;
  state_id?: number;
  district_id?: number;
  ward_id?: number;
  locality_id?: number;
}

/** The cascading State -> City -> Ward -> Area picker for a worker's OPERATIONAL area (Edit
 * Worker, admin only) -- the structured counterpart of the flat free-text ward field this
 * replaces. Same backend lookups and same real-data-only behavior as the citizen-facing home
 * location picker on Signup (see HomeLocationPicker.tsx, which this mirrors): a level with no
 * children falls back to free text rather than inventing options, and `ward` is composed here in
 * the exact "{ward} — {locality}, {city}" format real seeded worker wards already use, so this
 * never produces a string that looks different from a worker onboarded any other way.
 *
 * Kept as its own component rather than sharing HomeLocationPicker directly: that component's
 * public value type (`home_*`-prefixed fields) matches a citizen's home-residence columns
 * (User.home_state_id etc), a distinct concept from a worker's operational area (User.state_id
 * etc, see models.py's own docstring on the two) -- reusing it here would mean this component
 * either emitting the wrong field names or the caller renaming them, either way papering over that
 * the two concepts are genuinely different columns. */
const OTHER_STATE = "other";
export default function WorkerLocationPicker({
  lang, onChange, hasError, initial,
}: {
  lang: LangCode; onChange: (value: WorkerLocationValue) => void; hasError?: boolean;
  // Pre-fills the cascade from the worker's current operational area (see EditWorkerModal.tsx).
  // Read once, on mount -- deliberately NOT reactive to `initial` changing later, same as
  // HomeLocationPicker's own `initial` prop.
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

  const initialRef = useRef(initial).current;

  useEffect(() => {
    api.listStates().then((s) => { setStates(s); setStatesLoaded(true); }).catch(() => setStatesLoaded(true));
  }, []);

  useEffect(() => {
    if (!statesLoaded || !initialRef?.stateId || stateId !== "") return;
    if (!states.some((s) => s.id === initialRef.stateId)) return;
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

  function composeWard(): string {
    if (!wardName) return "";
    const withLocality = localityName ? `${wardName} — ${localityName}` : wardName;
    const withCity = cityName ? `${withLocality}, ${cityName}` : withLocality;
    const stateName = stateOther ? stateText.trim() : "";
    return stateName ? `${withCity}, ${stateName}` : withCity;
  }

  useEffect(() => {
    onChange({
      ward: composeWard(),
      state_id: stateId === "" ? undefined : stateId,
      district_id: cityId === "" ? undefined : cityId,
      ward_id: wardId === "" ? undefined : wardId,
      locality_id: localityId === "" ? undefined : localityId,
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

  const cityResolved = cityId !== "" || cityText.trim() !== "";
  const wardResolved = wardId !== "" || wardText.trim() !== "";

  const cityReady = stateOther || (stateId !== "" && citiesLoaded);
  const wardReady = cityId !== "" ? wardsLoaded : cityResolved;
  const areaReady = wardId !== "" ? localitiesLoaded : wardResolved;

  return (
    <div className={`home-location-picker worker-location-picker ${hasError ? "has-error" : ""}`}>
      <div className="home-location-subtitle">{t(lang, "signup.homeLocation.subtitle")}</div>

      <div className="field home-location-row">
        <label htmlFor="worker-location-state">{t(lang, "signup.homeLocation.state")}</label>
        {!statesLoaded ? (
          <select id="worker-location-state" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.statePlaceholder")}</option>
          </select>
        ) : (
          <select
            id="worker-location-state"
            value={stateOther ? OTHER_STATE : stateId}
            onChange={(e) => handleStateChange(e.target.value)}
          >
            <option value="">{t(lang, "signup.homeLocation.statePlaceholder")}</option>
            {states.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
            <option value={OTHER_STATE}>{t(lang, "signup.homeLocation.stateOther")}</option>
          </select>
        )}
        {stateOther && (
          <input
            id="worker-location-state-text"
            type="text"
            value={stateText}
            onChange={(e) => setStateText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.stateTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row">
        <label htmlFor="worker-location-city">{t(lang, "signup.homeLocation.city")}</label>
        {!cityReady ? (
          <select id="worker-location-city" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.cityPlaceholder")}</option>
          </select>
        ) : cities.length > 0 ? (
          <select id="worker-location-city" value={cityId} onChange={(e) => handleCityChange(e.target.value)}>
            <option value="">{t(lang, "signup.homeLocation.cityPlaceholder")}</option>
            {cities.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="worker-location-city"
            type="text"
            value={cityText}
            onChange={(e) => handleCityText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.cityTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row">
        <label htmlFor="worker-location-ward">{t(lang, "signup.homeLocation.ward")}</label>
        {!wardReady ? (
          <select id="worker-location-ward" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.wardPlaceholder")}</option>
          </select>
        ) : wardOptions.length > 0 ? (
          <select id="worker-location-ward" value={wardId} onChange={(e) => handleWardChange(e.target.value)}>
            <option value="">{t(lang, "signup.homeLocation.wardPlaceholder")}</option>
            {wardOptions.map((w) => (
              <option key={w.id} value={w.id}>{w.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="worker-location-ward"
            type="text"
            value={wardText}
            onChange={(e) => handleWardText(e.target.value)}
            placeholder={t(lang, "signup.homeLocation.wardTextPlaceholder")}
          />
        )}
      </div>

      <div className="field home-location-row home-location-row-last">
        <label htmlFor="worker-location-area">{t(lang, "signup.homeLocation.area")}</label>
        {!areaReady ? (
          <select id="worker-location-area" value="" disabled>
            <option value="">{t(lang, "signup.homeLocation.areaPlaceholder")}</option>
          </select>
        ) : localities.length > 0 ? (
          <select id="worker-location-area" value={localityId} onChange={(e) => setLocalityId(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">{t(lang, "signup.homeLocation.areaPlaceholder")}</option>
            {localities.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
        ) : (
          <input
            id="worker-location-area"
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
