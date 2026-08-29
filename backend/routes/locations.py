"""Read-only cascading lookups over the structured location hierarchy (State -> District (shown
to citizens as "city") -> ULB -> Ward -> Locality, see models.py). Backs the optional
State/City/Ward/Area picker on the signup form (routes/auth.py's SignupRequest) -- entirely
additive: nothing here changes what the existing free-text `ward` field does, and no other part
of the app reads `User.home_*_id` yet (see that field's own docstring in models.py).

Deliberately unauthenticated, same reasoning as GET /complaints/wards: this needs to work before
a citizen has a token, from the signup page itself.

Deliberately never fabricates data: only 6 of India's 36 states/UTs have real seeded
district/ULB/ward/locality data today (see scripts/seed_multi_ward_data*.py) -- every endpoint
here just returns whatever real rows exist, including an empty list for a state/city/ward that
has nothing under it yet. The frontend falls back to free-text entry in that case, exactly like
the existing ward field already does when GET /complaints/wards comes back empty.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import District, Locality, State, ULB, User, Ward
from backend.services.location_resolver import LocationResolver

router = APIRouter(prefix="/locations", tags=["locations"])
_location_resolver = LocationResolver()

# States we actually have data for, on either side -- the 6 seeded here (District/ULB/Ward rows,
# via scripts/seed_multi_ward_data*.py) plus the wider set the RAG knowledge base already covers
# (data/rag_knowledge_base/knowledge_records/verified/ on the
# research/rag-knowledge-base-data-foundation branch, not yet merged into this one). Listing a
# state here just means GET /states offers it; a state with no seeded District rows still falls
# back to free-text city/ward/area exactly as before. Add a code below once that state has real
# data on either side -- never invent a state's presence here ahead of the data.
_COVERED_STATE_CODES = {
    "AP", "AS", "BR", "DL", "GJ", "HR", "KA", "KL", "MP",
    "MH", "OD", "PB", "RJ", "TN", "TG", "UP", "WB",
}


# LIVE-REPORTED: every step below used to return every real row this app's own imported
# district/ULB/ward dataset has -- honest, never-fabricated data, but a district containing
# several municipalities (e.g. Bengaluru Urban, which has BBMP AND several smaller town
# councils) returned 400+ wards at once, most of them belonging to a municipality this app has
# no worker in at all, making the one relevant ward (e.g. BBMP's own "Ward 3") genuinely hard to
# find in the list. Every step here now only offers a State/City/Ward that leads to at least one
# ward with a real worker assigned -- the exact same "only what actually routes somewhere"
# principle GET /complaints/wards already applies, just extended up through the whole cascade
# instead of only the flat ward-text list. A district/state with real underlying data but zero
# workers anywhere under it now correctly returns empty (or is omitted a level up), same as a
# district with no seeded data at all -- the frontend's existing free-text fallback covers both
# identically, so this is never a dead end, only a smaller, actually-useful list.
def _worker_backed_ward_ids(db: Session) -> set[int]:
    return {
        row[0]
        for row in db.query(User.ward_id).filter(User.role == "worker", User.ward_id.isnot(None)).distinct().all()
    }


class LocationOption(BaseModel):
    """One selectable node at any level of the hierarchy -- deliberately the same shape at every
    level (id + display name) so the frontend can use one generic picker component for all four
    steps."""

    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


@router.get("/states", response_model=list[LocationOption])
def list_states(db: Session = Depends(get_db)) -> list[State]:
    worker_ward_ids = _worker_backed_ward_ids(db)
    if not worker_ward_ids:
        return []
    ulb_ids = {row[0] for row in db.query(Ward.ulb_id).filter(Ward.id.in_(worker_ward_ids)).all()}
    district_ids = {row[0] for row in db.query(ULB.district_id).filter(ULB.id.in_(ulb_ids)).all()}
    state_ids = {row[0] for row in db.query(District.state_id).filter(District.id.in_(district_ids)).all()}
    return (
        db.query(State)
        .filter(State.code.in_(_COVERED_STATE_CODES), State.id.in_(state_ids))
        .order_by(State.name)
        .all()
    )


@router.get("/states/{state_id}/cities", response_model=list[LocationOption])
def list_cities(state_id: int, db: Session = Depends(get_db)) -> list[District]:
    """"City" here is a District row -- the level whose `name` is the citizen-recognizable city
    name (e.g. "Pune"), as opposed to ULB.name, the formal civic-body name (e.g. "Pune Municipal
    Corporation") that a citizen wouldn't naturally search for."""
    if db.query(State).filter(State.id == state_id).first() is None:
        raise HTTPException(status_code=404, detail="State not found.")
    worker_ward_ids = _worker_backed_ward_ids(db)
    if not worker_ward_ids:
        return []
    ulb_ids = {row[0] for row in db.query(Ward.ulb_id).filter(Ward.id.in_(worker_ward_ids)).all()}
    district_ids = {row[0] for row in db.query(ULB.district_id).filter(ULB.id.in_(ulb_ids)).all()}
    return (
        db.query(District)
        .filter(District.state_id == state_id, District.id.in_(district_ids))
        .order_by(District.name)
        .all()
    )


@router.get("/cities/{district_id}/wards", response_model=list[LocationOption])
def list_wards(district_id: int, db: Session = Depends(get_db)) -> list[Ward]:
    if db.query(District).filter(District.id == district_id).first() is None:
        raise HTTPException(status_code=404, detail="City not found.")
    ulb_ids = [row[0] for row in db.query(ULB.id).filter(ULB.district_id == district_id).all()]
    if not ulb_ids:
        return []
    worker_ward_ids = _worker_backed_ward_ids(db)
    return (
        db.query(Ward)
        .filter(Ward.ulb_id.in_(ulb_ids), Ward.id.in_(worker_ward_ids))
        .order_by(Ward.name)
        .all()
    )


@router.get("/wards/{ward_id}/localities", response_model=list[LocationOption])
def list_localities(ward_id: int, db: Session = Depends(get_db)) -> list[Locality]:
    if db.query(Ward).filter(Ward.id == ward_id).first() is None:
        raise HTTPException(status_code=404, detail="Ward not found.")
    return db.query(Locality).filter(Locality.ward_id == ward_id).order_by(Locality.name).all()


@router.get("/wards/resolve", response_model=LocationOption | None)
def resolve_ward(text: str, db: Session = Depends(get_db)) -> Ward | None:
    """Resolves a ward's own free-text label (the app's "Ward <n> — <locality>, <city>"
    convention -- exactly what GET /complaints/wards' plain-string entries already look like) back
    to its real ward row, so a caller that only has that display string (Report an Issue's ward
    dropdown, ReportIssue.tsx) can still reach GET /wards/{id}/localities above it -- the one
    lookup this text-only list can't do on its own. Reuses LocationResolver.resolve_ward_by_text,
    the same deterministic parser scripts/migrate_existing_locations.py and live complaint
    creation already use, so this never guesses or fuzzy-matches; a ward string that doesn't parse
    or doesn't match any seeded ward returns `null` (not a 404) -- "no real ward here" is an
    expected, ordinary answer for this lookup (e.g. any ward a citizen typed as free text because
    their own city has none seeded), not an error."""
    return _location_resolver.resolve_ward_by_text(db, text)
