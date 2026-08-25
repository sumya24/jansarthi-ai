"""Unit tests for LocationExtractor's non-Latin-script city/state matching (backend/services/
location_extractor.py) -- specifically the attached-postposition bug found via a live report: a
citizen's real Marathi question about Bengaluru's water supply silently fell back to a generic
SYNTHETIC answer instead of the real VERIFIED BWSSB record, purely because the location text
never resolved at all. Two distinct, separately-verified root causes, both covered here:
(1) a missing alias spelling ("बेंगळुरू", ें + ळ together) never in `_CITY_ALIASES` at all, and
(2) a structural gap in `_bounded_search`'s word-boundary check -- a Hindi/Marathi postposition
("मध्ये"/"में", "in") attached directly to a city name with no space (normal, grammatically correct
usage in these languages) broke the boundary check even for spellings ALREADY in the alias list.
"""

from pathlib import Path

from backend.services.location_extractor import LocationExtractor, RagGazetteer

_CHUNKS_PATH = Path(__file__).resolve().parent.parent / "data" / "rag_knowledge_base" / "chunks" / "chunks.json"


def _extractor() -> LocationExtractor:
    return LocationExtractor(RagGazetteer(_CHUNKS_PATH))


def test_live_reported_marathi_query_resolves_to_bengaluru():
    """The exact query from the live bug report."""
    ext = _extractor()
    res = ext.resolve_from_text("बेंगळुरूमध्ये पाणीपुरवठ्याबाबत तक्रार करण्याची प्रक्रिया काय आहे?")
    assert res.city == "Bengaluru"
    assert res.state == "Karnataka"


def test_new_spelling_alias_matches_standalone():
    """The specific missing alias ("ें" + "ळ" together), even with a following space -- isolates
    the alias-list gap from the attached-postposition gap."""
    ext = _extractor()
    res = ext.resolve_from_text("बेंगळुरू पाणीपुरवठा")
    assert res.city == "Bengaluru"


def test_attached_postposition_on_an_already_known_alias():
    """Isolates the OTHER root cause: even a spelling already in _CITY_ALIASES ("बंगळुरू") failed
    to match once "मध्ये" was glued directly onto it with no space."""
    ext = _extractor()
    res = ext.resolve_from_text("बंगळुरूमध्ये पाणीपुरवठा")
    assert res.city == "Bengaluru"


def test_live_reported_city_word_collision_prefers_the_postposition_marked_city():
    """LIVE-REPORTED BUG: "गया" is _CITY_ALIASES's own alias for Gaya, Bihar -- and ALSO the
    ordinary Hindi/Marathi past-tense verb form "went"/"has [happened]" ("हो गया", "has become").
    A real citizen sentence reporting garbage piling up "...जमा हो गया है, कोलकाता में" ("...has
    piled up, in Kolkata") matched "गया" first (earlier in _CITY_ALIASES's own definition order)
    and resolved to Gaya WITHOUT ever reaching "कोलकाता" (Kolkata), the city actually named in the
    same sentence -- filing/answering against the wrong city entirely. Fixed by preferring a match
    with an explicit locative postposition attached ("कोलकाता में", "in Kolkata") over an earlier,
    unmarked dict-order match (see `_bounded_search_with_postposition`'s own docstring)."""
    ext = _extractor()
    res = ext.resolve_from_text("मेरे घर के पास कचरा जमा हो गया है, कोलकाता में")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_live_reported_city_word_collision_with_the_dropped_anusvara_spelling():
    """LIVE-REPORTED GAP found immediately after the fix above shipped: a real citizen sentence
    used "कोलकाता मे" (the formal "में" with its anusvara/chandrabindu dropped -- extremely common
    informal Hindi typing), not "कोलकाता में" -- the exact-string "में" match alone missed it,
    silently falling back to the unmarked "गया" match again, the exact same failure mode the
    sibling test above already covers for the formal spelling."""
    ext = _extractor()
    res = ext.resolve_from_text("मेरे घर के पास कचरा जमा हो गया है, कोलकाता मे")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_a_real_gaya_query_with_no_competing_city_still_resolves_to_gaya():
    """The fix above must not make "गया" permanently unmatchable -- a genuine query naming ONLY
    Gaya, with no OTHER city present and no postposition directly marking "गया" itself ("शहर",
    "city", sits in between), must still resolve correctly via the existing bare-match fallback
    pass, exactly as it did before this fix."""
    ext = _extractor()
    res = ext.resolve_from_text("गया शहर में सड़क खराब है")
    assert res.city == "Gaya"
    assert res.state == "Bihar"


def test_attached_postposition_generalizes_beyond_bengaluru():
    """Not a Bengaluru-specific patch -- any city + "मध्ये" must resolve, since this is a general
    Marathi grammar pattern (postpositions attach to the noun), not one city's quirk."""
    ext = _extractor()
    res = ext.resolve_from_text("मुंबईमध्ये रस्ता खराब आहे")
    assert res.city == "Mumbai"
    assert res.state == "Maharashtra"


def test_plain_space_separated_matching_still_works():
    """Regression guard: the fix must not require an attached postposition -- the original,
    already-working space-separated case (see this module's own live "मोहाली." bug fix) must keep
    working unchanged."""
    ext = _extractor()
    res = ext.resolve_from_text("मोहाली.")
    assert res.city == "Sahibzada Ajit Singh Nagar (Mohali)"


