"""One-time helper: generate draft translations for the frontend i18n dictionary.

Uses the same SarvamClient already configured and tested elsewhere in this backend to
translate every English UI string into Hindi, Marathi, Odia, Gujarati, and Bengali, so we're
not hand-authoring ~300 strings (especially risky for Odia/Gujarati/Bengali, where this
codebase has no existing baseline to check against, unlike Hindi/Marathi).

This is a draft generator, not a source of truth: run it, review the output (especially the
3 newly-added languages), fix anything awkward, then paste the result into
frontend-react/src/lib/i18n.ts by hand. Re-run whenever new keys are added in English.

Usage: python scripts/generate_i18n_translations.py
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.services.sarvam_client import AIServiceError, SarvamClient
from backend.services.translation_service import TranslationService

OUTPUT_PATH = "scripts/i18n_translations_output.json"

# Every UI string that needs a translation. Existing hi/mr values that already exist (and were
# hand-reviewed) in i18n.ts are NOT regenerated here -- see PRESERVE below -- this only covers
# what's genuinely new: existing keys in the 3 new languages, and all-new keys in every language.
ENGLISH_SOURCE: dict[str, str] = {
    # --- Ask Sarthi RAG-retrieval-phase additions (all 6 languages hand-added directly to
    # i18n.ts at the same time as these source entries -- see that file's "ask.loading" etc, and
    # docs/ask_sarthi_rag_architecture.md's known-limitations section for why these three are
    # hand-translated rather than run through translate_with_retry()'s live Sarvam calls here) ---
    "ask.loading": "Thinking…",
    "ask.error": "Something went wrong. Please try again.",
    "ask.followUp.label": "Please clarify:",
    # --- existing keys, need or/gu/bn only (hi/mr already hand-reviewed in i18n.ts) ---
    "landing.login": "Log in",
    "landing.signup": "Sign up",
    "hero.headline": "Report it in your language. It gets read in theirs.",
    "hero.sub": "JanSarthi AI turns your voice into a complaint your local worker can read — translated automatically, no matter which language either of you speaks.",
    "hero.cta.primary": "Get started",
    "hero.cta.secondary": "Log in",
    "auth.field.phone": "Phone number",
    "auth.field.name": "Full name",
    "auth.field.password": "Password",
    "auth.login.button": "Log in",
    "auth.signup.button": "Create account",
    "auth.login.switch": "New here?",
    "auth.login.switchlink": "Create an account",
    "auth.signup.switch": "Already have an account?",
    "auth.signup.switchlink": "Log in",
    "auth.signup.workernote": "Sanitation worker? Worker accounts are created by your ward's Super Admin — ask them for your login details.",
    # --- new keys, need every non-English language ---
    "auth.tab.login": "Log in",
    "auth.tab.signup": "Sign up",
    "common.fieldRequired": "This field is required.",
    "common.loading": "Loading…",
    "common.cancel": "Cancel",
    "common.close": "Close",
    "common.somethingWrong": "Something went wrong. Please try again.",
    "gate.title": "Choose your language",
    "gate.changeAnytime": "You can change this anytime later in Settings",
    "gate.privacyNote": "Used to decide what language JanSarthi AI shows you in — nothing is sent anywhere yet.",
    "topbar.subtitle": "Municipal Grievance Redressal",
    "role.citizen": "Citizen",
    "role.worker": "Worker",
    "role.admin": "Super Admin",
    "topbar.settings": "Settings",
    "settings.title": "Profile & settings",
    "settings.fullName": "Full name",
    "settings.phone": "Phone number",
    "settings.preferredLanguage": "Preferred language",
    "settings.languageHelp": "This changes the language JanSarthi AI shows you — complaint forms, notifications, everything.",
    "settings.logout": "Log out",
    "settings.save": "Save changes",
    "settings.saving": "Saving…",
    "settings.error": "Could not save changes. Please try again.",
    "citizen.title": "Report a civic issue",
    "citizen.subtitle": "Speak, type, or add a photo — it's translated automatically and sent to the worker responsible for your area.",
    "citizen.describe": "Describe the problem",
    "citizen.type": "✏️ Type",
    "citizen.speak": "🎙️ Speak",
    "citizen.textPlaceholder": "e.g. Garbage not collected near my house for 3 days",
    "citizen.startRecording": "🎙️ Start recording",
    "citizen.stopRecording": "⏹ Stop",
    "citizen.recordAgain": "🔁 Record again",
    "citizen.voiceRecorded": "✅ Recorded",
    "citizen.ward": "Area / ward",
    "citizen.wardOptional": "Area / ward (optional)",
    "citizen.wardSelectPlaceholder": "Select the area",
    "citizen.wardHelpHasWards": "This determines which team's queue your complaint lands in.",
    "citizen.wardHelpNoWards": "No areas are set up yet, so this won't reach anyone's queue until an admin adds a worker for it.",
    "citizen.wardPlaceholder": "e.g. Ward 14 — Rukadi Road",
    "citizen.photo": "Attach a photo (optional)",
    "citizen.submit": "Submit complaint",
    "citizen.submitting": "Submitting…",
    "citizen.open": "Open",
    "citizen.resolved": "Resolved",
    "citizen.yourComplaints": "Your complaints",
    "citizen.noComplaints": "You haven't submitted any complaints yet.",
    "citizen.attached": "Attached",
    "citizen.errNoText": "Please describe the problem before submitting.",
    "citizen.errNoAudio": "Please record your complaint before submitting.",
    "citizen.errNoWard": "Please select the area so your complaint reaches the right team.",
    "citizen.errSubmitFailed": "Could not submit complaint. Please try again.",
    "citizen.errLoadFailed": "Could not load your complaints.",
    "citizen.submitSuccess": "Complaint submitted successfully.",
    "worker.title": "Your queue",
    "worker.wardPrefix": "Ward",
    "worker.noWard": "No ward assigned yet",
    "worker.open": "Open",
    "worker.resolved": "Resolved",
    "worker.filterAll": "All",
    "worker.filterOpen": "Open",
    "worker.filterResolved": "Resolved",
    "worker.filterAssigned": "Needs response",
    "worker.filterAccepted": "In progress",
    "worker.nothingHere": "Nothing here.",
    "worker.markResolved": "Mark Resolved",
    "worker.attached": "Attached",
    "worker.errLoadFailed": "Could not load your queue.",
    "worker.errUpdateFailed": "Could not update this complaint.",
    "worker.accept": "Accept",
    "worker.reject": "Reject",
    "worker.accepting": "Accepting…",
    "worker.rejecting": "Rejecting…",
    "worker.statusAssigned": "Awaiting your response",
    "worker.statusAccepted": "In progress",
    "worker.errAcceptFailed": "Could not accept this complaint. Please try again.",
    "worker.errRejectFailed": "Could not reject this complaint. Please try again.",
    "worker.errResolveFailed": "Could not mark this resolved. Please try again.",
    "admin.title": "Ward oversight",
    "admin.subtitle": "All wards",
    "admin.workersStat": "Workers",
    "admin.openComplaintsStat": "Open complaints",
    "admin.resolvedStat": "Resolved",
    "admin.workersSection": "Workers",
    "admin.addWorker": "+ Add worker",
    "admin.addWorkerNote": "Worker accounts can only be created here by a Super Admin — there's no public sign-up for workers.",
    "admin.loading": "Loading…",
    "admin.noWorkers": "No workers yet — add the first one.",
    "admin.colWorker": "Worker",
    "admin.colWard": "Ward",
    "admin.colOpen": "Open",
    "admin.colResolved": "Resolved",
    "admin.colLanguage": "Language",
    "admin.errLoadFailed": "Could not load workers.",
    "addWorker.title": "Add a worker",
    "addWorker.note": "Only Super Admins can create worker accounts. Share the phone number and password with the worker so they can log in.",
    "addWorker.fullName": "Full name",
    "addWorker.fullNamePlaceholder": "e.g. Sunita Pawar",
    "addWorker.phone": "Phone number",
    "addWorker.tempPassword": "Temporary password",
    "addWorker.ward": "Assign to ward",
    "addWorker.wardPlaceholder": "e.g. Ward 14 — Rukadi Road",
    "addWorker.preferredLanguage": "Preferred language",
    "addWorker.cancel": "Cancel",
    "addWorker.submit": "Add worker",
    "addWorker.submitting": "Adding…",
    "addWorker.errFailed": "Could not create this worker account.",
    "citizen.statusPending": "Waiting to be assigned",
    "citizen.statusAssigned": "Assigned",
    "citizen.statusAccepted": "Worker is on it",
    "citizen.statusResolved": "Resolved",
    "citizen.trackSubmitted": "Submitted",
    "citizen.trackAssigned": "Assigned",
    "citizen.trackInProgress": "In progress",
    "worker.rejectedConfirm": "Rejected — sent to the next available worker in your ward.",
    "citizen.assignedTo": "Assigned to",
    "citizen.workerPhone": "Contact number",
    "citizen.searchingNextWorker": "Previous worker declined — searching for another worker…",
    "citizen.feedbackTitle": "How was this resolved?",
    "citizen.feedbackCommentPlaceholder": "Optional comment",
    "citizen.feedbackSubmit": "Submit feedback",
    "citizen.feedbackSubmitting": "Submitting…",
    "citizen.feedbackSubmitted": "Thanks for your feedback!",
    "citizen.errFeedbackFailed": "Could not submit feedback. Please try again.",
}

# Keys already hand-reviewed in i18n.ts for hi/mr -- don't waste calls/regenerate these two
# languages for them, only need or/gu/bn for this subset.
EXISTING_KEYS = {
    "landing.login", "landing.signup", "hero.headline", "hero.sub", "hero.cta.primary",
    "hero.cta.secondary", "auth.field.phone", "auth.field.name", "auth.field.password",
    "auth.login.button", "auth.signup.button", "auth.login.switch", "auth.login.switchlink",
    "auth.signup.switch", "auth.signup.switchlink", "auth.signup.workernote",
}

TARGET_LANGUAGES = ["hi", "mr", "or", "gu", "bn"]


def jobs_for(lang: str) -> list[str]:
    if lang in ("hi", "mr"):
        return [k for k in ENGLISH_SOURCE if k not in EXISTING_KEYS]
    return list(ENGLISH_SOURCE.keys())


def translate_with_retry(service: TranslationService, text: str, lang: str, retries: int = 5) -> str:
    delay = 2.0
    for attempt in range(retries):
        try:
            return service.to_language(text, lang)
        except AIServiceError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2  # exponential backoff on rate limits
    raise AssertionError("unreachable")


def main() -> None:
    sarvam = SarvamClient()
    service = TranslationService(sarvam)

    # Resume support: skip anything already translated in a prior (possibly rate-limited) run.
    results: dict[str, dict[str, str]] = {lang: {} for lang in TARGET_LANGUAGES}
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            results.update(json.load(f))

    all_jobs = [
        (lang, key)
        for lang in TARGET_LANGUAGES
        for key in jobs_for(lang)
        if key not in results.get(lang, {})
    ]
    print(f"Translating {len(all_jobs)} remaining strings across {len(TARGET_LANGUAGES)} languages...")

    def translate_one(lang: str, key: str) -> tuple[str, str, str]:
        text = translate_with_retry(service, ENGLISH_SOURCE[key], lang)
        return lang, key, text

    # Low concurrency + retry/backoff: Sarvam's rate limit is per-account, not per-connection,
    # so a handful of workers is safer than maximizing throughput and getting 429'd.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(translate_one, lang, key): (lang, key) for lang, key in all_jobs}
        done = 0
        for future in as_completed(futures):
            lang, key = futures[future]
            try:
                lang, key, text = future.result()
                results[lang][key] = text
            except AIServiceError as exc:
                print(f"  FAILED after retries: {lang}/{key}: {exc}")
                continue
            done += 1
            if done % 10 == 0:
                # Save incrementally so a later failure doesn't lose earlier progress.
                with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                print(f"  {done}/{len(all_jobs)}")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"Done. Output written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
