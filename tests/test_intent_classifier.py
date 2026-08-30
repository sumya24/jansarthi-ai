"""Unit tests for backend/services/intent_classifier.py's service-category detection --
specifically the STREETLIGHTS-vs-ROADS_POTHOLES check-order fix.

Live-reported: a citizen's real Odia question about a broken streetlight, "...ରାସ୍ତା-ଆଲୁଅ
(streetlight)...", was misclassified as ROADS_POTHOLES instead of STREETLIGHTS, because
ROADS_POTHOLES's own road-word ("ରାସ୍ତା") matched as a substring and classify()'s "first match
wins" category loop checked ROADS_POTHOLES before STREETLIGHTS. Direct probing (not just the one
reported language) found the identical collision in Hindi, Gujarati, and Bengali too -- the root
cause is structural (a streetlight is almost always described as being ON a road, but a genuine
road/pothole complaint essentially never mentions a light/lamp word), so it was fixed generally by
reordering STREETLIGHTS before ROADS_POTHOLES in _CATEGORY_KEYWORDS, not by patching each
language's keyword list individually.
"""

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.intent_classifier import classify, detect_multiple_categories, is_explicit_confirmation


def test_odia_road_light_compound_is_streetlights_not_roads():
    """The exact live-reported query."""
    result = classify("କୋଲକାତାରେ ଏକ ଅଚଳ ରାସ୍ତା-ଆଲୁଅ (streetlight) ବିଷୟରେ ମୁଁ କିପରି ଜଣାଇବି?")
    assert result.service_category is not None
    assert result.service_category.name == "STREETLIGHTS"


def test_hindi_road_light_compound_is_streetlights_not_roads():
    result = classify("सड़क की बत्ती खराब है।")
    assert result.service_category.name == "STREETLIGHTS"


def test_gujarati_road_light_compound_is_streetlights_not_roads():
    """Confirms the fix generalizes past the one narrow keyword-substring bug already known here:
    even gu's existing genitive-only narrowing ("રસ્તાન") still matches "રસ્તાની" -- the fix that
    actually closes this is the STREETLIGHTS-first check order, not a keyword tweak."""
    result = classify("રસ્તાની બત્તી બંધ છે.")
    assert result.service_category.name == "STREETLIGHTS"


def test_bengali_road_light_compound_is_streetlights_not_roads():
    result = classify("রাস্তার বাতি নষ্ট হয়ে গেছে।")
    assert result.service_category.name == "STREETLIGHTS"


def test_marathi_road_light_compound_is_streetlights_not_roads():
    """Was already correct before this fix (mr's genitive-only narrowing happened not to overlap
    the locative form) -- regression guard that the reorder doesn't change this."""
    result = classify("रस्त्यावरचा दिवा बंद आहे")
    assert result.service_category.name == "STREETLIGHTS"


def test_english_streetlight_still_classifies_correctly():
    result = classify("The streetlight near my house is broken")
    assert result.service_category.name == "STREETLIGHTS"


def test_genuine_pothole_complaints_still_classify_as_roads_not_streetlights():
    """The reorder must not overcorrect -- a real road/pothole complaint with no light/lamp
    mention at all must still land on ROADS_POTHOLES, in every language checked above."""
    cases = [
        "There is a pothole on the road",
        "सड़क खराब है",
        "रस्त्याची तक्रार",
        "રસ્તાની ફરિયાદ",
    ]
    for text in cases:
        result = classify(text)
        assert result.service_category is not None, text
        assert result.service_category.name == "ROADS_POTHOLES", text


def test_waste_on_road_still_classifies_as_waste_not_roads():
    """Pre-existing regression guard (unaffected by this fix, but re-verified alongside it): a
    road mentioned only as the LOCATION of an unrelated complaint must not become ROADS_POTHOLES."""
    result = classify("રસ્તા પર કચરો છે")
    assert result.service_category.name == "WASTE_SANITATION"


# --- is_explicit_confirmation() -- expanded recognized phrase list (manual test round) ---


def test_newly_recognized_confirmation_phrases():
    """Live-reported: "yes please" repeated the same confirmation prompt instead of filing the
    complaint. Expanded the recognized-phrase allowlist rather than trying to have the AI JUDGE
    whether a reply "sounds like" a yes -- this action gates a real database write, so the safe
    direction stays "recognize more exact phrases", never "guess more loosely" (see
    is_explicit_confirmation's own docstring). "sure" deliberately NOT included here -- see
    test_sure_is_still_deliberately_excluded below for why."""
    for text in ("yes please", "yes, please", "go ahead", "ok submit it", "okay, submit it", "confirm it"):
        assert is_explicit_confirmation(text), text


def test_sure_is_still_deliberately_excluded():
    """A first attempt at the fix above also added "sure" to the exact-confirmation-word list --
    reverted after this codebase's OWN existing test suite caught it
    (test_confirmation_bare_sure_with_pending_draft_does_not_confirm and the parametrized
    test_ambiguous_replies_still_safely_reask_not_misread_as_fresh_complaints[sure] both already
    document "sure" as a deliberate exclusion, grouped with "okay"/"maybe"/"fine"). This test
    exists so a future attempt to re-add "sure" fails fast here too, not just in that other file."""
    assert not is_explicit_confirmation("sure")
    assert not is_explicit_confirmation("sure submit it")


