"""Subject-access export and erasure (GDPR).

Erasure anonymises the person everywhere we hold personal data but keeps what the club must keep: invoices,
payments and bank lines (financial records are retained for six years) carry only the anonymised name afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import finance
from app.models import (
    Attachment,
    AuditLog,
    AuthMagicLink,
    Boat,
    BoatCrewLink,
    Contact,
    ContactRelation,
    Invoice,
    ListMembership,
    Member,
    Membership,
    MembershipRenewal,
    OutboundEmail,
    ParticipationRecord,
    PaymentTransaction,
    Qualification,
    SessionRecord,
    TrainingBooking,
    UserRole,
)

ERASED = "Erased person"


def _deleted(db: Session, stmt: Any) -> int:
    return int(getattr(db.execute(stmt), "rowcount", 0) or 0)


def _row(obj: Any) -> dict[str, Any]:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def export_person(db: Session, contact_id: int | None = None, member_id: int | None = None) -> dict[str, Any]:
    """Everything held about one person, as plain data (for a subject access request)."""
    contact = db.get(Contact, contact_id) if contact_id else None
    member = db.get(Member, member_id) if member_id else None
    if contact is None and member is not None:
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
    if member is None and contact is not None and contact.member_id:
        member = db.get(Member, contact.member_id)
    if contact is None and member is None:
        raise finance.FinanceError("Person not found")
    cid = contact.id if contact else None
    mid = member.id if member else None
    invoices = db.scalars(select(Invoice).where((Invoice.contact_id == cid) | (Invoice.member_id == mid))).all() if (cid or mid) else []
    invoice_ids = [i.id for i in invoices]
    emails = [e for e in db.scalars(select(OutboundEmail)) if contact and contact.email and e.to_address.lower() == contact.email.lower()]
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "contact": _row(contact) if contact else None,
        "member": _row(member) if member else None,
        "memberships": [_row(m) for m in db.scalars(select(Membership).where(Membership.member_id == mid))] if mid else [],
        "renewals": [_row(r) for r in db.scalars(select(MembershipRenewal).where(MembershipRenewal.member_id == mid))] if mid else [],
        "invoices": [_row(i) for i in invoices],
        "payments": [_row(p) for p in db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id.in_(invoice_ids)))] if invoice_ids else [],
        "boats": [_row(b) for b in db.scalars(select(Boat).where((Boat.owner_contact_id == cid) | (Boat.owner_member_id == mid)))] if (cid or mid) else [],
        "qualifications": [_row(q) for q in db.scalars(select(Qualification).where(Qualification.contact_id == cid))] if cid else [],
        "training_bookings": [_row(t) for t in db.scalars(select(TrainingBooking).where(TrainingBooking.member_id == mid))] if mid else [],
        "lists": [_row(entry) for entry in db.scalars(select(ListMembership).where(ListMembership.contact_id == cid))] if cid else [],
        "emails_sent": [{"to": e.to_address, "subject": e.subject, "when": e.created_at.isoformat(), "status": e.status} for e in emails],
    }


def erase_person(db: Session, *, contact_id: int | None = None, member_id: int | None = None, actor: str) -> dict[str, int]:
    """Anonymise a person. Returns counts of what was changed. Idempotent."""
    contact = db.get(Contact, contact_id) if contact_id else None
    member = db.get(Member, member_id) if member_id else None
    if contact is None and member is not None:
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
    if member is None and contact is not None and contact.member_id:
        member = db.get(Member, contact.member_id)
    if contact is None and member is None:
        raise finance.FinanceError("Person not found")
    emails = {e.lower() for e in ((contact.email if contact else None), (member.email if member else None)) if e}
    counts = {"contact": 0, "member": 0, "emails": 0, "attachments": 0, "qualifications": 0, "lists": 0, "sessions": 0}

    if contact is not None:
        label = f"{ERASED} {contact.id}"
        contact.name, contact.email, contact.phone, contact.address, contact.birth_date = label, None, None, None, None
        contact.notes, contact.suppressed = "", True
        counts["contact"] = 1
        counts["attachments"] = _deleted(db, delete(Attachment).where(Attachment.owner_contact_id == contact.id))
        counts["qualifications"] = _deleted(db, delete(Qualification).where(Qualification.contact_id == contact.id))
        counts["lists"] = _deleted(db, delete(ListMembership).where(ListMembership.contact_id == contact.id))
        db.execute(delete(ContactRelation).where((ContactRelation.from_contact_id == contact.id) | (ContactRelation.to_contact_id == contact.id)))
        db.execute(delete(ParticipationRecord).where(ParticipationRecord.contact_id == contact.id))
        db.execute(delete(BoatCrewLink).where(BoatCrewLink.contact_id == contact.id))
    for target_type, target_id in (("contact", contact.id if contact else None), ("member", member.id if member else None)):
        if target_id is not None:  # the before / after values in the audit trail are personal data too
            for row in db.scalars(select(AuditLog).where(AuditLog.target_type == target_type, AuditLog.target_id == str(target_id))):
                if row.details:
                    row.details = {"erased": True}
    if member is not None:
        member.name = f"{ERASED} {member.id}"
        member.email, member.active, member.renewal_status = None, False, "erased"
        counts["member"] = 1
    for address in emails:
        counts["sessions"] += _deleted(db, delete(UserRole).where(func.lower(UserRole.email) == address))
        db.execute(delete(AuthMagicLink).where(func.lower(AuthMagicLink.email) == address))
        for session in db.scalars(select(SessionRecord).where(func.lower(SessionRecord.email) == address, SessionRecord.revoked_at.is_(None))):
            session.revoked_at = datetime.now(UTC)
        for mail in db.scalars(select(OutboundEmail).where(func.lower(OutboundEmail.to_address) == address)):
            mail.to_address, mail.body = "erased@invalid", "(erased)"
            counts["emails"] += 1
    db.add(
        AuditLog(
            actor_email=actor,
            actor_role="user",
            action="privacy.erase",
            target_type="contact" if contact else "member",
            target_id=str(contact.id if contact else member.id if member else ""),
            details=counts,
        )
    )
    db.flush()
    return counts
