"""Builds a Complaint Resolution Report -- both the JSON shape ("View Report") and a downloadable
PDF ("Download Report") -- entirely from data already in the database (Complaint,
ComplaintStatusHistory, ComplaintUpdate). NEVER calls an LLM or invents any fact: the report is a
deterministic rendering of rows that already exist, matching this project's hard rule that a
report must reflect only what was actually recorded (see routes/complaints.py's report
endpoints, which refuse to generate one at all for a complaint that isn't `resolved`).

PDF generation uses `reportlab` (already a project dependency as of this phase -- see
requirements.txt) -- a plain, dependency-light choice: no headless browser, no system libraries
beyond pure Python, appropriate for a short, structured, single-page-ish document. No existing
report-generation infrastructure was found in this codebase before this phase (checked
requirements.txt and grepped for pdf/reportlab/weasyprint -- none present).
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import BaseDocTemplate, Frame, Image as RLImage, PageTemplate, Paragraph, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import Complaint, ComplaintStatusHistory, ComplaintUpdate, District, State, ULB, User
from backend.repositories import complaint_workflow_repository, evidence_repository
from backend.services.complaint_translation_cache import get_display_text_and_summary
from backend.services.complaint_update_translation_cache import get_display_text as get_display_update_text
from backend.services.sarvam_client import AIServiceError
from backend.services.translation_service import TranslationService

logger = logging.getLogger(__name__)


@dataclass
class ComplaintReportData:
    """Everything the report shows -- one field per fact, so the JSON ("View Report") and PDF
    ("Download Report") renderers can't drift apart; both are built from this same object."""

    complaint_id: int
    display_id: str  # "JM-00042", matching the frontend's existing id formatting convention
    service_summary: str
    original_description: str
    created_at: datetime
    location_ward: str | None
    location_state: str | None
    location_district: str | None
    location_ulb: str | None
    location_address: str | None
    assigned_worker_name: str | None
    initial_assessment: str | None
    initial_assessment_at: datetime | None
    # [{text, photo_path, created_at, evidence: [file_path, ...]}] -- `evidence` is the
    # multi-file replacement for the old single `photo_path`, see ComplaintEvidence.
    progress_updates: list[dict] = field(default_factory=list)
    completion_status: str | None = None
    completion_evidence_photo: str | None = None  # legacy single-photo column, old rows only
    resolved_at: datetime | None = None
    timeline: list[dict] = field(default_factory=list)  # [{from_status, to_status, note, created_at}]
    # Evidence upload phase -- file_path lists from ComplaintEvidence, grouped by stage. Never
    # populated from the legacy single-photo columns (those stay in the fields above, for rows
    # written before this table existed).
    citizen_evidence: list[str] = field(default_factory=list)
    initial_assessment_evidence: list[str] = field(default_factory=list)
    completion_evidence: list[str] = field(default_factory=list)


def build_report_data(
    db: Session,
    complaint: Complaint,
    display_language: str | None = None,
    translation_service: TranslationService | None = None,
) -> ComplaintReportData:
    """Assembles a ComplaintReportData from the database. Caller (routes/complaints.py) is
    responsible for having already verified `complaint.status == "resolved"` and that the
    requester is authorized to see this complaint -- this function does no authorization/status
    checking itself, it only reads and shapes data.

    LIVE PRODUCT FINDING: `service_summary`/`original_description` used to always be the stored
    English text (`complaint.summary`/`complaint.translated_text`), even when every other view of
    the same complaint (the complaints list/detail endpoints, via `_to_response`'s own
    `get_display_text_and_summary` call) already translates them into the viewer's own language on
    read. Reported directly: a Marathi-speaking citizen saw the complaint LIST show her complaint
    in Marathi, but the Report/Summary view of that exact same complaint show it in raw English --
    a real, visible inconsistency, not a translation failure (the cache/translation mechanism
    already existed and already worked; the report just never called it).

    `display_language`/`translation_service` are both optional and default to None (an English-
    only caller, or a test, can omit them and get the stored English text back exactly as before).
    Both `view_report` (JSON) and `download_report` (PDF) now pass the viewer's own language --
    the PDF used to intentionally skip this, leaving the downloadable document's data untranslated
    even after the JSON view was fixed; `generate_pdf_bytes`'s own `display_language` param
    (translating the PDF's surrounding labels/headers, which have no cache/lookup of their own --
    see `_PDF_LABELS`) closes that same gap for the PDF's data values too.
    On any translation failure, falls back to the stored English text exactly like `_to_response`
    already does -- this must never turn a translation hiccup into a broken/missing report.
    """
    service_summary = complaint.summary
    original_description = complaint.translated_text
    if display_language and display_language != "en" and translation_service is not None:
        try:
            original_description, service_summary = get_display_text_and_summary(
                db, complaint, display_language, translation_service
            )
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint %s report: %s", complaint.id, exc)
            service_summary = complaint.summary
            original_description = complaint.translated_text

    def _display_update_text(update: ComplaintUpdate | None) -> str | None:
        """Same optional-translation-with-fallback shape as the block just above, applied to a
        worker-authored ComplaintUpdate instead of the complaint itself -- see
        complaint_update_translation_cache.py's own docstring for why this needs a DIFFERENT
        cache/lookup (no "always English" guarantee, source language is approximated per-update
        from the authoring worker's own preference)."""
        if update is None:
            return None
        if not display_language or display_language == "en" or translation_service is None:
            return update.text
        try:
            return get_display_update_text(db, update, display_language, translation_service)
        except AIServiceError as exc:
            logger.error("On-read translation failed for complaint update %s: %s", update.id, exc)
            return update.text

    location_state = location_district = location_ulb = None
    if complaint.state_id is not None:
        row = db.query(State).filter(State.id == complaint.state_id).first()
        location_state = row.name if row else None
    if complaint.district_id is not None:
        row = db.query(District).filter(District.id == complaint.district_id).first()
        location_district = row.name if row else None
    if complaint.ulb_id is not None:
        row = db.query(ULB).filter(ULB.id == complaint.ulb_id).first()
        location_ulb = row.name if row else None

    assigned_worker_name = None
    if complaint.assigned_worker_id is not None:
        worker = db.query(User).filter(User.id == complaint.assigned_worker_id).first()
        assigned_worker_name = worker.full_name if worker else None

    updates = complaint_workflow_repository.get_complaint_updates(db, complaint.id)
    initial = next((u for u in updates if u.update_type == "INITIAL_ASSESSMENT"), None)
    completion = next((u for u in updates if u.update_type == "COMPLETION"), None)
    progress = [u for u in updates if u.update_type == "PROGRESS_UPDATE"]

    history = complaint_workflow_repository.get_status_history(db, complaint.id)
    resolved_entry = next((h for h in reversed(history) if h.to_status == "resolved"), None)

    # Evidence upload phase: every ComplaintEvidence row for this complaint, grouped by stage
    # (and, for progress updates specifically, by which update it belongs to -- there can be
    # more than one). One query, grouped in Python, rather than one query per stage/update.
    all_evidence = evidence_repository.get_evidence_for_complaint(db, complaint.id)
    citizen_evidence = [e.file_path for e in all_evidence if e.stage == "CITIZEN_COMPLAINT"]
    initial_evidence = [e.file_path for e in all_evidence if e.stage == "INITIAL_ASSESSMENT"]
    completion_evidence = [e.file_path for e in all_evidence if e.stage == "COMPLETION"]
    evidence_by_update_id: dict[int, list[str]] = {}
    for e in all_evidence:
        if e.stage == "PROGRESS_UPDATE" and e.update_id is not None:
            evidence_by_update_id.setdefault(e.update_id, []).append(e.file_path)

    return ComplaintReportData(
        complaint_id=complaint.id,
        display_id=f"JM-{complaint.id:05d}",
        service_summary=service_summary,
        original_description=original_description,
        created_at=complaint.created_at,
        location_ward=complaint.ward,
        location_state=location_state,
        location_district=location_district,
        location_ulb=location_ulb,
        location_address=complaint.address,
        assigned_worker_name=assigned_worker_name,
        initial_assessment=_display_update_text(initial),
        initial_assessment_at=initial.created_at if initial else None,
        progress_updates=[
            {
                "text": _display_update_text(u), "photo_path": u.photo_path, "created_at": u.created_at,
                "evidence": evidence_by_update_id.get(u.id, []),
            }
            for u in progress
        ],
        completion_status=_display_update_text(completion),
        completion_evidence_photo=completion.photo_path if completion else None,
        resolved_at=resolved_entry.created_at if resolved_entry else None,
        timeline=[
            {"from_status": h.from_status, "to_status": h.to_status, "note": h.note, "created_at": h.created_at}
            for h in history
        ],
        citizen_evidence=citizen_evidence,
        initial_assessment_evidence=initial_evidence,
        completion_evidence=completion_evidence,
    )


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y, %I:%M %p") if dt else "—"