def test_unrelated_word_containing_the_postposition_text_is_not_a_false_match():
    """The postposition tolerance is only ever appended to an ALREADY-matched city/state name --
    "मध्ये" appearing elsewhere in text that names no real city/state must still resolve to
    nothing, not silently match some unrelated city by coincidence."""
    ext = _extractor()
    res = ext.resolve_from_text("यामध्ये काहीही समस्या नाही")  # "there is no problem in this"
    assert res.city is None
    assert res.state is None


def test_live_reported_kolkata_oblique_declension_resolves_correctly():
    """Live-reported (second report, after the postposition-attachment fix above): a citizen's
    real Marathi question, "कोलकात्यात बंद पडलेल्या पथदिव्याबद्दल मी तक्रार कशी करू?" ("How do I
    report a streetlight that's stopped working in Kolkata?"), silently resolved to NO location at
    all -- worse than a clarification prompt, the caller then fell back to the citizen's own
    home-ward location (a different, wrong city), which correctly had no verified data and
    honestly (but confusingly, from the citizen's perspective) returned SYNTHETIC content instead.
    Root cause: "कोलकात्यात" isn't "कोलकाता" + an attached postposition -- it's the base alias's
    सामान्यरूप (oblique declension: आ -> ्या before a case marker), a completely different surface
    form _bounded_search's existing postposition tolerance can't recognize."""
    ext = _extractor()
    res = ext.resolve_from_text("कोलकात्यात बंद पडलेल्या पथदिव्याबद्दल मी तक्रार कशी करू?")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_oblique_declension_generalizes_to_other_cities_with_the_same_ending():
    """Not a Kolkata-only patch -- any alias ending in Devanagari consonant + आ-matra (the same
    grammatical shape "कोलकाता" has) gets its oblique form computed on the fly, with no new
    dictionary entry required. Patna ("पटना") is a distinct city with the identical ending shape."""
    ext = _extractor()
    res = ext.resolve_from_text("पटन्यात कचरा संकलनाची तक्रार कशी करावी?")
    assert res.city == "Patna"
    assert res.state == "Bihar"


def test_oblique_declension_does_not_false_match_unrelated_text():
    """The computed oblique form is only ever tried when a KNOWN alias produces it -- it must
    never cause an unrelated word merely containing "त" after some other syllable to be
    mistaken for a declined city name."""
    ext = _extractor()
    res = ext.resolve_from_text("यामध्ये काहीही समस्या नाही")
    assert res.city is None
    assert res.state is None


# --- Live-reported (third report): the SAME attached-postposition failure mode as the Marathi
# मध्ये/में fix above, but never extended past Devanagari -- Odia, Gujarati, and Bengali each have
# their own attached locative suffix and were still broken. Confirmed by direct probing before
# fixing, not assumed: all three failed to resolve with the suffix attached, and all three worked
# fine with a space instead. ---


def test_live_reported_odia_locative_suffix_resolves_to_kolkata():
    """The exact live-reported query: "କୋଲକାତାରେ" = "କୋଲକାତା" (Kolkata) + "ରେ" (Odia locative
    suffix, "in/at"), attached with no space -- normal, grammatically correct Odia."""
    ext = _extractor()
    res = ext.resolve_from_text("କୋଲକାତାରେ ଏକ ଅଚଳ ରାସ୍ତା-ଆଲୁଅ (streetlight) ବିଷୟରେ ମୁଁ କିପରି ଜଣାଇବି?")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_odia_locative_suffix_generalizes_to_a_different_city():
    ext = _extractor()
    res = ext.resolve_from_text("ଗୁହାଟିରେ ଜଳ ଯୋଗାଣ ଅଭିଯୋଗ କିପରି କରିବେ?")
    assert res.city == "Guwahati"
    assert res.state == "Assam"


def test_gujarati_attached_postposition_resolves_to_kolkata():
    """"કોલકાતામાં" = "કોલકાતા" + "માં" (Gujarati "in", a separate postposition word exactly like
    Hindi/Marathi's में/मध्ये), attached with no space."""
    ext = _extractor()
    res = ext.resolve_from_text("કોલકાતામાં પાણી પુરવઠાની ફરિયાદ કેવી રીતે કરવી?")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_gujarati_attached_postposition_generalizes_to_a_different_city():
    ext = _extractor()
    res = ext.resolve_from_text("અમદાવાદમાં કચરાની ફરિયાદ કેવી રીતે કરવી?")
    assert res.city == "Ahmedabad"
    assert res.state == "Gujarat"


def test_bengali_attached_locative_suffix_resolves_to_kolkata():
    """"কলকাতায়" = "কলকাতা" + "য়" (Bengali locative suffix after a vowel-final noun)."""
    ext = _extractor()
    res = ext.resolve_from_text("কলকাতায় পানি সরবরাহের অভিযোগ কীভাবে করব?")
    assert res.city == "Kolkata"
    assert res.state == "West Bengal"


def test_bengali_attached_locative_suffix_generalizes_to_a_different_city():
    ext = _extractor()
    res = ext.resolve_from_text("হাওড়ায় রাস্তার গর্তের অভিযোগ কীভাবে করব?")
    assert res.city == "Howrah"
    assert res.state == "West Bengal"


def test_odia_attached_suffix_does_not_false_match_unrelated_text():
    """Same false-positive guard as the Devanagari case above, for the newly-added Odia suffix --
    "ରେ" appearing elsewhere in ordinary text (not glued onto a real city alias) must not produce
    a false match."""
    ext = _extractor()
    res = ext.resolve_from_text("ଏଥିରେ କୌଣସି ସମସ୍ୟା ନାହିଁ")  # "there is no problem in this"
    assert res.city is None
    assert res.state is None