def test_ambiguous_words_still_correctly_excluded_after_the_expansion():
    """Regression guard: the expansion above must not loosen the deliberate exclusion of vague
    acknowledgments this function's own docstring documents -- "okay"/"fine"/"continue" mean "I
    heard you", not necessarily "yes, submit this complaint"."""
    for text in ("okay", "ok", "fine", "continue", "tell me more", "yes what are the rules"):
        assert not is_explicit_confirmation(text), text


# --- detect_multiple_categories() -- the multi-category supervisor gate (orchestration/
# nodes.py's agent_flow_node, see docs/ask_sarthi_orchestration.md §17) --------------------


def test_detects_two_genuinely_distinct_categories():
    result = detect_multiple_categories(
        "There is a pothole on my street and also the streetlight near my house is broken."
    )
    assert set(result) == {ServiceCategory.ROADS_POTHOLES, ServiceCategory.STREETLIGHTS}


def test_detects_three_genuinely_distinct_categories():
    """The concrete case from the roadmap itself: a flooded street, a blocked drain, and a
    downed streetlight in one message."""
    result = detect_multiple_categories(
        "My street is flooded, the drain is completely blocked, and the streetlight is out too."
    )
    assert ServiceCategory.WATER_DRAINAGE in result
    assert ServiceCategory.STREETLIGHTS in result
    assert len(result) >= 2


def test_a_single_category_message_is_never_flagged_as_multi_category():
    result = detect_multiple_categories("The street light outside my house has stopped working.")
    assert result == []


def test_streetlight_on_a_road_is_not_falsely_flagged_as_roads_plus_streetlights():
    """The exact false-positive this check is deliberately narrowed to avoid -- see this
    function's own module-level comment. A streetlight complaint naming the road it's on (the
    single most common way people phrase this, per _CATEGORY_KEYWORDS's own STREETLIGHTS-vs-
    ROADS_POTHOLES collision comment) must NOT look like two categories."""
    result = detect_multiple_categories("The street light on Main Road near my house is broken.")
    assert result == []


def test_civil_work_department_mention_does_not_falsely_flag_roads_potholes():
    """BUG FIX (code review): "civil work" used to be a ROADS_POTHOLES multi-category keyword,
    matched as a bare substring -- "civil works department/office", mentioned only as WHERE an
    unrelated single issue is located, wrongly looked like a second (pothole) category."""
    result = detect_multiple_categories("The streetlight near the civil works department office is broken.")
    assert result == []


def test_genuinely_unrelated_single_word_overlap_is_not_enough():
    """A message with only one REAL category signal (streetlights) plus an incidental word that
    happens to also appear in another category's list must not multi-count -- "waste" doesn't
    appear here at all, so only one category should ever match."""
    result = detect_multiple_categories("My street light is broken.")
    assert result == []


def test_multilingual_two_category_detection_hindi():
    result = detect_multiple_categories("मेरे घर के पास कचरा भी है और सड़क पर गड्ढा भी है।")
    assert set(result) == {ServiceCategory.WASTE_SANITATION, ServiceCategory.ROADS_POTHOLES}


def test_known_gap_odia_has_no_pothole_specific_multi_category_keyword():
    """KNOWN, DOCUMENTED GAP (code review) -- not a desired behavior, a recorded limitation.
    `_CATEGORY_KEYWORDS[ROADS_POTHOLES]["or"]` was only ever the bare "road" word (no
    pothole-specific Odia word exists anywhere in this codebase's own verified data), and the
    multi-category gate deliberately drops bare "road" words (see this module's own comment) --
    so an Odia pothole mention alone can never contribute a ROADS_POTHOLES match here, even
    alongside a second, unambiguous category. This test exists so a future incidental "fix" that
    silently reintroduces the bare "road" word (undoing the STREETLIGHTS-collision narrowing) is
    caught, and so this gap stays visible rather than silently forgotten -- see
    intent_classifier.py's own comment for what a real fix requires (a live-verified Odia
    pothole-specific phrase, not a guess)."""
    # Deliberately uses ONLY already-verified Odia words from this file's own _CATEGORY_KEYWORDS
    # (ଆବର୍ଜନା "garbage", ରାସ୍ତା "road") -- not a guessed pothole-specific word, since none is
    # verified to exist (see the comment this test references). "ରାସ୍ତା" alone is exactly the
    # bare "road" word the multi-category gate deliberately excludes for every language.
    result = detect_multiple_categories("ମୋ ଘର ପାଖରେ ଆବର୍ଜନା ଅଛି ଏବଂ ରାସ୍ତା ମଧ୍ୟ ଖରାପ ଅଛି।")
    assert ServiceCategory.ROADS_POTHOLES not in result
