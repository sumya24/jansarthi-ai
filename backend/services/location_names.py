"""Backend counterpart of frontend-react/src/lib/locationNames.ts -- same real, bounded
translation tables (state/city/locality/ward names), generated from that exact file so the two
never drift (see the generation note in location_names_data.json's own git history/commit message:
a small Node script evals each `const X_NAME_TRANSLATIONS = {...}` object literal and dumps the
result as JSON, rather than hand-retyping ~120 translated strings a second time).

Needed here specifically because `_citizen_notification_message()` (routes/complaints.py) and
assignment_service.py's worker-notification builder both bake a citizen's/worker's real ward text
(e.g. "Surat (M Corp.) - Ward No.1, Surat") directly into a notification's `message` column at
CREATE time -- the same composed-ward-text gap the frontend's `localizeWardText()` fixes for
on-page display, but here it needs fixing at the point the text is written, since a Notification
row's `message` is never re-translated after that (see models.py's own Notification docstring:
notifications are a point-in-time record, not live-translated on every read)."""

import json
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).parent / "location_names_data.json"
with _DATA_PATH.open(encoding="utf-8") as _f:
    _DATA = json.load(_f)

_STATE_NAME_TRANSLATIONS: dict[str, dict[str, str]] = _DATA["STATE_NAME_TRANSLATIONS"]
_CITY_NAME_TRANSLATIONS: dict[str, dict[str, str]] = _DATA["CITY_NAME_TRANSLATIONS"]
_LOCALITY_NAME_TRANSLATIONS: dict[str, dict[str, str]] = _DATA["LOCALITY_NAME_TRANSLATIONS"]
_WARD_NAME_TRANSLATIONS: dict[str, dict[str, str]] = _DATA["WARD_NAME_TRANSLATIONS"]


def localize_state_name(name: str, lang: str) -> str:
    return _STATE_NAME_TRANSLATIONS.get(name, {}).get(lang, name)


def localize_city_name(name: str, lang: str) -> str:
    return _CITY_NAME_TRANSLATIONS.get(name, {}).get(lang, name)


def localize_locality_name(name: str, lang: str) -> str:
    return _LOCALITY_NAME_TRANSLATIONS.get(name, {}).get(lang, name)


def localize_ward_name(name: str, lang: str) -> str:
    return _WARD_NAME_TRANSLATIONS.get(name, {}).get(lang, name)


def localize_ward_text(text: Optional[str], lang: str) -> Optional[str]:
    """Translates a composed ward/location string end to end -- direct Python port of
    locationNames.ts's `localizeWardText`, same separators, same fallback-to-English-on-a-miss
    behavior (see that function's own docstring for the full reasoning). `text` may be falsy
    (a citizen/worker with no ward set) -- returned unchanged, same as the TS version's early
    return."""
    if not text or lang == "en":
        return text

    em_dash_index = text.find(" — ")
    if em_dash_index != -1:
        ward_part = text[:em_dash_index]
        after_ward = text[em_dash_index + 3 :]  # "locality, city" (or just "locality")
        last_comma = after_ward.rfind(", ")
        if last_comma == -1:
            return f"{localize_ward_name(ward_part, lang)} — {localize_locality_name(after_ward, lang)}"
        locality_part = after_ward[:last_comma]
        city_part = after_ward[last_comma + 2 :]
        return (
            f"{localize_ward_name(ward_part, lang)} — "
            f"{localize_locality_name(locality_part, lang)}, {localize_city_name(city_part, lang)}"
        )

    # No locality segment -- composeWard() still appends ", city" directly onto the ward name. The
    # LAST ", " is what separates them (never the first): several real ward names already contain
    # their own comma (e.g. "Maninagar, Ahmedabad (M.Corp.) Ward No. 37"), so splitting on the
    # first comma would wrongly cut a real ward name in half.
    last_comma = text.rfind(", ")
    if last_comma == -1:
        return localize_ward_name(text, lang)
    ward_part = text[:last_comma]
    city_part = text[last_comma + 2 :]
    return f"{localize_ward_name(ward_part, lang)}, {localize_city_name(city_part, lang)}"