def _spaced(text: str) -> str:
    """Letter-track short uppercase labels (e.g. "COMPLAINT" -> "C O M P L A I N T") -- the
    tracked-caps look used throughout the reference report this template is styled after, for
    titles and section/panel headers only. Never applied to body text or values -- unreadable
    past a few words.

    Word gaps use a doubled non-breaking space rather than plain spaces: ReportLab's Paragraph
    parses text XML/HTML-style and collapses runs of plain whitespace to one space, which would
    otherwise erase the very word gap this is meant to add (multi-word labels rendered as one
    fused word). Plain single spaces between letters are unaffected since collapsing only touches
    multi-space runs.
    """
    return "  ".join(" ".join(word) for word in text.split(" "))


# ===== PDF label translations -- a small, backend-local mirror of the same phrases already
# established in frontend-react/src/lib/i18n.ts (reusing the exact same wording wherever a label
# already exists there, e.g. "worker.report.filedOn"/"citizen.statusResolved"), plus a handful of
# labels that only ever existed in this PDF (Ward/District/State panel rows, table headers,
# footer text) and so needed a first translation here. Kept backend-local (not imported from the
# frontend) because this is plain Python rendering code with no access to a TS module, and pulling
# translated strings from the database on every PDF render would be needless -- these are a fixed,
# short, closed set of labels, not user content. =====
_PDF_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "report_title": "Complaint Resolution Report",
        "filed": "Filed",
        "complaint_id": "Complaint ID",
        "filed_on": "Filed on",
        "resolved_on": "Resolved on",
        "assigned_worker": "Assigned worker",
        "ward": "Ward",
        "ulb": "ULB",
        "district": "District",
        "state": "State",
        "address": "Address",
        "complaint_panel": "Complaint",
        "location_panel": "Location",
        "resolved_banner_title": "Complaint Resolved",
        "resolved_banner_sub": "confirmed via recorded worker completion update.",
        "resolved_chip": "Resolved",
        "description_section": "Description",
        "initial_assessment_section": "Initial Assessment",
        "progress_updates_section": "Progress Updates",
        "completion_section": "Completion",
        "not_recorded": "Not recorded",
        "status_timeline_section": "Status Timeline",
        "time_col": "Time",
        "status_col": "Status",
        "details_col": "Details",
        "no_status_changes": "No status changes recorded.",
        "footer_tagline": "Municipal Grievance Redressal",
        "footer_note": "Reflects only actions recorded on Complaint {id}.",
        "page_of": "Page {page} of {total}",
        "status_open": "Submitted",
        "status_pending": "Waiting to be assigned",
        "status_assigned": "Assigned",
        "status_accepted": "Worker is on it",
        "status_in_progress": "In progress",
        "status_resolved": "Resolved",
    },
    "hi": {
        "report_title": "शिकायत समाधान रिपोर्ट",
        "filed": "दर्ज",
        "complaint_id": "शिकायत आईडी",
        "filed_on": "दर्ज की गई तारीख",
        "resolved_on": "हल होने की तारीख",
        "assigned_worker": "नियुक्त कार्यकर्ता",
        "ward": "वार्ड",
        "ulb": "ULB",
        "district": "जिला",
        "state": "राज्य",
        "address": "पता",
        "complaint_panel": "शिकायत",
        "location_panel": "स्थान",
        "resolved_banner_title": "शिकायत हल हो गई",
        "resolved_banner_sub": "कार्यकर्ता के दर्ज पूर्णता अपडेट द्वारा पुष्टि की गई।",
        "resolved_chip": "समाधान हो गया",
        "description_section": "विवरण",
        "initial_assessment_section": "प्रारंभिक आकलन",
        "progress_updates_section": "प्रगति अपडेट",
        "completion_section": "पूर्णता",
        "not_recorded": "दर्ज नहीं किया गया",
        "status_timeline_section": "स्थिति समयरेखा",
        "time_col": "समय",
        "status_col": "स्थिति",
        "details_col": "विवरण",
        "no_status_changes": "कोई स्थिति परिवर्तन दर्ज नहीं किया गया।",
        "footer_tagline": "नगरपालिका शिकायत निवारण",
        "footer_note": "केवल शिकायत {id} पर दर्ज की गई कार्रवाइयों को दर्शाता है।",
        "page_of": "पृष्ठ {page} का {total}",
        "status_open": "प्रस्तुत किया गया",
        "status_pending": "नियुक्त होने का इंतज़ार कर रही हूँ।",
        "status_assigned": "सौंपा गया",
        "status_accepted": "कर्मचारी इस पर काम कर रही है।",
        "status_in_progress": "जारी है",
        "status_resolved": "समाधान हो गया",
    },
    "mr": {
        "report_title": "तक्रार निराकरण अहवाल",
        "filed": "नोंदणी",
        "complaint_id": "तक्रार आयडी",
        "filed_on": "नोंदणी तारीख",
        "resolved_on": "सोडवल्याची तारीख",
        "assigned_worker": "नियुक्त कामगार",
        "ward": "वॉर्ड",
        "ulb": "ULB",
        "district": "जिल्हा",
        "state": "राज्य",
        "address": "पत्ता",
        "complaint_panel": "तक्रार",
        "location_panel": "स्थान",
        "resolved_banner_title": "तक्रार सोडवली",
        "resolved_banner_sub": "कामगाराच्या नोंदवलेल्या पूर्णता अपडेटद्वारे पुष्टी केली.",
        "resolved_chip": "सोडवले",
        "description_section": "वर्णन",
        "initial_assessment_section": "प्रारंभिक मूल्यांकन",
        "progress_updates_section": "प्रगती अपडेट्स",
        "completion_section": "पूर्णता",
        "not_recorded": "नोंदवलेले नाही",
        "status_timeline_section": "स्थिती टाइमलाइन",
        "time_col": "वेळ",
        "status_col": "स्थिती",
        "details_col": "तपशील",
        "no_status_changes": "कोणताही स्थिती बदल नोंदवलेला नाही.",
        "footer_tagline": "नगरपालिका तक्रार निवारण",
        "footer_note": "केवळ तक्रार {id} वर नोंदवलेल्या कृतीच दर्शवते.",
        "page_of": "पृष्ठ {page} पैकी {total}",
        "status_open": "सादर केले.",
        "status_pending": "नियुक्तीची प्रतीक्षा करत आहे.",
        "status_assigned": "नियुक्त",
        "status_accepted": "कामगार त्यावर काम करत आहे.",
        "status_in_progress": "प्रगतीपथावर",
        "status_resolved": "सोडवले.",
    },
    "or": {
        "report_title": "ଅଭିଯୋଗ ସମାଧାନ ରିପୋର୍ଟ",
        "filed": "ଦାଖଲ",
        "complaint_id": "ଅଭିଯୋଗ ID",
        "filed_on": "ଦାଖଲ ତାରିଖ",
        "resolved_on": "ସମାଧାନ ତାରିଖ",
        "assigned_worker": "ଅର୍ପିତ ଶ୍ରମିକ",
        "ward": "ୱାର୍ଡ",
        "ulb": "ULB",
        "district": "ଜିଲ୍ଲା",
        "state": "ରାଜ୍ୟ",
        "address": "ଠିକଣା",
        "complaint_panel": "ଅଭିଯୋଗ",
        "location_panel": "ଅବସ୍ଥାନ",
        "resolved_banner_title": "ଅଭିଯୋଗ ସମାଧାନ ହେଲା",
        "resolved_banner_sub": "ଶ୍ରମିକଙ୍କ ରେକର୍ଡ ହୋଇଥିବା ସମାପ୍ତି ଅପଡେଟ୍ ମାଧ୍ୟମରେ ନିଶ୍ଚିତ ହୋଇଛି।",
        "resolved_chip": "ସମାଧାନ ହେଲା",
        "description_section": "ବର୍ଣ୍ଣନା",
        "initial_assessment_section": "ପ୍ରାରମ୍ଭିକ ମୂଲ୍ୟାଙ୍କନ",
        "progress_updates_section": "ଅଗ୍ରଗତି ଅପଡେଟ୍",
        "completion_section": "ସମାପ୍ତି",
        "not_recorded": "ରେକର୍ଡ ହୋଇନାହିଁ",
        "status_timeline_section": "ସ୍ଥିତି ଟାଇମଲାଇନ",
        "time_col": "ସମୟ",
        "status_col": "ସ୍ଥିତି",
        "details_col": "ବିବରଣୀ",
        "no_status_changes": "କୌଣସି ସ୍ଥିତି ପରିବର୍ତ୍ତନ ରେକର୍ଡ ହୋଇନାହିଁ।",
        "footer_tagline": "ପୌର ଅଭିଯୋଗ ନିବାରଣ",
        "footer_note": "କେବଳ ଅଭିଯୋଗ {id} ରେ ରେକର୍ଡ ହୋଇଥିବା କାର୍ଯ୍ୟଗୁଡ଼ିକୁ ଦର୍ଶାଏ।",
        "page_of": "ପୃଷ୍ଠା {page} ର {total}",
        "status_open": "ଦାଖଲ କରାଗଲା",
        "status_pending": "ଅସାଇନ୍ ହେବାକୁ ଅପେକ୍ଷା କରୁଛି",
        "status_assigned": "ଅର୍ପିତ",
        "status_accepted": "କର୍ମଚାରୀ ଏହା ଉପରେ ଅଛନ୍ତି",
        "status_in_progress": "ଅଗ୍ରଗତିରେ",
        "status_resolved": "ସମାଧାନ ହେଲା",
    },
    "gu": {
        "report_title": "ફરિયાદ ઉકેલ રિપોર્ટ",
        "filed": "નોંધણી",
        "complaint_id": "ફરિયાદ ID",
        "filed_on": "નોંધણી તારીખ",
        "resolved_on": "ઉકેલાયાની તારીખ",
        "assigned_worker": "સોંપાયેલ કામદાર",
        "ward": "વોર્ડ",
        "ulb": "ULB",
        "district": "જિલ્લો",
        "state": "રાજ્ય",
        "address": "સરનામું",
        "complaint_panel": "ફરિયાદ",
        "location_panel": "સ્થાન",
        "resolved_banner_title": "ફરિયાદ ઉકેલાઈ",
        "resolved_banner_sub": "કામદારના નોંધાયેલા પૂર્ણતા અપડેટ દ્વારા પુષ્ટિ થયેલ છે.",
        "resolved_chip": "ઉકેલાયું",
        "description_section": "વર્ણન",
        "initial_assessment_section": "પ્રારંભિક મૂલ્યાંકન",
        "progress_updates_section": "પ્રગતિ અપડેટ",
        "completion_section": "પૂર્ણતા",
        "not_recorded": "નોંધાયેલ નથી",
        "status_timeline_section": "સ્થિતિ ટાઈમલાઈન",
        "time_col": "સમય",
        "status_col": "સ્થિતિ",
        "details_col": "વિગતો",
        "no_status_changes": "કોઈ સ્થિતિ ફેરફાર નોંધાયેલ નથી.",
        "footer_tagline": "નગરપાલિકા ફરિયાદ નિવારણ",
        "footer_note": "ફક્ત ફરિયાદ {id} પર નોંધાયેલ ક્રિયાઓ દર્શાવે છે.",
        "page_of": "પૃષ્ઠ {page} નું {total}",
        "status_open": "સબમિટ કરેલ",
        "status_pending": "સોંપણી થવાની રાહ જોઈ રહી છે",
        "status_assigned": "સોંપાયેલ",
        "status_accepted": "કામદાર તેના પર છે.",
        "status_in_progress": "ચાલુ છે",
        "status_resolved": "ઉકેલાયું",
    },
    "bn": {
        "report_title": "অভিযোগ সমাধান রিপোর্ট",
        "filed": "দাখিল",
        "complaint_id": "অভিযোগ আইডি",
        "filed_on": "দাখিলের তারিখ",
        "resolved_on": "সমাধানের তারিখ",
        "assigned_worker": "নিযুক্ত শ্রমিক",
        "ward": "ওয়ার্ড",
        "ulb": "ULB",
        "district": "জেলা",
        "state": "রাজ্য",
        "address": "ঠিকানা",
        "complaint_panel": "অভিযোগ",
        "location_panel": "অবস্থান",
        "resolved_banner_title": "অভিযোগ সমাধান হয়েছে",
        "resolved_banner_sub": "শ্রমিকের রেকর্ড করা সমাপ্তি আপডেটের মাধ্যমে নিশ্চিত করা হয়েছে।",
        "resolved_chip": "সমাধান হয়েছে",
        "description_section": "বিবরণ",
        "initial_assessment_section": "প্রাথমিক মূল্যায়ন",
        "progress_updates_section": "অগ্রগতি আপডেট",
        "completion_section": "সমাপ্তি",
        "not_recorded": "রেকর্ড করা হয়নি",
        "status_timeline_section": "অবস্থার টাইমলাইন",
        "time_col": "সময়",
        "status_col": "অবস্থা",
        "details_col": "বিবরণ",
        "no_status_changes": "কোনো অবস্থার পরিবর্তন রেকর্ড করা হয়নি।",
        "footer_tagline": "পৌর অভিযোগ নিবারণ",
        "footer_note": "শুধুমাত্র অভিযোগ {id}-এ রেকর্ড করা কার্যক্রম প্রতিফলিত করে।",
        "page_of": "পৃষ্ঠা {page} এর {total}",
        "status_open": "জমা দেওয়া হয়েছে",
        "status_pending": "নিযুক্ত হওয়ার অপেক্ষায়।",
        "status_assigned": "নিযুক্ত",
        "status_accepted": "কর্মীটি এটা নিয়ে কাজ করছে।",
        "status_in_progress": "অব্যাহত।",
        "status_resolved": "সমাধান হয়েছে।",
    },
}

