"""Classifies an Ask Sarthi question into TYPE_A (complaint), TYPE_B (service info), or
TYPE_C (personal complaint status/tracking) — and, for TYPE_A/TYPE_B, which civic ServiceCategory
it's about, if any.

Design choice: rule-based keyword matching, not an LLM call. This project already uses Sarvam's
LLM (see summary_service.py/normalization_service.py) — deliberately NOT reused here, because
intent routing is exactly the kind of decision that should be fast, free, deterministic, and
directly unit-testable without a network call or nondeterministic model output (see §46 of the
implementation spec: "correctness, testability, clarity, replaceability -- not framework/API-call
count"). The LLM is reserved for what it's actually needed for: turning retrieved knowledge into
natural-language prose (see ask_sarthi_service.py's answer-generation step).

Honest limitation: this is keyword matching, not true NLU. It will misclassify phrasings that
don't share vocabulary with its keyword lists — measured accuracy against this project's own
labeled test question files is reported in docs/ask_sarthi_rag_architecture.md, not asserted
here as some claimed percentage.

TYPE_C (status/tracking) is checked FIRST and takes priority over everything else — misrouting a
personal-status question into RAG (which has no way to know about any specific citizen's
complaint) is the single most important thing this classifier must never do (see the module's
test coverage in tests/test_ask_sarthi.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from backend.schemas.rag_knowledge import ServiceCategory


class QuestionIntent(str, Enum):
    TYPE_A_COMPLAINT = "TYPE_A_COMPLAINT"
    TYPE_B_SERVICE_INFO = "TYPE_B_SERVICE_INFO"
    TYPE_C_STATUS = "TYPE_C_STATUS"
    # "What services do you provide?" / "What can you help with?" -- a real, answerable question
    # about Sarthi's own scope, not a complaint or a RAG-answerable civic-service question. Kept
    # distinct from TYPE_A's "no signal either way" fallback (see classify()'s docstring) so it
    # gets its own accurate, static answer instead of being asked "what issue would you like to
    # report?" as if it were an unspecified complaint.
    CAPABILITIES = "CAPABILITIES"
    # Genuinely no signal matched anything (not a complaint verb, not a named service category,
    # not a service-info phrase, not a status/tracking phrase, not a known-unsupported service,
    # not a capabilities question) -- e.g. "What is my name?" or unrelated small talk. Previously
    # silently fell through to TYPE_A_COMPLAINT/service_category=None, which asked "what issue
    # would you like to report?" as if the question had been about filing a complaint -- a real,
    # user-reported bug (every off-topic question got that same complaint-shaped clarification,
    # regardless of what was actually asked). UNCLEAR gets its own honest "I didn't understand
    # that" response instead (see nodes.py's unclear_flow_node).
    UNCLEAR = "UNCLEAR"
    # P0 SAFETY FIX: a bare service-category mention ("garbage", "streetlight repair rules") or a
    # bare complaint-verb match inside a question-shaped sentence, with NO problem-state language
    # and NO clear complaint framing -- genuinely ambiguous between "reporting a problem" and
    # "asking about one". Previously this fell straight through to TYPE_A_COMPLAINT (see the old
    # `if complaint_matches or category:` fallback this replaces), which is exactly how a
    # production-safety audit found real, live-reproduced complaints silently filed and assigned
    # to real workers for pure information questions ("What is the procedure for garbage
    # collection complaints in Pune?" -> complaint #52; "What are the rules for streetlight
    # repair in Kanpur?" -> complaint #54). Never guess in either direction: see classify()'s own
    # tail logic for how this is decided, and orchestration/nodes.py's clarification_flow_node
    # ("intent_ambiguous" reason) for how the caller asks instead of assuming.
    TYPE_A_MAYBE = "TYPE_A_MAYBE"
    # PRODUCTION ARCHITECTURE UPGRADE: a real, closed-vocabulary greeting/small-talk opener
    # ("Hello", "Hi", "Good morning", "My name is Sumit") -- previously fell through to UNCLEAR
    # (safe -- never a false complaint/location claim, verified live before this change -- but a
    # genuine request/response MISMATCH: a citizen saying hello got the same generic "I didn't
    # understand that, I can help with garbage/water/roads/streetlights..." reply as any other
    # unrecognized message). Greetings are a small, genuinely enumerable vocabulary -- unlike
    # general-knowledge/trivia/math questions (deliberately NOT given their own intent tier here;
    # see classify()'s own docstring for why an ever-growing keyword list for "every possible
    # off-topic topic" would be the opposite of generalizing, and UNCLEAR's own honest fallback
    # already handles that whole open-ended class correctly and safely) -- so a dedicated,
    # narrow, word-boundary-matched (see `_matches_word_boundary`) keyword tier is the same kind
    # of legitimate, closed-set intent this file already uses for CAPABILITIES, not a "keyword
    # hack" special-casing one specific sentence.
    GREETING = "GREETING"


@dataclass
class ClassificationResult:
    intent: QuestionIntent
    service_category: ServiceCategory | None
    # True when the question clearly names a *known but unsupported* service (electricity is the
    # only one in this data set today) -- lets the caller return an honest "don't have that"
    # response immediately, without ever running a vector search that might turn up an
    # unrelated-but-textually-similar chunk (see the module docstring's WATER_DRAINAGE example).
    out_of_scope_service: str | None
    matched_keywords: list[str]
    # True when the question is specifically about applying for a NEW water/sewerage connection
    # (as opposed to a fault/repair on an existing one). Added in the KB-expansion phase alongside
    # real new-connection KnowledgeRecords for Mohali/Patiala/Odisha (see
    # _NEW_CONNECTION_TOPIC_KEYWORDS below) -- lets the RAG flow apply a stricter post-retrieval
    # filter for exactly this topic, so a city with no new-connection record still answers
    # honestly instead of returning a semantically-similar-but-wrong repair/leak chunk (see
    # rag_flow_node's docstring for the measured false positive this closes).
    requests_new_connection: bool = False


# --- TYPE_C: personal complaint status/tracking -- checked first, highest priority ---
# gu/bn added after a live stress-test survey found this project's 6 supported UI languages
# (see frontend-react/src/lib/i18n.ts) only ever had keyword coverage for 4 of them (en/hi/mr/or)
# -- Gujarati and Bengali had ZERO entries in every dict in this file, so a clear complaint typed
# in either language (e.g. "મારા ઘર પાસે સ્ટ્રીટ લાઈટ બંધ છે" -- "the streetlight near my house is
# off") fell all the way through to UNCLEAR. Real, measured gap, not hypothetical -- reproduced
# live against the running backend before this fix.
_STATUS_KEYWORDS: dict[str, list[str]] = {
    "en": ["status", "track", "tracking", "has my complaint", "when will my", "assigned to",
           "complaint id", "complaint number", "complaint #", "resolved yet", "my complaint"],
    "hi": ["स्थिति", "ट्रैक", "मेरी शिकायत", "मेरा कंप्लेंट"],
    "mr": ["स्थिती", "माझी तक्रार", "ट्रॅक"],
    "or": ["ସ୍ଥିତି", "ମୋ ଅଭିଯୋଗ", "ଟ୍ରାକ୍"],
    "gu": ["સ્થિતિ", "ટ્રેક", "મારી ફરિયાદ"],
    "bn": ["অবস্থা", "ট্র্যাক", "আমার অভিযোগ"],
}
_STATUS_NUMBER_PATTERN = re.compile(r"complaint\s*#?\s*\d+|#\d+", re.IGNORECASE)

# --- TYPE_B: service/information request (new connection, documents, contact, application) ---
_SERVICE_INFO_KEYWORDS: dict[str, list[str]] = {
    "en": ["new connection", "new water connection", "new electricity connection", "new pipeline",
           # "new sewerage connection" was missing even before this phase (only water/electricity/
           # pipeline were listed) -- surfaced by this phase's own new VERIFIED sewerage-connection
           # records (Mohali/Patiala), which need this to classify as TYPE_B like their water
           # counterpart already does. Caught by test_new_sewerage_connection_in_patiala_answered_
           # from_verified_record, a real measured failure, not a hypothetical.
           "new sewerage connection", "new sewer connection",
           "apply", "application", "documents required", "which department", "who should i contact",
           "who handles", "who do i contact", "official website", "contact number", "download",
           "application form", "how can i get", "how do i get", "i am new here", "i want a new",
           "i need a new",
           # "documents required" (above) doesn't match the more natural question form a citizen
           # actually types -- caught via the service-flow phase's own worked example ("What
           # documents do I need for a water connection?"), which measurably misclassified as
           # TYPE_A_COMPLAINT (falls to the "something is wrong" default, no service-info keyword
           # matched) instead of TYPE_B_SERVICE_INFO before this fix. Same category of real,
           # measured false negative as the NEW_CONNECTION_KEYWORDS fix below -- not hypothetical.
           "what documents", "which documents", "documents do i need", "documents needed",
           # Plain English fee/cost questions ("What is the water connection fee in Patiala?")
           # had NO English keyword at all here -- only the Hinglish equivalents below ("kitni
           # fees") were covered. A real, measured failure caught by this project's own live
           # testing: with zero service-info match and zero complaint-verb match, such a question
           # fell to classify()'s bare-category-only default (TYPE_A_COMPLAINT) and ACTUALLY FILED
           # A COMPLAINT for someone who only asked what something costs. `complaint_matches` still
           # wins if a genuine complaint mentions a fee too (e.g. "I was overcharged, please look
           # into this") -- classify() checks `service_info_matches and not complaint_matches`
           # before falling here, so this fix only catches pure info-seeking phrasing.
           "fee", "fees", "cost of", "how much does", "how much is", "how much will", "charges for",
           "what does it cost", "what is the cost", "what is the charge", "price of",
           # Same gap, same fix pattern, for schedule/timing questions -- "When does garbage
           # collection resume?" is exactly as info-seeking as a fee question, but had the
           # identical zero-English-keyword gap (only the Hinglish "schedule kya hai" below was
           # covered). Real, measured: caught live filing an actual phantom complaint for someone
           # who only asked when a service runs. "when will" is intentionally NOT added bare here
           # -- it's already a TYPE_C status keyword ("when will my"), checked first in classify()
           # and would win regardless, so adding it here would be redundant, not a fix.
           #
           # P0 SAFETY FIX: bare "when does"/"what time does"/"what time is"/"how often does"/
           # "how often is" were too broad -- "what time is" substring-matches "What time is it
           # in Mumbai?", a plain small-talk question with zero civic-service content, and routed
           # it to a RAG answer about an unrelated topic (live-reproduced by the production-safety
           # audit that found this). Replaced with the same phrases anchored to an actual civic
           # service word, so a genuine schedule question ("What time does garbage collection
           # happen?") still matches while a bare time-of-day question does not.
           "when does collection", "when does garbage collection", "when does pickup",
           "when does the collection", "when does water supply", "when does the water supply",
           "what time does collection", "what time does the collection",
           "what time does garbage collection", "what time does pickup",
           "what time does water supply", "what time is collection", "what time is the collection",
           "what time is water supply", "what time is the water supply",
           "how often does collection", "how often does pickup", "how often does garbage collection",
           "how often is collection", "how often is pickup",
           "resume collection", "collection schedule", "pickup schedule", "next collection",
           "collection hours", "pickup hours", "service hours",
           "when is collection", "maintenance schedule", "cleaning schedule",
           # "What is the procedure for X complaints/repairs?" / "What are the rules for X?" --
           # clearly information-seeking framing about a civic process, not a complaint (same P0
           # fix; a live-reproduced example: "What is the procedure for garbage collection
           # complaints in Pune?" and "What are the rules for streetlight repair in Kanpur?" both
           # silently became real, filed-and-assigned complaints before this fix, because
           # "complaints"/"repair" contributed no info-seeking signal of their own).
           "what is the procedure", "what's the procedure", "the procedure for",
           "what are the rules", "what is the rule", "the rules for",
           # Same gap, same fix pattern, caught by testing this project's own built-in suggested
           # prompts (frontend-react/src/lib/i18n.ts's "ask.suggested.pothole"/"garbage"/
           # "streetlight", shown to every new user) -- process/policy questions with zero
           # matching keyword, so a bare category match ("garbage"/"pothole") alone forced
           # TYPE_A_COMPLAINT.
           "how are potholes prioritized", "prioritized for repair", "how is repair prioritized",
           "collection was skipped", "garbage collection was skipped", "if collection is skipped",
           "how long does a repair take", "how long does repair take", "how long does a streetlight repair take",
           # Romanized/Hinglish phrasing -- caught via the KB-expansion phase's live testing:
           # "Mujhe naya water connection chahiye, documents kya lagenge?" misclassified as
           # TYPE_A_COMPLAINT because none of the keyword lists above have any Latin-script Hindi
           # entries at all, only English and Devanagari/Odia script. This project's classifier has
           # no language-specific routing (classify() checks every list regardless of the request's
           # declared `language`, see _any_match below), so these entries work for romanized input
           # without any other change. Deliberately phrases, not single generic words (e.g. not a
           # bare "chahiye", which means "want/need" and would false-positive on unrelated
           # complaint sentences) -- see tests/test_ask_sarthi.py's Hinglish regression tests for
           # the non-overfitting checks (paraphrases beyond the exact four example sentences, and a
           # confirmation that a plain complaint sentence is unaffected).
           "naya water connection", "nayi water connection", "naya pipeline connection",
           "nayi pipeline connection", "naya sewerage connection", "nayi sewerage connection",
           "naya connection chahiye", "nayi connection chahiye",
           "documents kya lagenge", "kya lagenge", "kya lagega", "kaise milega", "kaise milegi",
           "kahan se milega", "kaha se milega", "kahan milega",
           "kitne paise", "kitna paisa", "paise lagenge", "fees kitni", "kitni fees",
           "kaunsa department", "department kaunsa", "responsible department",
           "schedule kya hai", "kya schedule hai", "maintenance kaun karta hai", "kaun karta hai",
           # LIVE-REPORTED GAP: this app's own intent_ambiguous clarification (nodes.py, ~line
           # 1014) literally ASKS "...or would you like information about it?" -- but a citizen
           # who naturally replies by echoing that exact phrasing back ("Information about it.")
           # had no matching keyword anywhere in this list, only the bare button label "What is
           # the procedure?" did. That free-typed, entirely natural reply fell through to the
           # complaint-leaning default and wrongly asked "What issue would you like to report?" --
           # a real, reproduced failure, not hypothetical. Phrases, not a bare "information" alone,
           # to avoid over-matching an unrelated sentence that happens to contain that word.
           "information about it", "more information", "just information", "some information",
           "want information", "need information", "give me information", "want more information"],
    "hi": ["नया कनेक्शन", "नया पानी कनेक्शन", "आवेदन", "दस्तावेज", "संपर्क नंबर", "कैसे लगवाएं",
           # Same class of bug as the English fee/schedule fixes above, caught the same way (this
           # time by testing the app's own built-in "How are potholes prioritized for repair?" /
           # "What if garbage collection was skipped?" / "How long does a streetlight repair
           # take?" suggested prompts, shown to every new user, in every one of this project's 6
           # languages -- not hypothetical). These are genuine info-seeking process/policy
           # questions with no English-style keyword coverage at all for any non-English language,
           # so a bare category match ("कचरा"/"गड्ढा"/...) alone forced TYPE_A_COMPLAINT, or
           # nothing matched at all and it fell through to UNCLEAR.
           "प्राथमिकता कैसे तय होती है", "संग्रह छूट जाए", "छूट जाए तो क्या करें",
           "मरम्मत में कितना समय लगता है", "कितना समय लगता है"],
    "mr": ["नवीन कनेक्शन", "नवीन पाणी कनेक्शन", "अर्ज", "कागदपत्रे",
           "प्राधान्य कसे ठरवले जाते", "संकलन चुकल्यास", "चुकल्यास काय करावे",
           "दुरुस्तीला किती वेळ लागतो", "किती वेळ लागतो"],
    # "ନୂଆ ପାଣି ସଂଯୋଗ" ("new water connection") is NOT just "ନୂଆ ସଂଯୋଗ" ("new connection") with a
    # word removed -- the service noun sits between "new" and "connection" in natural Odia
    # phrasing, same as English/Hindi/Marathi. A bare "ନୂଆ ସଂଯୋଗ" substring never matches real
    # sentences and was a genuine bug (caught by test_multilingual_odia_new_connection_is_type_b),
    # not a hypothetical -- list the full phrases actually used instead of assuming adjacency.
    "or": ["ନୂଆ ପାଣି ସଂଯୋଗ", "ନୂଆ ସଂଯୋଗ", "ଆବେଦନ",
           "କିପରି ପ୍ରାଥମିକତା ସ୍ଥିର ହୁଏ", "ସଂଗ୍ରହ ଛାଡ଼ି", "ଛାଡ଼ି ଦିଆଗଲେ କଣ କରିବି",
           "ମରାମତିରେ କେତେ ସମୟ ଲାଗେ", "କେତେ ସମୟ ଲାଗେ"],
    "gu": ["નવું પાણી જોડાણ", "નવું જોડાણ", "અરજી", "દસ્તાવેજો", "સંપર્ક નંબર",
           "કેવી રીતે પ્રાથમિકતા અપાય છે", "ઉપાડવાનું ચૂકી", "ચૂકી ગયું હોય તો શું કરવું",
           "સમારકામમાં કેટલો સમય લાગે છે", "કેટલો સમય લાગે છે"],
    "bn": ["নতুন পানির সংযোগ", "নতুন সংযোগ", "আবেদন", "কাগজপত্র", "যোগাযোগ নম্বর",
           "কীভাবে অগ্রাধিকার ঠিক করা হয়", "সংগ্রহ বাদ পড়লে", "বাদ পড়লে কী করব",
           "মেরামত করতে কত সময় লাগে", "কত সময় লাগে"],
}

# --- GREETING: small talk / conversational opener, not a civic question at all. Split the same
# way _CATEGORY_KEYWORDS/_CONFIRMATION_EXACT_WORDS already split short bare words from longer
# phrases: `_GREETING_SHORT_WORDS` are matched with a WORD-BOUNDARY regex (`_matches_word_
# boundary` below), not the file's usual plain substring `_any_match` -- "hi"/"hey" are short
# enough that plain substring matching would false-positive inside unrelated words ("history",
# "higher", "chirp"); a 2-3 letter greeting word is exactly the case this file's own established
# "specific multi-word phrases, not single generic words" convention (see _CAPABILITIES_KEYWORDS'
# own comment) exists to guard against, so this uses regex word boundaries instead of just
# lengthening every entry into an artificial phrase. `_GREETING_PHRASES` are long enough (4+
# characters, usually multi-word) to stay with the file's normal substring convention. ---
_GREETING_SHORT_WORDS: dict[str, list[str]] = {
    "en": ["hi", "hii", "hey", "hiya", "yo"],
}
_GREETING_PHRASES: dict[str, list[str]] = {
    "en": ["hello", "good morning", "good afternoon", "good evening", "good night",
           "greetings", "howdy", "my name is", "i am ", "this is "],
    "hi": ["नमस्ते", "नमस्कार", "हैलो", "हाय", "गुड मॉर्निंग"],
    "mr": ["नमस्कार", "हॅलो"],
    "or": ["ନମସ୍କାର", "ହେଲୋ"],
    "gu": ["નમસ્તે", "હેલો"],
    "bn": ["নমস্কার", "হ্যালো"],
}


def _matches_word_boundary(text: str, keyword_lists: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    matches = []
    for kws in keyword_lists.values():
        for kw in kws:
            if re.search(rf"\b{re.escape(kw.lower())}\b", lowered):
                matches.append(kw)
    return matches


# --- CAPABILITIES: "what can this app do?" -- a real, in-domain, answerable question about
# Sarthi's own scope. Deliberately specific multi-word phrases, not bare "help" or "services"
# (which appear in genuine complaint/service-info sentences too, e.g. "please help fix this
# pothole" or "water services in my area are cut off") -- see _any_match's substring matching,
# which would otherwise false-positive on those. ---
_CAPABILITIES_KEYWORDS: dict[str, list[str]] = {
    "en": ["what services", "which services", "what can you do", "what can you help",
           "what do you do", "what all can you", "list of services", "services do you provide",
           "services do you offer", "services you provide", "services you offer",
           "what is janmitra", "what is jansarthi", "what is sarthi", "what is this app", "how can you help", "what kind of help",
           "what type of complaints", "what type of issues", "what can i report",
           "what can i complain about"],
    "hi": ["आप क्या कर सकते हैं", "कौन सी सेवाएं", "कौनसी सेवाएं", "जनमित्र क्या है"],
    "mr": ["तुम्ही काय करू शकता", "कोणत्या सेवा", "जनमित्र म्हणजे काय"],
    "or": ["ଆପଣ କଣ କରିପାରିବେ", "କେଉଁ ସେବା", "ଜନମିତ୍ର କଣ"],
    "gu": ["તમે શું કરી શકો છો", "કઈ સેવાઓ", "જનમિત્ર શું છે"],
    "bn": ["আপনি কী করতে পারেন", "কোন পরিষেবা", "জনমিত্র কী"],
}


# --- TYPE_A: complaint/issue -- "something is wrong" phrasing ---
#
# Split into two tiers -- STATE (an active problem is being described: "not working"/"leaking"/
# टूटा/ভাঙা/...) vs META (a word ABOUT the act of complaining/reporting itself: "report"/
# "complain"/शिकायत/तक्रार/ଅଭିଯୋଗ/ફરિયાદ/অভিযোগ) -- after a real, reported bug: "पानी के रिसाव की
# शिकायत कैसे करें?" ("How do I file a complaint about a water leak?") is a citizen asking about
# the PROCESS, not reporting anything right now, but शिकायत alone matched the old undivided list
# exactly like a genuine complaint would, so combined with the "पानी" category match it silently
# FILED AND ASSIGNED a real complaint (#39) for someone who only wanted to know how. Confirmed
# systemic, not Hindi-specific: "How do I report a water leakage?"/"How can I file a complaint
# about garbage?" hit the identical bug in English via "report"/"complain" -- and every one of
# this file's 6 languages has the same bare complaint-noun in its old list. A genuine STATE verb
# ("How do I fix my leaking pipe") still means TYPE_A unconditionally -- only a bare META word
# combined with explicit how-to framing (see `_HOW_TO_FILE_COMPLAINT_KEYWORDS` below) is
# downgraded, in `classify()`.
_COMPLAINT_STATE_KEYWORDS: dict[str, list[str]] = {
    "en": ["not working", "broken", "not collected", "problem", "issue",
           "leaking", "leak", "overflow", "near my house", "near my home", "near me",
           # "not collected" alone doesn't match "has NOT BEEN collected" (an extra word breaks
           # the plain substring match) -- a real, measured gap caught by this fix's own test
           # battery: "Garbage has not been collected from my street for three days." fell
           # through to the bare-category-only fallback instead of a confident state match.
           "not been collected", "hasn't been collected", "has not been collected",
           "isn't being collected", "not being collected",
           # Romanized/Hinglish state phrases -- same convention already established elsewhere in
           # this file for _SERVICE_INFO_KEYWORDS's Hinglish entries (see that dict's own
           # docstring: "this project's classifier has no language-specific routing... these
           # entries work for romanized input without any other change"). Added after this fix's
           # own tail-logic rewrite (a bare category match is no longer alone sufficient for
           # TYPE_A_COMPLAINT) turned a pre-existing romanized-script gap into a real regression:
           # "Mere ghar ke paas streetlight kharab hai" only matched the English word
           # "streetlight" (category), with zero state/verb signal in Latin script, so it can no
           # longer confidently resolve to TYPE_A without this.
           "kharab hai", "kharab h", "kaam nahi kar raha", "kaam nahi kar rahi",
           "toota hai", "toota hua hai", "tuta hai", "band hai"],
    "hi": ["काम नहीं कर रहा", "खराब है", "टूटा"],
    # FIX-AND-VERIFY PASS: "बंद आहे" ("is off/closed", used for things like a streetlight) does
    # NOT match "is broken" for a masculine/feminine noun like a pipe -- Marathi adjectives agree
    # in gender with the noun ("तुटला" masc., "तुटली" fem., "तुटले" neut.), the same class of gap
    # this file's own oblique-case comments already document elsewhere. Real, measured: caught by
    # this fix's own live 6-language test battery -- "माझ्या घराजवळ पाण्याचा पाईप तुटला आहे."
    # ("the water pipe near my house is broken") fell through to the ambiguous TYPE_A_MAYBE
    # fallback (safe, but not confident) purely because no gendered "broken" form was covered.
    "mr": ["काम करत नाही", "बंद आहे", "तुटला आहे", "तुटली आहे", "तुटले आहे"],
    "or": ["କାମ କରୁନାହିଁ", "ଖରାପ"],
    # Same gap, same fix, for Gujarati: "તૂટેલું" (neuter/masculine-adjacent form) doesn't match
    # "તૂટેલી" (feminine form, e.g. for પાઇપ/pipe) -- caught the same way, by the same test battery.
    "gu": ["કામ કરતું નથી", "તૂટેલું", "તૂટેલી"],
    "bn": ["কাজ করছে না", "ভাঙা"],
}
_COMPLAINT_META_KEYWORDS: dict[str, list[str]] = {
    "en": ["report", "complain"],
    "hi": ["शिकायत"],
    "mr": ["तक्रार"],
    "or": ["ଅଭିଯୋଗ"],
    "gu": ["ફરિયાદ"],
    "bn": ["অভিযোগ"],
}
# Union of the two tiers -- kept so every EXISTING caller/test that reasons about "did some
# complaint-shaped keyword match at all" (matched_keywords, the out-of-scope/service-info
# interplay below) keeps working unchanged; only `classify()`'s own TYPE_A decision needs the
# tiers kept separate.
_COMPLAINT_VERB_KEYWORDS: dict[str, list[str]] = {
    lang: _COMPLAINT_STATE_KEYWORDS[lang] + _COMPLAINT_META_KEYWORDS[lang] for lang in _COMPLAINT_STATE_KEYWORDS
}

# "How do I report/file a complaint?" -- explicitly asking about Sarthi's OWN process, not
# reporting an issue. Deliberately paired phrases (word for "how" + word for "complaint/report"
# together), never a bare "how"/"कैसे" alone, which would be far too generic and risk swallowing
# unrelated questions (see this file's own established convention of specific multi-word phrases
# over single generic words, e.g. the Hinglish entries above).
_HOW_TO_FILE_COMPLAINT_KEYWORDS: dict[str, list[str]] = {
    "en": ["how do i report", "how do i file", "how can i report", "how can i file",
           "how to report", "how to file", "how do i complain", "how can i complain",
           "how to complain", "how do i lodge", "how to lodge", "how do i raise", "how to raise a complaint"],
    "hi": ["शिकायत कैसे करें", "शिकायत कैसे करूं", "शिकायत कैसे दर्ज करें", "कैसे शिकायत करें",
           "कैसे शिकायत दर्ज करें", "शिकायत कैसे दर्ज करूं"],
    "mr": ["तक्रार कशी करावी", "तक्रार कशी करू", "तक्रार कशी नोंदवावी", "कशी तक्रार करावी"],
    # or/gu/bn: the app's own real suggested-prompt strings (frontend-react/src/lib/i18n.ts's
    # "ask.suggested.waterLeak") use "report"/"inform" wording (ରିପୋର୍ଟ/જાણ/রিপোর্ট), NOT
    # "complaint" (ଅଭିଯୋଗ/ફરિયાদ/অভিযোগ) -- an earlier version of this fix only covered the
    # "complaint"-word phrasing (an unverified guess), missing these entirely: `how_to_file_
    # matches` came back empty for the app's own real "ପାଣି ଲିକେଜ୍ କିପରି ରିପୋର୍ଟ କରିବି?"/"પાણીના
    # લીકેજની જાણ કેવી રીતે કરવી?"/"জলের লিকেজ কীভাবে রিপোর্ট করব?" prompts, so the override never
    # fired and the bare "ପାଣି/પાણી/পানি" (water) category match alone still forced
    # TYPE_A_COMPLAINT -- caught live by testing the app's OWN built-in prompts, not a hypothetical.
    "or": ["ଅଭିଯୋଗ କିପରି କରିବେ", "କିପରି ଅଭିଯୋଗ କରିବେ", "କିପରି ରିପୋର୍ଟ କରିବି", "ରିପୋର୍ଟ କିପରି କରିବି",
           "କିପରି ରିପୋର୍ଟ କରିବେ", "ରିପୋର୍ଟ କିପରି କରିବେ"],
    "gu": ["ફરિયાદ કેવી રીતે કરવી", "કેવી રીતે ફરિયાદ કરવી", "જાણ કેવી રીતે કરવી", "કેવી રીતે જાણ કરવી"],
    "bn": ["অভিযোগ কীভাবে করব", "কীভাবে অভিযোগ করব", "রিপোর্ট কীভাবে করব", "কীভাবে রিপোর্ট করব"],
}

# --- Service category detection (own keyword sets, deliberately NOT reusing
# frontend-react/src/lib/serviceCategories.tsx's looser list -- that list includes bare
# "electric" under STREETLIGHTS, which would make "new ELECTRICITY connection" misclassify as a
# streetlight question. Precision matters more here than in a client-side UI hint.)
#
# Oblique-case stems: both Hindi and Marathi are (partly, for Hindi; fully, for Marathi) fusional
# -- a masculine noun ending in -आ changes shape under a case suffix (की/में/पर/ने/...) instead of
# staying fixed while a separate postposition word attaches, e.g. Marathi पाणी -> पाण्याची, Hindi
# गड्ढा -> गड्ढे की. A citizen saying "गड्ढ्याची तक्रार" ("a complaint about the pothole") is at
# least as natural as, if not more natural than, a bare-nominative sentence -- so a keyword list
# with only the nominative form silently misses it, returning service_category=None and asking
# the citizen to pick a category from a list that already includes the one they named. First
# caught for पाणी/पाण्या (see git history), then confirmed as the same systemic gap across every
# other masculine -आ noun in this list by direct classify() probing with natural oblique-case
# sentences -- each "X, X-oblique" pair below is a real, measured fix, not a guess:
# कचरा/कचऱ्या (mr), कचरा/कचरे + कूड़ा/कूड़े (hi), रस्ता/रस्त्या + खड्डा/खड्ड्या (mr),
# गड्ढा/गड्ढे (hi), दिवा/दिव्या (mr), नाला/नाले (hi). Feminine/consonant-ending nouns (सड़क, बत्ती,
# वीज, ...) and loanword phrases (स्ट्रीट लाइट) don't inflect this way and needed no change --
# confirmed by the same probing, not assumed. Odia not audited the same way (no verified oblique
# forms in hand) -- flagged, not guessed.
_CATEGORY_KEYWORDS: dict[ServiceCategory, dict[str, list[str]]] = {
    ServiceCategory.WASTE_SANITATION: {
        # "stray dog"/"public toilet"/"restroom" added after a live-reported gap: both are real
        # municipal sanitation-department responsibilities (animal control and public toilet
        # upkeep), but neither mentions "garbage"/"waste" etc., so they fell through to
        # unclassified even though a clear category exists for them.
        "en": ["garbage", "waste", "trash", "sanitation", "dump", "litter", "rubbish",
               "stray dog", "public toilet", "public restroom", "restroom"],
        "hi": ["कचरा", "कचरे", "कूड़ा", "कूड़े"], "mr": ["कचरा", "कचऱ्या"], "or": ["ଆବର୍ଜନା"],
        "gu": ["કચરો", "કચરાની"], "bn": ["আবর্জনা", "ময়লা"],
    },
    ServiceCategory.WATER_DRAINAGE: {
        "en": ["water", "drain", "drainage", "sewage", "sewer", "pipeline", "pipe connection", "flood"],
        "hi": ["पानी", "नाला", "नाले", "पाइप"], "mr": ["पाणी", "पाण्या"], "or": ["ପାଣି"],
        "gu": ["પાણી", "ગટર"], "bn": ["পানি", "জল", "নর্দমা"],
    },
    # STREETLIGHTS is checked BEFORE ROADS_POTHOLES -- deliberately reordered (was after), because
    # "first match wins" (see classify()'s own loop) otherwise systematically favors ROADS_POTHOLES
    # for the single most common way people phrase a streetlight complaint: naming the road a
    # streetlight is ON. Live-reproduced across FOUR of this app's five non-English languages by
    # direct classify() probing, not just the one live-reported case (Odia): "सड़क की बत्ती खराब है"
    # (hi), "રસ્તાની બત્તી બંધ છે" (gu), "রাস্তার বাতি নষ্ট হয়ে গেছে" (bn), and "ରାସ୍ତା-ଆଲୁଅ...
    # (streetlight)" (or, the original report) all misclassified as ROADS_POTHOLES purely because
    # ROADS_POTHOLES's own road-word matched as a substring first -- even gu's existing genitive-
    # only narrowing (રસ્તાન, added for a DIFFERENT reason below) still matches રસ્તાની, since
    # "રસ્તાન" is itself a substring of "રસ્તાની". Narrowing each language's road-keyword further
    # to dodge every possible "road's X" phrasing is a losing, whack-a-mole fight (a road-owned
    # streetlight can be phrased a dozen ways); reordering fixes the whole class at once, generally,
    # because the asymmetry is structural, not per-language: a streetlight complaint very commonly
    # names "road" (it's describing WHERE the light is), but a genuine road/pothole complaint
    # essentially never mentions "light/bulb/lamp" words at all. Verified this reorder doesn't
    # regress the OTHER direction: no en/hi/mr/or/gu/bn STREETLIGHTS keyword appears in this file's
    # existing ROADS_POTHOLES-only regression phrases.
    ServiceCategory.STREETLIGHTS: {
        "en": ["streetlight", "street light", "street lamp", "lamp post"],
        # CONVERSATION & REQUEST/RESPONSE ALIGNMENT AUDIT: each non-English transliteration of
        # "street light" below previously only had the SPACED two-word form -- a real, measured
        # gap caught by this audit's own live testing: "मोहाली में स्ट्रीटलाइट खराब है।" (written
        # as one compound word, at least as natural a way to type an English loanword as with a
        # space) matched no category at all, only the generic "is broken" state keyword, so the
        # citizen was asked "what issue would you like to report?" despite having already named
        # one. Same fix, same reasoning, applied consistently to every language whose existing
        # entry was the spaced form (hi/mr share Devanagari; gu/bn have their own scripts).
        # LIVE-REPORTED GAP: "स्ट्रीट लाईट"/"स्ट्रीटलाईट" (long ई) added alongside the existing
        # "स्ट्रीट लाइट"/"स्ट्रीटलाइट" (short इ) -- both are genuinely common ways to spell this
        # English loanword's second syllable in casual Hindi/Marathi typing (the long-ई form
        # actually reads closer to how "light" is pronounced), and only the short-इ form was
        # covered -- a real citizen's "...स्ट्रीट लाईट खराब आहे..." matched no STREETLIGHTS
        # keyword at all, only the generic "is broken" state, so they were asked "what issue would
        # you like to report?" despite having already named one.
        "hi": ["स्ट्रीट लाइट", "स्ट्रीटलाइट", "स्ट्रीट लाईट", "स्ट्रीटलाईट", "बत्ती"],
        "mr": ["स्ट्रीट लाइट", "स्ट्रीटलाइट", "स्ट्रीट लाईट", "स्ट्रीटलाईट", "दिवा", "दिव्या"],
        # ଆଲୁଅ added alongside the existing ଆଲୋକ -- live-reported gap: a real citizen question,
        # "...ରାସ୍ତା-ଆଲୁଅ (streetlight) ବିଷୟରେ...", used this everyday colloquial word for "light"
        # (ଆଲୋକ is the more formal/literary register) and matched neither the old single Odia
        # entry nor, until the reorder above, would reordering alone have been enough -- the text
        # never matched ANY streetlight keyword at all before this addition, so it fell all the way
        # through to ROADS_POTHOLES's bare "ରାସ୍ତା" regardless of check order.
        "or": ["ଆଲୋକ", "ଆଲୁଅ"],
        "gu": ["સ્ટ્રીટ લાઈટ", "સ્ટ્રીટલાઈટ", "બત્તી"],
        "bn": ["স্ট্রিট লাইট", "স্ট্রিটলাইট", "বাতি"],
    },
    ServiceCategory.ROADS_POTHOLES: {
        "en": ["road", "pothole", "footpath", "pavement", "civil work"],
        # mr "रस्त्याच" (genitive-only, e.g. रस्त्याची/रस्त्याचा/रस्त्याचे -- "of/about the road"),
        # deliberately narrower than the bare oblique stem "रस्त्या" used for the other nouns in
        # this file: रस्ता's locative form "रस्त्यावर"/"रस्त्यावरचा" ("on the road") is an
        # extremely common way to LOCATE a completely different complaint (a streetlight or a
        # pothole ON the road, garbage ON the road), not a complaint about the road itself.
        "hi": ["सड़क", "गड्ढा", "गड्ढे"], "mr": ["रस्ता", "रस्त्याच", "खड्डा", "खड्ड्या"], "or": ["ରାସ୍ତା"],
        # gu "રસ્તાન" (genitive-only stem, covers રસ્તાની/રસ્તાનો/રસ્તાનું -- "of the road"),
        # same narrowing as mr "रस्त्याच" above and for the same reason: Gujarati masculine nouns
        # ending in -ો shift to oblique -ા before a postposition (દીકરો -> દીકરાની, "of the son"),
        # so રસ્તો ("road") has the identical bare-nominative-only gap પાણી/रस्ता had -- but its
        # locative form "રસ્તા પર" ("on the road") is just as common a way to locate an unrelated
        # complaint, confirmed by this fix's own regression check ("રસ્તા પર કચરો છે" correctly
        # stayed WASTE_SANITATION, not ROADS_POTHOLES, precisely because "રસ્તાન" does NOT match
        # the locative "રસ્તા પર"/"રસ્તામાં" forms). ખાડાની ("of the pothole") added as a literal
        # rather than a stem, matching કચરાની's precedent above -- ખાડો has no comparable
        # generic-location idiom, so the extra caution wasn't needed there. (The STREETLIGHTS-vs-
        # ROADS_POTHOLES collision this same "રસ્તાન" also turned out to have, against "રસ્તાની
        # બત્તી", is now handled by the category CHECK ORDER above instead -- no further narrowing
        # needed here.)
        "gu": ["રસ્તો", "રસ્તાન", "ખાડો", "ખાડાની"],
        "bn": ["রাস্তা", "গর্ত"],
    },
}

# Known-but-unsupported services -- checked explicitly so "electricity" never gets silently
# absorbed into STREETLIGHTS (they share the word "electric" in casual English) or ignored
# entirely (which would let a generic vector search return an unrelated WATER/ROADS chunk).
_OUT_OF_SCOPE_KEYWORDS: dict[str, list[str]] = {
    "en": ["electricity", "electric connection", "electric meter", "power connection", "power supply"],
    "hi": ["बिजली"], "mr": ["वीज"], "or": ["ବିଦ୍ୟୁତ"],
    "gu": ["વીજળી"],
    # bn: বিদ্যুৎ ("electricity") ends in the letter ৎ (khanda ta), which Bengali sandhi rules
    # regularly change to ত when a vowel-initial suffix follows -- বিদ্যুৎ + এর ("of electricity")
    # surfaces as বিদ্যুতের, not বিদ্যুতেরৎ. Unlike Marathi/Gujarati's vowel-final noun mutation
    # (this file's other oblique-case fixes), Bengali is otherwise agglutinative -- most nouns here
    # (রাস্তা/গর্ত/আবর্জনা) keep their own shape intact under a suffix, confirmed by this fix's own
    # regression check. বিদ্যুৎ is the one specific exception found: without this entry,
    # "বিদ্যুতের নতুন সংযোগ চাই" ("I want a new electricity connection") missed the ELECTRICITY
    # out-of-scope flag entirely and fell to the generic "new connection" label instead -- still
    # correctly kept out of RAG, but with a less specific/honest response than the dedicated
    # "don't have electricity" message this KB is supposed to give.
    "bn": ["বিদ্যুৎ", "বিদ্যুতে"],
}

# Bare "new connection" (utility unspecified) -- checked separately from the category detector
# above. Originally (RAG phase) this list also caught "new water connection"/"new pipeline
# connection"/"new sewer(age) connection" specifically, because this knowledge base had zero
# new-connection-application records in any city -- routing those phrases to RAG would have let
# the retriever return an unrelated water-LEAK-repair chunk as if it answered a new-connection
# question (a real, measured false positive; see docs/ask_sarthi_rag_architecture.md's
# evaluation section for the before/after numbers).
#
# That's no longer true for water/sewerage specifically: the KB-expansion phase added real,
# VERIFIED new-water-connection and new-sewerage-connection records for Mohali, Patiala, and
# (water only) Odisha, extracted directly from the same citizen-charter PDFs already used for
# this project's other VERIFIED records (see data/rag_knowledge_base/knowledge_records/verified/).
# Short-circuiting those phrases to a canned "don't have that" response would now be actively
# wrong for exactly the cities that DO have this data -- so "new water connection"/"new pipeline
# connection"/"new sewerage connection" (and their Hindi/Marathi/Odia equivalents) were removed
# from this list and now flow to TYPE_B_SERVICE_INFO -> RAG like any other service-info question.
# RagRetriever's own category+location filtering and relevance threshold already handle the
# "not covered for this specific city" case honestly (insufficient_knowledge=True, never a
# fabricated or unrelated-chunk answer) -- the same mechanism that protects every other question
# this system answers, not a special case for this one.
#
# Bare "new connection" (no stated utility) stays here deliberately: it's still genuinely
# ambiguous (water? sewerage? electricity, still out of scope? gas, never in scope?) and this
# project has no data for most of those, so routing it to a canned "which service?" response
# remains the honest choice pending a real disambiguation step this phase didn't build.
_NEW_CONNECTION_KEYWORDS: dict[str, list[str]] = {
    "en": ["new connection"],
    "hi": ["नया कनेक्शन"],
    "mr": ["नवीन कनेक्शन"],
    "or": ["ନୂଆ ସଂଯୋଗ"],
    "gu": ["નવું જોડાણ"],
    "bn": ["নতুন সংযোগ"],
}

# Detects "this question is specifically about a NEW water/sewerage connection" regardless of
# which city it's asked about -- the phrases moved out of _NEW_CONNECTION_KEYWORDS above (they no
# longer force out-of-scope) are reused here for a different purpose: flagging
# ClassificationResult.requests_new_connection so rag_flow_node can apply a stricter filter after
# retrieval (see that flag's own docstring). Deliberately a superset of what
# _SERVICE_INFO_KEYWORDS needs for TYPE_B classification -- this list only has to be *specific*
# enough to reliably mean "new connection", not exhaustive of every info-seeking phrase.
_NEW_CONNECTION_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "en": ["new water connection", "new sewerage connection", "new pipeline connection", "new pipeline",
           "naya water connection", "nayi water connection", "naya pipeline connection",
           "nayi pipeline connection", "naya sewerage connection", "nayi sewerage connection"],
    "hi": ["नया पानी कनेक्शन"],
    "mr": ["नवीन पाणी कनेक्शन"],
    "or": ["ନୂଆ ପାଣି ସଂଯୋଗ"],
    "gu": ["નવું પાણી જોડાણ"],
    "bn": ["নতুন পানির সংযোগ"],
}


def _any_match(text: str, keyword_lists: dict[str, list[str]]) -> list[str]:
    lowered = text.lower()
    return [kw for kws in keyword_lists.values() for kw in kws if kw.lower() in lowered]


# --- P0 SAFETY FIX helpers -- see QuestionIntent.TYPE_A_MAYBE's own docstring for what this
# closes. `_looks_like_question` distinguishes information-seeking phrasing ("What is the
# procedure for...?") from a bare fragment/statement -- deliberately simple (a trailing "?" or a
# leading interrogative word/phrase), matching this file's own established preference for plain,
# auditable string checks over anything fuzzy. `is_explicit_confirmation`/`is_explicit_
# cancellation` are used by orchestration/nodes.py's complaint_flow_node to gate the one action
# in this whole codebase that must NEVER fire on an inferred/guessed signal: actually creating a
# complaint. Deliberately a narrow allowlist, not a broad "sounds positive" heuristic -- "okay"/
# "fine"/"continue"/"tell me more" must NOT confirm, and a "yes" that's also a question ("yes,
# what are the rules?") must not either. No LLM anywhere in this file, including here -- see the
# module's own top-of-file docstring for why intent-adjacent decisions in this codebase are
# deterministic string matching, never a model call.
_QUESTION_MARKERS: dict[str, list[str]] = {
    "en": ["what", "how", "when", "who", "which", "why", "is there", "are there", "does",
           "do i", "can i", "could i", "should i", "is it", "are you", "kya", "kaise", "kab"],
    "hi": ["क्या", "कैसे", "कब", "कहाँ", "कौन"],
    "mr": ["काय", "कसे", "कधी", "कुठे", "कोण"],
}


def _looks_like_question(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.endswith("?"):
        return True
    lowered = stripped.lower()
    for markers in _QUESTION_MARKERS.values():
        for marker in markers:
            if lowered.startswith(marker.lower()):
                return True
    return False


_CONFIRMATION_EXACT_WORDS: dict[str, set[str]] = {
    # DELIBERATELY does NOT include "sure" -- tried once (manual test round's own "yes please"
    # gap fix), reverted after this file's own EXISTING test suite caught it:
    # test_confirmation_bare_sure_with_pending_draft_does_not_confirm and the parametrized
    # test_ambiguous_replies_still_safely_reask_not_misread_as_fresh_complaints[sure] both
    # already document, deliberately, that "sure" is grouped with "okay"/"maybe"/"fine" as a
    # plausible-sounding but NOT unambiguous affirmative -- must re-ask, never guess. Respecting
    # that earlier, already-tested judgment call rather than overriding it.
    "en": {"yes", "yeah", "yep", "yup", "submit", "confirm"},
    "hi": {"haan", "ha", "हाँ", "हां", "हा"},
    "mr": {"हो"},
    # FINAL FIX-AND-VERIFY PASS: Odia/Gujarati/Bengali were previously unconfigured entirely (a
    # citizen replying "yes" in these languages could never confirm a complaint -- safe, but
    # incomplete). Words below are real Sarvam-translated output, verified live (see this fix's
    # own report), extending the exact same allowlist pattern already used for hi/mr -- not a new
    # mechanism.
    "or": {"ହଁ"},
    "gu": {"હા"},
    "bn": {"হ্যাঁ"},
}
_CONFIRMATION_PHRASES: dict[str, list[str]] = {
    "en": [
        "yes submit it", "yes, submit it", "yes file it", "yes, file it",
        "yes register it", "yes, register it", "yes create it", "yes, create it",
        "yes confirm", "yes, confirm", "submit it", "submit the complaint",
        "file it", "file the complaint", "register it", "register the complaint",
        "create it", "create the complaint", "please submit", "please file",
        "please register", "go ahead and submit", "go ahead and file",
        # Live-reported gap (manual test round): "yes please" repeated the confirmation prompt
        # instead of filing -- added here alongside the other natural phrasings this allowlist was
        # missing. "go ahead" (bare, no "and submit"/"and file" after it) is the same unambiguous
        # register as the "go ahead and submit" pair already above. "ok submit it"/"okay, submit
        # it" and "confirm it" are the same "action word present, no real ambiguity" shape as
        # "submit it"/"yes confirm" already here -- "confirm" alone is deliberately excluded from
        # `_CONFIRMATION_EXACT_WORDS`'s own lookahead (see that dict's comment), so "confirm it"
        # needed its own exact-phrase entry instead.
        "yes please", "yes, please", "go ahead", "ok submit it", "ok, submit it",
        "okay submit it", "okay, submit it", "confirm it",
        "haan submit karo", "ha submit karo", "haan, submit karo", "haan complaint register karo",
        "ha, complaint register karo",
    ],
    "hi": ["हाँ जमा करो", "हाँ दर्ज करो", "शिकायत दर्ज करो", "शिकायत जमा करो", "हाँ सबमिट करो", "जमा करो", "दर्ज करो"],
    "mr": ["हो सबमिट करा", "हो नोंदवा", "तक्रार नोंदवा", "नोंदवा"],
    "or": ["ହଁ, ଏହାକୁ ଦାଖଲ କରନ୍ତୁ", "ଏହାକୁ ଦାଖଲ କରନ୍ତୁ", "ଦାଖଲ କରନ୍ତୁ"],
    "gu": ["હા, તેને સબમિટ કરો", "તે સબમિટ કરો", "સબમિટ કરો"],
    "bn": ["হ্যাঁ, জমা দিন", "এটা জমা দিন", "জমা দিন"],
}
_CANCELLATION_EXACT_WORDS: dict[str, set[str]] = {
    # CONVERSATION & REQUEST/RESPONSE ALIGNMENT AUDIT: "stop" added -- a real, evidenced gap found
    # by this audit's own adversarial confirmation-safety testing (a bare "Stop" reply to a
    # pending confirmation prompt was previously unrecognized, safely re-asking rather than
    # cancelling -- not unsafe, but a genuine request/response mismatch for an extremely common,
    # natural way to say "don't do that"). Same bare-word pattern already used for "no"/"nope"/
    # "cancel", not a new mechanism.
    "en": {"no", "nope", "cancel", "stop"},
    "hi": {"nahi", "नहीं", "ना"},
    "mr": {"नाही"},
    "or": {"ନା"},
    "gu": {"ના"},
    "bn": {"না"},
}
_CANCELLATION_PHRASES: dict[str, list[str]] = {
    "en": ["no, don't submit", "don't submit", "no don't submit", "never mind", "no thanks",
           "no cancel", "no, cancel", "cancel it", "cancel the complaint"],
    "hi": ["मत करो", "रद्द करो", "नहीं, मत करो"],
    "mr": ["नको", "रद्द करा"],
    "or": ["ଏହାକୁ ଦାଖଲ କରନ୍ତୁ ନାହିଁ", "ବାତିଲ୍ କରନ୍ତୁ"],
    "gu": ["તેને સબમિટ કરશો નહીં", "રદ કરો"],
    "bn": ["এটা জমা দিও না", "বাতিল করুন"],
}


def _normalize_for_confirmation(text: str) -> str:
    return text.strip().lower().strip(" .!,।")


# Deliberately narrow -- a bare yes-word followed CLOSELY (within this many words) by an action
# word ("yes submit it", "haan complaint register karo") is still an unambiguous confirmation not
# already covered by the exact `_CONFIRMATION_PHRASES` list below. Checking only a short window
# right after the yes-word -- not "does an action word appear anywhere at all in the message" --
# is what keeps this contextual rather than a bare keyword-anywhere match: a longer reply that
# merely STARTS with a yes-word but goes on to say something else ("yes, I already submitted a
# complaint about this") has its action-word-shaped substring ("submitted") several words further
# out, past this window, and correctly does NOT confirm. Real, reported edge case
# (production-safety review) this window closes: without it, that exact sentence satisfied the
# old, unbounded "yes-word anywhere + action-word anywhere in the whole message" check. The window
# is deliberately measured in WORDS after the yes-word, not total message length -- a short
# genuine reply padded with extra trailing content (e.g. an image/voice endpoint's photo caption
# folded onto the citizen's own short reply, see orchestration/nodes.py's intent_node docstring)
# must still confirm correctly, since the action word is still immediately adjacent to "yes" even
# though the message as a whole is long.
_CONFIRMATION_FALLBACK_LOOKAHEAD_WORDS = 2


def is_explicit_confirmation(text: str) -> bool:
    """True only for an unambiguous, deterministic "yes, submit this complaint" reply -- see this
    section's module-level docstring. Never treats a merely positive-sounding or continuation
    word ("okay", "fine", "continue", "tell me more") as confirmation, and never treats a "yes"
    that's part of a further question ("yes, what are the rules?") as one either. Also never
    treats a longer reply that merely STARTS with a yes-word but goes on to say something else
    ("yes, I already submitted a complaint about this") as confirmation -- see
    `_CONFIRMATION_FALLBACK_LOOKAHEAD_WORDS`'s own comment. A reply this function doesn't
    recognize is simply not a confirmation (never a cancellation either) -- the caller
    (`complaint_flow_node`) re-asks rather than guessing, the safe direction to fail in for a
    check that gates a database write."""
    normalized = _normalize_for_confirmation(text)
    if not normalized or _looks_like_question(text):
        return False
    for words in _CONFIRMATION_EXACT_WORDS.values():
        if normalized in words:
            return True
    for phrases in _CONFIRMATION_PHRASES.values():
        if normalized in phrases:
            return True
    words_in_text = normalized.split(" ")
    first_word = words_in_text[0].strip(",")
    yes_words = {w for words in _CONFIRMATION_EXACT_WORDS.values() for w in words if w not in {"submit", "confirm"}}
    if first_word in yes_words:
        action_words = ["submit", "file", "register", "create", "confirm", "जमा", "दर्ज", "सबमिट", "नोंदव",
                        "ଦାଖଲ", "ସବମିଟ", "સબમિટ", "জমা"]
        nearby = " ".join(words_in_text[1:1 + _CONFIRMATION_FALLBACK_LOOKAHEAD_WORDS])
        if any(w in nearby for w in action_words):
            return True
    return False


# PRODUCTION ARCHITECTURE UPGRADE: unlike _CANCELLATION_PHRASES (matched only when the ENTIRE
# normalized message equals one of those phrases exactly -- see is_explicit_cancellation's own
# `normalized in phrases` check), these are matched as a SUBSTRING anywhere in the message, so a
# real preamble ("Actually, forget the complaint.") still cancels -- caught by this phase's own
# adversarial test matrix ("Actually forget the complaint." -> cancel), which the exact-match-only
# check missed even though "never mind" alone was already in _CANCELLATION_PHRASES. Deliberately
# specific multi-word phrases ("forget the complaint"/"forget it"), never a bare "forget" --
# "forget" alone is common in unrelated genuine messages ("I forget my complaint number"). Safe to
# be a little more permissive here than the equivalent confirmation-side check
# (`is_explicit_confirmation`'s lookahead window): the failure mode of an over-eager
# CANCELLATION is one extra "please describe the issue again" round-trip, never an unwanted
# complaint being created -- the safe direction to fail in.
_CANCELLATION_SUBSTRING_PHRASES = ("forget the complaint", "forget it", "forget about it")


def is_explicit_cancellation(text: str) -> bool:
    """Deterministic counterpart to `is_explicit_confirmation` -- see that function's docstring."""
    normalized = _normalize_for_confirmation(text)
    if not normalized or _looks_like_question(text):
        return False
    for words in _CANCELLATION_EXACT_WORDS.values():
        if normalized in words:
            return True
    for phrases in _CANCELLATION_PHRASES.values():
        if normalized in phrases:
            return True
    if any(phrase in normalized for phrase in _CANCELLATION_SUBSTRING_PHRASES):
        return True
    first_word = normalized.split(" ", 1)[0].strip(",")
    # CONVERSATION & REQUEST/RESPONSE ALIGNMENT AUDIT: "cancel" used to be excluded here (the
    # bare word alone was already caught by the exact-match check above, but "cancel" + trailing
    # words -- "cancel that complaint", "cancel this complaint" -- fell through, live-tested and
    # found as a real gap). "no"/"nahi"/etc. already get this same first-word-plus-trailing-words
    # treatment; there was no principled reason for "cancel" alone to be treated differently, so
    # this closes the asymmetry rather than adding another phrase to a list -- generalizes to any
    # "cancel ..." reply, not just the specific wording tested.
    no_words = {w for words in _CANCELLATION_EXACT_WORDS.values() for w in words}
    return first_word in no_words


