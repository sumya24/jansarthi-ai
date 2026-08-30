// Scopes the manual "Select location" ward dropdown (Report an Issue, and Ask Sarthi's own
// location-clarification step) to the citizen's OWN city -- the same city implied by their own
// registered `ward` (set at Signup, editable in Settings) -- instead of a flat, unordered list of
// every serviceable ward nationwide. Gives that setting an actual, visible effect on these flows.
// Deliberately city-level, not ward-level: narrowing all the way to their exact single registered
// ward would leave one option, too narrow if they want to report something in a nearby ward of the
// same city. "Use current location" (GPS) is untouched by this -- it already reads the citizen's
// real position, not their registered city, so it correctly keeps working for someone reporting an
// issue while traveling elsewhere; this filter only narrows the fallback *manual* list.
//
// City is read as the text after the LAST comma in the real ward string (the same
// "{ward} — {locality}, {city}" format composeWard() produces everywhere this string is built --
// HomeLocationPicker.tsx, WorkerLocationPicker.tsx, and the backend's own update_worker()/
// create_worker()), so this needs no new structured data, just the existing text.
//
// Matching is a case-insensitive PREFIX check, not exact equality: Karnataka's real district for
// Bengaluru is officially named "Bengaluru Urban" (a real administrative fact -- Karnataka splits
// it from "Bengaluru Rural"), while every real ward string here ends in plain "Bengaluru". A
// citizen who picks their city through the real Settings/Signup cascade ends up with
// "...Bengaluru Urban" as their registered city -- exact-string matching against that would never
// find "...Bengaluru" wards, even though they're obviously the same city. One name being a prefix
// of the other (either direction) catches this without needing a hardcoded list of exceptions, and
// is still exact-equal for every other city, where the two names already match exactly.
//
// Once the citizen's city IS known (their ward string parses), an empty result is a real, honest
// answer -- "no wards here yet" -- not a signal to fall back to the unfiltered list; showing wards
// from unrelated cities would be actively misleading, not just unhelpful (a citizen could pick a
// wrong-city ward without realizing it, misrouting their own complaint). LocationPicker.tsx already
// has its own graceful fallback for an empty ward list -- it switches from a dropdown to a plain
// free-text "type your area" box -- so an empty scoped result here correctly lets that existing
// behavior take over. Only genuinely UNKNOWN city (no ward set, or a string that doesn't parse)
// falls back to the full list, since "no idea where they are" is a different case from "we know
// exactly where they are, and it has nothing yet."
function citiesMatch(a: string, b: string): boolean {
  const na = a.trim().toLowerCase();
  const nb = b.trim().toLowerCase();
  if (!na || !nb) return false;
  return na === nb || na.startsWith(nb) || nb.startsWith(na);
}

export function filterWardsToOwnCity(allWards: string[], citizenWard: string | null | undefined): string[] {
  if (!citizenWard) return allWards; // unknown city entirely -- best guess is "show everything"
  const parts = citizenWard.split(",");
  if (parts.length < 2) return allWards; // no parseable city segment -- same as unknown
  const ownCity = parts[parts.length - 1].trim();
  if (!ownCity) return allWards;
  return allWards.filter((w) => {
    const wardCityParts = w.split(",");
    const wardCity = wardCityParts[wardCityParts.length - 1];
    return wardCity !== undefined && citiesMatch(wardCity, ownCity);
  });
}

// Finds the option in an already-scoped ward list (the output of filterWardsToOwnCity) that is the
// citizen's own registered ward, so a form field can pre-select it. Deliberately NOT a plain
// `scoped.includes(citizenWard)` -- that exact-string check breaks for the same "Bengaluru" vs
// "Bengaluru Urban" case filterWardsToOwnCity's citiesMatch() exists to handle: a citizen whose
// registered city came from the Settings/Signup cascade (a district name, e.g. "...Bengaluru Urban")
// still needs to match a real ward-list entry (built from the ULB name, e.g. "...Bengaluru"), even
// though the two strings are never byte-for-byte equal. Matches the ward+locality portion (everything
// before the LAST comma) exactly, and the city portion with the same prefix-aware citiesMatch used for
// scoping -- then returns the scoped entry's own exact string (never citizenWard itself), since that's
// the literal value a <select>'s <option> actually holds and the only string that will render as
// selected. Returns null (no pre-fill) rather than guess when nothing matches -- see
// ReportIssue.tsx / AskSarthi.tsx for why a wrong guess here is worse than no guess.
export function findWardMatch(scoped: string[], citizenWard: string | null | undefined): string | null {
  if (!citizenWard) return null;
  const parts = citizenWard.split(",");
  if (parts.length < 2) return null;
  const ownWardPart = parts.slice(0, -1).join(",").trim();
  const ownCity = parts[parts.length - 1].trim();
  if (!ownWardPart || !ownCity) return null;
  for (const w of scoped) {
    const wParts = w.split(",");
    if (wParts.length < 2) continue;
    const wardPart = wParts.slice(0, -1).join(",").trim();
    const wardCity = wParts[wParts.length - 1].trim();
    if (wardPart === ownWardPart && citiesMatch(wardCity, ownCity)) return w;
  }
  return null;
}