_STATUS_LABEL_KEYS: dict[str, str] = {
    "open": "status_open",
    "pending": "status_pending",
    "assigned": "status_assigned",
    "accepted": "status_accepted",
    "in_progress": "status_in_progress",
    "resolved": "status_resolved",
}


def _label(display_language: str, key: str) -> str:
    """Look up a PDF label in `display_language`, falling back to English for an unrecognized
    language code (same fallback `t()` uses in i18n.ts) -- never a KeyError from a PDF render."""
    return _PDF_LABELS.get(display_language, _PDF_LABELS["en"]).get(key) or _PDF_LABELS["en"][key]


def _status_label(display_language: str, status: str) -> str:
    """The timeline's from_status/to_status only ever take these 6 fixed backend status codes
    (see complaint_workflow_repository.record_status_change()'s call sites, plus
    complaint_agent.py's initial "open") -- reuses the exact same translated wording as
    ComplaintReportView.tsx's own `_STATUS_LABEL_KEYS` map, so the PDF and the in-app report never
    disagree on what a status is called."""
    key = _STATUS_LABEL_KEYS.get(status)
    return _label(display_language, key) if key else status


# ===== Brand palette -- mirrors frontend-react/src/styles/global.css's :root tokens exactly, so
# the PDF reads as the same product as the web app rather than an independently-designed sibling. =====
_NAVY = colors.HexColor("#0F2D6B")  # --accent / --brand-surface
_ORANGE = colors.HexColor("#F97316")  # --service-roads, used here as the report's accent stripe
_INK = colors.HexColor("#0F172A")  # --ink
_INK_2 = colors.HexColor("#475569")  # --ink-2
_INK_3 = colors.HexColor("#64748B")  # --ink-3
_LINE = colors.HexColor("#E2E8F0")  # --line
_RESOLVED_FG = colors.HexColor("#166534")  # darker than --status-resolved, for text on its own bg
_RESOLVED_BG = colors.HexColor("#DCFCE7")  # --status-resolved-bg