def is_explicit_location_change_request(text: str) -> bool:
    """Deterministic counterpart to is_explicit_confirmation/is_explicit_cancellation above, for
    the third quick-reply option on the complaint confirmation prompt ("Change location" -- see
    orchestration/nodes.py's _CONFIRMATION_OPTIONS). Only ever matches the literal button value --
    deliberately NOT a fuzzy/multilingual phrase list like the two functions above, because every
    quick-reply button's clicked value is always one of these fixed, hardcoded English strings
    regardless of the citizen's own UI language (see nodes.py's _localize_options docstring for
    why) -- a citizen who instead TYPES something like "I want to use a different ward" isn't
    recognized here and simply isn't treated as a location-change request, the same safe-fails-
    closed direction every check in this module already takes (a miss costs one extra round-trip,
    never a wrong/skipped action)."""
    normalized = _normalize_for_confirmation(text)
    return normalized == "change location"


def looks_like_an_attempted_yes_or_no(text: str) -> bool:
    """LIVE-REPORTED BUG (voice input): "Yes, can you submit please?" -- a natural, SPOKEN way to
    confirm, unlike the terser typed/button "yes, submit it" -- ends in "?", so
    `is_explicit_confirmation` correctly declines to auto-confirm it (a "yes" that's part of a
    further question is deliberately never auto-confirmed, e.g. "yes, what are the rules?" must
    never silently file anything -- that safety boundary is right and untouched here). The actual
    gap this closes is elsewhere: nodes.py's `intent_node` needs to decide whether an ambiguous
    reply mid-confirmation should even be ROUTED to complaint_flow_node's own safe re-ask logic at
    all (which neither confirms nor cancels on its own, just safely re-asks) -- and routing based
    on `_awaiting_confirmation` alone is too broad: a genuinely unrelated question mid-confirmation
    ("What is the current time?") would ALSO get routed there and re-shown the stale confirmation,
    reading as an odd non-answer to a real question, not just "safe but incomplete" -- live-caught
    by this project's own regression suite.

    Narrower than "is this an explicit confirm/cancel": true only when the reply's own FIRST WORD
    is a recognized yes/no word in any supported language, regardless of question-shape or
    trailing content -- "Yes, can you submit please?" and "Yes, what are the rules?" both qualify
    (this function only decides whether to ROUTE toward the safe re-ask, never whether to actually
    treat either as a confirmation -- `is_explicit_confirmation` alone still gates that, and still
    correctly declines the second one); "What is the current time?" does not start with a yes/no
    word at all, so it correctly falls through to UNCLEAR exactly as before this fix."""
    normalized = _normalize_for_confirmation(text)
    if not normalized:
        return False
    first_word = normalized.split(" ", 1)[0].strip(",")
    yes_or_no_words = {
        w
        for words in (*_CONFIRMATION_EXACT_WORDS.values(), *_CANCELLATION_EXACT_WORDS.values())
        for w in words
        if w not in {"submit", "confirm"}
    }
    return first_word in yes_or_no_words


