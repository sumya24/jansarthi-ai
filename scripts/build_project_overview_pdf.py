"""Generates docs/JanSarthi_AI_Project_Overview.pdf -- a designed, first-time-reader onboarding
document, in the same style as the original JanMitra_AI_Project_Overview.pdf but rewritten to
describe the real, current app (Ask Sarthi / LangGraph / RAG / GCP), not the pre-Ask-Sarthi,
2-table, Streamlit-adjacent version the original doc was written against.

Run: python scripts/build_project_overview_pdf.py
Output: docs/JanSarthi_AI_Project_Overview.pdf

Uses reportlab (already a project dependency -- see backend/services/complaint_report_service.py
for the other place this project generates PDFs) and only its built-in Helvetica family, since this
document is English-only and doesn't need the Noto Sans multi-script registration that one does.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    KeepTogether,
    HRFlowable,
)

OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "JanSarthi_AI_Project_Overview.pdf"

# ---------------------------------------------------------------------------
# Palette -- a dark teal/ink for headings and the cover badge, a rust/terracotta
# for section eyebrows and accents, warm off-white panels, charcoal body text.
# ---------------------------------------------------------------------------
INK = colors.HexColor("#22322b")
TEAL = colors.HexColor("#2f4a3e")
RUST = colors.HexColor("#b5533c")
CREAM = colors.HexColor("#eae7dd")
PANEL = colors.HexColor("#f4f2ea")
CHARCOAL = colors.HexColor("#33322e")
MUTED = colors.HexColor("#6b6a62")
LINE = colors.HexColor("#d8d5c8")
BADGE_TEXT = colors.HexColor("#f4f2ea")

PAGE_W, PAGE_H = A4
MARGIN = 2.1 * cm

styles = {
    "eyebrow": ParagraphStyle(
        "eyebrow", fontName="Helvetica-Bold", fontSize=9, leading=11,
        textColor=RUST, spaceAfter=6, tracking=1,
    ),
    "h1": ParagraphStyle(
        "h1", fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=INK, spaceAfter=10,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=13, leading=16,
        textColor=INK, spaceBefore=14, spaceAfter=6,
    ),
    "body": ParagraphStyle(
        "body", fontName="Helvetica", fontSize=9.6, leading=14.5,
        textColor=CHARCOAL, spaceAfter=8,
    ),
    "small": ParagraphStyle(
        "small", fontName="Helvetica", fontSize=8.3, leading=12,
        textColor=MUTED,
    ),
    "cell": ParagraphStyle(
        "cell", fontName="Helvetica", fontSize=8.3, leading=11.5,
        textColor=CHARCOAL,
    ),
    "cellhead": ParagraphStyle(
        "cellhead", fontName="Helvetica-Bold", fontSize=7.6, leading=10,
        textColor=MUTED,
    ),
    "cover_title": ParagraphStyle(
        "cover_title", fontName="Helvetica-Bold", fontSize=34, leading=38,
        textColor=INK,
    ),
    "cover_sub": ParagraphStyle(
        "cover_sub", fontName="Helvetica", fontSize=11.5, leading=17,
        textColor=CHARCOAL,
    ),
    "callout": ParagraphStyle(
        "callout", fontName="Helvetica", fontSize=9, leading=13.5,
        textColor=CHARCOAL,
    ),
    "stat_num": ParagraphStyle(
        "stat_num", fontName="Helvetica-Bold", fontSize=20, leading=22,
        textColor=INK,
    ),
    "stat_label": ParagraphStyle(
        "stat_label", fontName="Helvetica", fontSize=7.3, leading=9.5,
        textColor=MUTED,
    ),
    "code": ParagraphStyle(
        "code", fontName="Courier", fontSize=8.4, leading=12,
        textColor=INK, backColor=PANEL,
    ),
}


def eyebrow_title(num: str, title: str):
    return [
        Paragraph(f"{num} &middot; {title.upper() if False else title}", styles["eyebrow"]),
    ]


def section(num: str, label: str, title: str):
    return [
        Spacer(1, 4),
        Paragraph(f"{num} &middot; {label}", styles["eyebrow"]),
        Paragraph(title, styles["h1"]),
    ]


def h2(text: str):
    return Paragraph(text, styles["h2"])


def p(text: str):
    return Paragraph(text, styles["body"])


def rule():
    return HRFlowable(width="100%", thickness=0.8, color=LINE, spaceBefore=10, spaceAfter=10)


def table(rows, col_widths, header=True):
    data = []
    for r_i, row in enumerate(rows):
        style = "cellhead" if (header and r_i == 0) else "cell"
        data.append([Paragraph(str(c), styles[style]) for c in row])
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    ts = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, LINE),
    ]
    if header:
        ts.append(("BACKGROUND", (0, 0), (-1, 0), CREAM))
    t.setStyle(TableStyle(ts))
    return t


def callout(text: str, color=RUST):
    inner = Table(
        [[Paragraph(text, styles["callout"])]],
        colWidths=[16.4 * cm],
    )
    inner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("LINEBEFORE", (0, 0), (0, -1), 2.4, color),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return inner


def stat_row(stats):
    """stats: list of (number, label) tuples, up to 4."""
    cells = []
    for num, label in stats:
        cells.append(
            Table(
                [[Paragraph(num, styles["stat_num"])], [Paragraph(label, styles["stat_label"])]],
                colWidths=[4.0 * cm],
            )
        )
    row = Table([cells], colWidths=[4.1 * cm] * len(stats))
    row.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("LINEAFTER", (0, 0), (-2, 0), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return row


# ---------------------------------------------------------------------------
# Diagrams (reportlab.graphics shapes -- real vector boxes/arrows, not an image)
# ---------------------------------------------------------------------------

def _box(d, x, y, w, h, title, lines, fill=colors.white, text_color=INK):
    d.add(Rect(x, y, w, h, fillColor=fill, strokeColor=INK, strokeWidth=0.8, rx=4, ry=4))
    d.add(String(x + 8, y + h - 14, title, fontName="Helvetica-Bold", fontSize=8.2, fillColor=text_color))
    ty = y + h - 27
    for line in lines:
        d.add(String(x + 8, ty, line, fontName="Helvetica", fontSize=6.6, fillColor=MUTED))
        ty -= 9


def _arrow(d, x1, y1, x2, y2, label=""):
    d.add(Line(x1, y1, x2, y2, strokeColor=INK, strokeWidth=0.9))
    # arrowhead
    if abs(x2 - x1) > abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        d.add(Polygon(
            points=[x2, y2, x2 - 6 * direction, y2 + 3, x2 - 6 * direction, y2 - 3],
            fillColor=INK, strokeColor=INK,
        ))
    else:
        direction = 1 if y2 > y1 else -1
        d.add(Polygon(
            points=[x2, y2, x2 + 3, y2 - 6 * direction, x2 - 3, y2 - 6 * direction],
            fillColor=INK, strokeColor=INK,
        ))
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        d.add(String(mx - len(label) * 2.2, my + 4, label, fontName="Helvetica", fontSize=6.2, fillColor=MUTED))


def architecture_diagram():
    d = Drawing(16.6 * cm, 8.6 * cm)
    W = 16.6 * cm
    _box(d, 0, 5.4 * cm, 4.2 * cm, 2.6 * cm, "Browser", ["React + TypeScript SPA", "Citizen / Worker / Admin", "views + Ask Sarthi chat UI"])
    _box(d, 6.2 * cm, 5.4 * cm, 4.6 * cm, 2.6 * cm, "Caddy (GCP VM)", ["reverse proxy, auto HTTPS", "serves built React files", "/auth /admin /complaints", "/ask-janmitra /uploads"])
    _box(d, 6.2 * cm, 1.6 * cm, 4.6 * cm, 2.6 * cm, "FastAPI backend", ["LangGraph orchestration", "the only piece that", "touches the DB / vector", "store / uploads folder"])
    _box(d, 12.2 * cm, 6.5 * cm, 4.2 * cm, 1.6 * cm, "SQLite", ["jansarthi.db, 22 tables"])
    _box(d, 12.2 * cm, 4.5 * cm, 4.2 * cm, 1.6 * cm, "ChromaDB", ["RAG vector store"])
    _box(d, 12.2 * cm, 2.5 * cm, 4.2 * cm, 1.6 * cm, "uploads/", ["complaint photos"])
    _box(d, 12.2 * cm, 0.2 * cm, 4.2 * cm, 1.9 * cm, "External services", ["Sarvam AI · Gemini vision", "LangSmith + Phoenix", "Sentry"], fill=CREAM)

    _arrow(d, 4.2 * cm, 6.7 * cm, 6.2 * cm, 6.7 * cm, "HTTPS + JWT")
    _arrow(d, 8.5 * cm, 5.4 * cm, 8.5 * cm, 4.2 * cm, "")
    _arrow(d, 10.8 * cm, 2.9 * cm, 12.2 * cm, 7.3 * cm, "")
    _arrow(d, 10.8 * cm, 2.9 * cm, 12.2 * cm, 5.3 * cm, "")
    _arrow(d, 10.8 * cm, 2.9 * cm, 12.2 * cm, 3.3 * cm, "")
    _arrow(d, 10.8 * cm, 1.9 * cm, 12.2 * cm, 1.1 * cm, "API calls")
    return d


def message_flow_diagram():
    d = Drawing(16.6 * cm, 6.4 * cm)
    _box(d, 0.0 * cm, 4.6 * cm, 3.6 * cm, 1.6 * cm, "Citizen message", ["text / voice / photo"])
    _box(d, 4.2 * cm, 4.6 * cm, 3.9 * cm, 1.6 * cm, "Guardrail: input scan", ["blocks known jailbreak", "/ injection phrasing"])
    _box(d, 8.7 * cm, 4.6 * cm, 3.6 * cm, 1.6 * cm, "Intent classification", ["keyword-based, tested"])
    _box(d, 12.9 * cm, 4.6 * cm, 3.6 * cm, 1.6 * cm, "Location resolution", ["gazetteer / hierarchy"])

    _box(d, 0.0 * cm, 1.0 * cm, 4.0 * cm, 2.6 * cm, "Complaint filing", ["builds a draft over", "multiple turns, needs a", "citizen confirm before", "it's actually created"])
    _box(d, 4.6 * cm, 1.0 * cm, 4.0 * cm, 2.6 * cm, "Civic Q&A (RAG)", ["retrieve grounded chunks,", "generate — never guess;", "\"I don't know\" if nothing", "clears the relevance bar"])
    _box(d, 9.2 * cm, 1.0 * cm, 4.0 * cm, 2.6 * cm, "Status check", ["looks up one complaint", "the citizen actually owns"])
    _box(d, 13.4 * cm, 1.0 * cm, 3.2 * cm, 2.6 * cm, "Output guardrail", ["scans + responds in", "citizen's own language"], fill=CREAM)

    _arrow(d, 3.6 * cm, 5.4 * cm, 4.2 * cm, 5.4 * cm)
    _arrow(d, 8.1 * cm, 5.4 * cm, 8.7 * cm, 5.4 * cm)
    _arrow(d, 12.3 * cm, 5.4 * cm, 12.9 * cm, 5.4 * cm)
    _arrow(d, 14.0 * cm, 4.6 * cm, 6.6 * cm, 3.6 * cm, "")
    _arrow(d, 14.0 * cm, 4.6 * cm, 11.2 * cm, 3.6 * cm, "")
    _arrow(d, 14.0 * cm, 4.6 * cm, 2.0 * cm, 3.6 * cm, "")
    _arrow(d, 4.0 * cm, 2.3 * cm, 4.6 * cm, 2.3 * cm)
    _arrow(d, 8.6 * cm, 2.3 * cm, 9.2 * cm, 2.3 * cm)
    _arrow(d, 13.2 * cm, 2.3 * cm, 13.4 * cm, 2.3 * cm)
    return d


# ---------------------------------------------------------------------------
# Build the story
# ---------------------------------------------------------------------------

def build():
    today = datetime.date.today().isoformat()
    story = []

    # --- Cover -----------------------------------------------------------
    badge = Table([[Paragraph("JS", ParagraphStyle("badge", fontName="Helvetica-Bold", fontSize=20, textColor=BADGE_TEXT, alignment=1))]], colWidths=[1.6 * cm], rowHeights=[1.6 * cm])
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    story.append(Spacer(1, 30))
    story.append(badge)
    story.append(Spacer(1, 18))
    story.append(Paragraph("ENGINEERING DOCUMENTATION &middot; WRITTEN FOR A FIRST-TIME READER", styles["eyebrow"]))
    story.append(Paragraph("JanSarthi AI", styles["cover_title"]))
    story.append(Paragraph("Project Overview", styles["cover_title"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "A ground-up walkthrough of what this codebase is, how its pieces fit together, and "
        "what a first-time reader needs to know — including the real, current AI "
        "architecture (Ask Sarthi, LangGraph, retrieval-augmented generation).",
        styles["cover_sub"],
    ))
    story.append(Spacer(1, 16))
    story.append(rule())
    meta = table([
        ["Live", "jansarthi-ai.duckdns.org"],
        ["Stack", "FastAPI · SQLite · React + TypeScript · Sarvam AI · LangGraph · ChromaDB · GCP"],
        ["Prepared", today],
    ], [3.0 * cm, 13.6 * cm], header=False)
    meta.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0, colors.white),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("TEXTCOLOR", (0, 0), (0, -1), INK),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(meta)
    story.append(PageBreak())

    # --- 01 START HERE -----------------------------------------------------
    story += section("01", "START HERE", "What JanSarthi AI actually is")
    story.append(p(
        "A multilingual civic-grievance platform — still built around the same core problem "
        "the project started with: the citizen reporting a problem and the municipal worker fixing "
        "it often don't share a language. What's changed since the earliest version is <b>how</b> "
        "that gap gets closed: not a translated web form, but <b>Ask Sarthi</b>, a single "
        "conversational AI agent a citizen can speak or type to, in any of 6 Indian languages "
        "(English, Hindi, Marathi, Odia, Gujarati, Bengali), to file a complaint, ask a civic "
        "question, or check a complaint's status."
    ))
    story.append(p(
        "Under the hood this is a real production system, not a prototype: a stateless "
        "<b>LangGraph</b> orchestration graph routes every message, a <b>RAG</b> (retrieval-"
        "augmented generation) pipeline answers civic questions only from a real, sourced "
        "knowledge base (never a guess), and hand-rolled guardrails scan every message in and "
        "every response out for prompt-injection attempts. <b>Sarvam AI</b> (speech-to-text, "
        "translation, chat) and <b>Google Gemini</b> (photo captioning, with a local model as an "
        "automatic fallback) are the two external AI vendors; everything else — the graph, the "
        "retrieval logic, the guardrails — is this codebase's own."
    ))
    story.append(h2("The three kinds of people who use it"))
    story.append(table([
        ["ROLE", "WHO THEY ARE", "WHAT THEY CAN DO", "HOW THEIR ACCOUNT IS CREATED"],
        ["CITIZEN", "A resident reporting a problem or asking a civic question",
         "Talk to Ask Sarthi (voice/text/photo) to file complaints or get grounded answers; track their own complaints",
         "Signs up themselves, with mandatory email OTP verification"],
        ["WORKER", "Municipal staff for one specific ward",
         "See every complaint assigned to their ward; accept, reject (auto-reassigns), mark in-progress/resolved",
         "Created only by an Admin — cannot self sign-up"],
        ["ADMIN", "Municipal office overseeing all wards",
         "See every complaint everywhere, manage workers, review AI cost/latency and knowledge-base gaps",
         "Seeded directly into the database via a script — never created through the app itself"],
    ], [2.1 * cm, 4.3 * cm, 6.9 * cm, 3.3 * cm]))
    story.append(callout(
        "That last row is still deliberate, not an oversight: no button, form, or API endpoint "
        "anywhere in this codebase can create an Admin. The first Admin account for a real "
        "deployment is planted straight into the database via <font face=\"Courier\">scripts/"
        "seed_admin.py</font>. “Can an attacker escalate their own privileges through the API” "
        "has a provably-no answer, because the capability doesn't exist in the API surface at all."
    ))
    story.append(PageBreak())

    # --- 02 SYSTEM ARCHITECTURE ---------------------------------------------
    story += section("02", "THE BIG PICTURE", "System architecture")
    story.append(p(
        "Everything runs on a single GCP VM: two Docker containers (the built React app served "
        "by Caddy, and the FastAPI backend) with Caddy as the one thing exposed to the internet. "
        "Four external services sit outside this codebase entirely, each reached over the "
        "internet with its own API key: Sarvam AI, Google Gemini, LangSmith + Arize Phoenix "
        "(tracing), and Sentry (error monitoring)."
    ))
    story.append(architecture_diagram())
    story.append(Spacer(1, 6))
    story.append(p(
        "<b>What this shows:</b> the browser only ever talks to Caddy, which either serves the "
        "built frontend directly or reverse-proxies to the backend. The backend is still the only "
        "piece that touches SQLite, ChromaDB, or the uploads folder — the browser is never "
        "trusted with direct access to any of them, or to an AI API key."
    ))
    story.append(PageBreak())

    # --- 03 CORE MECHANISM --------------------------------------------------
    story += section("03", "THE CORE MECHANISM", "How one message actually flows through Ask Sarthi")
    story.append(p(
        "Every message, regardless of what it's about, passes through the same shared guardrail "
        "input scan and intent classification before the graph decides which of three real jobs "
        "it's doing. Filing a complaint is the one path with a hard rule: nothing is written to "
        "the database until the citizen explicitly confirms the draft — Ask Sarthi never files "
        "a complaint a citizen didn't actually agree to."
    ))
    story.append(message_flow_diagram())
    story.append(Spacer(1, 6))
    story.append(p(
        "<b>Translation, concretely:</b> a complaint's <font face=\"Courier\">original_text</font> "
        "is never modified after creation; a canonical English <font face=\"Courier\">translated_"
        "text</font> is built once at submit time. Viewing a complaint in a different language "
        "translates <b>on first read, then caches the result</b> (a real cache table, not a "
        "translate-on-every-view design) — the same pattern used for a worker's own progress "
        "notes, whose source language is auto-detected rather than assumed from their profile."
    ))
    story.append(PageBreak())

    # --- 04 GLOSSARY ---------------------------------------------------------
    story += section("04", "BEFORE WE GO FURTHER", "Words used throughout this document")
    glossary = [
        ("LangGraph", "The state-machine framework Ask Sarthi's whole conversation flow is built on — 14 real nodes, conditional-edge routing, one shared response node."),
        ("RAG", "Retrieval-Augmented Generation: look up real, relevant knowledge-base chunks first, then generate an answer grounded only in those chunks — never a guess from the model's own training."),
        ("Vector store / ChromaDB", "A database that finds chunks of text by meaning (via embeddings), not exact keyword match. This project's RAG knowledge base is indexed here."),
        ("Embedding", "A numeric representation of a piece of text, positioned so semantically similar text ends up numerically close — what a vector search actually compares."),
        ("Reranker", "A second, more precise model that re-scores an initial shortlist of retrieved chunks — slower per-item than the first-pass search, so only run on a small shortlist."),
        ("Guardrails", "Hand-rolled pattern-based checks scanning messages in and responses out for known prompt-injection/jailbreak phrasing — a real floor, not a semantic guarantee."),
        ("MCP", "Model Context Protocol — a standard way to expose a service's own functions as callable “tools” for an external AI client. This app's services are exposed this way for any MCP-compatible client."),
        ("JWT", "JSON Web Token — a signed, tamper-proof string proving who a user is on every request, without the server needing to remember sessions."),
        ("CSRF", "Cross-Site Request Forgery — the attack a cookie-based session opens up; defended here with a double-submit cookie a forged cross-site request can't replicate."),
        ("ORM", "Object-Relational Mapper — lets Python code describe database rows as regular classes instead of writing raw SQL. This project uses SQLAlchemy."),
        ("OTP", "One-Time Password — a short emailed code used here to verify a citizen's email address and to authorize a password reset."),
    ]
    rows = [["TERM", "MEANING"]] + [[k, v] for k, v in glossary]
    story.append(table(rows, [3.6 * cm, 13.0 * cm]))
    story.append(PageBreak())

    # --- 05 FILE TOUR --------------------------------------------------------
    story += section("05", "FILE-BY-FILE TOUR", "The shape of the codebase today")
    story.append(p(
        "The codebase has grown a great deal since the earliest version — 6 backend route "
        "modules and 30+ services, 21 frontend pages and 36+ components. Rather than hand-"
        "maintain an exhaustive file list here (a list like that has already drifted stale twice "
        "in this project's history), this section names the load-bearing pieces worth knowing "
        "by name on a first read; the real, current folders are the source of truth."
    ))
    story.append(h2("Backend (backend/) — highlights"))
    story.append(table([
        ["PIECE", "WHAT IT'S FOR"],
        ["routes/ask_janmitra.py", "The Ask Sarthi endpoint — the entry point into the LangGraph orchestration"],
        ["services/orchestration/graph.py, nodes.py", "The 14-node LangGraph graph and every node's real logic"],
        ["services/rag_retriever.py, vector_store.py", "Embed → search → hybrid BM25 widen → threshold → rerank → tie-break"],
        ["services/guardrails.py", "Prompt-injection input/output scanning, shared by every flow"],
        ["services/assignment_service.py", "Ward-based worker assignment, with automatic reassignment on rejection"],
        ["services/complaint_agent.py", "The original AI pipeline: STT → normalize → translate → summarize → save"],
        ["services/vision_service.py", "Photo captioning — Gemini first, a local model as automatic fallback"],
        ["services/observability/tracing.py", "LangSmith + Phoenix dual tracing, real Rs. cost tracking"],
        ["repositories/complaint_repository.py", "Plain, reviewable DB queries — never LLM-constructed SQL, anywhere"],
        ["scripts/build_rag_knowledge_base.py, build_rag_embeddings.py", "Regenerate the RAG chunk file and the ChromaDB index"],
        ["scripts/seed_admin.py", "Plants the first Admin account directly into the database"],
    ], [5.4 * cm, 11.2 * cm]))
    story.append(h2("Frontend (frontend-react/src/) — highlights"))
    story.append(table([
        ["PIECE", "WHAT IT'S FOR"],
        ["pages/AskJanMitra.tsx", "The actual Ask Sarthi chat UI — citizens' main way of using the app"],
        ["pages/{Citizen,Worker,Admin}Dashboard.tsx", "The three role-scoped dashboards"],
        ["pages/{Citizen,Worker,Admin}ComplaintDetail.tsx", "Full complaint detail, including the location breadcrumb and PDF report"],
        ["components/LocationPicker.tsx", "GPS or manual location capture, with a live reverse-geocoded preview"],
        ["components/PasswordInput.tsx", "Shared show/hide password toggle, used across every auth form"],
        ["lib/api.ts", "Every backend call the frontend makes, in one file, with auth + CSRF handling"],
        ["lib/auth.tsx", "Session state from httpOnly cookies — never localStorage for tokens"],
        ["lib/useAudioRecorder.ts", "Client-side voice chunking (≤28s pieces) so long recordings never hit Sarvam's 30s cap"],
    ], [5.4 * cm, 11.2 * cm]))
    story.append(PageBreak())

    # --- 06 DATABASE ---------------------------------------------------------
    story += section("06", "WHERE THE DATA LIVES", "The database, in plain terms")
    story.append(p(
        "Still SQLite — one file on disk, no separate server process. What's changed is scale: "
        "this started as 4 tables (<font face=\"Courier\">users</font>, <font face=\"Courier\">"
        "complaints</font>, <font face=\"Courier\">complaint_rejections</font>, <font face=\"Courier\">"
        "complaint_translations</font>) and has grown to <b>22</b>, as the app grew a real location "
        "hierarchy, a fuller complaint lifecycle, notifications, AI observability, and auth."
    ))
    story.append(table([
        ["TABLE GROUP", "WHAT IT ADDED"],
        ["Original core (4 tables)", "Users (single-table, role-discriminated), complaints (original + translated text kept side by side, permanently), rejections, a translation cache"],
        ["Location hierarchy (7 tables)", "A real, ID-based state → district → sub-district → ulb → zone → ward → locality chain, so a ward can be referenced by a stable ID, not just free text"],
        ["Fuller complaint lifecycle", "A status-change audit trail, worker progress notes + their cached translations, multi-photo evidence"],
        ["Notifications & AI observability", "In-app alerts, an AI-answer cache, per-request cost/latency logs and alert state"],
        ["Auth", "Refresh-token rotation records, email OTP codes for verification and password reset"],
    ], [4.6 * cm, 12.0 * cm]))
    story.append(callout(
        "There is still no migration framework (no Alembic) — a known, honest gap. Adding a "
        "column to an existing table with real data needs a small, one-off manual script; only a "
        "brand-new table gets auto-created on startup.", color=RUST,
    ))
    story.append(PageBreak())

    # --- 07 AUTH --------------------------------------------------------------
    story += section("07", "PROVING WHO YOU ARE", "Authentication & roles, step by step")
    story.append(p(
        "The token itself is still a hand-rolled JWT (HS256, against Python's standard library "
        "only — no third-party JWT package, since this app only ever verifies tokens it issued "
        "itself). What's changed is where it lives and what protects it."
    ))
    story.append(table([
        ["THEN", "NOW"],
        ["Token kept in localStorage, sent as a Bearer header", "Token kept in an httpOnly cookie — unreadable to any script on the page, including one injected via XSS"],
        ["No CSRF concern (no cookie to forge)", "A cookie needs its own defense: a double-submit CSRF cookie a forged cross-site request can't replicate"],
        ["One token, no refresh", "Access + refresh tokens; refresh rotates on every use, and a reused (already-rotated) refresh token revokes every active session for that user"],
        ["No email/phone verification", "Mandatory email OTP verification at signup; a real password-reset flow, also via emailed OTP"],
        ["No rate limiting", "4 independent sliding-window limiters (login, signup, AI requests, a general baseline) plus a separate OTP limiter"],
    ], [7.9 * cm, 8.7 * cm]))
    story.append(p(
        "Authorization is still enforced two ways: route-level (<font face=\"Courier\">require_"
        "role(...)</font> blocks an entire endpoint before its logic runs) and row-level (an "
        "explicit ownership check confirms a specific record belongs to the caller, not just that "
        "their role is generally allowed)."
    ))
    story.append(PageBreak())

    # --- 08 WATCHING IT WORK ---------------------------------------------------
    story += section("08", "WATCHING IT WORK", "Three real journeys through the app")
    story.append(h2("A. A citizen files a complaint through Ask Sarthi"))
    story.append(p(
        "Opens Ask Sarthi, types or speaks a problem in Marathi. Ask Sarthi classifies the intent "
        "as a complaint, resolves the location (asking a clarifying question if it can't), builds "
        "a draft, and explicitly asks the citizen to confirm before anything is saved. Once "
        "confirmed, the complaint is created, translated to English for storage, and immediately "
        "handed to <font face=\"Courier\">assign_next_worker()</font> for that ward."
    ))
    story.append(h2("B. A worker rejects, and it reassigns itself"))
    story.append(p(
        "A worker opens their ward queue, sees a new complaint with its short AI summary above "
        "the full text, and rejects it (every ward has at least 2 workers for exactly this "
        "reason). The assignment service automatically routes it to the next eligible worker in "
        "that ward — no admin intervention needed, and the same worker can never reject the "
        "same complaint twice."
    ))
    story.append(h2("C. An admin reviews AI cost and a knowledge gap"))
    story.append(p(
        "Logs into the Admin AI Monitoring page, sees real Rs. cost by model (computed from "
        "Sarvam's own published pricing, not estimated), and follows a “High AI latency” "
        "alert straight to the specific slow requests behind it. Separately, reviews a citizen "
        "question the knowledge base couldn't answer, flagged automatically for review."
    ))
    story.append(PageBreak())

    # --- 09 KNOWN LIMITS --------------------------------------------------------
    story += section("09", "KNOWN LIMITS, HONESTLY", "What's real, and what's still open")
    story.append(stat_row([
        ("22", "database tables,\nstill SQLite"),
        ("1,057", "RAG chunks indexed\n(609 VERIFIED)"),
        ("≤28s", "voice chunk length —\nchunked client-side"),
        ("4", "independent rate\nlimiters"),
    ]))
    story.append(Spacer(1, 10))
    story.append(p("Real, open gaps — named on purpose rather than left implicit:"))
    story.append(table([
        ["GAP", "STATUS"],
        ["PostgreSQL", "Still SQLite — the top deferred production gap; mostly a DATABASE_URL change thanks to the ORM"],
        ["Category detection via LLM", "Evaluated live, declined on measured evidence (4/5 misclassified, including a confidently-wrong out-of-scope call) — stays a deterministic keyword classifier"],
        ["Priority detection", "Not evaluated yet"],
        ["Tool calling (LLM-driven)", "Routing is deterministic intent classification by design, not an LLM deciding at runtime"],
        ["Redis, async queues", "Not used anywhere in this codebase yet"],
        ["Prometheus / Grafana / Alertmanager", "Not used; a lighter in-app substitute exists instead (two admin alerts computed from the last 20 real requests)"],
        ["Centralized logging (beyond errors)", "Sentry centralizes errors specifically, not all application logs"],
        ["Phone number verification", "Only email is verified via OTP; a citizen's phone number itself is still never confirmed as theirs"],
    ], [5.2 * cm, 11.4 * cm]))
    story.append(PageBreak())

    # --- 10 RUNNING LOCALLY --------------------------------------------------------
    story += section("10", "TRY IT YOURSELF", "Running the whole thing locally")
    story.append(h2("1. Backend"))
    steps_backend = [
        "pip install -r requirements.txt",
        "cp .env.example .env  # fill in SARVAM_API_KEY, JWT_SECRET_KEY (any long random string)",
        "python scripts/build_rag_knowledge_base.py   # generates chunks.json from knowledge_records/",
        "python scripts/build_rag_embeddings.py       # populates the real ChromaDB index",
        "uvicorn backend.main:app --reload            # API docs at localhost:8000/docs",
        "python scripts/seed_admin.py --phone 9999999999 --password \"change-me\" --name \"Your Name\"",
    ]
    for s in steps_backend:
        story.append(Paragraph(s, styles["code"]))
        story.append(Spacer(1, 3))
    story.append(h2("2. Frontend"))
    for s in ["cd frontend-react && npm install", "npm run dev   # opens at localhost:5173"]:
        story.append(Paragraph(s, styles["code"]))
        story.append(Spacer(1, 3))
    story.append(h2("3. Try the three roles"))
    story.append(p(
        "Sign up as a citizen through the app itself (verify the email OTP). Log in as the admin "
        "you just seeded, add a worker for a ward, then log in as that worker in a different "
        "browser (or incognito window) to see both sides at once. Open Ask Sarthi as the citizen "
        "and try filing a complaint, asking a civic question, and checking a complaint's status."
    ))
    story.append(PageBreak())

    # --- 11 TESTING --------------------------------------------------------
    story += section("11", "CONFIDENCE", "How this is tested")
    story.append(p(
        "Two separate test suites, for two separate layers, both run automatically on every push "
        "via GitHub Actions before anything can reach production:"
    ))
    story.append(table([
        ["SUITE", "WHAT IT COVERS"],
        ["Backend (pytest tests/ -v)", "The full suite — auth, complaints, assignment, the whole Ask Sarthi/RAG pipeline, guardrails, tracing. All external AI calls are mocked except a small, deliberate set that hit the real Sarvam API to prove real behavior (e.g. language auto-detection)."],
        ["Frontend (Playwright, frontend-react/e2e/)", "Drives a real browser through language selection, signup/login, the full complaint lifecycle (submit → assign → reject → reassign → accept → resolve → feedback), and voice-recording UI."],
    ], [5.4 * cm, 11.2 * cm]))
    story.append(p(
        "CI (<font face=\"Courier\">ci.yml</font>) gates CD (<font face=\"Courier\">cd.yml</font>): "
        "a merge to <font face=\"Courier\">main</font> only reaches the live GCP server after both "
        "the backend test suite and a real frontend production build pass — see <font "
        "face=\"Courier\">docs/CI_CD_GITHUB_ACTIONS.md</font> for the full pipeline."
    ))
    story.append(Spacer(1, 16))
    story.append(rule())
    story.append(Paragraph(
        "JanSarthi AI &middot; Project Overview &middot; Public for viewing/evaluation only, not "
        "open source (see LICENSE) &middot; Generated for onboarding, not a substitute for reading "
        "the code",
        styles["small"],
    ))

    return story


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT_PATH),
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="JanSarthi AI -- Project Overview",
        author="sumya24",
    )
    doc.build(build())
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
