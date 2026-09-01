#!/usr/bin/env python3
"""Seed the first Super Admin account directly into the database.

There is no API or UI path that creates a Super Admin — by design, the only
way one exists is by being placed directly into the database when the system
is set up for a municipality. This script is that step.

Usage:
    python3 scripts/seed_admin.py --phone 9999999999 --password "change-me" --name "Anjali Kulkarni"

Safe to re-run: if an account with that phone number already exists, the
script reports it and exits without making changes.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import SessionLocal, init_db  # noqa: E402
from backend.models import User  # noqa: E402
from backend.services.auth_service import hash_password  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the first Super Admin account.")
    parser.add_argument("--phone", required=True, help="Login phone number for the admin account.")
    parser.add_argument("--password", required=True, help="Initial password (change it after first login).")
    parser.add_argument("--name", required=True, help="Display name for the admin account.")
    parser.add_argument("--language", default="en", help="Preferred language code (default: en).")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.phone == args.phone).first()
        if existing is not None:
            print(f"An account with phone {args.phone} already exists (id={existing.id}, role={existing.role}). Nothing to do.")
            return

        admin = User(
            full_name=args.name,
            phone=args.phone,
            password_hash=hash_password(args.password),
            role="admin",
            # LIVE-REPORTED: this script's own docstring/output call this "the Super Admin
            # account," but never actually set the flag that makes it one (super_admin defaults
            # to False -- see models.py) -- every account this script ever created was really
            # just a regular Admin. Predates the super_admin flag itself; never updated to match.
            super_admin=True,
            preferred_language=args.language,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        print(f"Created Super Admin account: id={admin.id}, phone={admin.phone}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
