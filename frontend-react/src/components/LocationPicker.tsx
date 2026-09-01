import { useEffect, useState } from "react";
import { useUiLang } from "../lib/uiLang";
import { t } from "../lib/i18n";
import { api } from "../lib/api";

export interface LocationValue {
  ward: string;
  coords: { lat: number; lng: number; accuracy: number } | null;
  // LIVE-REPORTED: a ward alone (e.g. "Ward 3 — Indiranagar, Bengaluru") often isn't precise
  // enough to actually find the issue -- a ward can span a large area. Always-present, always
  // optional free-text field for a street name/landmark/building, separate from the structured
  // ward pick above. Maps to the backend's own already-existing `address` field on
  // POST /complaints (routes/complaints.py's create_complaint) -- that field already existed and
  // already gets auto-filled from GPS reverse-geocoding when set, but nothing in this form ever
  // let a citizen type it manually until now.
  locality: string;
}

/**
 * Location step for Report an Issue: "Use current location" or "Select location" manually.
 * Geolocation permission denial (or any geolocation failure — unsupported browser, timeout)
 * never blocks the flow — it falls back to the manual picker with a plain-language notice,
 * per the spec's explicit requirement.
 *
 * `wards` is the same real, worker-backed ward list CitizenDashboard already fetches via
 * api.listWards — reused here rather than refetched, so this component never invents a ward
 * that doesn't route anywhere. When the list is empty, falls back to free-text entry, matching
 * the existing form's behavior.
 */