def classify(question: str) -> ClassificationResult:
    """Classifies one question. Never raises. Strong problem-state language ("broken"/"not
    working"/...), or a complaint verb outside a question-shaped sentence, means TYPE_A_COMPLAINT
    (possibly still missing a category/location, which the caller asks for). A bare category
    mention or complaint-verb match with no such signal, in a question-shaped sentence, is
    TYPE_B_SERVICE_INFO; the same bare signal in a non-question fragment is TYPE_A_MAYBE -- never
    guessed straight into TYPE_A_COMPLAINT (see QuestionIntent.TYPE_A_MAYBE's own docstring for
    the P0 bug this closes). Failing all of that, a real "what can you do" question is
    CAPABILITIES; anything else that matched nothing at all is UNCLEAR -- never silently
    defaulted to TYPE_A_COMPLAINT (see QuestionIntent.UNCLEAR's own docstring for the earlier bug
    this replaced)."""
    text = question.strip()
    requests_new_connection = bool(_any_match(text, _NEW_CONNECTION_TOPIC_KEYWORDS))

    status_matches = _any_match(text, _STATUS_KEYWORDS)
    if _STATUS_NUMBER_PATTERN.search(text):
        status_matches.append("<complaint-number-pattern>")
    if status_matches:
        return ClassificationResult(
            intent=QuestionIntent.TYPE_C_STATUS,
            service_category=None,
            out_of_scope_service=None,
            matched_keywords=status_matches,
            requests_new_connection=requests_new_connection,
        )

    out_of_scope_matches = _any_match(text, _OUT_OF_SCOPE_KEYWORDS)
    out_of_scope_label = "ELECTRICITY" if out_of_scope_matches else None

    new_connection_matches = _any_match(text, _NEW_CONNECTION_KEYWORDS)
    if not out_of_scope_matches and new_connection_matches:
        out_of_scope_matches = new_connection_matches
        out_of_scope_label = "NEW_SERVICE_CONNECTION"

    service_info_matches = _any_match(text, _SERVICE_INFO_KEYWORDS)
    state_matches = _any_match(text, _COMPLAINT_STATE_KEYWORDS)
    meta_matches = _any_match(text, _COMPLAINT_META_KEYWORDS)
    complaint_matches = state_matches + meta_matches  # kept for out_of_scope's own check below
    how_to_file_matches = _any_match(text, _HOW_TO_FILE_COMPLAINT_KEYWORDS)

    category = None
    category_matches: list[str] = []
    for cat, lang_keywords in _CATEGORY_KEYWORDS.items():
        matches = _any_match(text, lang_keywords)
        if matches:
            category = cat
            category_matches = matches
            break  # first match wins -- categories are kept mutually distinct by keyword choice

    if out_of_scope_matches:
        # A service-info-shaped OR complaint-shaped question about a service this KB doesn't
        # cover -- still classified (TYPE_B is the more common shape for "I want a new X"), but
        # flagged so the caller skips retrieval entirely.
        intent = QuestionIntent.TYPE_B_SERVICE_INFO if service_info_matches or not complaint_matches else QuestionIntent.TYPE_A_COMPLAINT
        return ClassificationResult(
            intent=intent,
            service_category=None,
            out_of_scope_service=out_of_scope_label,
            matched_keywords=out_of_scope_matches,
            requests_new_connection=requests_new_connection,
        )

    # "How do I file a complaint?" -- asking about the PROCESS, not reporting anything right now.
    # Deliberately wins UNCONDITIONALLY once a how-to-file phrase matches, even over a `state_
    # matches` hit -- an earlier version of this fix required `not state_matches`, which produced
    # a real, caught-before-shipping inconsistency: "How do I report a water leakage?" kept
    # returning TYPE_A_COMPLAINT (because "leak" is also a state keyword, matched inside
    # "leakage") while its own word-for-word Hindi equivalent, "पानी के रिसाव की शिकायत कैसे
    # करें?", correctly became CAPABILITIES (Hindi's state list has no "leak"-equivalent generic
    # topic-noun to false-positive on). Both phrasings mean exactly the same thing and must behave
    # identically. The `_HOW_TO_FILE_COMPLAINT_KEYWORDS` phrases are all specifically "how [do/
    # can] I [report/file/complain/lodge/raise]" -- a genuine, immediate complaint essentially
    # never uses that interrogative construction ("Street light not working near my house" has no
    # "how do i ..." in it at all, so this branch is never even reached for it); a state keyword
    # co-occurring with an explicit how-to-file phrase is far more often "leak" used as a bare
    # topic noun than a real active-problem report.
    #
    # LIVE PRODUCT FINDING: unconditionally discarding `category` here (as this branch used to)
    # was itself a real, reported request/response mismatch -- "How do I report a water leakage?"
    # (one of this app's own 4 featured starter questions) named a real, known service
    # (WATER_DRAINAGE, matched via the bare word "water") that this app genuinely has retrievable
    # information/complaint-filing support for, but got the generic "what can you do" menu instead
    # of an answer about water leaks specifically -- exactly the "RAG lookup that has no content to
    # find" assumption below, which is only true when NO category is named at all. Generalizes to
    # any "how do I report/file X" phrasing that also names a real category (pothole, garbage,
    # streetlight, ...), not just this one reported phrase: TYPE_B_SERVICE_INFO (an informational,
    # "how is this process handled" question, matching how a category+"what is the procedure for
    # X" question is already classified elsewhere in this function) rather than TYPE_A_COMPLAINT,
    # so the "leak"-as-bare-topic-noun false positive this whole branch exists to prevent still
    # never fires. Checked before service_info/state/meta so this can't fall through to those and
    # re-trigger that exact original bug. Only when no category is named at all -- a genuinely
    # generic "How do I file a complaint?" -- does CAPABILITIES's accurate, honest "just describe
    # your issue and location" answer remain the right one, since there truly is no more specific
    # RAG content to find for that case.
    if how_to_file_matches:
        if category is not None:
            return ClassificationResult(
                intent=QuestionIntent.TYPE_B_SERVICE_INFO,
                service_category=category,
                out_of_scope_service=None,
                matched_keywords=how_to_file_matches + category_matches,
                requests_new_connection=requests_new_connection,
            )
        return ClassificationResult(
            intent=QuestionIntent.CAPABILITIES,
            service_category=None,
            out_of_scope_service=None,
            matched_keywords=how_to_file_matches,
            requests_new_connection=requests_new_connection,
        )

    if service_info_matches and not state_matches:
        # A genuine problem-state signal ("broken"/"leaking"/...) still wins over a service-info
        # phrase match -- only a bare complaint-VERB match (meta_matches, e.g. "report"/
        # "complain") is weak enough to be overridden by a clear info-seeking phrase (the old
        # guard here was `not complaint_matches`, i.e. state+meta combined; loosened to
        # `not state_matches` as part of the P0 fix below, since meta_matches alone is no longer
        # trusted as a confident complaint signal either).
        return ClassificationResult(
            intent=QuestionIntent.TYPE_B_SERVICE_INFO,
            service_category=category,
            out_of_scope_service=None,
            matched_keywords=service_info_matches + category_matches,
            requests_new_connection=requests_new_connection,
        )

    if state_matches:
        # Strong, unambiguous problem-state language -- confidently a real complaint regardless
        # of sentence shape or category (category, if still missing, is asked for by the caller).
        return ClassificationResult(
            intent=QuestionIntent.TYPE_A_COMPLAINT,
            service_category=category,
            out_of_scope_service=None,
            matched_keywords=complaint_matches + category_matches,
            requests_new_connection=requests_new_connection,
        )

    if meta_matches and not _looks_like_question(text):
        # A complaint-shaped verb ("report"/"complain"/शिकायत/तक्रार/...) with no problem-state
        # language, and NOT phrased as a question -- e.g. "I want to file a complaint." Kept
        # confidently TYPE_A (see test_classifier_real_complaint_with_no_category_still_type_a).
        return ClassificationResult(
            intent=QuestionIntent.TYPE_A_COMPLAINT,
            service_category=category,
            out_of_scope_service=None,
            matched_keywords=complaint_matches + category_matches,
            requests_new_connection=requests_new_connection,
        )

    # P0 SAFETY FIX (see QuestionIntent.TYPE_A_MAYBE's own docstring, and the production-safety
    # audit that found this): what's left here is either a bare CATEGORY mention with no
    # complaint-shaped language at all (e.g. "garbage in Pune"), or a bare META verb match inside
    # a clearly question-shaped sentence (e.g. "complain" matching only as a substring of the
    # plural noun "complaints" in "What is the procedure for garbage collection complaints in
    # Pune?"). Neither is, on its own, confident evidence of a real complaint -- both are equally
    # consistent with an information question about that category. Previously this fell straight
    # through to TYPE_A_COMPLAINT (`if complaint_matches or category:`), which is the exact P0
    # bug this closes: that sentence, and "What are the rules for streetlight repair in Kanpur?",
    # both silently became real, filed-and-assigned complaints before this fix. Never guess in
    # either direction: a clearly question-phrased message is treated as information-seeking
    # (TYPE_B, answered via RAG, which already handles "no matching record" honestly); anything
    # else with only this weak a signal is genuinely ambiguous and gets TYPE_A_MAYBE, which asks
    # the citizen directly instead of assuming either way (see orchestration/nodes.py's
    # clarification_flow_node "intent_ambiguous" branch).
    if category or meta_matches:
        weak_matched_keywords = category_matches or meta_matches
        if _looks_like_question(text):
            return ClassificationResult(
                intent=QuestionIntent.TYPE_B_SERVICE_INFO,
                service_category=category,
                out_of_scope_service=None,
                matched_keywords=weak_matched_keywords,
                requests_new_connection=requests_new_connection,
            )
        return ClassificationResult(
            intent=QuestionIntent.TYPE_A_MAYBE,
            service_category=category,
            out_of_scope_service=None,
            matched_keywords=weak_matched_keywords,
            requests_new_connection=requests_new_connection,
        )

    # PRODUCTION ARCHITECTURE UPGRADE: checked here, not earlier -- only reached once every
    # complaint/service-info signal above has already failed to match (see the `category or
    # meta_matches` block just above), so a message that opens with a greeting but ALSO reports a
    # real problem ("Hi, my streetlight is broken") is never swallowed into GREETING -- the
    # state-match branch above already returned TYPE_A_COMPLAINT for it first.
    greeting_matches = _matches_word_boundary(text, _GREETING_SHORT_WORDS) or _any_match(text, _GREETING_PHRASES)
    if greeting_matches:
        return ClassificationResult(
            intent=QuestionIntent.GREETING,
            service_category=None,
            out_of_scope_service=None,
            matched_keywords=greeting_matches,
            requests_new_connection=requests_new_connection,
        )

    capabilities_matches = _any_match(text, _CAPABILITIES_KEYWORDS)
    if capabilities_matches:
        return ClassificationResult(
            intent=QuestionIntent.CAPABILITIES,
            service_category=None,
            out_of_scope_service=None,
            matched_keywords=capabilities_matches,
            requests_new_connection=requests_new_connection,
        )

    # Genuinely zero signal -- see QuestionIntent.UNCLEAR's own docstring for why this is no
    # longer silently treated as an unspecified complaint.
    return ClassificationResult(
        intent=QuestionIntent.UNCLEAR,
        service_category=None,
        out_of_scope_service=None,
        matched_keywords=[],
        requests_new_connection=requests_new_connection,
    )


