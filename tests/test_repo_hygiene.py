# ruff: noqa: S603, S607  (fixed git command)
"""Guards that stop personal data reaching git: no database is ever tracked, and the made-up demo data is made up."""

import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import fake_data
from app.db import SessionLocal
from app.models import Contact, Member, UserRole

ROOT = Path(__file__).resolve().parent.parent


def _tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return out.splitlines()


def test_no_database_is_tracked() -> None:
    databases = [f for f in _tracked() if re.search(r"\.(db|sqlite3?)(-\w+)?$", f)]
    assert databases == [], databases


def test_the_demo_club_is_made_up_and_reaches_nobody() -> None:
    with SessionLocal() as db:
        counts = fake_data.build(db, seed=3, today=date(2026, 10, 25))
        db.commit()
        assert counts["households"] > 100 and counts["boats"] > 50 and counts["invoices"] > 100
        assert all(email.endswith("@example.org") for email in db.scalars(select(Contact.email).where(Contact.email.is_not(None))))
        assert all(email.endswith("@example.org") for email in db.scalars(select(Member.email).where(Member.email.is_not(None))))
        assert all(phone.startswith("07700 900") for phone in db.scalars(select(Contact.phone).where(Contact.phone.is_not(None))))
        assert {r.email for r in db.scalars(select(UserRole))} >= {"admin@example.org", "treasurer@example.org", "secretary@example.org"}
        assert fake_data.build(db) == {"refused": 1, "reason_members_exist": 1}
