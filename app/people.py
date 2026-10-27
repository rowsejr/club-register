"""Working with groups of people: tags, lists, saved views, a whole-person view of a contact, and a data-quality check."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import finance
from app.models import (
    AuditLog,
    Boat,
    Contact,
    ContactList,
    ContactRelation,
    ContactTag,
    EmailChange,
    Invoice,
    ListMembership,
    Member,
    Membership,
    OutboundEmail,
    ParticipantProfile,
    PaymentTransaction,
    Qualification,
    SavedView,
    SessionRecord,
    StorageAllocation,
    StorageResource,
    TrainingBooking,
    TrainingCourse,
    UserRole,
)


class PeopleError(ValueError):
    """A rule about people data, safe to show to the user."""


# --------------------------------------------------------------------------- tags


def clean_tag(value: str) -> str:
    return " ".join((value or "").lower().replace(",", " ").split())[:60]


def tags_for(db: Session, contact_id: int) -> list[str]:
    return [t for (t,) in db.execute(select(ContactTag.tag).where(ContactTag.contact_id == contact_id).order_by(ContactTag.tag))]


def add_tag(db: Session, contact: Contact, tag: str) -> str:
    tag = clean_tag(tag)
    if not tag:
        raise PeopleError("A tag needs some text")
    if not db.scalar(select(ContactTag.id).where(ContactTag.contact_id == contact.id, ContactTag.tag == tag)):
        db.add(ContactTag(contact_id=contact.id, tag=tag))
        db.flush()
    return tag


def remove_tag(db: Session, contact: Contact, tag: str) -> None:
    for row in db.scalars(select(ContactTag).where(ContactTag.contact_id == contact.id, ContactTag.tag == clean_tag(tag))).all():
        db.delete(row)
    db.flush()


def all_tags(db: Session) -> list[tuple[str, int]]:
    return [(t, n) for t, n in db.execute(select(ContactTag.tag, func.count()).group_by(ContactTag.tag).order_by(ContactTag.tag))]


# --------------------------------------------------------------------------- lists


def lists_with_counts(db: Session) -> list[tuple[ContactList, int]]:
    counts = dict(db.execute(select(ListMembership.list_id, func.count()).group_by(ListMembership.list_id)).all())
    return [(lst, counts.get(lst.id, 0)) for lst in db.scalars(select(ContactList).order_by(ContactList.name))]


def create_list(db: Session, name: str) -> ContactList:
    name = " ".join(name.split())
    if not name:
        raise PeopleError("A list needs a name")
    if db.scalar(select(ContactList.id).where(func.lower(ContactList.name) == name.lower())):
        raise PeopleError(f"There is already a list called {name}")
    lst = ContactList(name=name)
    db.add(lst)
    db.flush()
    return lst


def add_to_list(db: Session, contact: Contact, lst: ContactList) -> bool:
    if db.scalar(select(ListMembership.id).where(ListMembership.contact_id == contact.id, ListMembership.list_id == lst.id)):
        return False
    db.add(ListMembership(contact_id=contact.id, list_id=lst.id))
    db.flush()
    return True


def remove_from_list(db: Session, contact: Contact, lst: ContactList) -> None:
    for row in db.scalars(select(ListMembership).where(ListMembership.contact_id == contact.id, ListMembership.list_id == lst.id)).all():
        db.delete(row)
    db.flush()


def list_members(db: Session, lst: ContactList) -> list[Contact]:
    return list(db.scalars(select(Contact).join(ListMembership, ListMembership.contact_id == Contact.id).where(ListMembership.list_id == lst.id).order_by(Contact.name)))


# --------------------------------------------------------------------------- saved views


def save_view(db: Session, scope: str, name: str, query: str, actor: str) -> SavedView:
    name = " ".join(name.split())
    if scope not in {"members", "contacts"}:
        raise PeopleError("Views belong to Members or Contacts")
    if not name:
        raise PeopleError("Give the view a name")
    if len(query) > 500:
        raise PeopleError("That filter is too long to save")
    view = db.scalar(select(SavedView).where(SavedView.scope == scope, func.lower(SavedView.name) == name.lower()))
    if view is None:
        view = SavedView(scope=scope, name=name, query="", created_by=actor)
        db.add(view)
    view.query = query
    db.flush()
    return view


def views_for(db: Session, scope: str) -> list[SavedView]:
    return list(db.scalars(select(SavedView).where(SavedView.scope == scope).order_by(SavedView.name)))


# --------------------------------------------------------------------------- the whole person


@dataclass
class Household:
    payer: Contact | None
    others: list[tuple[str, Contact]] = field(default_factory=list)  # (relation, contact)


@dataclass
class Person360:
    contact: Contact
    member: Member | None
    memberships: list[Membership]
    invoices: list[Invoice]
    balance: int
    paid_total: int
    boats: list[tuple[Boat, StorageResource | None]]
    bookings: list[tuple[TrainingBooking, TrainingCourse | None]]
    qualifications: list[Qualification]
    emails: list[OutboundEmail]
    tags: list[str]
    lists: list[ContactList]
    household: Household
    age: int | None
    profile: ParticipantProfile | None


def signed_in(db: Session, session: SessionRecord) -> tuple[Member | None, Contact | None]:
    """The member and contact behind a sign-in: whoever holds that email address. Every address belongs to one
    person (ensure_unique_email), so there is never a choice to make."""
    member = db.scalar(select(Member).where(func.lower(Member.email) == session.email.lower()).order_by(Member.id))
    if member is not None:
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id).order_by(Contact.id))
        return member, contact
    return None, db.scalar(select(Contact).where(func.lower(Contact.email) == session.email.lower()).order_by(Contact.id))


class EmailChangeError(ValueError):
    """Something the member can put right, shown to them as it is."""


def ensure_unique_email(db: Session, email: str | None, *, current: str | None = None, exclude_member_id: int | None = None, exclude_contact_id: int | None = None) -> None:
    """One email address belongs to one person (every household member has their own). Raise EmailChangeError if
    another member, contact or office sign-in already uses it. ``current`` is the address already on this record, if
    any: passing it skips the check when nothing is actually changing."""
    if not email:
        return
    email = email.strip().lower()
    if current and email == current.strip().lower():
        return
    if db.scalar(select(Member.id).where(func.lower(Member.email) == email, Member.id != (exclude_member_id or 0))):
        raise EmailChangeError("Another member already uses that email address. Give this person their own.")
    if db.scalar(select(Contact.id).where(func.lower(Contact.email) == email, Contact.id != (exclude_contact_id or 0))):
        raise EmailChangeError("Another person already uses that email address. Give this person their own.")
    if db.scalar(select(UserRole.id).where(func.lower(UserRole.email) == email)):
        raise EmailChangeError("That email address already signs in to the office as a volunteer. Check with an administrator.")


EMAIL_CHANGE_HOURS = 24
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def request_email_change(db: Session, member: Member, contact: Contact | None, new_email: str, actor: str) -> str:
    """Start changing a member's email address. Nothing changes until they click the link sent to the new address,
    which proves it is theirs. Returns the link's token (only for tests and the sandbox)."""
    from app import auth, mailer
    from app.security import expiry_after, issue_token

    new_email = (new_email or "").strip().lower()
    old = (member.email or (contact.email if contact else "") or "").lower()
    if not EMAIL_PATTERN.match(new_email) or len(new_email) > 255:
        raise EmailChangeError("That does not look like an email address")
    if new_email == old:
        raise EmailChangeError("That is already your email address")
    if auth.is_staff(db, actor) or (old and auth.is_staff(db, old)):
        raise EmailChangeError("Your sign-in also opens volunteer pages, so an administrator changes your email. Please use Ask the club.")
    taken = db.scalar(select(Member.id).where(func.lower(Member.email) == new_email, Member.id != member.id)) or db.scalar(select(UserRole.id).where(func.lower(UserRole.email) == new_email))
    if taken:
        raise EmailChangeError("Another member already uses that address. Please use Ask the club.")
    token, token_hash = issue_token()
    db.add(EmailChange(token_hash=token_hash, member_id=member.id, new_email=new_email, requested_by=actor, expires_at=expiry_after(EMAIL_CHANGE_HOURS * 60)))
    db.flush()
    club = finance.get_setting(db, "club_name")
    base = os.getenv("CLUB_BASE_URL", "").rstrip("/")
    mailer.send_email(db, to=new_email, subject=f"{club}: please confirm your new email address",
                      body=f"Hello {member.name.split()[0]},\n\nPlease confirm this is your new email address for {club} by opening this link within {EMAIL_CHANGE_HOURS} hours:\n\n{base}/me/email/{token}\n\nIf you did not ask for this, ignore this message and nothing will change.\n\n{club}",
                      stored_body="(confirmation link not stored)", related_type="email_change", related_id=member.id, actor=actor)
    if old:
        mailer.send_email(db, to=old, subject=f"{club}: a change to your email address was asked for",
                          body=f"Hello {member.name.split()[0]},\n\nSomeone signed in as you asked to change your email address for {club} to {new_email}. It only changes if that address confirms it.\n\nIf this was not you, please tell the membership secretary.\n\n{club}",
                          related_type="email_change", related_id=member.id, actor=actor)
    return token


