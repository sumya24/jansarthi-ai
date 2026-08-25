"""One-time migration: adds Complaint.service_category and backfills existing complaints by
classifying their stored English text -- tries the real AI classifier first (works whenever
Sarvam credits are available), falling back to the same keyword list intent_classifier.py's own
Ask Sarthi routing already uses, so a complaint is only ever left uncategorized if BOTH layers
fail (genuinely ambiguous text, or no keyword match at all).

LIVE-REPORTED GAP this closes: the civic-service category has always been classified at filing
time (Ask Sarthi's complaint_flow_node, and the Report an Issue wizard's own 3-layer classifier),
but until now that result was used only in the moment and never persisted -- every complaint
filed before this migration has service_category = NULL until this script runs.

Safe to run once; running it again is a no-op (guarded on the column already existing, matching
scripts/migrate_assignment_tracking.py's own convention).

Usage: python scripts/migrate_complaint_category.py
(backend must be stopped first, so the db file isn't locked)
"""

import sqlite3

from backend.schemas.rag_knowledge import ServiceCategory
from backend.services.complaint_category_service import ComplaintCategoryService
from backend.services.intent_classifier import _CATEGORY_KEYWORDS


def _keyword_guess(text: str) -> ServiceCategory | None:
    """Free, instant fallback for when the AI layer is unavailable (e.g. Sarvam credits
    exhausted) -- reuses the exact English keyword list Ask Sarthi's own routing already
    maintains, rather than inventing a second list that could drift out of sync with it."""
    lowered = (text or "").lower()
    for cat, lang_keywords in _CATEGORY_KEYWORDS.items():
        for kw in lang_keywords.get("en", []):
            if kw in lowered:
                return cat
    return None


def main() -> None:
    con = sqlite3.connect("janmitra.db")
    cur = con.cursor()

    cur.execute("PRAGMA table_info(complaints)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "service_category" in existing_cols:
        print("Already migrated (service_category exists) — nothing to do.")
        return

    cur.execute("ALTER TABLE complaints ADD COLUMN service_category VARCHAR(32)")
    con.commit()
    print("Column added.")

    cur.execute("SELECT id, translated_text FROM complaints WHERE service_category IS NULL")
    rows = cur.fetchall()

    ai_service = ComplaintCategoryService()
    ai_count = keyword_count = unclassified_count = 0
    for complaint_id, translated_text in rows:
        category = ai_service.classify(translated_text or "")
        if category is not None:
            ai_count += 1
        else:
            category = _keyword_guess(translated_text)
            if category is not None:
                keyword_count += 1
            else:
                unclassified_count += 1

        if category is not None:
            cur.execute(
                "UPDATE complaints SET service_category = ? WHERE id = ?",
                (category.value, complaint_id),
            )

    con.commit()
    print(f"Total complaints processed: {len(rows)}")
    print(f"Classified via real AI model: {ai_count}")
    print(f"Classified via keyword fallback: {keyword_count}")
    print(f"Left uncategorized (both layers unsure): {unclassified_count}")


if __name__ == "__main__":
    main()