# Mirrors StatusBadge.tsx's NORMALIZED status->color mapping exactly, so a status reads the same
# color in the PDF as it does everywhere else in the app.
_STATUS_STYLES: dict[str, tuple[colors.Color, colors.Color]] = {
    "pending": (colors.HexColor("#B45309"), colors.HexColor("#FEF3C7")),
    "assigned": (colors.HexColor("#EA580C"), colors.HexColor("#FFEDD5")),
    "accepted": (colors.HexColor("#0284C7"), colors.HexColor("#E0F2FE")),
    "in_progress": (colors.HexColor("#0284C7"), colors.HexColor("#E0F2FE")),
    "resolved": (colors.HexColor("#16A34A"), colors.HexColor("#DCFCE7")),
    "rejected": (colors.HexColor("#DC2626"), colors.HexColor("#FEE2E2")),
    "escalated": (colors.HexColor("#DC2626"), colors.HexColor("#FEE2E2")),
    "cancelled": (_INK_3, colors.HexColor("#F1F5F9")),
}

_PAGE_W, _PAGE_H = A4
_MARGIN = 16 * mm
_CONTENT_W = _PAGE_W - 2 * _MARGIN
_HEADER_H = 27 * mm
_STRIPE_H = 2.2 * mm
_FOOTER_H = 14 * mm

