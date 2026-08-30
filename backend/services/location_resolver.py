"""Resolves raw GPS coordinates into the administrative location hierarchy (state/district/
ULB/...), without ever fabricating a ward or locality the resolver has no reliable basis for.

Deliberately NOT coupled to a single geocoding vendor at the call site — `LocationResolver` is
the abstraction; `NominatimGeocoder` (below) is the one implementation currently plugged into
it, chosen because it needs no API key and adds no new dependency (`requests` is already in
requirements.txt). Swapping in a different/paid/official provider later means writing a new
class with the same two methods and passing it into `LocationResolver(geocoder=...)` — nothing
else in the codebase should need to change.

HARD LIMITATION, by design, not a bug: this resolver never returns a ward or locality. OSM/
Nominatim's coverage of India's municipal ward boundaries is inconsistent-to-absent in most
cities (verified by inspection of sample responses during development of this module — ward-
level `address` keys are simply not present for the coordinates tested), so claiming ward-level
precision from it would be exactly the kind of fabrication this project has explicitly committed
not to do (see docs/location_data_audit.md, data/rag_knowledge_base's VERIFIED/SYNTHETIC rule,
and the location migration plan). `resolve_coordinates()` only ever populates state/district/
city-level fields (best-effort, from a free community-maintained data source — NOT an official
government source, see `ResolvedLocation.resolver_name`) plus the raw coordinates themselves,
which are always preserved exactly as received regardless of how much administrative detail
could be resolved. Matching a resolved city name against this project's own `wards` table (see
`normalize_location`) is a separate, explicit step — never assumed to have happened just because
coordinates were resolved.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from backend.models import ULB, District, Locality, State, User, Ward, Zone

logger = logging.getLogger(__name__)

# The app's own free-text ward convention (scripts/seed_multi_ward_data.py):
# "Ward <number> — <locality>, <city>" (real em dash, U+2014). The ", <city>" suffix is
# optional in existing data -- rows without it can never be matched (no city to resolve
# against), which is intentional, not a bug: see resolve_ward_by_text's docstring.
_WARD_TEXT_PATTERN = re.compile(r"^Ward\s+(\d+)\s*—\s*(.+)$")

NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
# Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/) requires a
# descriptive User-Agent identifying the application -- not a generic/browser-spoofing one.
_USER_AGENT = "JanSarthiAI-LocationResolver/1.0 (civic complaint app; dev/prototype use)"
_REQUEST_TIMEOUT_SECONDS = 6


@dataclass
class ResolvedLocation:
    """What a geocoder could determine from a pair of coordinates -- best-effort, never
    fabricated. Every field is None if the resolver couldn't determine it; there is no
    ward_name/locality_name field at all (see module docstring for why)."""

    latitude: float
    longitude: float
    state_name: str | None = None
    district_name: str | None = None
    city_name: str | None = None
    formatted_address: str | None = None
    resolver_name: str = "none"
    resolved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    warnings: list[str] = field(default_factory=list)


class NominatimGeocoder:
    """Thin wrapper around OpenStreetMap's free Nominatim reverse-geocoding API.

    NOT an official government data source -- OSM is community-maintained. Used here only
    because it requires no API key/account and needs no new dependency, making it a reasonable
    default for a prototype phase. Documented explicitly as swappable (see module docstring).
    """

    def reverse(self, latitude: float, longitude: float) -> dict | None:
        """Returns the raw Nominatim JSON response IN FULL (both its top-level `display_name` --
        the one human-readable formatted address string -- and its nested `address` dict of
        structured components), or None on any failure (timeout, non-200, malformed response, no
        network). Never raises -- a geocoding failure must never break complaint submission (see
        LocationResolver.resolve_coordinates).

        LIVE-REPORTED BUG (found alongside the zoom fix below): this used to return only
        `data.get("address")` -- the nested structured-component dict -- discarding the
        response's OWN top-level `display_name` entirely. resolve_coordinates() below then read
        `address.get("display_name")`, which can never exist on that nested dict (display_name is
        a SIBLING key of "address" in Nominatim's real response shape, not a child of it) -- so
        `formatted_address` silently evaluated to None on every single call, forcing every caller
        (including the citizen-facing "location detected" preview) to fall back to the coarser
        city/district/state join, no matter how much real detail Nominatim actually returned.

        LIVE-REPORTED GAP: `zoom` previously requested 10 -- Nominatim's own reverse-geocode
        "zoom" parameter is a request for how FINE-GRAINED a result to prioritize (its scale
        roughly: 3=country, 8=county, 10=city, 14=suburb, 16-18=street/building), not just a
        map-display zoom level -- and 10 capped `display_name`/formatted_address at city level,
        so the citizen-facing "location detected" preview (routes/locations.py's
        resolve-coordinates) never showed anything more specific than "City, District, State"
        even when the underlying GPS fix was itself precise to a few meters. Bumped to 18 (the
        finest Nominatim offers) so `formatted_address` carries real street/neighborhood detail
        when OSM has it. Does NOT affect this module's own "never claim ward-level precision"
        rule -- the `address` dict's city/state_district/state keys (all this resolver ever
        extracts into ResolvedLocation) are still returned at any zoom; a higher zoom only adds
        FINER keys (road, house_number, suburb) this code still never reads for ward matching.
        """
        try:
            resp = requests.get(
                NOMINATIM_REVERSE_URL,
                params={"lat": latitude, "lon": longitude, "format": "jsonv2", "zoom": 18, "addressdetails": 1},
                headers={"User-Agent": _USER_AGENT},
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            logger.warning("Nominatim reverse geocode failed for (%s, %s): %s", latitude, longitude, exc)
            return None
        return data


class LocationResolver:
    """Resolves coordinates into administrative location, and separately matches resolved names
    against this project's own seeded location-hierarchy tables (states/districts/ulbs).

    Constructor-injected geocoder (matches this codebase's existing service pattern, e.g.
    TranslationService's injected SarvamClient) so tests can substitute a fake geocoder instead
    of making real network calls.
    """

    def __init__(self, geocoder: NominatimGeocoder | None = None) -> None:
        self._geocoder = geocoder or NominatimGeocoder()

    def resolve_ward_by_text(self, db: Session, ward_text: str) -> Ward | None:
        """Deterministically parses the app's own "Ward <n> — <locality>, <city>" free-text
        convention and looks for an exact-enough match in the seeded `wards` table -- the same
        logic scripts/migrate_existing_locations.py uses for historical data, exposed here so
        newly-created complaints/workers get it applied live, not just at migration time.

        Never guesses: returns None if the text doesn't parse, has no city suffix to resolve
        against, or doesn't match any seeded ULB/district by name. No fuzzy matching.
        """
        match = _WARD_TEXT_PATTERN.match(ward_text)
        if not match:
            return None
        ward_number, remainder = match.group(1), match.group(2)
        if "," not in remainder:
            return None
        city_hint = remainder.rsplit(",", 1)[-1].strip().lower()
        if not city_hint:
            return None

        for ward in db.query(Ward).filter(Ward.ward_number == ward_number).all():
            ulb = db.query(ULB).filter(ULB.id == ward.ulb_id).first()
            district = db.query(District).filter(District.id == ulb.district_id).first() if ulb else None
            haystacks = [ulb.name.lower() if ulb else "", district.name.lower() if district else ""]
            if any(city_hint in haystack for haystack in haystacks):
                return ward
        return None

    def find_worker_ward_text(self, db: Session, hint: str) -> str | None:
        """Finds a real, currently-registered worker's exact `ward` string that best matches
        `hint` -- either an exact (case-insensitive) match, or `hint` naming the same city a
        worker's ward is in (parsed via this app's own "Ward N -- Locality, City" convention,
        same as `resolve_ward_by_text` above).

        Deliberately returns the WORKER's own `ward` string verbatim, not a `wards` table row's
        `name` column -- that column is bare ("Ward 22", no locality/city suffix; see the wards
        table's actual seeded data), which can never exact-match a worker's full free-text `ward`
        ("Ward 22 -- Kothrud, Pune") again. `assignment_service.py`'s `_candidates()` matches
        workers by EXACT TEXT (`User.ward == complaint.ward`) whenever `ward_id` isn't set on both
        sides -- true for every currently-seeded worker (none have `ward_id` backfilled yet, see
        scripts/seed_multi_ward_data.py) -- so whatever this method returns is guaranteed to
        exact-match a real worker by construction, closing a real bug: `resolve_ward_by_text`
        succeeding and the caller saving its bare `Ward.name` as `complaint.ward` LOOKED like a
        correct structured resolution but silently produced an unassignable complaint anyway
        (caught live: a complaint with `ward="Ward 22"` against a real worker registered as
        `ward="Ward 22 — Kothrud, Pune"` never matched, sat "pending" forever).

        Never guesses beyond substring city-name matching; returns None if nothing lines up (an
        honest "not currently served" is correct there, not a fabricated match).
        """
        needle = hint.strip().lower()
        if not needle:
            return None
        workers = db.query(User).filter(User.role == "worker", User.ward.isnot(None)).all()
        for worker in workers:
            if worker.ward.strip().lower() == needle:
                return worker.ward
        for worker in workers:
            match = _WARD_TEXT_PATTERN.match(worker.ward)
            if not match or "," not in match.group(2):
                continue
            city = match.group(2).rsplit(",", 1)[-1].strip().lower()
            if city and city in needle:
                return worker.ward
        return None

    def location_chain_for_ward(self, db: Session, ward: Ward) -> dict[str, int | None]:
        """The full state->...->locality ID chain for a resolved Ward row. Shared by the live
        complaint-creation path and scripts/migrate_existing_locations.py so both compute this
        identically instead of maintaining two copies."""
        ulb = db.query(ULB).filter(ULB.id == ward.ulb_id).first()
        district = db.query(District).filter(District.id == ulb.district_id).first() if ulb else None
        state = db.query(State).filter(State.id == district.state_id).first() if district else None
        zone = db.query(Zone).filter(Zone.id == ward.zone_id).first() if ward.zone_id else None
        locality = db.query(Locality).filter(Locality.ward_id == ward.id).first()
        return {
            "state_id": state.id if state else None,
            "district_id": district.id if district else None,
            "sub_district_id": None,  # not yet seeded for any current ULB -- see seed script
            "ulb_id": ulb.id if ulb else None,
            "zone_id": zone.id if zone else None,
            "ward_id": ward.id,
            "locality_id": locality.id if locality else None,
        }

    def resolve_coordinates(self, latitude: float, longitude: float) -> ResolvedLocation:
        """Best-effort reverse geocode. Always returns a ResolvedLocation (never raises, never
        returns None) so a caller can always at least keep the raw coordinates -- a geocoding
        failure must never block complaint submission (see routes/complaints.py)."""
        data = self._geocoder.reverse(latitude, longitude)
        if data is None:
            return ResolvedLocation(
                latitude=latitude,
                longitude=longitude,
                resolver_name="nominatim (unavailable)",
                warnings=["Reverse geocoding failed or timed out; only raw coordinates are available."],
            )

        # `display_name` (the one human-readable formatted string) is a TOP-LEVEL key of
        # Nominatim's response; `address` (structured components) is a separate, nested dict --
        # the two are siblings, not parent/child (see NominatimGeocoder.reverse's own docstring
        # for the real bug this fixes: reading display_name off the nested dict instead).
        address = data.get("address") or {}

        # Nominatim's address dict uses varying keys for "city" depending on place type
        # (city/town/village/municipality/county) -- take the first that's present, in that
        # order, rather than assuming one key always applies.
        city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
        district = address.get("state_district") or address.get("county")
        state = address.get("state")

        warnings = [
            "Ward and locality are never resolved by this provider -- OSM/Nominatim does not "
            "reliably cover Indian municipal ward boundaries. Ward-level matching, if any, must "
            "come from a separate, explicit step (see LocationResolver.normalize_location)."
        ]
        if not city:
            warnings.append("City/town could not be determined from this provider's response.")

        return ResolvedLocation(
            latitude=latitude,
            longitude=longitude,
            state_name=state,
            district_name=district,
            city_name=city,
            formatted_address=data.get("display_name"),
            resolver_name="nominatim (OpenStreetMap, community-maintained -- not an official government source)",
            warnings=warnings,
        )

    def normalize_location(self, db: Session, resolved: ResolvedLocation) -> dict[str, int | None]:
        """Matches a ResolvedLocation's free-text names against this project's own seeded
        states/districts/ulbs tables (see scripts/seed_location_master_data.py) and returns
        whichever IDs can be confidently matched. Case-insensitive exact match only -- no fuzzy
        matching, so a geocoder spelling/naming variant that doesn't exactly match a seeded row
        simply yields None for that level, rather than guessing at the closest one.

        Always returns state_id/district_id/ulb_id/zone_id/ward_id/locality_id keys;
        zone_id/ward_id/locality_id are always None here by construction (see module docstring)
        -- present in the dict only so callers can merge it directly into the same fields
        Complaint uses, without special-casing which keys exist.
        """
        result: dict[str, int | None] = {
            "state_id": None, "district_id": None, "sub_district_id": None,
            "ulb_id": None, "zone_id": None, "ward_id": None, "locality_id": None,
        }

        state = None
        if resolved.state_name:
            state = db.query(State).filter(State.name.ilike(resolved.state_name)).first()
            result["state_id"] = state.id if state else None

        if resolved.district_name and state:
            district = db.query(District).filter(
                District.state_id == state.id, District.name.ilike(resolved.district_name)
            ).first()
            result["district_id"] = district.id if district else None

        if resolved.city_name and state:
            # A city is usually the ULB name itself (e.g. "Pune Municipal Corporation" contains
            # "Pune") -- match by substring within the state, not a separate "cities" table
            # (this project doesn't have one; ULB is the closest structured equivalent).
            ulb = (
                db.query(ULB)
                .join(District, ULB.district_id == District.id)
                .filter(District.state_id == state.id, ULB.name.ilike(f"%{resolved.city_name}%"))
                .first()
            )
            result["ulb_id"] = ulb.id if ulb else None

        return result