# --- Multi-category detection (see orchestration/nodes.py's agent_flow_node, and
# docs/ask_sarthi_orchestration.md §17 for the future-agent integration point this fills) ------
#
# `classify()` above deliberately picks exactly ONE category per message ("first match wins" --
# see `_CATEGORY_KEYWORDS`'s own top comment) because most real messages genuinely name one issue,
# and because several categories share ambiguous location-context words (a streetlight complaint
# commonly names the ROAD it's on). `detect_multiple_categories()` is a SEPARATE, narrower check
# used only to gate the multi-category supervisor node -- it must never be used in place of
# `classify()` for ordinary single-category routing, and is deliberately conservative (biased
# toward missing a real multi-category message rather than false-triggering on an ordinary
# single-issue one, since the supervisor path is more expensive and structurally different).
#
# The conservatism is concrete, not just stated: ROADS_POTHOLES here uses a NARROWED keyword set
# (English "pothole"/"civil work", not the bare "road" `_CATEGORY_KEYWORDS` also matches on) --
# reusing the bare "road" family would make almost every streetlight-on-a-road complaint look
# like a "streetlight + roads" multi-category message, which it structurally is not (see
# `_CATEGORY_KEYWORDS`'s own STREETLIGHTS-vs-ROADS_POTHOLES collision comment -- this is that same
# collision, just relevant to a different check). Non-English pothole-specific words
# (hi "गड्ढा"/"गड्ढे", mr "खड्डा"/"खड्ड्या", gu "ખાડો"/"ખાડાની", bn "গর্ত") are unaffected -- none
# of them double as a generic "road" location word, only the bare "road" family words themselves
# are dropped here.
#
# KNOWN GAP (code review, honestly documented rather than guessed around): Odia has none of
# this -- `_CATEGORY_KEYWORDS[ROADS_POTHOLES]["or"]` was ONLY ever the bare "ରାସ୍ତା" ("road"),
# never a separate pothole-specific word, so dropping it here leaves Odia's list empty. This
# means an Odia-speaking citizen reporting a pothole alongside another issue in one message will
# never trigger the multi-category path today, while the identical message in en/hi/mr/gu/bn
# would. NOT fixed by guessing an Odia word here -- every other language-specific keyword in this
# file was added only after live-testing a real reported phrase (see e.g. the STREETLIGHTS
# ଆଲୁଅ/ଆଲୋକ entries above), and no verified Odia pothole-specific word exists anywhere in this
# codebase's own knowledge base or labeled test data (checked directly). Guessing one here risks
# a wrong or unnatural translation shipping silently, which is worse than the honest gap this
# comment documents. Needs a real, live-verified Odia phrase before this list can safely grow.
_MULTI_CATEGORY_KEYWORDS: dict[ServiceCategory, dict[str, list[str]]] = {
    **{cat: kws for cat, kws in _CATEGORY_KEYWORDS.items() if cat != ServiceCategory.ROADS_POTHOLES},
    ServiceCategory.ROADS_POTHOLES: {
        # BUG FIX (code review): "civil work" dropped -- matched as a bare, unbounded substring
        # (via _any_match's `kw.lower() in lowered`), it isn't actually pothole-specific and
        # false-triggered the multi-category gate for ordinary single-issue messages that merely
        # mention an unrelated "civil works department/office" alongside a real complaint (e.g.
        # "The streetlight near the civil works department office is broken" -- one issue,
        # wrongly detected as two). "pothole" alone is already unambiguous and sufficient for
        # this deliberately conservative gate (see this section's own top comment).
        "en": ["pothole"],
        "hi": ["गड्ढा", "गड्ढे"], "mr": ["खड्डा", "खड्ड्या"],
        "or": [],  # KNOWN GAP -- see this section's top comment; no verified Odia word yet
        "gu": ["ખાડો", "ખાડાની"], "bn": ["গর্ত"],
    },
}


def detect_multiple_categories(question: str) -> list[ServiceCategory]:
    """Returns every `ServiceCategory` with at least one real, unambiguous keyword match in
    `question` -- empty if none or exactly one matched (a single match isn't a multi-category
    signal at all; see this section's own top comment for why). Deterministic keyword matching
    only, same as every other classification decision in this file -- no LLM decides this."""
    matched = [
        cat for cat, lang_keywords in _MULTI_CATEGORY_KEYWORDS.items()
        if _any_match(question, lang_keywords)
    ]
    return matched if len(matched) >= 2 else []