_LOGO_PATH = Path(__file__).resolve().parents[2] / "frontend-react" / "public" / "brand" / "logo-mark.png"

# ===== Font registration for non-Latin scripts -- ReportLab's built-in "Helvetica" base font
# (Adobe's standard 14, WinAnsi-encoded) has no Devanagari/Bengali/Gujarati/Oriya glyphs at all,
# so drawing translated PDF content with it wouldn't raise an error -- it would just silently
# come out blank/garbled (confirmed directly: reportlab "succeeds" and produces zero visible
# glyphs for text it can't encode). Google's Noto Sans family (SIL Open Font License -- free to
# bundle and redistribute, unlike e.g. Windows' own Nirmala UI) covers exactly the 4 non-Latin
# scripts this app's SUPPORTED_LANGUAGES needs. Noto's own built-in Latin coverage means a string
# mixing English brand text with translated content never needs to switch fonts mid-string.
_FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_SCRIPT_FONTS: dict[str, tuple[str, str]] = {
    "hi": ("NotoDevanagari", "NotoSansDevanagari.ttf"),
    "mr": ("NotoDevanagari", "NotoSansDevanagari.ttf"),
    "bn": ("NotoBengali", "NotoSansBengali.ttf"),
    "gu": ("NotoGujarati", "NotoSansGujarati.ttf"),
    "or": ("NotoOriya", "NotoSansOriya.ttf"),
}
_registered_fonts: set[str] = set()


def _font_name(display_language: str) -> str:
    """The ReportLab font name to use for `display_language` -- "Helvetica" (built in, no
    registration needed) for English, or a lazily-registered Noto Sans font for the 4 non-Latin
    scripts. Registration is cached at module level (`_registered_fonts`) since ReportLab keeps
    registered fonts for the life of the process -- re-registering on every single report render
    would just be wasted file I/O, not a correctness issue.
    """
    entry = _SCRIPT_FONTS.get(display_language)
    if entry is None:
        return "Helvetica"
    font_name, filename = entry
    if font_name not in _registered_fonts:
        pdfmetrics.registerFont(TTFont(font_name, str(_FONTS_DIR / filename)))
        _registered_fonts.add(font_name)
    return font_name


_STYLES = getSampleStyleSheet()


def _build_styles(display_language: str) -> dict[str, ParagraphStyle]:
    """Fresh ParagraphStyle objects for one report render, using the right font for
    `display_language`. English keeps ReportLab's built-in Helvetica family, which has true
    Bold/Oblique faces; the 4 non-Latin scripts use a single registered Noto Sans face for
    everything (no separate bold/italic weight was bundled), so headers in those languages lean on
    the surrounding color/background for emphasis rather than a heavier font weight -- a minor,
    accepted simplification, not a readability problem.
    """
    font = _font_name(display_language)
    bold = "Helvetica-Bold" if font == "Helvetica" else font
    oblique = "Helvetica-Oblique" if font == "Helvetica" else font
    return {
        "title": ParagraphStyle("JMTitle", fontName=bold, fontSize=18, textColor=_NAVY, leading=21),
        "meta": ParagraphStyle("JMMeta", fontName=font, fontSize=8.5, textColor=_INK_2, leading=12, alignment=2),
        "panel_header": ParagraphStyle("JMPanelHeader", fontName=bold, fontSize=8, textColor=colors.white),
        "label": ParagraphStyle("JMLabel", fontName=oblique, fontSize=8.5, textColor=_INK_3),
        "value": ParagraphStyle("JMValue", fontName=font, fontSize=9, textColor=_INK, leading=12),
        "section_label": ParagraphStyle("JMSectionLabel", fontName=bold, fontSize=9, textColor=_INK),
        "body": ParagraphStyle("JMBody", parent=_STYLES["BodyText"], fontName=font, fontSize=9.5, textColor=_INK, leading=13),
        "muted": ParagraphStyle("JMMuted", parent=_STYLES["BodyText"], fontName=oblique, fontSize=8.5, textColor=_INK_3, leading=12),
        "timestamp": ParagraphStyle("JMTimestamp", fontName=font, fontSize=7.5, textColor=_INK_3, leading=10),
        "banner_title": ParagraphStyle("JMBannerTitle", fontName=bold, fontSize=11, textColor=_RESOLVED_FG),
        "resolved_chip": ParagraphStyle("JMResolvedChip", fontName=bold, fontSize=9, textColor=_RESOLVED_FG, alignment=1),
        "chip": ParagraphStyle("JMChip", fontName=bold, fontSize=7.5, leading=9),
        "th": ParagraphStyle("JMTh", fontName=bold, fontSize=7.5, textColor=colors.white),
        "td": ParagraphStyle("JMTd", fontName=font, fontSize=8, textColor=_INK_2, leading=11),
        "td_detail": ParagraphStyle("JMTdDetail", fontName=font, fontSize=8.5, textColor=_INK, leading=11),
    }


def _status_chip(status: str, display_language: str, chip_style: ParagraphStyle) -> Table:
    fg, bg = _STATUS_STYLES.get(status, (_INK_3, colors.HexColor("#F1F5F9")))
    style = ParagraphStyle("JMChipColored", parent=chip_style, textColor=fg)
    chip = Table([[Paragraph(_status_label(display_language, status).upper(), style)]])
    chip.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return chip


