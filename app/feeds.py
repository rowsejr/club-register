"""Read-only copies of club data for the club website.

Nothing here can change anything in the office. The website has its own key and may only read the feeds listed
below. (Phase 1 also had a key for a Google Sheet; that Sheet is retired — Phase 2 decisions D3/D4 — because the
website now feeds its own Sheet, and money never leaves the office. Remove ``sheet:...`` from ``CLUB_FEED_KEYS``.)

Keys (environment): ``CLUB_FEED_KEYS="website:<long random secret>"``

A tool asks for a feed with a signed address::

    GET /feeds/<name>.json?client=website&ts=<unix seconds>&nonce=<random text>&sig=<hex>

    sig = HMAC-SHA256(secret, "<name>\\n<client>\\n<ts>\\n<nonce>")

The office refuses a request that is more than five minutes old, one whose nonce it has already seen (so a copied
address cannot be used again), and one with the wrong signature. Each feed is a table: ``columns`` and ``rows``, ready to
write straight into a spreadsheet.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import events, training
from app.models import (
    Boat,
    BoatDesign,
    Contact,
    ContactRelation,
    Member,
    Role,
    TrainingCourse,
    UserRole,
    WebhookEvent,
)

MAX_AGE_SECONDS = 300

# feed name: (what it is, which tools may read it)
FEEDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "events": ("Coming events, with booking links", ("website",)),
    "courses": ("Courses open for booking, with booking links", ("website",)),
    "boats": ("Boats with their owner, class, sail number and Portsmouth Yardstick", ("website",)),
    "directory": ("Members who agreed to be in the members' directory", ("website",)),
    "siteMembers": ("Every current member, keyed by the office's member id", ("website",)),
}

# How hats are named in feeds. The base roles Admin and Committee come first.
HAT_NAMES = {"treasurer": "Treasurer", "secretary": "Secretary", "bosun": "Bosun", "social": "Social", "training": "Training", "race_box": "Race Box"}


class FeedRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def keys() -> dict[str, str]:
    out: dict[str, str] = {}
    for item in os.getenv("CLUB_FEED_KEYS", "").split(","):
        client, _, secret = item.strip().partition(":")
        if client and len(secret) >= 16:
            out[client] = secret
    return out


def sign_parts(secret: str, *parts: str) -> str:
    """HMAC-SHA256 of the parts joined by new lines, as lower-case hex. Every signed request to the office uses this."""
    return hmac.new(secret.encode(), "\n".join(parts).encode(), hashlib.sha256).hexdigest()


def sign(secret: str, name: str, client: str, ts: str, nonce: str) -> str:
    return sign_parts(secret, name, client, ts, nonce)


def fresh(ts: str, max_age: int, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return ts.isdigit() and abs(now - int(ts)) <= max_age


def remember(db: Session, provider: str, nonce: str, what: str = "") -> None:
    """Note a request id so the same signed request cannot be used twice. Ids are forgotten after a day."""
    provider = provider[:20]
    db.execute(delete(WebhookEvent).where(WebhookEvent.provider == provider, WebhookEvent.received_at < datetime.now(UTC) - timedelta(days=1)))
    if not (8 <= len(nonce) <= 100) or db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == provider, WebhookEvent.event_id == nonce)):
        raise FeedRefused(401, "this request has already been used")
    db.add(WebhookEvent(provider=provider, event_id=nonce, outcome=what[:120] or None))
    db.flush()


def check(db: Session, name: str, client: str, ts: str, nonce: str, sig: str, now: float | None = None) -> None:
    """Refuse anything that is not a fresh, correctly signed request from a tool allowed to read this feed."""
    if name not in FEEDS:
        raise FeedRefused(404, "no such feed")
    secret = keys().get(client)
    if secret is None or client not in FEEDS[name][1]:
        raise FeedRefused(403, "this tool may not read this feed")
    if not fresh(ts, MAX_AGE_SECONDS, now):
        raise FeedRefused(401, "the request is too old (check the clock)")
    if not hmac.compare_digest(sign(secret, name, client, ts, nonce), sig or ""):
        raise FeedRefused(401, "bad signature")
    remember(db, f"feed:{client}", nonce, name)


def _pounds(minor: int | None) -> float:
    return round((minor or 0) / 100, 2)


def _day(value: date | datetime | None) -> str:
    return value.date().isoformat() if isinstance(value, datetime) else (value.isoformat() if value else "")


def _when(value: datetime | None) -> str:
    """A time with its UTC offset. SQLite hands back times without one; they were stored in UTC."""
    if value is None:
        return ""
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat(timespec="seconds")


def _events(db: Session, today: date, base_url: str) -> tuple[list[str], list[list[Any]]]:
    rows = []
    for event in events.upcoming(db, limit=200, published_only=True, now=datetime.combine(today, datetime.min.time(), tzinfo=UTC)):
        prices = {t.audience: t.price_minor for t in events.ticket_types(db, event, only_active=True)}
        free = events.availability(db, event).free
        on_sale, _why = events.sales_state(event)
        start, end = events._aware(event.start_at) or event.start_at, events._aware(event.end_at)
        rows.append([event.id, event.title, start.isoformat(), end.isoformat() if end else "",
                     event.location or "", events.event_kind(event.event_type), "" if "members" not in prices and "all" not in prices else _pounds(prices.get("members", prices.get("all"))),
                     "" if "public" not in prices and "all" not in prices else _pounds(prices.get("public", prices.get("all"))),
                     "" if free is None else free, f"{base_url}/whats-on/{event.id}" if on_sale else "", "no" if event.website_show is False else "yes"])
    return ["Id", "Title", "Starts", "Ends", "Where", "Kind", "Member price (£)", "Visitor price (£)", "Places left", "Book at", "Website show"], rows


def _boats(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    """Every boat, one row per boat, active or not (see Status). No spaces or berths: those stay in the office."""
    designs = {d.id: d for d in db.scalars(select(BoatDesign))}
    rows = []
    for boat in db.scalars(select(Boat).order_by(Boat.name, Boat.id)):
        owner = db.get(Contact, boat.owner_contact_id) if boat.owner_contact_id else None
        if owner is None and boat.owner_member_id:
            owner = db.scalar(select(Contact).where(Contact.member_id == boat.owner_member_id))
        design = designs.get(boat.design_id or 0)
        number = boat.portsmouth_number or (design.portsmouth_rating if design else None)
        club = bool(boat.is_club_boat)
        owner_member = None if club else (boat.owner_member_id or (owner.member_id if owner else None))
        rows.append([boat.id, boat.name, boat.boat_class or (design.name if design else ""), boat.sail_number or "", number or "", owner.name if owner and not club else ("club" if club else ""),
                     boat.id, owner_member or "", "yes" if club else "no", boat.status or "", _day(boat.insurance_expiry), _when(boat.updated_at), boat.external_ref or ""])
    return ["Id", "Name", "Class", "Sail number", "Portsmouth number", "Owner", "Boat id", "Owner member id", "Club boat", "Status", "Insurance until", "Updated", "External ref"], rows


def _courses(db: Session, today: date, base_url: str) -> tuple[list[str], list[list[Any]]]:
    rows = []
    start = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    for course in db.scalars(select(TrainingCourse).where(TrainingCourse.starts_at >= start, TrainingCourse.status == "open").order_by(TrainingCourse.starts_at)):
        _taken, free, _waiting = training.seats(db, course)
        rows.append([course.id, course.name, _when(course.starts_at), _when(course.ends_at), course.venue or "", course.level or "", _pounds(course.price_minor),
                     free, course.min_age or "", f"{base_url}/courses#course-{course.id}"])
    return ["Course id", "Title", "Starts", "Ends", "Where", "Level", "Price (£)", "Places left", "Minimum age", "Book at"], rows


def hats_label(role: UserRole | None) -> str:
    """The office roles someone holds, by name: Admin or Committee first, then hats. Empty for an ordinary member."""
    if role is None:
        return ""
    names = [role.role.value] if role.role in (Role.ADMIN, Role.COMMITTEE) else []
    worn = {h.strip().lower() for h in (role.extra_roles or "").split(",")}
    names += [label for key, label in HAT_NAMES.items() if key in worn]
    return ", ".join(names)


def _households(db: Session) -> dict[int, int]:
    """Member id -> household id: the lowest member id among people linked as family in the office. Each person has
    their own email address now (Phase 2, D6), so this groups by family link only, never by a shared address."""
    parent: dict[tuple[str, int], tuple[str, int]] = {}

    def find(node: tuple[str, int]) -> tuple[str, int]:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def join(a: tuple[str, int], b: tuple[str, int]) -> None:
        parent[find(a)] = find(b)

    for member_id in db.scalars(select(Member.id)):
        find(("m", member_id))
    for contact_id, linked_member in db.execute(select(Contact.id, Contact.member_id)):
        find(("c", contact_id))
        if linked_member:
            join(("c", contact_id), ("m", linked_member))
    for a, b in db.execute(select(ContactRelation.from_contact_id, ContactRelation.to_contact_id)):
        join(("c", a), ("c", b))
    lowest: dict[tuple[str, int], int] = {}
    for node in list(parent):
        if node[0] == "m":
            root = find(node)
            lowest[root] = min(lowest.get(root, node[1]), node[1])
    return {node[1]: lowest[find(node)] for node in parent if node[0] == "m"}


def _site_members(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    """Every current member: active, a membership that ended in the last 13 months, joined in that time, or a new
    member waiting to pay. No bank, invoice, medical, consent, birth date or notes."""
    since = today - timedelta(days=400)
    roles = {r.email.lower(): r for r in db.scalars(select(UserRole))}
    households = _households(db)
    rows = []
    query = select(Member).where((Member.active.is_(True)) | (Member.expiry_date >= since) | (Member.join_date >= since) | (Member.renewal_status == "pending"))
    for member in db.scalars(query.order_by(Member.name, Member.id)):
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id).order_by(Contact.id))
        email = member.email or (contact.email if contact else None) or ""
        updated = max((t if t.tzinfo else t.replace(tzinfo=UTC) for t in (member.updated_at, contact.updated_at if contact else None) if t is not None), default=None)
        rows.append([member.id, member.name, email, (contact.phone if contact else None) or "", member.membership_type or "",
                     "active" if member.active else (member.renewal_status or "lapsed"), _day(member.expiry_date), households.get(member.id, member.id),
                     hats_label(roles.get(email.lower())) if email else "", _when(updated), member.committee_post or "", member.external_ref or ""])
    return ["Member id", "Name", "Email", "Mobile", "Membership", "Status", "Until", "Household", "Hats", "Updated", "Committee post", "External ref"], rows


def _directory(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    rows = []
    for contact, member in db.execute(select(Contact, Member).join(Member, Member.id == Contact.member_id).where(Member.active.is_(True), Contact.directory_consent.is_(True)).order_by(Contact.name)):
        rows.append([contact.name, member.membership_type or "", contact.email or "", contact.phone or ""])
    return ["Name", "Membership", "Email", "Phone"], rows


def build(db: Session, name: str, today: date | None = None, base_url: str = "") -> dict[str, Any]:
    today = today or date.today()
    base_url = (base_url or os.getenv("CLUB_BASE_URL", "")).rstrip("/")
    makers = {"boats": _boats, "directory": _directory, "siteMembers": _site_members}
    if name == "events":
        columns, rows = _events(db, today, base_url)
    elif name == "courses":
        columns, rows = _courses(db, today, base_url)
    else:
        columns, rows = makers[name](db, today)
    return {"feed": name, "title": FEEDS[name][0], "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "columns": columns, "rows": rows}