def pending_email_change(db: Session, token: str) -> EmailChange | None:
    from app.auth import is_expired
    from app.security import hash_token

    change = db.scalar(select(EmailChange).where(EmailChange.token_hash == hash_token(token or "")))
    if change is None or change.used_at is not None or is_expired(change.expires_at):
        return None
    return change


def confirm_email_change(db: Session, token: str) -> tuple[Member, str]:
    """The member clicked the link at their new address: change it everywhere it is their sign-in, and sign out old sessions."""
    from app import audit, auth

    change = pending_email_change(db, token)
    member = db.get(Member, change.member_id) if change else None
    if change is None or member is None:
        raise EmailChangeError("This link has run out or has already been used. Please ask again from My details.")
    if db.scalar(select(Member.id).where(func.lower(Member.email) == change.new_email, Member.id != member.id)):
        raise EmailChangeError("Another member now uses that address. Please use Ask the club.")
    old = (member.email or "").lower()
    contact = db.scalar(select(Contact).where(Contact.member_id == member.id).order_by(Contact.id))
    before = audit.snapshot(contact, audit.CONTACT_FIELDS) if contact else {}
    member.email = change.new_email
    if contact is not None:
        old = old or (contact.email or "").lower()
        contact.email = change.new_email
    change.used_at = datetime.now(UTC)
    if old:
        auth.revoke_sessions(db, old)
    db.add(AuditLog(actor_email=change.new_email, actor_role="member", action="member.email_changed", target_type="member", target_id=str(member.id),
                    details={"from": old, "to": change.new_email, "changes": audit.changes(before, audit.snapshot(contact, audit.CONTACT_FIELDS)) if contact else {}}))
    db.flush()
    return member, change.new_email