def _section_header(label: str, style: ParagraphStyle, letter_track: bool) -> Table:
    """A short accent-barred label (orange bar + text) marking off each report section -- the
    reference's own "TEST RESULTS — ..." heading style.

    `letter_track` (the tracked-caps look, e.g. "COMPLAINT" -> "C O M P L A I N T") only applies
    for English -- inserting spaces between Devanagari/Bengali/Gujarati/Oriya characters would
    detach a dependent vowel sign (matra) from the consonant it has to stay adjacent to, breaking
    the very glyphs it's meant to make more readable. Those scripts also have no concept of
    uppercase, so skipping `.upper()` too is not a stylistic choice, just correctness.
    """
    text = _spaced(label.upper()) if letter_track else label
    bar_w = 2.6 * mm
    t = Table([["", Paragraph(text, style)]], colWidths=[bar_w, _CONTENT_W - bar_w])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), _ORANGE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0), ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("TOPPADDING", (0, 0), (0, 0), 0), ("BOTTOMPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 6),
        ("TOPPADDING", (1, 0), (1, 0), 3), ("BOTTOMPADDING", (1, 0), (1, 0), 3),
    ]))
    return t


def _info_panel(
    title: str, rows: list[tuple[str, str]], width: float,
    header_style: ParagraphStyle, label_style: ParagraphStyle, value_style: ParagraphStyle,
    letter_track: bool,
) -> Table:
    """A titled, bordered fact panel -- navy header bar + label/value rows -- matching the
    reference's "CLIENT & SITE" / "SAMPLE & PANEL" panels. See `_section_header`'s docstring for
    why `letter_track` (tracked-caps) is English-only."""
    header_text = _spaced(title.upper()) if letter_track else title
    header = Table([[Paragraph(header_text, header_style)]], colWidths=[width])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    label_w = 30 * mm
    body_data = [[Paragraph(label, label_style), Paragraph(value, value_style)] for label, value in rows]
    body = Table(body_data, colWidths=[label_w, width - label_w])
    body.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, _LINE),
    ]))
    wrapper = Table([[header], [body]], colWidths=[width])
    wrapper.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
    ]))
    return wrapper


_EVIDENCE_THUMB_MAX = 78 * mm  # longest side of a thumbnail box
_EVIDENCE_THUMBS_PER_ROW = 2


def _evidence_thumbnails(file_paths: list[str]) -> Table | None:
    """Lays out evidence photos as rows of aspect-ratio-correct thumbnails, reading the real
    files from settings.UPLOAD_FOLDER (the same local-filesystem storage every other photo in
    this app already uses -- no new storage mechanism). Returns None if there's nothing to show,
    so callers can skip the surrounding section entirely rather than render an empty box.

    A file that's missing or unreadable is silently skipped (not an error) -- evidence is
    supplementary to the report's text content, and a single bad/corrupted file must never
    prevent a citizen or worker from getting their report at all.
    """
    if not file_paths:
        return None

    upload_dir = Path(settings.UPLOAD_FOLDER)
    thumbs = []
    for file_path in file_paths:
        full_path = upload_dir / file_path
        if not full_path.is_file():
            continue
        # Force a full decode now, eagerly, with PIL directly -- reportlab's Image flowable is
        # lazy: constructing it (and even reading .imageWidth/.imageHeight below) only touches
        # the header, not the actual pixel data, so a corrupt file's real decode error doesn't
        # surface until doc.build() draws it much later, by which point it's too late to skip
        # gracefully and the whole report generation crashes. Catching it here, before the file
        # is ever added to `thumbs`, is what actually makes "skip a bad file, don't break the
        # report" (this function's own contract, see docstring) true rather than aspirational.
        try:
            with PILImage.open(full_path) as probe:
                probe.load()
        except Exception:
            logger.warning("Evidence file %s could not be decoded as an image -- skipping it in the report.", file_path)
            continue
        try:
            img = RLImage(str(full_path))
            # Scale to fit within a square box, preserving aspect ratio -- RLImage(width=,
            # height=) alone would stretch a non-square photo instead of fitting it.
            ratio = min(_EVIDENCE_THUMB_MAX / img.imageWidth, _EVIDENCE_THUMB_MAX / img.imageHeight, 1.0)
            img.drawWidth = img.imageWidth * ratio
            img.drawHeight = img.imageHeight * ratio
        except Exception:
            continue
        thumbs.append(img)

    if not thumbs:
        return None

    rows = [thumbs[i : i + _EVIDENCE_THUMBS_PER_ROW] for i in range(0, len(thumbs), _EVIDENCE_THUMBS_PER_ROW)]
    if len(rows[-1]) < _EVIDENCE_THUMBS_PER_ROW:
        rows[-1] = rows[-1] + [""] * (_EVIDENCE_THUMBS_PER_ROW - len(rows[-1]))
    col_w = _CONTENT_W / _EVIDENCE_THUMBS_PER_ROW
    table = Table(rows, colWidths=[col_w] * _EVIDENCE_THUMBS_PER_ROW)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _draw_watermark(c: pdfcanvas.Canvas) -> None:
    """A faint, centered, medium-size logo mark behind the body content on every page -- a
    watermark sized to sit clear of the header band and footer line (see the size/centering math
    below), not a full-bleed background image."""
    if not _LOGO_PATH.exists():
        return
    size = 120 * mm
    c.saveState()
    c.setFillAlpha(0.06)
    c.drawImage(
        str(_LOGO_PATH), (_PAGE_W - size) / 2, (_PAGE_H - size) / 2,
        width=size, height=size, mask="auto", preserveAspectRatio=True,
    )
    c.restoreState()


