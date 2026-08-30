"""Repository layer for Complaint/worker persistence -- the LangGraph orchestration nodes (see
backend/services/orchestration/nodes.py) call these instead of running SQLAlchemy queries inline,
keeping graph nodes focused on orchestration/business-decision logic while persistence details
live in one reviewable place. Extends this codebase's existing layering (routes -> services ->
models) by one level: LangGraph nodes -> repository -> models.

    LangGraph  ->  Business Service (ComplaintAgent, assign_next_worker)  ->  Repository  ->  DB

NEVER:

    LangGraph  ->  LLM  ->  generated SQL  ->  DB

Every query here is a fixed, reviewable statement -- no LLM ever constructs or influences a
database operation in this codebase (see docs/ask_sarthi_service_flow.md's database-architecture
section for why this boundary matters and how it's enforced).

Deliberately thin: this is NOT a generic ORM abstraction layer with speculative CRUD methods --
every function here exists because something in nodes.py actually calls it. `ComplaintAgent.
create_complaint()` (backend/services/complaint_agent.py) and `assign_next_worker()`
(backend/services/assignment_service.py) remain the business-logic services that already existed
before this phase and are unchanged; this module only covers the plain lookups/updates that were
previously written as raw `db.query(...)` calls directly inside graph nodes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.models import Complaint, User


def get_complaint_by_id(db: Session, complaint_id: int) -> Complaint | None:
    """Used by the status flow (complaint-status lookup) and the complaint flow (re-reading a
    complaint after assignment to build the response)."""
    return db.query(Complaint).filter(Complaint.id == complaint_id).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """Used by the complaint flow to look up an assigned worker's name for the response."""
    return db.query(User).filter(User.id == user_id).first()


def save_complaint_location(
    db: Session,
    complaint: Complaint,
    *,
    ward: str | None = None,
    location_chain: dict[str, int | None] | None = None,
    formatted_address: str | None = None,
) -> None:
    """Applies the resolved ward text and/or structured location-hierarchy IDs to `complaint` and
    commits -- the same field-by-field update `routes/complaints.py`'s own complaint-creation
    endpoint already performs (see that route's docstring), centralized here so the complaint
    flow graph node doesn't run `setattr`/`commit`/`refresh` inline.

    Args:
        db: Active session.
        complaint: The complaint to update (already persisted -- has an id).
        ward: Free-text ward name, or None to leave `complaint.ward` unchanged.
        location_chain: state_id/district_id/.../locality_id -> value, matching
            `LocationResolver.location_chain_for_ward()`'s/`normalize_location()`'s return shape,
            or None to leave the structured hierarchy unchanged.
        formatted_address: A resolver-provided formatted address, only applied if the complaint
            doesn't already have one (never overwrites a citizen-typed address).
    """
    if ward is not None:
        complaint.ward = ward
    if location_chain is not None:
        for field, value in location_chain.items():
            setattr(complaint, field, value)
    if formatted_address is not None and not complaint.address:
        complaint.address = formatted_address
    db.commit()
    db.refresh(complaint)
