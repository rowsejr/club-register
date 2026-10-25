"""Reference data that every club database should have, applied on start-up and safe to repeat."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cashbook, catalogue, membership
from app.models import MembershipType


def apply_known_fees(db: Session) -> int:
    """Give every membership type that has no fee yet the club's standard fee; a fee the treasurer has set is never touched."""
    changed = 0
    for mtype in db.scalars(select(MembershipType).where(MembershipType.fee_minor.is_(None))):
        if mtype.name in membership.KNOWN_FEES:
            mtype.fee_minor = membership.KNOWN_FEES[mtype.name]
            changed += 1
    return changed


def seed(db: Session) -> None:
    apply_known_fees(db)
    catalogue.seed_starter_items(db)
    cashbook.seed_codes(db)
    db.commit()