def _draw_header(c: pdfcanvas.Canvas, _doc, display_language: str = "en") -> None:
    """Drawn on every page (BaseDocTemplate's onPage callback, bound to a specific
    `display_language` via functools.partial in generate_pdf_bytes) -- the watermark, navy brand
    band, orange accent stripe, and logo+wordmark lockup the reference report repeats on each
    page."""
    _draw_watermark(c)

    # Light green header background -- --service-waste-bg / --status-resolved-bg, this app's own
    # existing light-green token (already used for the resolved banner further down this same
    # report), reused here rather than inventing a new color. Still a light surface, so the
    # logo mark (unmodified original colors) keeps full contrast, same reasoning as before.
    c.saveState()
    c.setFillColor(colors.HexColor("#DCFCE7"))
    c.rect(0, _PAGE_H - _HEADER_H, _PAGE_W, _HEADER_H, stroke=0, fill=1)
    c.setFillColor(_ORANGE)
    c.rect(0, _PAGE_H - _HEADER_H - _STRIPE_H, _PAGE_W, _STRIPE_H, stroke=0, fill=1)

    center_y = _PAGE_H - _HEADER_H / 2

    # Mark pinned to the left edge, wordmark block pinned to the right edge -- two distinct
    # groups spanning the header's width, not a single tight lockup.
    mark_size = 20 * mm
    mark_x = _MARGIN
    if _LOGO_PATH.exists():
        c.drawImage(
            str(_LOGO_PATH), mark_x, center_y - mark_size / 2,
            width=mark_size, height=mark_size, mask="auto", preserveAspectRatio=True,
        )

    # Wordmark in the source logo's own "Jan"/"Sarthi"/"AI" colors, unmodified -- navy/green/blue
    # all read correctly on this light surface, the same three brand colors (--accent, --primary,
    # --accent-fg) used everywhere else in the app on light surfaces. The brand name itself is
    # never translated (matches the frontend, which also always renders "JanSarthi AI" as-is).
    #
    # LIVE-REPORTED: this said "Mitra" instead of "Sarthi" -- a leftover from the app's old name
    # (JanMitra AI), never updated when the header wordmark was written, even though the footer a
    # few hundred lines below (and every other user-facing string in this file/the frontend)
    # already correctly says "JanSarthi AI". Every downloaded report's header literally
    # contradicted its own footer until this was caught.
    c.setFont("Helvetica-Bold", 19)
    segments = [("Jan", _NAVY), ("Sarthi", colors.HexColor("#16A34A")), (" AI", colors.HexColor("#0284C7"))]
    total_w = sum(c.stringWidth(text, "Helvetica-Bold", 19) for text, _ in segments)
    text_x = _PAGE_W - _MARGIN - total_w  # right-align the wordmark's right edge to the margin
    cx = text_x
    for segment_text, color in segments:
        c.setFillColor(color)
        c.drawString(cx, center_y + 1.5 * mm, segment_text)
        cx += c.stringWidth(segment_text, "Helvetica-Bold", 19)

    font = _font_name(display_language)
    tagline = _label(display_language, "footer_tagline")
    tagline_text = _spaced(tagline.upper()) if font == "Helvetica" else tagline
    c.setFillColor(_INK_3)
    c.setFont(font, 7.5)
    c.drawRightString(_PAGE_W - _MARGIN, center_y - 6 * mm, tagline_text)
    c.restoreState()


class _NumberedCanvas(pdfcanvas.Canvas):
    """Standard ReportLab two-pass pattern for a "Page X of Y" footer: total page count isn't
    known until the whole document has been laid out, so each page's drawing state is captured
    on `showPage()` and the footer is only actually drawn once, in `save()`, after every page has
    been seen."""

    def __init__(self, *args, report_id: str = "", display_language: str = "en", **kwargs):
        pdfcanvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states: list[dict] = []
        self._report_id = report_id
        self._display_language = display_language

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total)
            pdfcanvas.Canvas.showPage(self)
        pdfcanvas.Canvas.save(self)

    def _draw_footer(self, total: int) -> None:
        font = _font_name(self._display_language)
        self.saveState()
        self.setStrokeColor(_LINE)
        self.setLineWidth(0.6)
        self.line(_MARGIN, _FOOTER_H - 3 * mm, _PAGE_W - _MARGIN, _FOOTER_H - 3 * mm)
        self.setFont(font, 6.8)
        self.setFillColor(_INK_3)
        tagline = _label(self._display_language, "footer_tagline")
        note = _label(self._display_language, "footer_note").format(id=self._report_id)
        self.drawString(_MARGIN, _FOOTER_H - 7 * mm, f"JanSarthi AI — {tagline}   |   {note}")
        self.setFont(font, 7.5)
        self.setFillColor(_INK_2)
        page_of = _label(self._display_language, "page_of").format(page=self._pageNumber, total=total)
        self.drawRightString(_PAGE_W - _MARGIN, _FOOTER_H - 7 * mm, page_of)
        self.restoreState()


