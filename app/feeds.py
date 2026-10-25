"""Read-only copies of club data for other tools: the committee's Google Sheet and the club website.

Nothing here can change anything in the office. Each tool has its own key and may only read the feeds listed for it.

Keys (environment): ``CLUB_FEED_KEYS="sheet:<long random secret>,website:<another one>"``

A tool asks for a feed with a signed address::

    GET /feeds/<name>.json?client=sheet&ts=<unix seconds>&nonce=<random text>&sig=<hex>

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

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import events, finance, office
from app.models import (
    Boat,
    BoatDesign,
    Contact,
    Event,
    EventTicket,
    Invoice,
    Member,
    TicketType,
    WebhookEvent,
)

MAX_AGE_SECONDS = 300

# feed name: (what it is, which tools may read it)
FEEDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "chase": ("Who owes money, longest late first", ("sheet",)),
    "payments": ("Money received in the last 13 months", ("sheet",)),
    "members": ("Members, with no contact details", ("sheet",)),
    "bookings": ("Places booked on coming events", ("sheet",)),
    "census": ("RYA census counts by age band and gender", ("sheet",)),
    "money": ("Money summary", ("sheet",)),
    "events": ("Coming events, with booking links", ("sheet", "website")),
    "boats": ("Boats with their owner, class, sail number and Portsmouth Yardstick", ("sheet", "website")),
    "directory": ("Members who agreed to be in the members' directory", ("website",)),
}


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


def _chase(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    rows = [[d.contact.name, _pounds(d.owed), d.days_overdue, ", ".join(i.reference for i, _ in d.invoices), _day(d.last_chased), d.note.body if d.note else "",
             {"collecting": "being collected", "set up": "yes"}.get(d.direct_debit, "")] for d in office.chase_list(db, today)]
    return ["Name", "Owes (£)", "Days late", "Invoices", "Last chased", "Latest note", "Direct Debit"], rows


def _payments(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    rows = [[p["date"], p["invoice"], p["contact"], p["method"], _pounds(p["amount"])] for p in finance.payment_lines(db, today - timedelta(days=400), today)]
    return ["Date", "Invoice", "Name", "How", "Amount (£)"], rows


def _members(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    rows = []
    for member in db.scalars(select(Member).where((Member.active.is_(True)) | (Member.expiry_date >= today - timedelta(days=400))).order_by(Member.name)):
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
        rows.append([member.name, member.membership_number or "", member.membership_type or "", "active" if member.active else (member.renewal_status or "lapsed"),
                     _day(member.join_date), _day(member.expiry_date), office.money_status(db, contact, today).words])
    return ["Name", "Number", "Membership", "Status", "Joined", "Until", "Money"], rows


def _bookings(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    start = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    query = (select(EventTicket, Event, Contact).join(Event, Event.id == EventTicket.event_id).join(Contact, Contact.id == EventTicket.contact_id)
             .where(Event.start_at >= start, EventTicket.status.in_(("held", "booked", "waiting"))).order_by(Event.start_at, EventTicket.id))
    types = {t.id: t.name for t in db.scalars(select(TicketType))}
    rows = []
    for ticket, event, contact in db.execute(query):
        invoice = db.get(Invoice, ticket.invoice_id) if ticket.invoice_id else None
        paid = "free" if invoice is None else ("paid" if invoice.status == "paid" else "not paid yet")
        rows.append([event.title, _day(event.start_at), ticket.holder_name or contact.name, types.get(ticket.ticket_type_id or 0, ""), ticket.status, paid])
    return ["Event", "Date", "Name", "Ticket", "Place", "Paid"], rows


def _census(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    counts = office.census(db, today)
    rows = [[band, *[by_gender[g] for g, _ in office.GENDERS], sum(by_gender.values())] for band, by_gender in counts.by_band.items()]
    return ["Age band", *[label for _, label in office.GENDERS], "Total"], rows


def _money(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    debtors = office.chase_list(db, today)
    fy_start = date(today.year if today.month >= int(finance.get_setting(db, "fy_start_month") or 1) else today.year - 1, int(finance.get_setting(db, "fy_start_month") or 1), 1)
    received = finance.payment_lines(db, fy_start, today)
    month = [p for p in received if p["date"] >= today.replace(day=1).isoformat()]
    rows = [
        ["Owed to the club", _pounds(sum(d.owed for d in debtors))],
        ["Of which more than 30 days late", _pounds(sum(d.owed for d in debtors if d.days_overdue > 30))],
        ["People who owe", len(debtors)],
        ["Received this month", _pounds(sum(p["amount"] for p in month))],
        [f"Received since {fy_start:%d %b %Y}", _pounds(sum(p["amount"] for p in received))],
        ["Active members", db.scalar(select(func.count()).select_from(Member).where(Member.active.is_(True))) or 0],
    ]
    return ["What", "Amount"], rows


def _events(db: Session, today: date, base_url: str) -> tuple[list[str], list[list[Any]]]:
    rows = []
    for event in events.upcoming(db, limit=200, published_only=True, now=datetime.combine(today, datetime.min.time(), tzinfo=UTC)):
        prices = {t.audience: t.price_minor for t in events.ticket_types(db, event, only_active=True)}
        free = events.availability(db, event).free
        on_sale, _why = events.sales_state(event)
        start, end = events._aware(event.start_at) or event.start_at, events._aware(event.end_at)
        rows.append([event.id, event.title, start.isoformat(), end.isoformat() if end else "",
                     event.location or "", event.event_type or "", "" if "members" not in prices and "all" not in prices else _pounds(prices.get("members", prices.get("all"))),
                     "" if "public" not in prices and "all" not in prices else _pounds(prices.get("public", prices.get("all"))),
                     "" if free is None else free, f"{base_url}/whats-on/{event.id}" if on_sale else ""])
    return ["Id", "Title", "Starts", "Ends", "Where", "Kind", "Member price (£)", "Visitor price (£)", "Places left", "Book at"], rows


def _boats(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    designs = {d.id: d for d in db.scalars(select(BoatDesign))}
    rows = []
    for boat in db.scalars(select(Boat).where(Boat.status == "active").order_by(Boat.name)):
        owner = db.get(Contact, boat.owner_contact_id) if boat.owner_contact_id else None
        if owner is None and boat.owner_member_id:
            owner = db.scalar(select(Contact).where(Contact.member_id == boat.owner_member_id))
        design = designs.get(boat.design_id or 0)
        number = boat.portsmouth_number or (design.portsmouth_rating if design else None)
        rows.append([boat.id, boat.name, boat.boat_class or (design.name if design else ""), boat.sail_number or "", number or "", owner.name if owner else ""])
    return ["Id", "Name", "Class", "Sail number", "Portsmouth number", "Owner"], rows


def _directory(db: Session, today: date) -> tuple[list[str], list[list[Any]]]:
    rows = []
    for contact, member in db.execute(select(Contact, Member).join(Member, Member.id == Contact.member_id).where(Member.active.is_(True), Contact.directory_consent.is_(True)).order_by(Contact.name)):
        rows.append([contact.name, member.membership_type or "", contact.email or "", contact.phone or ""])
    return ["Name", "Membership", "Email", "Phone"], rows


def build(db: Session, name: str, today: date | None = None, base_url: str = "") -> dict[str, Any]:
    today = today or date.today()
    base_url = (base_url or os.getenv("CLUB_BASE_URL", "")).rstrip("/")
    makers = {"chase": _chase, "payments": _payments, "members": _members, "bookings": _bookings, "census": _census, "money": _money, "boats": _boats, "directory": _directory}
    columns, rows = _events(db, today, base_url) if name == "events" else makers[name](db, today)
    return {"feed": name, "title": FEEDS[name][0], "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "columns": columns, "rows": rows}