def age_on(born: date | None, today: date | None = None) -> int | None:
    if born is None:
        return None
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def person360(db: Session, contact: Contact) -> Person360:
    member = db.get(Member, contact.member_id) if contact.member_id else None
    memberships = list(db.scalars(select(Membership).where(Membership.member_id == member.id).order_by(Membership.starts_on.desc()))) if member else []
    owned = Invoice.contact_id == contact.id
    if member:
        owned = owned | (Invoice.member_id == member.id)
    invoices = list(db.scalars(select(Invoice).where(owned, Invoice.status.not_in(["void", "cancelled"])).order_by(Invoice.issue_date.desc().nulls_first(), Invoice.id.desc())))
    balance = sum(finance.outstanding(db, inv) for inv in invoices if inv.status in finance.OPEN)
    paid = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(PaymentTransaction.invoice_id.in_([i.id for i in invoices] or [0]), PaymentTransaction.status == "received", PaymentTransaction.payment_method != "credit_note")) or 0
    boats = []
    for boat in db.scalars(select(Boat).where(Boat.owner_contact_id == contact.id, Boat.status == "active").order_by(Boat.name)):
        allocation = db.scalars(select(StorageAllocation).where(StorageAllocation.boat_id == boat.id, StorageAllocation.status == "active").order_by(StorageAllocation.end_date.desc())).first()
        boats.append((boat, db.get(StorageResource, allocation.resource_id) if allocation else None))
    mine = TrainingBooking.contact_id == contact.id
    if contact.member_id:
        mine = or_(mine, TrainingBooking.member_id == contact.member_id)
    bookings = [(b, db.get(TrainingCourse, b.course_id)) for b in db.scalars(select(TrainingBooking).where(mine).order_by(TrainingBooking.id.desc()))]
    quals = list(db.scalars(select(Qualification).where(Qualification.contact_id == contact.id).order_by(Qualification.name)))
    emails = list(db.scalars(select(OutboundEmail).where(func.lower(OutboundEmail.to_address) == (contact.email or "\0").lower(), OutboundEmail.related_type != "sign_in").order_by(OutboundEmail.id.desc()).limit(15))) if contact.email else []
    lists = list(db.scalars(select(ContactList).join(ListMembership, ListMembership.list_id == ContactList.id).where(ListMembership.contact_id == contact.id).order_by(ContactList.name)))
    return Person360(contact, member, memberships, invoices, balance, paid, boats, bookings, quals, emails, tags_for(db, contact.id), lists, household_of(db, contact), age_on(contact.birth_date), db.get(ParticipantProfile, contact.id))


# --------------------------------------------------------------------------- households


RELATIONS = ("partner", "child", "parent", "guardian", "other")


def household_of(db: Session, contact: Contact) -> Household:
    """The people this contact is linked to. The member holding the membership is the payer."""
    links = db.scalars(select(ContactRelation).where((ContactRelation.from_contact_id == contact.id) | (ContactRelation.to_contact_id == contact.id))).all()
    others: list[tuple[str, Contact]] = []
    for link in links:
        other_id = link.to_contact_id if link.from_contact_id == contact.id else link.from_contact_id
        other = db.get(Contact, other_id)
        if other is not None:
            others.append((link.relation_type if link.from_contact_id == contact.id else _inverse(link.relation_type), other))
    payer = contact if contact.member_id else next((c for _, c in others if c.member_id), None)
    return Household(payer, sorted(others, key=lambda pair: pair[1].name))