export default function LocationPicker({
  value,
  onChange,
  wards,
}: {
  value: LocationValue;
  onChange: (v: LocationValue) => void;
  wards: string[];
}) {
  const { lang } = useUiLang();
  const [mode, setMode] = useState<"choose" | "manual">("choose");
  const [locating, setLocating] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);
  // LIVE-REPORTED GAP: the "location detected" badge used to be a static, generic message with no
  // indication of WHERE was actually detected -- the ward shown below it is always the citizen's
  // own pre-filled home ward, never anything derived from GPS (ward-level reverse geocoding only
  // ever ran server-side, after final submission). Resolved live via api.resolveCoordinates the
  // moment GPS succeeds, purely for this honest confirmation message -- never blocks the flow and
  // is never sent to the backend itself (the raw coordinates already are, unchanged).
  const [resolvedAddress, setResolvedAddress] = useState<string | null>(null);
  // Real, already-known localities for whichever ward is currently picked (e.g. "Indiranagar",
  // "Koramangala") -- offered as suggestions on the Area/Address field below, never a forced
  // choice: it stays the same plain free-text input regardless, this only adds a browser-native
  // autocomplete dropdown when real data happens to exist for that ward. Resolves the ward's own
  // display string back to its structured row via GET /locations/wards/resolve (needed because
  // this component only ever has that string, not a ward id), then fetches that ward's real
  // localities -- both routes return an honest empty/null for a ward with nothing seeded, which
  // just means no suggestions, not an error.
  const [localitySuggestions, setLocalitySuggestions] = useState<string[]>([]);

  useEffect(() => {
    if (!value.ward) {
      setLocalitySuggestions([]);
      return;
    }
    let cancelled = false;
    api
      .resolveWard(value.ward)
      .then((ward) => (ward ? api.listLocalitiesForWard(ward.id) : Promise.resolve([])))
      .then((localities) => {
        if (!cancelled) setLocalitySuggestions(localities.map((l) => l.name));
      })
      .catch(() => {
        if (!cancelled) setLocalitySuggestions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [value.ward]);

  function useCurrentLocation() {
    if (!("geolocation" in navigator)) {
      setGeoError(t(lang, "location.unavailable"));
      setMode("manual");
      return;
    }
    setLocating(true);
    setGeoError(null);
    setResolvedAddress(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocating(false);
        const { latitude, longitude, accuracy } = pos.coords;
        onChange({ ...value, coords: { lat: latitude, lng: longitude, accuracy } });
        setMode("manual"); // still let them confirm/pick the ward — coords alone don't map to a ward name
        // Best-effort, purely informational -- a failed/slow resolve leaves resolvedAddress null,
        // and the badge below falls back to showing the raw coordinates instead. Never blocks
        // proceeding with the wizard either way, same as every other geolocation failure here.
        api
          .resolveCoordinates(latitude, longitude)
          .then((resolved) => {
            // Nominatim's formatted_address is the ENTIRE administrative chain (road, neighbourhood,
            // city, district, state, postcode, country) -- only the very TAIL (postcode, country) is
            // pure noise for a quick-glance badge; everything before that (however many segments --
            // a dense city address has many, a rural village fix might only have a few) is genuinely
            // useful detail. Dropping only the last two (postcode, then country) keeps everything
            // meaningful regardless of how many segments a given address actually has.
            const parts = [resolved.city_name, resolved.district_name, resolved.state_name].filter(Boolean);
            const segments = resolved.formatted_address ? resolved.formatted_address.split(",").map((s) => s.trim()) : [];
            const trimmedFormatted = segments.length > 2 ? segments.slice(0, -2).join(", ") : segments.join(", ") || null;
            setResolvedAddress(trimmedFormatted || (parts.length > 0 ? parts.join(", ") : null));
          })
          .catch(() => setResolvedAddress(null));
      },
      () => {
        setLocating(false);
        setGeoError(t(lang, "location.unavailable"));
        setMode("manual");
      },
      { timeout: 8000 }
    );
  }

  // LIVE-REPORTED BUG: choosing "Select manually" after a previous "Use current location" attempt
  // left that earlier GPS fix's coords (and the "location detected" badge) silently attached --
  // going back and explicitly picking the non-GPS path this time never cleared it, so the badge
  // kept claiming a location was detected under a flow that no longer has anything to do with GPS.
  function selectManually() {
    setGeoError(null);
    setResolvedAddress(null);
    if (value.coords) onChange({ ...value, coords: null });
    setMode("manual");
  }

  if (mode === "choose") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {geoError && <div className="banner-error">{geoError}</div>}
        <button type="button" className="location-option" onClick={useCurrentLocation} disabled={locating}>
          <span className="location-option-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
          </span>
          <span>
            <strong style={{ display: "block" }}>{locating ? t(lang, "location.locating") : t(lang, "location.useCurrent")}</strong>
            <span style={{ fontSize: 11.5, color: "var(--ink-2)" }}>{t(lang, "location.useCurrentHint")}</span>
          </span>
        </button>
        <button type="button" className="location-option" onClick={selectManually}>
          <span className="location-option-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
              <path d="M12 21s7-6.5 7-11.5A7 7 0 0 0 5 9.5C5 14.5 12 21 12 21Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
              <circle cx="12" cy="9.5" r="2.3" stroke="currentColor" strokeWidth="1.5" />
            </svg>
          </span>
          <span>
            <strong style={{ display: "block" }}>{t(lang, "location.selectManually")}</strong>
            <span style={{ fontSize: 11.5, color: "var(--ink-2)" }}>{t(lang, "location.selectManuallyHint")}</span>
          </span>
        </button>
      </div>
    );
  }

  return (
    <div>
      {geoError && <div className="banner-error">{geoError}</div>}
      {value.coords && (
        <>
          <div className="location-detected-badge">
            {/* Same crosshair used on the "Use current location" option above -- visually ties
                this confirmation back to the button that produced it, rather than a new symbol. */}
            <svg className="location-detected-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
              <path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
            </svg>
            {resolvedAddress ? (
              <span className="location-detected-text">
                <span className="location-detected-label">{t(lang, "location.gpsAttachedPrefix")}</span>
                <span className="location-detected-address">{resolvedAddress}</span>
              </span>
            ) : (
              <span style={{ fontWeight: 600 }}>{t(lang, "location.gpsAttached")}</span>
            )}
          </div>
          {/* Honest, permanent limitation, not "coming soon" -- ward-level detail is never
              resolved from GPS alone (see backend/services/location_resolver.py's own documented
              hard limit: OSM/Nominatim doesn't reliably cover Indian ward boundaries), so this
              always shows alongside a GPS fix rather than only when something "went wrong". */}
          <div className="location-detected-notice">
            <svg className="location-detected-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 3 2 20h20L12 3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
              <path d="M12 9.5v4M12 17v.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
            </svg>
            <span>{t(lang, "location.wardNotAutoDetected")}</span>
          </div>
        </>
      )}
      <div className="field">
        <label htmlFor="wizard-ward">
          {wards.length === 0 ? (
            t(lang, "citizen.wardOptional")
          ) : (
            <>
              {t(lang, "citizen.ward")}
              <span className="field-required-mark" aria-hidden="true" />
            </>
          )}
        </label>
        {wards.length > 0 ? (
          <select id="wizard-ward" value={value.ward} onChange={(e) => onChange({ ...value, ward: e.target.value })} required>
            <option value="" disabled>
              {t(lang, "citizen.wardSelectPlaceholder")}
            </option>
            {wards.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        ) : (
          <input id="wizard-ward" type="text" value={value.ward} onChange={(e) => onChange({ ...value, ward: e.target.value })} placeholder={t(lang, "citizen.wardPlaceholder")} />
        )}
      </div>
      <div className="field">
        <label htmlFor="wizard-locality">{t(lang, "citizen.locality")}</label>
        <input
          id="wizard-locality"
          type="text"
          value={value.locality}
          onChange={(e) => onChange({ ...value, locality: e.target.value })}
          placeholder={t(lang, "citizen.localityPlaceholder")}
          list={localitySuggestions.length > 0 ? "wizard-locality-suggestions" : undefined}
        />
        {localitySuggestions.length > 0 && (
          <datalist id="wizard-locality-suggestions">
            {localitySuggestions.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>
        )}
      </div>
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMode("choose")}>
        {t(lang, "location.backToOptions")}
      </button>
    </div>
  );
}
