"""The everyday office: each volunteer's to-do list, the new-members pipeline, members' requests, the chase list and the RYA census.

These pages are where most volunteers spend their time, so everything here answers one question: what needs doing next?
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import applications, digests, finance, mailer, people
from app.models import (
    Application,
    AuditLog,
    ChaseNote,
    Contact,
    ContactRelation,
    Enquiry,
    Invoice,
    Member,
    MemberRequest,
    OutboundEmail,
    UserRole,
)

# kind: (what the member sees, who looks after it, the permission that lets a volunteer close it)
REQUEST_KINDS: dict[str, tuple[str, str, str]] = {
    "change_membership": ("Change my membership type", "secretary", "members.write"),
    "boat_space": ("Ask for a boat park space", "bosun", "boats.write"),
    "change_boat": ("Tell you about a new or changed boat", "bosun", "boats.write"),
    "bosun": ("Report something to the Bosun", "bosun", "boats.write"),
    "other": ("Something else", "secretary", "members.write"),
}
ROLE_NAMES = {"secretary": "membership secretary", "bosun": "Bosun", "treasurer": "treasurer"}


class OfficeError(ValueError):
    """Safe to show to the person."""


# --------------------------------------------------------------------------- the to-do list


@dataclass
class TodoSection:
    title: str
    lines: list[digests.Line] = field(default_factory=list)


def todo(db: Session, perms: set[str], hats: list[str], is_admin: bool, today: date | None = None) -> list[TodoSection]:
    """What needs doing, grouped by the volunteer roles this person holds. Only lines with something in them are shown."""
    today = today or date.today()
    sections = []
    for hat, builder in digests.BUILDERS.items():
        if not (is_admin or hat in hats):
            continue
        digest = builder(db, today)
        lines = list(digest.todo)
        for kind, (label, role, _perm) in REQUEST_KINDS.items():
            if role != hat:
                continue
            count = db.scalar(select(func.count()).select_from(MemberRequest).where(MemberRequest.kind == kind, MemberRequest.status == "open")) or 0
            if count:
                lines.append(digests.Line(f"requests from members: {label.lower()}", count, "/app/requests", True))
        if lines:
            lines.sort(key=lambda line: not line.urgent)
            sections.append(TodoSection(digest.title, lines))
    return sections


# --------------------------------------------------------------------------- new members, start to finish


@dataclass
class Pipeline:
    enquiries: list[Enquiry]
    to_decide: list[Application]
    awaiting_payment: list[Application]
    joined: list[Application]


def pipeline(db: Session, today: date | None = None) -> Pipeline:
    today = today or date.today()
    since = datetime.combine(today - timedelta(days=60), datetime.min.time(), tzinfo=UTC)
    return Pipeline(
        enquiries=list(db.scalars(select(Enquiry).where(Enquiry.status.in_(["new", "contacted"])).order_by(Enquiry.created_at))),
        to_decide=list(db.scalars(select(Application).where(Application.status.in_(applications.PENDING_APPLICATION)).order_by(Application.submitted_at))),
        awaiting_payment=list(db.scalars(select(Application).where(Application.status == "approved").order_by(Application.decided_at))),
        joined=list(db.scalars(select(Application).where(Application.status == "completed", Application.decided_at >= since).order_by(Application.decided_at.desc()))),
    )


# --------------------------------------------------------------------------- requests from members


def _role_emails(db: Session, hat: str) -> list[str]:
    return [r.email for r in db.scalars(select(UserRole)) if hat in [h.strip() for h in (r.extra_roles or "").split(",")]]


def create_request(db: Session, contact: Contact, kind: str, details: str, *, member_id: int | None = None, boat_id: int | None = None) -> MemberRequest:
    if kind not in REQUEST_KINDS:
        raise OfficeError("Choose what you would like")
    details = (details or "").strip()
    if len(details) < 3:
        raise OfficeError("Please tell us a little more")
    request = MemberRequest(contact_id=contact.id, member_id=member_id, kind=kind, details=details[:4000], boat_id=boat_id)
    db.add(request)
    db.flush()
    label, role, _perm = REQUEST_KINDS[kind]
    club = finance.get_setting(db, "club_name")
    for email in _role_emails(db, role):
        mailer.send_email(db, to=email, subject=f"{club}: {contact.name} asks: {label.lower()}", body=f"{contact.name} has asked: {label}.\n\n{details}\n\nSee it in the club office under Home.",
                          related_type="member_request", related_id=request.id, actor="system")
    db.add(AuditLog(actor_email=contact.email or "member", actor_role="member", action="request.create", target_type="member_request", target_id=str(request.id), details={"kind": kind}))
    return request


def requests_for(db: Session, perms: set[str], status: str = "open") -> list[tuple[MemberRequest, Contact | None]]:
    from app import permissions

    kinds = [k for k, (_l, _r, perm) in REQUEST_KINDS.items() if permissions.allows(perms, perm)]
    rows = db.scalars(select(MemberRequest).where(MemberRequest.kind.in_(kinds), MemberRequest.status == status).order_by(MemberRequest.created_at)).all()
    return [(r, db.get(Contact, r.contact_id)) for r in rows]


def close_request(db: Session, request: MemberRequest, status: str, outcome: str, actor: str) -> None:
    if status not in {"done", "declined"}:
        raise OfficeError("Choose done or declined")
    if request.status != "open":
        raise OfficeError("This request has already been dealt with")
    request.status, request.outcome, request.handled_by, request.handled_at = status, outcome.strip()[:500] or None, actor, datetime.now(UTC)
    contact = db.get(Contact, request.contact_id)
    if contact is not None and contact.email:
        label = REQUEST_KINDS[request.kind][0]
        word = "done" if status == "done" else "not possible this time"
        body = f"Hello {contact.name.split()[0]},\n\nYou asked: {label}. That is {word}." + (f"\n\n{request.outcome}" if request.outcome else "") + f"\n\n{finance.get_setting(db, 'club_name')}"
        mailer.send_email(db, to=contact.email, subject=f"Your request: {label.lower()}", body=body, related_type="member_request", related_id=request.id, actor=actor)
    db.add(AuditLog(actor_email=actor, actor_role="user", action=f"request.{status}", target_type="member_request", target_id=str(request.id), details={"outcome": request.outcome or ""}))


# --------------------------------------------------------------------------- the chase list


@dataclass
class Debtor:
    contact: Contact
    invoices: list[tuple[Invoice, int]]  # (invoice, still owed)
    owed: int
    oldest_due: date | None
    days_overdue: int
    last_chased: date | None
    note: ChaseNote | None


def _payer(db: Session, invoice: Invoice) -> Contact | None:
    if invoice.contact_id:
        return db.get(Contact, invoice.contact_id)
    if invoice.member_id:
        return db.scalar(select(Contact).where(Contact.member_id == invoice.member_id))
    return None


def chase_list(db: Session, today: date | None = None) -> list[Debtor]:
    """Everyone who owes the club money, those owing longest first. It never empties by itself: a person stays on it until they have paid."""
    today = today or date.today()
    chased: dict[int, datetime] = {}
    for related_id, created in db.execute(select(OutboundEmail.related_id, func.max(OutboundEmail.created_at)).where(OutboundEmail.related_type == "invoice_reminder").group_by(OutboundEmail.related_id)):
        if related_id is not None:
            chased[related_id] = created
    grouped: dict[int, list[tuple[Invoice, int]]] = defaultdict(list)
    contacts: dict[int, Contact] = {}
    for invoice in db.scalars(select(Invoice).where(Invoice.status.in_(finance.OPEN), func.coalesce(Invoice.kind, "invoice") == "invoice")):
        owed = finance.outstanding(db, invoice)
        payer = _payer(db, invoice)
        if owed <= 0 or payer is None:
            continue
        grouped[payer.id].append((invoice, owed))
        contacts[payer.id] = payer
    notes: dict[int, ChaseNote] = {}
    for note in db.scalars(select(ChaseNote).where(ChaseNote.contact_id.in_(list(grouped) or [0])).order_by(ChaseNote.id)):
        notes[note.contact_id] = note
    out = []
    for cid, rows in grouped.items():
        dues = [i.due_date for i, _ in rows if i.due_date]
        oldest = min(dues) if dues else None
        last = max((chased[i.id] for i, _ in rows if i.id in chased), default=None)
        out.append(Debtor(contacts[cid], sorted(rows, key=lambda r: r[0].due_date or today), sum(o for _, o in rows), oldest, max(0, (today - oldest).days) if oldest else 0,
                          last.date() if last else None, notes.get(cid)))
    return sorted(out, key=lambda d: (-d.days_overdue, -d.owed, d.contact.name))


def add_chase_note(db: Session, contact: Contact, body: str, author: str) -> ChaseNote:
    body = (body or "").strip()
    if not body:
        raise OfficeError("Write the note first")
    note = ChaseNote(contact_id=contact.id, body=body[:1000], author=author)
    db.add(note)
    db.flush()
    return note


# --------------------------------------------------------------------------- the RYA census


GENDERS = [("female", "Female"), ("male", "Male"), ("other", "Other"), ("not_given", "Not given")]


def parse_bands(text: str) -> list[tuple[int, int]]:
    bands = []
    for part in (text or "").split(","):
        lo, _, hi = part.strip().partition("-")
        if lo.strip().isdigit() and hi.strip().isdigit() and int(lo) <= int(hi):
            bands.append((int(lo), int(hi)))
    return bands or [(0, 17), (18, 120)]


def category(type_name: str | None) -> str:
    """The membership category a type counts under for the RYA return."""
    name = (type_name or "").lower()
    if "honorary" in name:
        return "Honorary"
    if "non" in name and "sailing" in name:
        return "Non-sailing"
    if "student" in name:
        return "Student"
    if "family" in name or "+" in name and "c" in name.split("+")[-1]:
        return "Family"
    if any(word in name for word in ("temp", "month", "course")):
        return "Short term"
    return "Adult sailing"


@dataclass
class Census:
    on: date
    bands: list[tuple[int, int]]
    by_band: dict[str, dict[str, int]]  # band label -> gender -> count
    by_category: dict[str, dict[str, int]]  # category -> band label -> count
    missing_dob: list[Contact]
    missing_gender: list[Contact]
    total: int


def _band_label(band: tuple[int, int]) -> str:
    return f"{band[0]} to {band[1]}" if band[1] < 120 else f"{band[0]} and over"


def census(db: Session, on: date | None = None) -> Census:
    """Everyone a current membership covers, counted by age band, gender and membership category, on one day."""
    on = on or date.today()
    bands = parse_bands(finance.get_setting(db, "census_age_bands"))
    members = {m.id: m for m in db.scalars(select(Member).where(Member.active.is_(True)))}
    people_rows: dict[int, tuple[Contact, str]] = {}
    for contact in db.scalars(select(Contact).where(Contact.member_id.in_(list(members) or [0]))):
        people_rows[contact.id] = (contact, members[contact.member_id].membership_type)  # type: ignore[index]
    for relation in db.scalars(select(ContactRelation).where(ContactRelation.from_contact_id.in_(list(people_rows) or [0]), ContactRelation.relation_type == "child")):
        child = db.get(Contact, relation.to_contact_id)
        if child is not None and child.id not in people_rows:
            people_rows[child.id] = (child, people_rows[relation.from_contact_id][1])
    labels = [_band_label(b) for b in bands] + ["Age not known"]
    by_band = {label: {g: 0 for g, _ in GENDERS} for label in labels}
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: {label: 0 for label in labels})
    missing_dob, missing_gender = [], []
    for contact, type_name in people_rows.values():
        age = people.age_on(contact.birth_date, on)
        label = next((_band_label(b) for b in bands if age is not None and b[0] <= age <= b[1]), "Age not known")
        gender = contact.gender if contact.gender in dict(GENDERS) else "not_given"
        by_band[label][gender] += 1
        by_category[category(type_name)][label] += 1
        if contact.birth_date is None:
            missing_dob.append(contact)
        if not contact.gender:
            missing_gender.append(contact)
    return Census(on, bands, by_band, dict(by_category), sorted(missing_dob, key=lambda c: c.name), sorted(missing_gender, key=lambda c: c.name), len(people_rows))
