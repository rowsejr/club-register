"""The route into membership: enquiry -> application -> approval -> first invoice -> activation on payment.

Three doors feed one service:
* a public enquiry form (or a phone call / walk-in logged by a volunteer),
* a public "join online" application for membership types marked joinable,
* a volunteer adding a member directly (phone, paper, in person).

Nothing is activated until the first invoice is paid (or is free), and nobody has to chase the payment: the bank
matching and the invoice payment rules call ``complete_on_payment`` which finishes the job and sends the welcome.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import finance, mailer, membership
from app.invoice_pdf import render_invoice_pdf
from app.models import (
    Application,
    ApplicationPerson,
    AuditLog,
    Contact,
    ContactList,
    ContactRelation,
    Enquiry,
    EnquiryNote,
    Invoice,
    ListMembership,
    Member,
    Membership,
    MembershipType,
)
from app.security import hash_token, issue_token

OPEN_ENQUIRY = ("unverified", "new", "contacted")
INTERESTS = ("sailing", "training", "boat_storage", "social", "other")
PENDING_APPLICATION = ("submitted", "info_requested")


class ApplicationError(ValueError):
    """A rule violation that is safe to show to the volunteer or applicant."""


def _email(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return validate_email(value, check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ApplicationError(f"That email address does not look right: {exc}") from exc


def _base_url() -> str:
    return os.getenv("CLUB_BASE_URL", "").rstrip("/")


def _audit(db: Session, actor: str, action: str, target: str, target_id: Any, **details: Any) -> None:
    db.add(AuditLog(actor_email=actor, actor_role="public" if actor == "public" else "user", action=action, target_type=target, target_id=str(target_id), details=details))


def find_contact(db: Session, email: str | None) -> Contact | None:
    if not email:
        return None
    return db.scalar(select(Contact).where(func.lower(Contact.email) == email.lower()).order_by(Contact.id))


# --------------------------------------------------------------------------- enquiries


def submit_enquiry(
    db: Session,
    *,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    interest: str | None = None,
    message: str | None = None,
    source: str = "web",
    consent: bool = False,
    ip_hash: str | None = None,
    verify: bool = True,
    actor: str = "public",
) -> tuple[Enquiry, bool]:
    """Record an enquiry. Returns (enquiry, created). A repeat within a day from the same address adds to the existing one."""
    name = " ".join((name or "").split())
    if not name:
        raise ApplicationError("Please tell us your name")
    clean = _email(email)
    if verify and not clean:
        raise ApplicationError("An email address is needed so we can reply")
    if not clean and not (phone or "").strip():
        raise ApplicationError("Give an email address or phone number")
    if interest and interest not in INTERESTS:
        interest = "other"
    since = datetime.now(UTC) - timedelta(hours=24)
    if clean:
        existing = db.scalar(select(Enquiry).where(func.lower(Enquiry.email) == clean, Enquiry.status.in_(OPEN_ENQUIRY), Enquiry.created_at >= since))
        if existing is not None:
            if message and message.strip() not in (existing.message or ""):
                db.add(EnquiryNote(enquiry_id=existing.id, author="enquirer", body=message.strip()[:4000]))
            return existing, False
    enquiry = Enquiry(
        name=name[:255],
        email=clean,
        phone=(phone or "").strip()[:80] or None,
        interest=interest,
        source=source,
        message=(message or "").strip()[:4000] or None,
        status="unverified" if verify else "new",
        consent_contact=consent,
        consent_at=datetime.now(UTC) if consent else None,
        ip_hash=ip_hash,
        last_action_at=datetime.now(UTC),
    )
    token = None
    if verify:
        token, enquiry.verify_token_hash = issue_token()
    db.add(enquiry)
    db.flush()
    if token and clean:
        link = f"{_base_url()}/verify/{token}"
        mailer.send_email(
            db,
            to=clean,
            subject="Please confirm your enquiry to the club",
            body=f"Hello {name.split()[0]},\n\nThanks for getting in touch. Please confirm your address so we can reply:\n\n{link}\n\nIf you did not ask us anything, ignore this message.",
            stored_body="(verification link not stored)",
            related_type="enquiry_verify",
            related_id=enquiry.id,
        )
    _audit(db, actor, "enquiry.create", "enquiry", enquiry.id, source=source)
    return enquiry, True


def verify_enquiry(db: Session, token: str) -> Enquiry | None:
    enquiry = db.scalar(select(Enquiry).where(Enquiry.verify_token_hash == hash_token(token), Enquiry.status == "unverified"))
    if enquiry is None:
        return None
    enquiry.status, enquiry.verified_at, enquiry.last_action_at = "new", datetime.now(UTC), datetime.now(UTC)
    enquiry.verify_token_hash = None
    db.flush()
    return enquiry


def add_note(db: Session, enquiry: Enquiry, author: str, body: str) -> EnquiryNote:
    if not body.strip():
        raise ApplicationError("Write a note first")
    note = EnquiryNote(enquiry_id=enquiry.id, author=author, body=body.strip()[:4000])
    db.add(note)
    enquiry.last_action_at = datetime.now(UTC)
    return note


def set_enquiry_status(db: Session, enquiry: Enquiry, status: str, actor: str, outcome: str = "") -> None:
    if status not in {"new", "contacted", "closed", "spam"}:
        raise ApplicationError("Unknown status")
    if enquiry.status == "converted" and status != "closed":
        raise ApplicationError("This enquiry has already become an application")
    if enquiry.status == "unverified" and status == "contacted":
        raise ApplicationError("This address has not been confirmed yet")
    enquiry.status = status
    if status in {"closed", "spam"}:
        enquiry.outcome = outcome.strip() or ("spam" if status == "spam" else "closed")
    enquiry.last_action_at = datetime.now(UTC)
    _audit(db, actor, f"enquiry.{status}", "enquiry", enquiry.id)
    db.flush()


def assign(db: Session, enquiry: Enquiry, assignee: str | None, actor: str) -> None:
    enquiry.assigned_to = (assignee or "").strip() or None
    enquiry.last_action_at = datetime.now(UTC)
    _audit(db, actor, "enquiry.assign", "enquiry", enquiry.id, to=enquiry.assigned_to)


def convert_enquiry(db: Session, enquiry: Enquiry, membership_type_id: int | None, actor: str) -> Application:
    """One click from an enquiry to an application waiting for a decision."""
    if enquiry.status == "unverified":
        raise ApplicationError("This address has not been confirmed yet")
    if enquiry.application_id and db.get(Application, enquiry.application_id):
        return db.get(Application, enquiry.application_id)  # type: ignore[return-value]
    application = Application(
        enquiry_id=enquiry.id,
        name=enquiry.name,
        email=enquiry.email,
        phone=enquiry.phone,
        membership_type_id=membership_type_id,
        source="enquiry",
        status="submitted",
        notes=enquiry.message,
    )
    db.add(application)
    db.flush()
    enquiry.application_id, enquiry.status, enquiry.last_action_at = application.id, "converted", datetime.now(UTC)
    _audit(db, actor, "enquiry.convert", "enquiry", enquiry.id, application=application.id)
    db.flush()
    return application


# --------------------------------------------------------------------------- applications


@dataclass
class PersonIn:
    name: str
    role: str = "partner"
    email: str | None = None
    birth_date: date | None = None


@dataclass
class ApplicationIn:
    name: str
    email: str | None
    membership_type_id: int | None
    phone: str | None = None
    address: str | None = None
    birth_date: date | None = None
    experience: str | None = None
    notes: str | None = None
    people: list[PersonIn] = field(default_factory=list)
    rules_agreed: bool = False
    consent: bool = False
    consent_marketing: bool = False


def submit_application(db: Session, data: ApplicationIn, *, source: str = "web", verify: bool = True, ip_hash: str | None = None, actor: str = "public") -> tuple[Application, bool]:
    name = " ".join(data.name.split())
    if not name:
        raise ApplicationError("Please tell us your name")
    clean = _email(data.email)
    if verify and not clean:
        raise ApplicationError("An email address is needed so we can reply")
    if source == "web" and not (data.rules_agreed and data.consent):
        raise ApplicationError("Please accept the club rules and the privacy notice")
    mtype = db.get(MembershipType, data.membership_type_id) if data.membership_type_id else None
    if source == "web" and (mtype is None or not mtype.online_joinable or mtype.active is False):
        raise ApplicationError("Please choose one of the membership types shown")
    capacity = membership.household_capacity(mtype.name) if mtype else None
    named = [person for person in data.people if person.name.strip()]
    if capacity is not None and 1 + len(named) > capacity:
        raise ApplicationError(f"{mtype.name} covers {capacity} {'person' if capacity == 1 else 'people'}; you have listed {1 + len(named)}. Choose a larger membership type or remove someone.")  # type: ignore[union-attr]
    since = datetime.now(UTC) - timedelta(hours=24)
    if clean:
        existing = db.scalar(select(Application).where(func.lower(Application.email) == clean, Application.status.in_(["unverified", *PENDING_APPLICATION]), Application.submitted_at >= since))
        if existing is not None:
            return existing, False
    application = Application(
        name=name[:255],
        email=clean,
        phone=(data.phone or "").strip()[:80] or None,
        address=(data.address or "").strip() or None,
        birth_date=data.birth_date,
        experience=(data.experience or "").strip()[:4000] or None,
        notes=(data.notes or "").strip()[:4000] or None,
        membership_type_id=mtype.id if mtype else None,
        source=source,
        status="unverified" if verify else "submitted",
        rules_agreed_at=datetime.now(UTC) if data.rules_agreed else None,
        consent_at=datetime.now(UTC) if data.consent else None,
        consent_marketing=data.consent_marketing,
        ip_hash=ip_hash,
    )
    token = None
    if verify:
        token, application.verify_token_hash = issue_token()
    db.add(application)
    db.flush()
    for person in data.people:
        if person.name.strip():
            db.add(ApplicationPerson(application_id=application.id, role=person.role if person.role in {"partner", "child"} else "partner", name=" ".join(person.name.split())[:255], email=_email(person.email), birth_date=person.birth_date))
    if token and clean:
        mailer.send_email(
            db,
            to=clean,
            subject="Please confirm your membership application",
            body=f"Hello {name.split()[0]},\n\nThanks for applying to join. Please confirm your address and we will pass your application to the committee:\n\n{_base_url()}/verify/{token}\n\nIf this was not you, ignore this message.",
            stored_body="(verification link not stored)",
            related_type="application_verify",
            related_id=application.id,
        )
    _audit(db, actor, "application.create", "application", application.id, source=source)
    return application, True


def verify_application(db: Session, token: str) -> Application | None:
    application = db.scalar(select(Application).where(Application.verify_token_hash == hash_token(token), Application.status == "unverified"))
    if application is None:
        return None
    application.status, application.verify_token_hash = "submitted", None
    application.submitted_at = datetime.now(UTC)
    db.flush()
    return application


def _decide(application: Application, status: str, actor: str, note: str) -> None:
    application.status, application.decided_by, application.decided_at, application.decision_note = status, actor, datetime.now(UTC), note.strip() or None


def reject(db: Session, application: Application, actor: str, note: str) -> None:
    if application.status not in PENDING_APPLICATION:
        raise ApplicationError(f"A {application.status} application cannot be rejected")
    if not note.strip():
        raise ApplicationError("Give a reason (it is kept on the record)")
    _decide(application, "rejected", actor, note)
    _audit(db, actor, "application.reject", "application", application.id)
    db.flush()


def request_info(db: Session, application: Application, actor: str, note: str) -> None:
    if application.status not in PENDING_APPLICATION:
        raise ApplicationError(f"A {application.status} application cannot be queried")
    if not note.strip():
        raise ApplicationError("Say what you need to know")
    application.status, application.decision_note = "info_requested", note.strip()
    if application.email:
        mailer.send_email(
            db,
            to=application.email,
            subject="About your membership application",
            body=f"Hello {application.name.split()[0]},\n\nThe membership team needs a little more information:\n\n{note.strip()}\n\nJust reply to this email.",
            related_type="application",
            related_id=application.id,
            actor=actor,
            reply_to=finance.get_setting(db, "reply_to") or None,
        )
    _audit(db, actor, "application.info_requested", "application", application.id)


def withdraw(db: Session, application: Application, actor: str) -> None:
    if application.status in {"completed", "rejected", "withdrawn"}:
        raise ApplicationError(f"A {application.status} application cannot be withdrawn")
    if application.invoice_id:
        invoice = db.get(Invoice, application.invoice_id)
        if invoice is not None and invoice.status in finance.OPEN and finance.paid_total(db, invoice.id) == 0:
            finance.void_invoice(db, invoice, actor, "Application withdrawn")
    application.status = "withdrawn"
    _audit(db, actor, "application.withdraw", "application", application.id)
    db.flush()


def _person_contact(db: Session, name: str, email: str | None, birth_date: date | None = None) -> Contact:
    contact = find_contact(db, email)
    if contact is None:
        contact = Contact(name=name, email=email, birth_date=birth_date)
        db.add(contact)
        db.flush()
    return contact


def _member_for(db: Session, contact: Contact, type_name: str) -> Member:
    member = db.get(Member, contact.member_id) if contact.member_id else None
    if member is None and contact.email:
        member = db.scalar(select(Member).where(func.lower(Member.email) == contact.email.lower()))
    if member is None:
        member = Member(name=contact.name, email=contact.email, membership_type=type_name, active=False, renewal_status="pending", payment_status="unpaid", join_date=date.today())
        db.add(member)
        db.flush()
    contact.member_id = member.id
    return member


def approve(db: Session, application: Application, actor: str, note: str = "", membership_type_id: int | None = None) -> Invoice | None:
    """Turn an application into a member: contact(s), pending membership and first invoice. Idempotent per application."""
    if application.status == "approved" and application.invoice_id:
        return db.get(Invoice, application.invoice_id)
    if application.status not in PENDING_APPLICATION:
        raise ApplicationError(f"A {application.status} application cannot be approved")
    type_id = membership_type_id or application.membership_type_id
    mtype = db.get(MembershipType, type_id) if type_id else None
    if mtype is None:
        raise ApplicationError("Choose a membership type first")
    if mtype.fee_minor is None:
        raise ApplicationError(f"No fee is set for {mtype.name}: set it under Finance > Fees first")
    clean = _email(application.email)
    contact = db.get(Contact, application.contact_id) if application.contact_id else find_contact(db, clean)
    if contact is None:
        contact = Contact(name=application.name, email=clean, phone=application.phone, address=application.address, birth_date=application.birth_date, notes=(application.experience or ""))
        db.add(contact)
        db.flush()
    else:
        for attr, value in (("phone", application.phone), ("address", application.address), ("birth_date", application.birth_date)):
            if value and not getattr(contact, attr):
                setattr(contact, attr, value)
    existing = db.get(Member, contact.member_id) if contact.member_id else None
    if existing is not None and existing.active and membership.latest_membership(db, existing.id) is not None and membership.latest_membership(db, existing.id).status == "active":  # type: ignore[union-attr]
        raise ApplicationError(f"{contact.name} is already an active member: use renewal instead")
    member = _member_for(db, contact, mtype.name)
    member.membership_type = mtype.name
    period = membership.create_period(db, date.today().year)
    pending = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == period.starts_on))
    if pending is None:
        pending = Membership(member_id=member.id, plan_name=mtype.name, starts_on=period.starts_on, ends_on=period.ends_on, status="pending_payment")
        db.add(pending)
    else:
        pending.plan_name, pending.status = mtype.name, "pending_payment"
    for person in db.scalars(select(ApplicationPerson).where(ApplicationPerson.application_id == application.id)):
        other = _person_contact(db, person.name, person.email, person.birth_date)
        _member_for(db, other, mtype.name)
        if not db.scalar(select(ContactRelation.id).where(ContactRelation.from_contact_id == contact.id, ContactRelation.to_contact_id == other.id)):
            db.add(ContactRelation(from_contact_id=contact.id, to_contact_id=other.id, relation_type=person.role))
    lines: list[dict[str, Any]] = []
    if mtype.fee_minor > 0:
        lines.append({"description": f"{mtype.name} membership {period.name}", "amount_minor": mtype.fee_minor, "nominal_code": mtype.nominal_code or "0101", "source_type": "application", "source_id": str(application.id)})
    if (mtype.joining_fee_minor or 0) > 0:
        lines.append({"description": "Joining fee", "amount_minor": mtype.joining_fee_minor, "nominal_code": mtype.nominal_code or "0101", "source_type": "application_joining", "source_id": str(application.id)})
    application.contact_id, application.member_id, application.membership_type_id = contact.id, member.id, mtype.id
    _decide(application, "approved", actor, note)
    invoice: Invoice | None = None
    if lines:
        invoice = finance.create_invoice(db, contact_id=contact.id, member_id=member.id, lines=lines, actor=actor, notes=f"Application {application.id}")
        finance.issue(db, invoice, actor)
        application.invoice_id = invoice.id
    if application.enquiry_id and (enquiry := db.get(Enquiry, application.enquiry_id)):
        enquiry.status, enquiry.contact_id = "converted", contact.id
    db.flush()
    _audit(db, actor, "application.approve", "application", application.id, member=member.id, invoice=invoice.id if invoice else None)
    if invoice is None:  # a free membership starts straight away
        _activate(db, application, actor)
    elif contact.email:
        _send_approval(db, application, contact, invoice, actor)
    return invoice


def _send_approval(db: Session, application: Application, contact: Contact, invoice: Invoice, actor: str) -> None:
    settings = finance.all_settings(db)
    club = settings["club_name"]
    parts = [
        f"Hello {contact.name.split()[0]},",
        "",
        f"Welcome to {club}! Your application has been approved. Your membership starts as soon as the first invoice is paid.",
        "",
        f"Invoice {invoice.reference}: £{invoice.amount_minor / 100:,.2f}, due by {invoice.due_date.strftime('%d %B %Y') if invoice.due_date else 'the date shown'}.",
    ]
    for block in (settings["payment_instructions"], settings["bank_details"]):
        if block:
            parts += ["", block]
    parts += ["", f"Please quote invoice number {invoice.reference} when you pay. You can sign in at {_base_url()}/login with this email address to see your invoice.", "", club]
    mailer.send_email(
        db,
        to=contact.email or "",
        subject=f"Welcome to {club}: your membership invoice",
        body="\n".join(parts),
        attachment=(f"Invoice-{invoice.reference}.pdf", render_invoice_pdf(db, invoice), "application/pdf"),
        related_type="application",
        related_id=application.id,
        actor=actor,
        reply_to=settings["reply_to"] or None,
    )


def next_membership_number(db: Session) -> str:
    seq_exists = db.get(finance.NumberSequence, "membership_number")
    if seq_exists is None:
        highest = 0
        for (number,) in db.execute(select(Member.membership_number).where(Member.membership_number.is_not(None))):
            for part in str(number).replace(",", "/").split("/"):
                if part.strip().isdigit():
                    highest = max(highest, int(part))
        db.add(finance.NumberSequence(name="membership_number", value=highest))
        db.flush()
    return str(finance.next_number(db, "membership_number"))


def _activate(db: Session, application: Application, actor: str) -> None:
    member = db.get(Member, application.member_id) if application.member_id else None
    contact = db.get(Contact, application.contact_id) if application.contact_id else None
    if member is None or contact is None:
        return
    number = member.membership_number or next_membership_number(db)
    household = [member]
    for relation in db.scalars(select(ContactRelation).where(ContactRelation.from_contact_id == contact.id)):
        other = db.get(Contact, relation.to_contact_id)
        if other is not None and other.member_id and (m := db.get(Member, other.member_id)):
            household.append(m)
    period_membership = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.status == "pending_payment").order_by(Membership.starts_on.desc()))
    ends = period_membership.ends_on if period_membership else date(date.today().year, 12, 31)
    if period_membership:
        period_membership.status = "active"
    for person in household:
        person.active, person.renewal_status, person.payment_status, person.expiry_date = True, "active", "paid", ends
        person.membership_number = person.membership_number or number
        person.join_date = person.join_date or date.today()
    new_members = db.scalar(select(ContactList).where(ContactList.name == "New Members"))
    if new_members is None:
        new_members = ContactList(name="New Members")
        db.add(new_members)
        db.flush()
    for who in [contact]:
        if not db.scalar(select(ListMembership.id).where(ListMembership.contact_id == who.id, ListMembership.list_id == new_members.id)):
            db.add(ListMembership(contact_id=who.id, list_id=new_members.id))
    application.status = "completed"
    db.flush()
    _audit(db, actor, "application.complete", "application", application.id, membership_number=number)
    if contact.email:
        club = finance.get_setting(db, "club_name")
        mailer.send_email(
            db,
            to=contact.email,
            subject=f"You are now a member of {club}",
            body=f"Hello {contact.name.split()[0]},\n\nThank you: your payment has arrived and your membership is active. Your membership number is {number}.\n\nSign in at {_base_url()}/login with this email address to see your membership, invoices and details.\n\nSee you on the water!\n\n{club}",
            related_type="application",
            related_id=application.id,
            actor=actor,
            reply_to=finance.get_setting(db, "reply_to") or None,
        )


def complete_on_payment(db: Session, invoice: Invoice) -> None:
    """Hook: when an application's first invoice is fully paid, the person becomes a member."""
    application = db.scalar(select(Application).where(Application.invoice_id == invoice.id, Application.status == "approved"))
    if application is not None:
        _activate(db, application, "system")


finance.PAID_HOOKS.append(complete_on_payment)


def queue_counts(db: Session) -> dict[str, int]:
    return {
        "enquiries": db.scalar(select(func.count()).select_from(Enquiry).where(Enquiry.status.in_(["new", "contacted"]))) or 0,
        "applications": db.scalar(select(func.count()).select_from(Application).where(Application.status.in_(PENDING_APPLICATION))) or 0,
        "awaiting_payment": db.scalar(select(func.count()).select_from(Application).where(Application.status == "approved")) or 0,
    }


def stale_enquiries(db: Session, days: int = 3) -> list[Enquiry]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return list(db.scalars(select(Enquiry).where(Enquiry.status == "new", or_(Enquiry.last_action_at.is_(None), Enquiry.last_action_at < cutoff)).order_by(Enquiry.id)))
