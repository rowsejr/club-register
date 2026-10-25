"""Emailing groups of people: choose who by filter, list or tag, write once with merge fields, preview, send, and keep a record.

People who have asked not to be emailed, and people with no address, are always left out and reported. An address shared by a
family is emailed once. Nothing is sent until the mailing is checked and confirmed.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import finance, mailer, people_filters, ratelimit
from app.models import Contact, Invoice, Mailing, MailTemplate, Member, OutboundEmail

MERGE_FIELDS = {
    "first_name": "their first name",
    "name": "their full name",
    "membership_number": "their membership number",
    "membership_type": "their membership type",
    "expiry": "when their membership ends",
    "balance": "what they owe the club (for example £25.00)",
    "club": "the club's name",
}
STARTER_TEMPLATES = [
    ("Renewal reminder", "Time to renew your {club} membership", "Hello {first_name},\n\nYour {membership_type} membership runs to {expiry}. Please renew online (sign in with this email address) or reply and we will send you an invoice.\n\nThank you for being part of {club}."),
    ("AGM notice", "{club}: Annual General Meeting", "Dear {first_name},\n\nThe Annual General Meeting of {club} will be held on [date] at [place]. The agenda and last year's accounts are attached.\n\nAll members are welcome, and members vote.\n\nSee you there."),
    ("Social event", "{club}: [event name]", "Hello {first_name},\n\nWe would love to see you at [event] on [date]. Tickets are available from the club or by replying to this email.\n\n{club}"),
    ("Overdue balance", "A reminder from {club}", "Hello {first_name},\n\nOur records show {balance} is outstanding on your account. If you have already paid, thank you and please ignore this message; otherwise please pay by bank transfer quoting your invoice number.\n\n{club}"),
]
_FIELD = re.compile(r"\{([a-z_]+)\}")


def unsubscribe_token(db: Session, contact_id: int) -> str:
    """A link token only this server could have made, so nobody can unsubscribe someone else by guessing numbers."""
    sig = hmac.new(ratelimit._secret(db), f"unsubscribe:{contact_id}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{contact_id}.{sig}"


def contact_for_token(db: Session, token: str) -> Contact | None:
    head, _, sig = (token or "").partition(".")
    if not head.isdigit():
        return None
    expected = unsubscribe_token(db, int(head)).partition(".")[2]
    return db.get(Contact, int(head)) if hmac.compare_digest(expected, sig) else None


def unsubscribe_url(db: Session, contact: Contact) -> str:
    return f"{os.getenv('CLUB_BASE_URL', '').rstrip('/')}/unsubscribe/{unsubscribe_token(db, contact.id)}"


class MailingError(ValueError):
    """A rule of mailings, safe to show to the user."""


@dataclass
class Recipient:
    contact: Contact
    email: str
    subject: str
    body: str


@dataclass
class Preview:
    recipients: list[Recipient] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # "Name: reason"
    unknown_fields: list[str] = field(default_factory=list)


def seed_templates(db: Session) -> int:
    if db.scalar(select(MailTemplate.id).limit(1)) is not None:
        return 0
    for name, subject, body in STARTER_TEMPLATES:
        db.add(MailTemplate(name=name, subject=subject, body=body))
    db.flush()
    return len(STARTER_TEMPLATES)


def unknown_fields(text: str) -> list[str]:
    return sorted({f for f in _FIELD.findall(text) if f not in MERGE_FIELDS})


def render(text: str, values: dict[str, str]) -> str:
    """Fill in {merge_fields}. Anything that is not a known field (square-bracket placeholders, stray braces) is left as written."""
    return _FIELD.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def _values(db: Session, contact: Contact, club: str) -> dict[str, str]:
    member = db.get(Member, contact.member_id) if contact.member_id else None
    owed = 0
    if member or contact:
        for invoice in db.scalars(select(Invoice).where(Invoice.contact_id == contact.id, Invoice.status.in_(finance.OPEN))):
            owed += finance.outstanding(db, invoice)
    return {
        "first_name": contact.name.split()[0] if contact.name else "there",
        "name": contact.name,
        "membership_number": (member.membership_number or "") if member else "",
        "membership_type": (member.membership_type if member and member.membership_type != "standard" else "") if member else "",
        "expiry": member.expiry_date.strftime("%d %B %Y") if member and member.expiry_date else "",
        "balance": f"£{owed / 100:,.2f}",
        "club": club,
    }


def audience(db: Session, params: dict[str, str]) -> list[Contact]:
    """The contacts a filter describes (the same filter language as the Members and Contacts pages)."""
    f = people_filters.PeopleFilter(
        q=params.get("q", ""), status=params.get("status", "all"), type=params.get("type", ""), age=params.get("age", ""), list=params.get("list", ""), boat=params.get("boat", ""),
        unpaid=params.get("unpaid", ""), member=params.get("member", ""), tag=params.get("tag", ""),
    )
    return list(db.scalars(people_filters.contacts_statement(f)).all())


def preview(db: Session, mailing: Mailing) -> Preview:
    result = Preview(unknown_fields=unknown_fields(mailing.subject + " " + mailing.body))
    club = finance.get_setting(db, "club_name")
    footer = finance.get_setting(db, "mailing_footer") or f"You are receiving this because you are a member or friend of {club}. Reply to this email if you would rather not hear from us."
    seen: set[str] = set()
    for contact in audience(db, mailing.audience or {}):
        if contact.suppressed:
            result.skipped.append(f"{contact.name}: asked not to be emailed")
            continue
        if not contact.email:
            result.skipped.append(f"{contact.name}: no email address")
            continue
        key = contact.email.lower()
        if key in seen:
            result.skipped.append(f"{contact.name}: shares an address already included")
            continue
        seen.add(key)
        values = _values(db, contact, club)
        result.recipients.append(Recipient(contact, contact.email, render(mailing.subject, values), render(mailing.body, values) + "\n\n--\n" + footer + f"\nTo stop these emails: {unsubscribe_url(db, contact)}"))
    return result


def save_draft(db: Session, mailing_id: int | None, *, subject: str, body: str, audience_params: dict[str, str], actor: str) -> Mailing:
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise MailingError("A mailing needs a subject and some text")
    mailing = db.get(Mailing, mailing_id) if mailing_id else None
    if mailing_id and mailing is None:
        raise MailingError("Mailing not found")
    if mailing is not None and mailing.status != "draft":
        raise MailingError("This mailing has already been sent")
    if mailing is None:
        mailing = Mailing(subject=subject, body=body, audience={}, created_by=actor)
        db.add(mailing)
    mailing.subject, mailing.body = subject, body
    mailing.audience = {k: v for k, v in audience_params.items() if v and v != "all"}
    db.flush()
    return mailing


def send(db: Session, mailing: Mailing, actor: str) -> dict[str, int]:
    """Send a checked draft. Each message is logged like every other email; a mailing can only go out once."""
    if mailing.status != "draft":
        raise MailingError("This mailing has already been sent")
    ready = preview(db, mailing)
    if ready.unknown_fields:
        raise MailingError("Unknown merge field(s): " + ", ".join("{" + f + "}" for f in ready.unknown_fields))
    if not ready.recipients:
        raise MailingError("Nobody would receive this mailing: check the audience")
    sent = failed = 0
    reply_to = finance.get_setting(db, "reply_to") or None
    for recipient in ready.recipients:
        record: OutboundEmail = mailer.send_email(db, to=recipient.email, subject=recipient.subject, body=recipient.body, related_type="mailing", related_id=mailing.id, actor=actor, reply_to=reply_to)
        if record.status == "failed":
            failed += 1
            ready.skipped.append(f"{recipient.contact.name}: the email failed")
        else:
            sent += 1
    mailing.status, mailing.sent_at, mailing.sent_count, mailing.skipped = "sent", datetime.now(UTC), sent, ready.skipped
    db.flush()
    return {"sent": sent, "skipped": len(ready.skipped), "failed": failed}


def save_template(db: Session, name: str, subject: str, body: str) -> MailTemplate:
    name = " ".join(name.split())
    if not name or not subject.strip() or not body.strip():
        raise MailingError("A template needs a name, a subject and some text")
    template = db.scalar(select(MailTemplate).where(MailTemplate.name == name))
    if template is None:
        template = MailTemplate(name=name, subject="", body="")
        db.add(template)
    template.subject, template.body = subject.strip(), body.strip()
    db.flush()
    return template
