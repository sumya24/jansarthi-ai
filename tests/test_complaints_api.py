"""Integration tests for the /complaints API endpoints.

All endpoints require authentication and are scoped by role (see backend/deps.py): citizens see
only their own complaints, workers see only complaints currently assigned to them specifically
(not just anyone in their ward), and admins see everything.

Lifecycle covered here: pending -> assigned -> accepted -> resolved -> feedback, plus reject ->
reassign to the next worker in the same ward (or back to pending if none are left).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import backend.routes.complaints as complaints_module
from backend.models import Complaint, ComplaintRejection, ComplaintStatusHistory, User
from backend.services.auth_service import hash_password
from backend.services.sarvam_client import AIServiceError


def _fake_agent_create_complaint(db, citizen_id, language_code, text, audio_chunks, photo_path, category=None):
    """Stand in for ComplaintAgent.create_complaint without calling any external API."""
    complaint = Complaint(
        citizen_id=citizen_id,
        original_text=text or "(voice complaint)",
        original_language=language_code,
        translated_text=f"[en] {text or 'voice complaint'}",
        summary="A short summary.",
        photo_path=photo_path,
        status="pending",
        service_category=category.value if category else None,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return complaint


def _make_worker_row(db_session, phone: str, ward: str, full_name: str = "Worker") -> int:
    """Insert a worker directly into the db (bypassing the /admin/workers API + its bootstrap-
    admin fixture, which can't be called twice with the same hardcoded admin phone) — needed for
    tests that want more than one worker in the same ward."""
    db = db_session()
    worker = User(
        full_name=full_name, phone=phone, password_hash=hash_password("secret123!"),
        role="worker", preferred_language="en", ward=ward,
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)
    worker_id = worker.id
    db.close()
    return worker_id


def test_create_complaint_with_text_succeeds(client, monkeypatch, make_citizen):
    """POST /complaints with typed text should store and return a new complaint."""
    monkeypatch.setattr(
        complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint)
    )
    token, user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={"language": "mr", "text": "कचरा उचलला नाही"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["citizen_id"] == str(user["id"])
    assert body["original_language"] == "mr"
    assert body["status"] == "pending"  # no ward given, so no worker to assign to
    assert body["translated_text"] == "[en] कचरा उचलला नाही"
    assert body["assigned_worker_name"] is None


def test_create_complaint_assigns_to_worker_in_matching_ward(client, monkeypatch, make_citizen, make_worker):
    """A complaint filed into a ward with an eligible worker is immediately assigned, not pending."""
    monkeypatch.setattr(
        complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint)
    )
    _worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    citizen_token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {citizen_token}"},
        data={"language": "en", "text": "Garbage issue", "ward": "Ward 14"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assigned"
    assert body["assigned_worker_name"] == worker["full_name"]
    assert body["assigned_worker_phone"] is None  # not revealed until accepted


def test_create_complaint_stores_category_when_given(client, monkeypatch, make_citizen):
    """LIVE-REPORTED GAP: the category classified by the Report an Issue wizard's own 3-layer
    classification (real model -> keyword -> manual picker) used to be thrown away -- it must now
    actually persist onto the complaint row and come back in the response."""
    monkeypatch.setattr(
        complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint)
    )
    token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Streetlight not working", "category": "STREETLIGHTS"},
    )

    assert response.status_code == 200
    assert response.json()["service_category"] == "STREETLIGHTS"


def test_create_complaint_rejects_unknown_category(client, monkeypatch, make_citizen):
    monkeypatch.setattr(
        complaints_module, "_agent", Mock(create_complaint=_fake_agent_create_complaint)
    )
    token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={"language": "en", "text": "Something", "category": "NOT_A_REAL_CATEGORY"},
    )

    assert response.status_code == 400


def test_create_complaint_requires_authentication(client):
    response = client.post("/complaints", data={"language": "en", "text": "hello"})
    assert response.status_code == 401


def test_worker_cannot_create_complaint(client, make_worker):
    """Only citizens may submit complaints — not workers, not admins."""
    token, _user = make_worker(phone="9000000002")
    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"}, data={"language": "en", "text": "hello"}
    )
    assert response.status_code == 403


def test_create_complaint_without_text_or_audio_returns_400(client, make_citizen):
    token, _user = make_citizen(phone="9000000001")
    response = client.post(
        "/complaints", headers={"Authorization": f"Bearer {token}"}, data={"language": "en"}
    )
    assert response.status_code == 400


def test_create_complaint_unsupported_language_returns_400(client, make_citizen):
    token, _user = make_citizen(phone="9000000001")
    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={"language": "fr", "text": "Bonjour"},
    )
    assert response.status_code == 400


def test_create_complaint_ai_failure_returns_502(client, monkeypatch, make_citizen):
    """If the AI pipeline fails, the API should return a clear 502, not crash."""

    def _raise(*args, **kwargs):
        raise AIServiceError("Sarvam AI is not configured.")

    monkeypatch.setattr(complaints_module, "_agent", Mock(create_complaint=_raise))
    token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints",
        headers={"Authorization": f"Bearer {token}"},
        data={"language": "mr", "text": "कचरा उचलला नाही"},
    )
    assert response.status_code == 502


def test_citizen_only_sees_own_complaints(client, make_citizen, db_session):
    token_a, user_a = make_citizen(phone="9000000001")
    token_b, user_b = make_citizen(phone="9000000002")

    db = db_session()
    db.add(Complaint(
        citizen_id=str(user_a["id"]), original_text="a", original_language="en",
        translated_text="Complaint from citizen A", summary="a", status="pending",
    ))
    db.add(Complaint(
        citizen_id=str(user_b["id"]), original_text="b", original_language="en",
        translated_text="Complaint from citizen B", summary="b", status="pending",
    ))
    db.commit()
    db.close()

    response = client.get("/complaints", headers={"Authorization": f"Bearer {token_a}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["translated_text"] == "Complaint from citizen A"


def test_worker_only_sees_complaints_assigned_to_them(client, make_worker, db_session):
    """A worker sees complaints assigned to *them*, not just anyone sharing their ward."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 14", full_name="Other Worker")

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="Assigned to me", summary="a", ward="Ward 14",
        status="assigned", assigned_worker_id=worker["id"],
    ))
    db.add(Complaint(
        citizen_id="1", original_text="b", original_language="en",
        translated_text="Assigned to the other worker", summary="b", ward="Ward 14",
        status="assigned", assigned_worker_id=other_worker_id,
    ))
    db.commit()
    db.close()

    response = client.get("/complaints", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["translated_text"] == "Assigned to me"


def test_admin_sees_every_complaint(client, make_admin, db_session):
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="Ward 14 complaint", summary="a", ward="Ward 14", status="pending",
    ))
    db.add(Complaint(
        citizen_id="2", original_text="b", original_language="en",
        translated_text="Ward 9 complaint", summary="b", ward="Ward 9", status="pending",
    ))
    db.commit()
    db.close()

    response = client.get("/complaints", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_admin_can_filter_by_worker_id(client, make_admin, make_worker, db_session):
    """The Admin Worker Detail page's data source -- see routes/complaints.py's list_complaints()
    docstring for why this is admin-only-effective, not a separate permission check."""
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]
    _, worker = make_worker(phone="9000000002", ward="Ward 14")

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="a", original_language="en", translated_text="a",
        summary="a", ward="Ward 14", status="assigned", assigned_worker_id=worker["id"],
    ))
    db.add(Complaint(
        citizen_id="2", original_text="b", original_language="en", translated_text="b",
        summary="b", ward="Ward 9", status="pending",  # unassigned -- must not show up
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints", params={"worker_id": worker["id"]}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["assigned_worker_name"] == worker["full_name"]


def test_worker_id_filter_is_ignored_for_non_admin_roles(client, make_worker, db_session):
    """A worker passing worker_id=<someone else> must still only ever see their OWN queue --
    the param is a no-op outside the admin role, never a way to see another worker's complaints."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")

    db = db_session()
    own = Complaint(
        citizen_id="1", original_text="a", original_language="en", translated_text="a",
        summary="a", ward="Ward 14", status="assigned", assigned_worker_id=worker["id"],
    )
    someone_elses = Complaint(
        citizen_id="2", original_text="b", original_language="en", translated_text="b",
        summary="b", ward="Ward 9", status="assigned", assigned_worker_id=999999,
    )
    db.add_all([own, someone_elses])
    db.commit()
    other_id = someone_elses.assigned_worker_id
    db.close()

    response = client.get(
        "/complaints", params={"worker_id": other_id}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["assigned_worker_name"] == worker["full_name"]


def test_list_complaints_page_and_page_size_slice_server_side(client, make_admin, db_session):
    """LIVE-REPORTED GAP: GET /complaints always returned every matching row -- opting into
    page/page_size must now return a real, bounded slice plus an accurate X-Total-Count header,
    while a caller that passes neither still gets everything (see other tests in this file that
    don't pass these params -- all pre-existing, all still pass unmodified)."""
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    for i in range(5):
        db.add(Complaint(
            citizen_id="1", original_text=f"c{i}", original_language="en",
            translated_text=f"Complaint {i}", summary=f"s{i}", ward="Ward 14", status="pending",
        ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints", params={"page": 1, "page_size": 2},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.headers["X-Total-Count"] == "5"

    page2 = client.get(
        "/complaints", params={"page": 2, "page_size": 2},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert len(page2.json()) == 2
    # Different rows than page 1 -- a real slice, not the same page repeated.
    assert {c["id"] for c in page2.json()}.isdisjoint({c["id"] for c in response.json()})


def test_list_complaints_status_filter(client, make_admin, db_session):
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="a", summary="a", ward="Ward 14", status="pending",
    ))
    db.add(Complaint(
        citizen_id="1", original_text="b", original_language="en",
        translated_text="b", summary="b", ward="Ward 14", status="resolved",
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints", params={"status": "resolved"}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "resolved"


def test_list_complaints_rejects_unknown_status_filter(client, make_admin):
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    response = client.get(
        "/complaints", params={"status": "not_a_real_status"}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400


def test_list_complaints_search_matches_id_ward_summary_and_worker_name(client, make_admin, make_worker, db_session):
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]
    _, worker = make_worker(phone="9000000002", ward="Ward 14", full_name="Ramesh Kumar")

    db = db_session()
    target = Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="A pothole near the market", summary="Pothole report",
        ward="Kothrud", status="assigned", assigned_worker_id=worker["id"],
    )
    other = Complaint(
        citizen_id="1", original_text="b", original_language="en",
        translated_text="Garbage not collected", summary="Garbage",
        ward="Indiranagar", status="pending",
    )
    db.add_all([target, other])
    db.commit()
    target_id = target.id
    db.close()

    for query in (str(target_id), "Kothrud", "Pothole", "Ramesh"):
        response = client.get(
            "/complaints", params={"search": query}, headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert response.status_code == 200, query
        body = response.json()
        assert len(body) == 1, f"query={query!r} matched {len(body)} rows"
        assert body[0]["id"] == target_id


def test_list_complaints_category_filter(client, make_admin, db_session):
    """LIVE-REPORTED GAP: Complaint.service_category didn't exist at all until this fix -- every
    complaint's classified category was discarded after filing. This is the filter that backs My
    Area's per-service chips (and GET /complaints' own equivalent)."""
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="a", original_language="en", translated_text="a",
        summary="a", ward="Ward 14", status="pending", service_category="STREETLIGHTS",
    ))
    db.add(Complaint(
        citizen_id="1", original_text="b", original_language="en", translated_text="b",
        summary="b", ward="Ward 14", status="pending", service_category="ROADS_POTHOLES",
    ))
    db.add(Complaint(
        citizen_id="1", original_text="c", original_language="en", translated_text="c",
        summary="c", ward="Ward 14", status="pending", service_category=None,
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints", params={"category": "STREETLIGHTS"}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["service_category"] == "STREETLIGHTS"


def test_list_complaints_rejects_unknown_category_filter(client, make_admin):
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    response = client.get(
        "/complaints", params={"category": "NOT_A_REAL_CATEGORY"}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400


def test_area_summary_search_and_pagination(client, make_citizen, db_session):
    token, citizen = make_citizen(phone="9000000010", ward="Kothrud")

    db = db_session()
    for i in range(3):
        db.add(Complaint(
            citizen_id=str(citizen["id"]), original_text=f"c{i}", original_language="en",
            translated_text=f"Pothole issue {i}", summary=f"s{i}", ward="Kothrud", status="pending",
        ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="d", original_language="en",
        translated_text="Garbage overflow", summary="d", ward="Kothrud", status="resolved",
    ))
    db.commit()
    db.close()

    all_response = client.get("/complaints/area-summary", headers={"Authorization": f"Bearer {token}"})
    assert all_response.status_code == 200
    all_body = all_response.json()
    assert all_body["total"] == 4
    assert len(all_body["complaints"]) == 4
    # Stat counts describe the WHOLE ward, unaffected by any later search/page params.
    assert all_body["pending_count"] == 3
    assert all_body["resolved_count"] == 1

    paged = client.get(
        "/complaints/area-summary", params={"page": 1, "page_size": 2},
        headers={"Authorization": f"Bearer {token}"},
    )
    paged_body = paged.json()
    assert paged_body["total"] == 4
    assert len(paged_body["complaints"]) == 2

    searched = client.get(
        "/complaints/area-summary", params={"search": "Garbage"},
        headers={"Authorization": f"Bearer {token}"},
    )
    searched_body = searched.json()
    assert searched_body["total"] == 1
    assert len(searched_body["complaints"]) == 1
    assert "Garbage" in searched_body["complaints"][0]["display_text"]
    # The stat counts must stay ward-wide even on a search response, not re-derived from the
    # filtered list.
    assert searched_body["pending_count"] == 3
    assert searched_body["resolved_count"] == 1


def test_area_summary_status_filter_accepts_a_comma_separated_bucket(client, make_citizen, db_session):
    """LIVE-REPORTED NEED: My Area's own "Pending" stat groups THREE raw statuses (pending/
    assigned/accepted) into one citizen-legible bucket -- a status filter chip for it must be
    able to match all three at once, not just one exact value."""
    token, citizen = make_citizen(phone="9000000011", ward="Indiranagar")

    db = db_session()
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="a", original_language="en",
        translated_text="a", summary="a", ward="Indiranagar", status="pending",
    ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="b", original_language="en",
        translated_text="b", summary="b", ward="Indiranagar", status="assigned",
    ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="c", original_language="en",
        translated_text="c", summary="c", ward="Indiranagar", status="accepted",
    ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="d", original_language="en",
        translated_text="d", summary="d", ward="Indiranagar", status="resolved",
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints/area-summary", params={"status": "pending,assigned,accepted"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert {c["status"] for c in body["complaints"]} == {"pending", "assigned", "accepted"}


def test_area_summary_category_filter(client, make_citizen, db_session):
    """Backs My Area's own per-service (Waste/Water/Roads/Streetlights) filter chips.

    LIVE-REPORTED REQUEST: picking a category must reframe the stat counts to that category's
    own breakdown ("if I select Roads, show a dashboard for Roads") -- unlike status/search,
    which only ever narrow the list, never the stat counts."""
    token, citizen = make_citizen(phone="9000000012", ward="Kothrud")

    db = db_session()
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="a", original_language="en",
        translated_text="Pothole", summary="a", ward="Kothrud", status="pending",
        service_category="ROADS_POTHOLES",
    ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="b", original_language="en",
        translated_text="Streetlight out", summary="b", ward="Kothrud", status="pending",
        service_category="STREETLIGHTS",
    ))
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="c", original_language="en",
        translated_text="Another streetlight out", summary="c", ward="Kothrud", status="resolved",
        service_category="STREETLIGHTS",
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints/area-summary", params={"category": "STREETLIGHTS"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {c["service_category"] for c in body["complaints"]} == {"STREETLIGHTS"}
    # Stat counts now scope to the selected category, not the whole ward.
    assert body["pending_count"] == 1
    assert body["resolved_count"] == 1

    all_categories = client.get(
        "/complaints/area-summary", headers={"Authorization": f"Bearer {token}"}
    ).json()
    # With no category filter, stats go back to describing the whole ward.
    assert all_categories["pending_count"] == 2
    assert all_categories["resolved_count"] == 1


def test_area_summary_category_filter_paginates_correctly(client, make_citizen, db_session):
    """LIVE-REPORTED QUESTION: does picking a service (e.g. Roads & Potholes) still page
    correctly, or does pagination only work on the unfiltered "All" view? -- `category` is
    applied to `listing_query` BEFORE `_paginate` (see get_area_summary), so `total`/the page
    slice must both reflect the category-filtered count, not the whole ward's."""
    token, citizen = make_citizen(phone="9000000013", ward="Kothrud")

    db = db_session()
    for i in range(12):
        db.add(Complaint(
            citizen_id=str(citizen["id"]), original_text=f"r{i}", original_language="en",
            translated_text=f"Pothole {i}", summary=f"r{i}", ward="Kothrud", status="pending",
            service_category="ROADS_POTHOLES",
        ))
    for i in range(3):
        db.add(Complaint(
            citizen_id=str(citizen["id"]), original_text=f"g{i}", original_language="en",
            translated_text=f"Garbage {i}", summary=f"g{i}", ward="Kothrud", status="pending",
            service_category="WASTE_SANITATION",
        ))
    db.commit()
    db.close()

    page1 = client.get(
        "/complaints/area-summary", params={"category": "ROADS_POTHOLES", "page": 1, "page_size": 10},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert page1["total"] == 12  # only the 12 Roads complaints, not all 15
    assert len(page1["complaints"]) == 10
    assert {c["service_category"] for c in page1["complaints"]} == {"ROADS_POTHOLES"}

    page2 = client.get(
        "/complaints/area-summary", params={"category": "ROADS_POTHOLES", "page": 2, "page_size": 10},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert page2["total"] == 12
    assert len(page2["complaints"]) == 2  # the remaining 2, a real second page, not a repeat
    page1_ids = {c["id"] for c in page1["complaints"]}
    page2_ids = {c["id"] for c in page2["complaints"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_area_summary_category_status_and_search_combine_with_and_not_or(client, make_citizen, db_session):
    """LIVE-REPORTED QUESTION: if I pick a category (e.g. Streetlights) AND a status (e.g.
    Pending) AND type something in the search box, does the search box search WITHIN that
    filtered set, or does it ignore the other two filters? Must be AND -- a complaint only shows
    up if it matches the category, the status, AND the search text, all at once."""
    token, citizen = make_citizen(phone="9000000014", ward="Kothrud")

    db = db_session()
    # Matches all three filters -- the only one that should ever come back.
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="a", original_language="en",
        translated_text="Streetlight flickering near the market", summary="a",
        ward="Kothrud", status="pending", service_category="STREETLIGHTS",
    ))
    # Right category + status, wrong search text.
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="b", original_language="en",
        translated_text="Streetlight completely dark", summary="b",
        ward="Kothrud", status="pending", service_category="STREETLIGHTS",
    ))
    # Right category + search text, wrong status (resolved, not pending).
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="c", original_language="en",
        translated_text="Streetlight flickering, now fixed", summary="c",
        ward="Kothrud", status="resolved", service_category="STREETLIGHTS",
    ))
    # Right status + search text, wrong category (roads, not streetlights).
    db.add(Complaint(
        citizen_id=str(citizen["id"]), original_text="d", original_language="en",
        translated_text="Streetlight-adjacent pothole, flickering nearby lamp", summary="d",
        ward="Kothrud", status="pending", service_category="ROADS_POTHOLES",
    ))
    db.commit()
    db.close()

    response = client.get(
        "/complaints/area-summary",
        params={"category": "STREETLIGHTS", "status": "pending,assigned,accepted", "search": "flickering"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert "flickering near the market" in body["complaints"][0]["display_text"]


def test_list_complaints_translates_on_read(client, monkeypatch, make_admin, db_session):
    """GET /complaints?lang=hi should translate stored English text on read only."""
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="कचरा उचलला नाही", original_language="mr",
        translated_text="Garbage has not been collected.", summary="Garbage not collected.",
        status="pending",
    ))
    db.commit()
    db.close()

    fake_translation_service = Mock()
    fake_translation_service.to_language.side_effect = ["कचरा एकत्र नहीं किया गया।", "कचरा शिकायत।"]
    monkeypatch.setattr(complaints_module, "_translation_service", fake_translation_service)

    response = client.get("/complaints", params={"lang": "hi"}, headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["display_text"] == "कचरा एकत्र नहीं किया गया।"
    assert body[0]["translated_text"] == "Garbage has not been collected."
    assert body[0]["display_summary"] == "कचरा शिकायत।"
    assert body[0]["summary"] == "Garbage not collected."  # summary field itself stays English


def test_list_complaints_falls_back_to_english_on_translation_failure(client, monkeypatch, make_admin, db_session):
    """If on-read translation fails, the API should still return the English text."""
    make_admin(phone="9999999999", password="adminpass")
    login = client.post("/auth/login", json={"identifier": "9999999999", "password": "adminpass"})
    admin_token = login.json()["access_token"]

    db = db_session()
    db.add(Complaint(
        citizen_id="1", original_text="Garbage issue", original_language="en",
        translated_text="Garbage has not been collected.", summary="Garbage not collected.",
        status="pending",
    ))
    db.commit()
    db.close()

    fake_translation_service = Mock()
    fake_translation_service.to_language.side_effect = AIServiceError("translation down")
    monkeypatch.setattr(complaints_module, "_translation_service", fake_translation_service)

    response = client.get("/complaints", params={"lang": "hi"}, headers={"Authorization": f"Bearer {admin_token}"})

    assert response.status_code == 200
    body = response.json()[0]
    assert body["display_text"] == "Garbage has not been collected."
    assert body["display_summary"] == "Garbage not collected."


def _make_assigned_complaint(db_session, worker_id: int, ward: str = "Ward 14", status: str = "assigned") -> int:
    db = db_session()
    complaint = Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="Garbage issue", summary="Garbage not collected.",
        ward=ward, status=status, assigned_worker_id=worker_id,
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()
    return complaint_id


def test_accept_complaint_unlocks_phone_number(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    response = client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["assigned_worker_phone"] == worker["phone"]


def test_accept_complaint_not_assigned_to_you_returns_403(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 9")
    complaint_id = _make_assigned_complaint(db_session, other_worker_id, ward="Ward 9")

    response = client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_accept_complaint_already_accepted_returns_400(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="accepted")

    response = client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 400


def test_citizen_cannot_accept_a_complaint(client, make_citizen, make_worker, db_session):
    _worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    citizen_token, _user = make_citizen(phone="9000000001")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    response = client.post(f"/complaints/{complaint_id}/accept", headers={"Authorization": f"Bearer {citizen_token}"})
    assert response.status_code == 403


def test_reject_complaint_reassigns_to_next_worker_in_ward(client, make_worker, db_session):
    """The first worker in a ward rejects — it should move to the second, not vanish."""
    token1, worker1 = make_worker(phone="9000000002", ward="Ward 14")
    worker2_id = _make_worker_row(db_session, phone="9000000098", ward="Ward 14", full_name="Second Worker")
    complaint_id = _make_assigned_complaint(db_session, worker1["id"])

    response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token1}"},
        json={"reason": "Outside my assigned area."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "assigned"
    assert body["assigned_worker_name"] == "Second Worker"
    assert body["rejection_count"] == 1
    # worker1 no longer sees it — it moved to worker2.
    response = client.get("/complaints", headers={"Authorization": f"Bearer {token1}"})
    assert len(response.json()) == 0


def test_reject_complaint_with_no_other_worker_becomes_pending(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"},
        json={"reason": "Not my specialty."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["assigned_worker_name"] is None


def test_reject_complaint_wrong_status_returns_400(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="accepted")

    response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"},
        json={"reason": "Doesn't matter, wrong status."},
    )
    assert response.status_code == 400


def test_reject_complaint_requires_a_reason(client, make_worker, db_session):
    """Mandatory rejection reason -- worker-workflow phase. Empty and whitespace-only reasons
    must both be rejected (Pydantic's min_length catches the former, an explicit .strip() check
    catches the latter)."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    empty = client.post(f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"}, json={"reason": ""})
    assert empty.status_code == 422  # Pydantic min_length=1

    whitespace = client.post(f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"}, json={"reason": "   "})
    assert whitespace.status_code == 400
    assert "reason" in whitespace.json()["detail"].lower()


def test_reject_complaint_reason_is_stored(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"},
        json={"reason": "Wrong ward, this belongs elsewhere."},
    )
    assert response.status_code == 200

    db = db_session()
    from backend.models import ComplaintRejection
    rejection = db.query(ComplaintRejection).filter(ComplaintRejection.complaint_id == complaint_id).first()
    assert rejection is not None
    assert rejection.reason == "Wrong ward, this belongs elsewhere."
    assert rejection.worker_id == worker["id"]
    db.close()


def test_reject_complaint_notifies_every_admin(client, make_worker, make_admin, db_session):
    """Every admin gets a COMPLAINT_REJECTED notification naming the complaint -- citizens are
    deliberately never notified (see reject_complaint()'s own docstring); this only checks the
    admin side."""
    admin1 = make_admin(phone="9999999991", full_name="Admin One")
    admin2 = make_admin(phone="9999999992", full_name="Admin Two")
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"])

    response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {token}"},
        json={"reason": "Outside my assigned area."},
    )
    assert response.status_code == 200

    db = db_session()
    from backend.models import Notification
    for admin in (admin1, admin2):
        notif = (
            db.query(Notification)
            .filter(Notification.recipient_id == admin.id, Notification.type == "COMPLAINT_REJECTED")
            .first()
        )
        assert notif is not None, f"admin {admin.id} got no COMPLAINT_REJECTED notification"
        assert notif.complaint_id == complaint_id
    db.close()


def test_complaint_detail_shows_rejections_to_admin_only(client, make_worker, make_citizen, make_admin, db_session):
    """The core access-control guarantee: a rejection's reason is visible via GET /complaints/{id}
    for an admin, but the `rejections` field stays empty for the citizen who owns the complaint
    and for the worker currently assigned to it -- enforced server-side (_to_detail_response's
    viewer_role gate), not just hidden in a frontend component."""
    citizen_token, citizen = make_citizen(phone="9000000005")
    worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")
    make_admin(phone="9999999993", password="adminpass")
    admin_login = client.post("/auth/login", json={"identifier": "9999999993", "password": "adminpass"})
    admin_token = admin_login.json()["access_token"]

    db = db_session()
    complaint = Complaint(
        citizen_id=str(citizen["id"]), original_text="a", original_language="en",
        translated_text="Garbage issue", summary="Garbage not collected.",
        ward="Ward 14", status="assigned", assigned_worker_id=worker["id"],
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    reject_response = client.post(
        f"/complaints/{complaint_id}/reject", headers={"Authorization": f"Bearer {worker_token}"},
        json={"reason": "Confidential ops note -- citizen must never see this."},
    )
    assert reject_response.status_code == 200

    admin_view = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_view.status_code == 200
    admin_rejections = admin_view.json()["rejections"]
    assert len(admin_rejections) == 1
    assert admin_rejections[0]["worker_name"] == worker["full_name"]
    assert admin_rejections[0]["reason"] == "Confidential ops note -- citizen must never see this."

    citizen_view = client.get(f"/complaints/{complaint_id}", headers={"Authorization": f"Bearer {citizen_token}"})
    assert citizen_view.status_code == 200
    assert citizen_view.json()["rejections"] == []


def test_resolve_complaint_requires_accepted_first(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="assigned")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Done."},
    )
    assert response.status_code == 400


def test_resolve_complaint_requires_in_progress_not_just_accepted(client, make_worker, db_session):
    """Worker-workflow phase: "accepted" is no longer sufficient to resolve -- the complaint must
    have gone through start_work() into "in_progress" first (accepted -> in_progress ->
    resolved, not accepted -> resolved directly)."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="accepted")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Done."},
    )
    assert response.status_code == 400


def test_resolve_complaint_succeeds_after_in_progress(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="in_progress")

    response = client.post(
        f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Fixture replaced and tested successfully."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"


def test_resolve_complaint_requires_completion_status(client, make_worker, db_session):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    complaint_id = _make_assigned_complaint(db_session, worker["id"], status="in_progress")

    empty = client.post(f"/complaints/{complaint_id}/resolve", headers={"Authorization": f"Bearer {token}"}, data={"completion_status": "   "})
    assert empty.status_code == 400

    db = db_session()
    from backend.models import Complaint as ComplaintModel
    complaint = db.query(ComplaintModel).filter(ComplaintModel.id == complaint_id).first()
    assert complaint.status == "in_progress"  # unchanged -- never silently resolved
    db.close()


def test_resolve_complaint_missing_returns_404(client, make_worker):
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    response = client.post(
        "/complaints/999999/resolve", headers={"Authorization": f"Bearer {token}"},
        data={"completion_status": "Done."},
    )
    assert response.status_code == 404


def test_submit_feedback_on_resolved_complaint(client, make_citizen, make_worker, db_session):
    citizen_token, user = make_citizen(phone="9000000001")
    _worker_token, worker = make_worker(phone="9000000002", ward="Ward 14")

    db = db_session()
    complaint = Complaint(
        citizen_id=str(user["id"]), original_text="a", original_language="en",
        translated_text="a", summary="a", ward="Ward 14",
        status="resolved", assigned_worker_id=worker["id"],
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/feedback",
        headers={"Authorization": f"Bearer {citizen_token}"},
        json={"rating": 5, "comment": "Fixed quickly, thank you!"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["feedback_rating"] == 5
    assert body["feedback_comment"] == "Fixed quickly, thank you!"


def test_submit_feedback_before_resolved_returns_400(client, make_citizen, db_session):
    citizen_token, user = make_citizen(phone="9000000001")

    db = db_session()
    complaint = Complaint(
        citizen_id=str(user["id"]), original_text="a", original_language="en",
        translated_text="a", summary="a", status="assigned",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/feedback",
        headers={"Authorization": f"Bearer {citizen_token}"},
        json={"rating": 3},
    )
    assert response.status_code == 400


def test_submit_feedback_on_someone_elses_complaint_returns_403(client, make_citizen, db_session):
    token_a, _user_a = make_citizen(phone="9000000001")
    _token_b, user_b = make_citizen(phone="9000000002")

    db = db_session()
    complaint = Complaint(
        citizen_id=str(user_b["id"]), original_text="a", original_language="en",
        translated_text="a", summary="a", status="resolved",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/feedback",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"rating": 1},
    )
    assert response.status_code == 403


def test_submit_feedback_rating_out_of_range_returns_422(client, make_citizen, db_session):
    citizen_token, user = make_citizen(phone="9000000001")

    db = db_session()
    complaint = Complaint(
        citizen_id=str(user["id"]), original_text="a", original_language="en",
        translated_text="a", summary="a", status="resolved",
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    complaint_id = complaint.id
    db.close()

    response = client.post(
        f"/complaints/{complaint_id}/feedback",
        headers={"Authorization": f"Bearer {citizen_token}"},
        json={"rating": 7},
    )
    assert response.status_code == 422


# --- POST /complaints/classify-category -- first layer of the wizard's 3-layer category
# classification (real model -> keyword match -> manual picker, see ReportIssue.tsx). Mocked at
# the _category_service level, same convention as _agent/_translation_service above -- these
# tests are about the route's contract (auth, rate limiting, response shape), not
# ComplaintCategoryService's own Sarvam-call behavior (see test_complaint_category_service.py
# for that).


def test_classify_category_returns_model_result(client, monkeypatch, make_citizen):
    from backend.schemas.rag_knowledge import ServiceCategory

    monkeypatch.setattr(complaints_module, "_category_service", Mock(classify=lambda text: ServiceCategory.ROADS_POTHOLES))
    token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints/classify-category",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "There is a huge pothole outside my building."},
    )

    assert response.status_code == 200
    assert response.json() == {"category": "ROADS_POTHOLES"}


def test_classify_category_returns_null_when_model_unsure(client, monkeypatch, make_citizen):
    monkeypatch.setattr(complaints_module, "_category_service", Mock(classify=lambda text: None))
    token, _user = make_citizen(phone="9000000001")

    response = client.post(
        "/complaints/classify-category",
        headers={"Authorization": f"Bearer {token}"},
        json={"text": "Something is wrong."},
    )

    assert response.status_code == 200
    assert response.json() == {"category": None}


def test_classify_category_requires_authentication(client):
    response = client.post("/complaints/classify-category", json={"text": "Garbage everywhere."})
    assert response.status_code == 401


def test_classify_category_rejects_non_citizen(client, monkeypatch, make_worker):
    monkeypatch.setattr(complaints_module, "_category_service", Mock(classify=lambda text: None))
    worker_token, _worker = make_worker(phone="9000000002", ward="Ward 14")

    response = client.post(
        "/complaints/classify-category",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"text": "Garbage everywhere."},
    )
    assert response.status_code == 403


def test_complaints_trend_counts_resolved_from_status_history_not_current_status(client, make_worker, db_session):
    """The worker dashboard's "Opened vs. resolved" chart needs to know WHEN a complaint became
    resolved, not just that it currently is -- Complaint.status alone can't say that, only
    ComplaintStatusHistory's own timestamped rows can (see routes/complaints.py's complaints_trend
    docstring). This pins that down directly: a complaint resolved 3 days ago must show up in day
    -3's `resolved` count even though its `status` field is checked nowhere in this endpoint."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")
    other_worker_id = _make_worker_row(db_session, phone="9000000099", ward="Ward 14", full_name="Other Worker")

    now = datetime.now(timezone.utc)
    three_days_ago = now - timedelta(days=3)

    db = db_session()
    resolved_complaint = Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="Resolved 3 days ago", summary="a", ward="Ward 14",
        status="resolved", assigned_worker_id=worker["id"], created_at=now - timedelta(days=10),
    )
    db.add(resolved_complaint)
    db.flush()
    db.add(ComplaintStatusHistory(
        complaint_id=resolved_complaint.id, from_status="in_progress", to_status="resolved",
        actor_role="worker", actor_user_id=worker["id"], created_at=three_days_ago,
    ))
    # Belongs to a different worker -- must not count toward this worker's trend at all.
    other_complaint = Complaint(
        citizen_id="1", original_text="b", original_language="en",
        translated_text="Someone else's", summary="b", ward="Ward 14",
        status="resolved", assigned_worker_id=other_worker_id, created_at=now - timedelta(days=10),
    )
    db.add(other_complaint)
    db.flush()
    db.add(ComplaintStatusHistory(
        complaint_id=other_complaint.id, from_status="in_progress", to_status="resolved",
        actor_role="worker", actor_user_id=other_worker_id, created_at=three_days_ago,
    ))
    db.commit()
    db.close()

    response = client.get("/complaints/trend?days=7", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()

    assert len(body) == 7
    day_key = three_days_ago.strftime("%Y-%m-%d")
    matching_days = [row for row in body if row["date"] == day_key]
    assert len(matching_days) == 1
    assert matching_days[0]["resolved"] == 1


def test_complaints_trend_returns_exactly_the_requested_number_of_days(client, make_worker):
    token, _worker = make_worker(phone="9000000002", ward="Ward 14")
    response = client.get("/complaints/trend?days=7", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 7
    # Every day present even with zero complaints -- a fixed x-axis, not an activity-only range.
    assert all(
        row["opened"] == 0 and row["resolved"] == 0 and row["accepted"] == 0 and row["rejected"] == 0
        for row in body
    )


def test_complaints_trend_counts_accepted_from_status_history_and_rejected_from_its_own_table(
    client, make_worker, db_session
):
    """"Accepted" is a status transition (ComplaintStatusHistory), same reasoning as "resolved" --
    but "rejected" is NOT: a rejected complaint goes right back to "pending"/reassignment, so it
    never appears as a ComplaintStatusHistory.to_status value at all. It has its own table
    (ComplaintRejection) instead, and this pins down that the trend endpoint actually reads from
    it rather than expecting a "rejected" status that can never occur."""
    token, worker = make_worker(phone="9000000002", ward="Ward 14")

    now = datetime.now(timezone.utc)
    two_days_ago = now - timedelta(days=2)

    db = db_session()
    accepted_complaint = Complaint(
        citizen_id="1", original_text="a", original_language="en",
        translated_text="Accepted 2 days ago", summary="a", ward="Ward 14",
        status="accepted", assigned_worker_id=worker["id"], created_at=now - timedelta(days=10),
    )
    db.add(accepted_complaint)
    db.flush()
    db.add(ComplaintStatusHistory(
        complaint_id=accepted_complaint.id, from_status="assigned", to_status="accepted",
        actor_role="worker", actor_user_id=worker["id"], created_at=two_days_ago,
    ))
    rejected_complaint = Complaint(
        citizen_id="1", original_text="b", original_language="en",
        translated_text="Rejected 2 days ago", summary="b", ward="Ward 14",
        status="pending", created_at=now - timedelta(days=10),
    )
    db.add(rejected_complaint)
    db.flush()
    db.add(ComplaintRejection(complaint_id=rejected_complaint.id, worker_id=worker["id"], reason="Not my area", created_at=two_days_ago))
    db.commit()
    db.close()

    response = client.get("/complaints/trend?days=7", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()

    day_key = two_days_ago.strftime("%Y-%m-%d")
    matching_days = [row for row in body if row["date"] == day_key]
    assert len(matching_days) == 1
    assert matching_days[0]["accepted"] == 1
    assert matching_days[0]["rejected"] == 1
