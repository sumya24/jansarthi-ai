#!/usr/bin/env python3
"""Displace every non-kept candidate from a citizen's most recent complaint, for e2e tests only.

Why this exists: the Add Worker picker only ever offers already worker-backed wards (see
backend/routes/locations.py's _worker_backed_ward_ids), and every real ward it can reach already
has real seed/demo workers in it (see scripts/seed_multi_ward_data.py) with a lower id than
anything a test creates today -- assign_next_worker() always picks the lowest-id eligible worker
in a ward (backend/services/assignment_service.py), so a freshly created worker can never win the
very first assignment through the real UI alone.

This script does NOT bypass or fake that logic -- it drives the exact same real backend behavior
a worker actually rejecting a complaint would: it inserts a ComplaintRejection row (the same table
routes/complaints.py's reject_complaint() writes to) for every candidate NOT in --keep-phone, then
calls assignment_service.assign_next_worker() -- the same function both the initial assignment and
every real rejection call -- so it reassigns through its own real "who's left" logic, not a
hardcoded shortcut. No seed/demo worker is deleted, moved, or otherwise altered; they simply
become ineligible for this ONE complaint, exactly as they would if they'd genuinely rejected it.

--keep-phone may be repeated: pass every worker a test still needs to be a REAL, live candidate
for (e.g. a "reject reassigns to the next worker" test needs both worker A and worker B still
eligible -- only A should win the first assignment, and rejecting-A-for-real later must still be
able to fall through to a genuinely untouched B). The lowest-id kept worker wins the immediate
reassignment, same lowest-id-first rule assign_next_worker() always uses.

Usage:
    python scripts/e2e_force_reassign_to_worker.py --citizen-phone <phone> --keep-phone <phone> [--keep-phone <phone> ...]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import Complaint, ComplaintRejection, User  # noqa: E402
from backend.services.assignment_service import _candidates, assign_next_worker  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Displace every non-kept worker from a citizen's latest complaint.")
    parser.add_argument("--citizen-phone", required=True)
    parser.add_argument("--keep-phone", required=True, action="append", dest="keep_phones")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        citizen = db.query(User).filter(User.phone == args.citizen_phone, User.role == "citizen").first()
        if citizen is None:
            raise SystemExit(f"No citizen with phone {args.citizen_phone}.")

        keep_workers = (
            db.query(User).filter(User.phone.in_(args.keep_phones), User.role == "worker").all()
        )
        found_phones = {w.phone for w in keep_workers}
        missing = set(args.keep_phones) - found_phones
        if missing:
            raise SystemExit(f"No worker found for phone(s): {sorted(missing)}.")
        keep_ids = {w.id for w in keep_workers}

        complaint = (
            db.query(Complaint)
            .filter(Complaint.citizen_id == str(citizen.id))
            .order_by(Complaint.id.desc())
            .first()
        )
        if complaint is None:
            raise SystemExit(f"No complaint found for citizen {args.citizen_phone}.")

        candidates = _candidates(db, complaint)
        candidate_ids = {c.id for c in candidates}
        missing_candidates = keep_ids - candidate_ids
        if missing_candidates:
            names = [w.full_name for w in keep_workers if w.id in missing_candidates]
            raise SystemExit(
                f"Worker(s) {names} aren't real candidates for complaint {complaint.id} "
                f"(ward={complaint.ward!r}, ward_id={complaint.ward_id}) -- refusing to force it."
            )

        already_rejected = {
            row.worker_id
            for row in db.query(ComplaintRejection).filter(ComplaintRejection.complaint_id == complaint.id).all()
        }
        for candidate in candidates:
            if candidate.id in keep_ids or candidate.id in already_rejected:
                continue
            db.add(
                ComplaintRejection(
                    complaint_id=complaint.id,
                    worker_id=candidate.id,
                    reason="e2e test setup: displaced so freshly created test worker(s) can be exercised.",
                )
            )
        db.commit()

        assign_next_worker(db, complaint)
        db.refresh(complaint)
        print(
            f"Complaint {complaint.id} now assigned_worker_id={complaint.assigned_worker_id} "
            f"(kept candidates: {sorted(keep_ids)}, status={complaint.status})."
        )
        if complaint.assigned_worker_id not in keep_ids:
            raise SystemExit("Reassignment did not land on any kept worker -- see output above.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