def _inverse(relation: str) -> str:
    return {"child": "parent", "parent": "child"}.get(relation, relation)


def link_people(db: Session, a: Contact, b: Contact, relation: str) -> None:
    if a.id == b.id:
        raise PeopleError("A person cannot be linked to themselves")
    if relation not in RELATIONS:
        raise PeopleError(f"Unknown relationship '{relation}'")
    exists = db.scalar(
        select(ContactRelation.id).where(
            ((ContactRelation.from_contact_id == a.id) & (ContactRelation.to_contact_id == b.id)) | ((ContactRelation.from_contact_id == b.id) & (ContactRelation.to_contact_id == a.id))
        )
    )
    if exists:
        raise PeopleError(f"{a.name} and {b.name} are already linked")
    db.add(ContactRelation(from_contact_id=a.id, to_contact_id=b.id, relation_type=relation))
    db.flush()


def unlink_people(db: Session, a: Contact, b: Contact) -> None:
    for link in db.scalars(select(ContactRelation).where(((ContactRelation.from_contact_id == a.id) & (ContactRelation.to_contact_id == b.id)) | ((ContactRelation.from_contact_id == b.id) & (ContactRelation.to_contact_id == a.id)))).all():
        db.delete(link)
    db.flush()


# --------------------------------------------------------------------------- data quality


@dataclass
class Issue:
    key: str
    title: str
    help: str
    contacts: list[Contact] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)  # for issues that are not about one contact


def data_issues(db: Session, today: date | None = None) -> list[Issue]:
    """Things that quietly cause trouble later: members who cannot be emailed, shared addresses, juniors without consent."""
    today = today or date.today()
    active = {m.id: m for m in db.scalars(select(Member).where(Member.active.is_(True)))}
    contacts = db.scalars(select(Contact).where(Contact.member_id.in_(active.keys()))).all() if active else []
    pairs = [(c, active[c.member_id]) for c in contacts if c.member_id is not None]  # each active member's contact, with the member
    issues: list[Issue] = []

    issues.append(Issue("no_email", "Active members with no email address", "They cannot be emailed invoices, reminders or notices: these go on paper.", [c for c in contacts if not c.email]))
    by_email: dict[str, list[Contact]] = defaultdict(list)
    for c in db.scalars(select(Contact).where(Contact.email.is_not(None), Contact.email != "")):
        by_email[c.email.lower()].append(c)  # type: ignore[union-attr]
    shared = [c for group in by_email.values() if len(group) > 1 for c in group]
    issues.append(Issue("shared_email", "People sharing an email address", "Often a family. They cannot sign in separately; give each adult their own address where you can.", shared))

    names: dict[str, list[Contact]] = defaultdict(list)
    for c in db.scalars(select(Contact)):
        names[" ".join(c.name.lower().split())].append(c)
    issues.append(Issue("duplicate_name", "Possible duplicates (same name)", "Check whether these are the same person entered twice.", [c for group in names.values() if len(group) > 1 for c in group]))

    issues.append(Issue("no_number", "Active members with no membership number", "They will not appear in number-based exports.", [c for c, m in pairs if not m.membership_number]))
    issues.append(Issue("no_type", "Active members with no real membership type", "The type is blank or 'standard', so they cannot be renewed or billed.", [c for c, m in pairs if (m.membership_type or "standard") in {"standard", "unknown", ""}]))
    issues.append(Issue("expired", "Active members whose membership has ended", "Marked active but past their expiry date: lapse them or renew them.", [c for c, m in pairs if m.expiry_date and m.expiry_date < today]))

    juniors = [c for c in contacts if (age_on(c.birth_date, today) or 99) < 18]
    profiles = {p.contact_id: p for p in db.scalars(select(ParticipantProfile))}
    issues.append(
        Issue("junior_consent", "Juniors without valid consent and emergency details", "Needed before they can join a course or go afloat.", [c for c in juniors if c.id not in profiles or not (profiles[c.id].consent_valid_to or date.min) >= today])
    )
    no_owner = [f"{b.name}" for b in db.scalars(select(Boat).where(Boat.status == "active", Boat.owner_contact_id.is_(None), Boat.owner_member_id.is_(None), func.coalesce(Boat.is_club_boat, False).is_(False)))]
    issues.append(Issue("boat_no_owner", "Boats with no owner", "They cannot be billed for a space.", [], no_owner))
    return issues