def generate_pdf_bytes(data: ComplaintReportData, display_language: str = "en") -> bytes:
    """Renders `data` into a PDF, returned as raw bytes (never written to disk -- the download
    route streams it directly). Pure formatting of already-assembled facts; no new data is
    computed here -- `data`'s own text fields (service_summary, initial_assessment, etc.) are
    expected to already be in `display_language` by the time they reach this function (see
    build_report_data's own display_language/translation_service params); this function only
    translates the surrounding labels/headers, which have no equivalent upstream translation step
    of their own (see the `_PDF_LABELS` module docstring above).

    One shared template for every viewer (admin, worker, citizen) -- the report describes the
    complaint, not the reader, and all three already see the same underlying `ComplaintReportData`
    (see routes/complaints.py's `_get_visible_complaint`), so a role-specific layout would just be
    the same facts re-skinned for no reason.

    Layout is styled after a reference lab-report PDF the user supplied (navy brand header with
    accent stripe, boxed two-column fact panels, a colored pass/fail-style status banner, and a
    running "Page X of Y" footer) -- reimplemented here with JanSarthi AI's own logo and palette
    (see the `frontend-react/src/styles/global.css` brand tokens mirrored in the color constants
    above), not the reference's.
    """
    styles = _build_styles(display_language)
    letter_track = display_language == "en"  # see _section_header's docstring

    def L(key: str) -> str:
        return _label(display_language, key)

    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4,
        leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN, bottomMargin=_MARGIN,
    )
    frame = Frame(
        _MARGIN, _FOOTER_H,
        _CONTENT_W, _PAGE_H - _HEADER_H - _STRIPE_H - 4 * mm - _FOOTER_H,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(
        id="report", frames=[frame], onPage=partial(_draw_header, display_language=display_language),
    )])

    story: list = []

    story.append(Table(
        [[Paragraph(L("report_title"), styles["title"]),
          Paragraph(f"<b>{data.display_id}</b><br/>{L('filed')} {_fmt(data.created_at)}", styles["meta"])]],
        colWidths=[_CONTENT_W - 55 * mm, 55 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]),
    ))
    story.append(Spacer(1, 5 * mm))

    panel_gap = 4 * mm
    panel_w = (_CONTENT_W - panel_gap) / 2
    left_rows = [
        (L("complaint_id"), data.display_id),
        (L("filed_on"), _fmt(data.created_at)),
        (L("resolved_on"), _fmt(data.resolved_at)),
        (L("assigned_worker"), data.assigned_worker_name or "—"),
    ]
    right_rows = [
        (L("ward"), data.location_ward or "—"),
        (L("ulb"), data.location_ulb or "—"),
        (L("district"), data.location_district or "—"),
        (L("state"), data.location_state or "—"),
    ]
    if data.location_address:
        right_rows.append((L("address"), data.location_address))
    story.append(Table(
        [[
            _info_panel(L("complaint_panel"), left_rows, panel_w, styles["panel_header"], styles["label"], styles["value"], letter_track),
            "",
            _info_panel(L("location_panel"), right_rows, panel_w, styles["panel_header"], styles["label"], styles["value"], letter_track),
        ]],
        colWidths=[panel_w, panel_gap, panel_w],
        style=TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]),
    ))
    story.append(Spacer(1, 5 * mm))

    banner_chip_w = 30 * mm
    banner_font = _font_name(display_language)
    # The check mark is pinned to Helvetica regardless of `display_language`: Noto's script-
    # specific fonts (Devanagari/Bengali/Gujarati/Oriya) only cover their own script plus Basic
    # Latin, not general symbols like U+2713 -- confirmed by rendering an actual test PDF, where
    # it came out as a blank/missing-glyph box instead of a checkmark. Helvetica has it.
    banner_text = Paragraph(
        f"<font name='Helvetica'>✓</font> {L('resolved_banner_title')}<br/>"
        f"<font name='{banner_font}' size=8>{L('resolved_on')} {_fmt(data.resolved_at)} — {L('resolved_banner_sub')}</font>",
        styles["banner_title"],
    )
    story.append(Table(
        [[banner_text, Paragraph(L("resolved_chip").upper(), styles["resolved_chip"])]],
        colWidths=[_CONTENT_W - banner_chip_w, banner_chip_w],
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), _RESOLVED_BG),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#16A34A")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (1, 0), "CENTER"),
            ("LEFTPADDING", (0, 0), (0, 0), 10), ("RIGHTPADDING", (1, 0), (1, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]),
    ))
    story.append(Spacer(1, 6 * mm))

    story.append(_section_header(L("description_section"), styles["section_label"], letter_track))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(data.service_summary or "—", styles["body"]))
    if data.original_description and data.original_description != data.service_summary:
        story.append(Paragraph(f"<i>{data.original_description}</i>", styles["muted"]))
    citizen_thumbs = _evidence_thumbnails(data.citizen_evidence)
    if citizen_thumbs is not None:
        story.append(Spacer(1, 3 * mm))
        story.append(citizen_thumbs)
    story.append(Spacer(1, 5 * mm))

    if data.initial_assessment:
        story.append(_section_header(L("initial_assessment_section"), styles["section_label"], letter_track))
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(_fmt(data.initial_assessment_at), styles["timestamp"]))
        story.append(Paragraph(data.initial_assessment, styles["body"]))
        initial_thumbs = _evidence_thumbnails(data.initial_assessment_evidence)
        if initial_thumbs is not None:
            story.append(Spacer(1, 3 * mm))
            story.append(initial_thumbs)
        story.append(Spacer(1, 5 * mm))

    if data.progress_updates:
        story.append(_section_header(L("progress_updates_section"), styles["section_label"], letter_track))
        story.append(Spacer(1, 3 * mm))
        for u in data.progress_updates:
            story.append(Paragraph(_fmt(u["created_at"]), styles["timestamp"]))
            story.append(Paragraph(u["text"], styles["body"]))
            update_thumbs = _evidence_thumbnails(u.get("evidence", []))
            if update_thumbs is not None:
                story.append(Spacer(1, 2 * mm))
                story.append(update_thumbs)
            story.append(Spacer(1, 3 * mm))
        story.append(Spacer(1, 2 * mm))

    story.append(_section_header(L("completion_section"), styles["section_label"], letter_track))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(data.completion_status or L("not_recorded"), styles["body"]))
    completion_thumbs = _evidence_thumbnails(data.completion_evidence)
    if completion_thumbs is not None:
        story.append(Spacer(1, 3 * mm))
        story.append(completion_thumbs)
    story.append(Spacer(1, 6 * mm))

    story.append(_section_header(L("status_timeline_section"), styles["section_label"], letter_track))
    story.append(Spacer(1, 3 * mm))
    if data.timeline:
        col_time, col_status = 30 * mm, 28 * mm
        rows = [[
            Paragraph(L("time_col").upper(), styles["th"]),
            Paragraph(L("status_col").upper(), styles["th"]),
            Paragraph(L("details_col").upper(), styles["th"]),
        ]]
        for event in data.timeline:
            # Mirrors ComplaintReportView.tsx's own timeline rendering exactly (never shows
            # `note` either) so the PDF and the in-app report never disagree on what a row says --
            # `note` is free English text set by backend code (e.g. "Assigned to worker."), with
            # no translation of its own, so showing it here would silently reintroduce the same
            # mixed-language problem this whole change exists to fix.
            if event["from_status"]:
                # The arrow is pinned to Helvetica for the same reason the resolved banner's
                # check mark is (see above): Noto's script-specific fonts don't cover general
                # symbols like U+2192, only their own script plus Basic Latin.
                detail = (
                    f"{_status_label(display_language, event['from_status'])} "
                    f"<font name='Helvetica'>→</font> "
                    f"{_status_label(display_language, event['to_status'])}"
                )
            else:
                detail = _status_label(display_language, event["to_status"])
            rows.append([
                Paragraph(_fmt(event["created_at"]), styles["td"]),
                _status_chip(event["to_status"], display_language, styles["chip"]),
                Paragraph(detail, styles["td_detail"]),
            ])
        timeline_table = Table(rows, colWidths=[col_time, col_status, _CONTENT_W - col_time - col_status], repeatRows=1)
        timeline_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _NAVY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, _LINE),
        ]))
        story.append(timeline_table)
    else:
        story.append(Paragraph(L("no_status_changes"), styles["muted"]))

    doc.build(story, canvasmaker=partial(_NumberedCanvas, report_id=data.display_id, display_language=display_language))
    return buffer.getvalue()
