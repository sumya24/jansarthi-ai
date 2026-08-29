"""Tests for the read-only cascading location lookup endpoints (backend/routes/locations.py) --
backs the optional State/City/Ward/Area picker on Signup. Reuses `_seed_full_hierarchy` from
test_location_system.py (same real State->District->ULB->Ward->Locality chain already used
there) rather than duplicating seed logic.
"""

from backend.models import District, State, ULB, Ward
from backend.routes.locations import _COVERED_STATE_CODES
from tests.test_location_system import _insert_worker, _seed_full_hierarchy


def test_list_states_only_returns_states_we_have_data_for(client, db_session):
    """GET /locations/states is restricted to backend.routes.locations._COVERED_STATE_CODES,
    AND (since the worker-backed-scoping change) to states that actually have a real worker
    somewhere under them -- a seeded state with a covered code but nothing worker-backed under it
    (like the shared fixture's "Test State"/TS, whose code isn't even covered to begin with) must
    not show up, even though it's a perfectly real row with real districts under it."""
    db = db_session()
    chain = _seed_full_hierarchy(db)
    assert chain["state"].code not in _COVERED_STATE_CODES
    # A second, separately-seeded chain under a COVERED code (MH) -- needs its own real
    # worker-backed ward to actually show up, not just a covered code with nothing under it.
    covered_state = State(name="Covered Test State", code="MH", country_code="IN", is_union_territory=False)
    db.add(covered_state)
    db.flush()
    covered_district = District(state_id=covered_state.id, name="Covered Test District")
    db.add(covered_district)
    db.flush()
    covered_ulb = ULB(district_id=covered_district.id, name="Covered Test ULB", type="Municipal Corporation")
    db.add(covered_ulb)
    db.flush()
    covered_ward = Ward(ulb_id=covered_ulb.id, name="Covered Test Ward", ward_number="1")
    db.add(covered_ward)
    db.commit()
    covered_ward_id = covered_ward.id
    db.close()
    _insert_worker(db_session, "9000000001", "Test Worker", "Covered Test Ward, Covered Test City", ward_id=covered_ward_id)

    response = client.get("/locations/states")
    assert response.status_code == 200
    names = [s["name"] for s in response.json()]
    assert "Covered Test State" in names
    assert "Test State" not in names


def test_list_states_is_unauthenticated(client, db_session):
    """No Authorization header at all -- Signup needs this before a citizen has a token."""
    response = client.get("/locations/states")
    assert response.status_code == 200


def test_list_cities_for_state_returns_real_districts(client, db_session):
    db = db_session()
    chain = _seed_full_hierarchy(db)
    state_id = chain["state"].id
    ward_id = chain["ward"].id
    ward_name = chain["ward"].name
    db.close()
    # Worker-backed scoping applies here too -- a district with no real worker anywhere under it
    # is now correctly treated the same as an empty one.
    _insert_worker(db_session, "9000000002", "Test Worker", ward_name, ward_id=ward_id)

    response = client.get(f"/locations/states/{state_id}/cities")
    assert response.status_code == 200
    names = [c["name"] for c in response.json()]
    assert names == ["Test District"]


def test_list_cities_for_state_with_no_districts_returns_empty_list_not_error(client, db_session):
    """A state with zero seeded cities (30 of 36 states today) must return an honest empty
    list -- never an error, and never a fabricated city."""
    db = db_session()
    state = State(name="Empty State", code="ES", country_code="IN", is_union_territory=False)
    db.add(state)
    db.commit()
    state_id = state.id
    db.close()

    response = client.get(f"/locations/states/{state_id}/cities")
    assert response.status_code == 200
    assert response.json() == []


def test_list_cities_for_nonexistent_state_is_404(client, db_session):
    response = client.get("/locations/states/999999/cities")
    assert response.status_code == 404


def test_list_wards_for_city_returns_real_wards(client, db_session):
    db = db_session()
    chain = _seed_full_hierarchy(db)
    district_id = chain["district"].id
    ward_id = chain["ward"].id
    ward_name = chain["ward"].name
    db.close()
    # Worker-backed scoping applies here too -- a ward with no real worker on it is now
    # correctly treated the same as one that was never seeded at all.
    _insert_worker(db_session, "9000000003", "Test Worker", ward_name, ward_id=ward_id)

    response = client.get(f"/locations/cities/{district_id}/wards")
    assert response.status_code == 200
    names = [w["name"] for w in response.json()]
    assert names == ["Ward 99 — Testville, Test City"]


def test_list_wards_for_city_with_no_ulb_returns_empty_list(client, db_session):
    db = db_session()
    chain = _seed_full_hierarchy(db)
    state_id = chain["state"].id
    district = District(state_id=state_id, name="No-ULB District")
    db.add(district)
    db.commit()
    district_id = district.id
    db.close()

    response = client.get(f"/locations/cities/{district_id}/wards")
    assert response.status_code == 200
    assert response.json() == []


def test_list_wards_for_nonexistent_city_is_404(client, db_session):
    response = client.get("/locations/cities/999999/wards")
    assert response.status_code == 404


def test_list_localities_for_ward_returns_real_localities(client, db_session):
    db = db_session()
    chain = _seed_full_hierarchy(db)
    ward_id = chain["ward"].id
    db.close()

    response = client.get(f"/locations/wards/{ward_id}/localities")
    assert response.status_code == 200
    names = [l["name"] for l in response.json()]
    assert names == ["Testville"]


def test_list_localities_for_nonexistent_ward_is_404(client, db_session):
    response = client.get("/locations/wards/999999/localities")
    assert response.status_code == 404
