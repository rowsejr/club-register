"""Events and tickets.

Tickets work like course places: a place is **held** while its invoice is unpaid (and released if nobody pays), **booked** once
paid (or at once if free), and a full event keeps a **waiting list** that is offered places as they come free. Members and the
public can buy tickets; each ticket type says who it is for. Refunds are credit notes, never deleted rows.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance, mailer
from app.models import (
    AuditLog,
    Contact,
    Event,
    EventTicket,
    Invoice,
    Member,
    NominalCode,
    TicketType,
)

LIVE = ("held", "booked")  # statuses that use up a place
DEFAULT_NOMINAL = "0108"
MAX_PER_BOOKING = 8


class EventError(ValueError):
    """A rule of events and tickets, safe to show to the user."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _setting_int(db: Session, key: str, default: int) -> int:
    try:
        return max(0, int(finance.get_setting(db, key)))
    except ValueError:
        return default


def _audit(db: Session, actor: str, action: str, target_id, **details) -> None:
    db.add(AuditLog(actor_email=actor, actor_role="system" if actor == "system" else "user", action=action, target_type="event", target_id=str(target_id), details=details))


# --------------------------------------------------------------------------- events and ticket types


def save_event(
    db: Session, event_id: int | None, *, title: str, start_at: datetime, end_at: datetime | None = None, location: str = "", event_type: str = "", description: str = "",
    bookable: bool = False, tickets_limit: int | None = None, waiting_limit: int | None = None, published: bool = False, sales_close_at: datetime | None = None,
    nominal_code: str = "", group_name: str = "", notes: str = "",
) -> Event:
    title = " ".join(title.split())
    if not title:
        raise EventError("An event needs a title")
    if end_at is not None and end_at < start_at:
        raise EventError("An event cannot end before it starts")
    if tickets_limit is not None and tickets_limit < 0:
        raise EventError("The ticket limit cannot be negative")
    if nominal_code and db.scalar(select(NominalCode.id).limit(1)) is not None and not db.scalar(select(NominalCode.id).where(NominalCode.code == nominal_code)):
        raise EventError(f"Unknown nominal code {nominal_code}")
    event = db.get(Event, event_id) if event_id else None
    if event_id and event is None:
        raise EventError("Event not found")
    if event is None:
        event = Event(title=title, start_at=start_at, location=location)
        db.add(event)
    if event.id and tickets_limit is not None:
        taken = used_places(db, event)
        if tickets_limit < taken:
            raise EventError(f"{taken} places are already taken, so the limit cannot be {tickets_limit}")
    event.title, event.start_at, event.end_at, event.location = title, start_at, end_at, location.strip()
    event.event_type, event.description, event.bookable, event.published = event_type.strip() or None, description.strip() or None, bookable, published
    event.tickets_limit, event.waiting_limit, event.sales_close_at = tickets_limit, waiting_limit, sales_close_at
    event.nominal_code, event.group_name, event.notes = nominal_code or None, group_name.strip() or None, notes.strip() or event.notes or ""
    db.flush()
    promote_waitlist(db, event, "system")  # a bigger limit gives the people already waiting their places first
    return event


def save_ticket_type(db: Session, event: Event, type_id: int | None, *, name: str, price_minor: int, audience: str = "all", capacity: int | None = None, active: bool = True) -> TicketType:
    name = " ".join(name.split())
    if not name:
        raise EventError("A ticket type needs a name")
    if price_minor < 0:
        raise EventError("A price cannot be negative")
    if audience not in {"members", "public", "all"}:
        raise EventError("Choose who the ticket is for")
    ttype = db.get(TicketType, type_id) if type_id else None
    if type_id and (ttype is None or ttype.event_id != event.id):
        raise EventError("Ticket type not found")
    if ttype is None:
        ttype = TicketType(event_id=event.id, name=name)
        db.add(ttype)
    ttype.name, ttype.price_minor, ttype.audience, ttype.capacity, ttype.active = name, price_minor, audience, capacity, active
    db.flush()
    promote_waitlist(db, event, "system")
    return ttype


def ticket_types(db: Session, event: Event, only_active: bool = False) -> list[TicketType]:
    stmt = select(TicketType).where(TicketType.event_id == event.id).order_by(TicketType.price_minor, TicketType.id)
    if only_active:
        stmt = stmt.where(TicketType.active.is_(True))
    return list(db.scalars(stmt))


# --------------------------------------------------------------------------- how many places


@dataclass
class Availability:
    limit: int | None
    taken: int  # held + booked
    waiting: int
    by_type: dict[int, int] = field(default_factory=dict)

    @property
    def free(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.taken)

    @property
    def sold_out(self) -> bool:
        return self.limit is not None and self.taken >= self.limit


def used_places(db: Session, event: Event) -> int:
    return db.scalar(select(func.count()).select_from(EventTicket).where(EventTicket.event_id == event.id, EventTicket.status.in_(LIVE))) or 0


def availability(db: Session, event: Event) -> Availability:
    waiting = db.scalar(select(func.count()).select_from(EventTicket).where(EventTicket.event_id == event.id, EventTicket.status == "waiting")) or 0
    by_type = {tid: n for tid, n in db.execute(select(EventTicket.ticket_type_id, func.count()).where(EventTicket.event_id == event.id, EventTicket.status.in_(LIVE)).group_by(EventTicket.ticket_type_id)) if tid is not None}
    return Availability(event.tickets_limit, used_places(db, event), waiting, by_type)


def _free_for_type(db: Session, event: Event, ttype: TicketType) -> int | None:
    """Places left for this ticket type: the smaller of the event's and the type's own limit; None if neither is limited."""
    avail = availability(db, event)
    left = [x for x in (avail.free, None if ttype.capacity is None else max(0, ttype.capacity - avail.by_type.get(ttype.id, 0))) if x is not None]
    return min(left) if left else None


def sales_state(event: Event, now: datetime | None = None) -> tuple[bool, str]:
    """Can tickets be bought right now, and if not, why."""
    now = now or _now()
    if not event.bookable:
        return False, "Tickets are not on sale for this event"
    if not event.published:
        return False, "This event is not published yet"
    closes = _aware(event.sales_close_at) or _aware(event.start_at)
    if closes is not None and now >= closes:
        return False, "Ticket sales have closed"
    return True, ""


# --------------------------------------------------------------------------- buying


@dataclass
class Booking:
    tickets: list[EventTicket]
    invoice: Invoice | None
    waiting: bool


def _is_member(db: Session, contact: Contact) -> bool:
    member = db.get(Member, contact.member_id) if contact.member_id else None
    return bool(member and member.active)


def check_audience(db: Session, ttype: TicketType, contact: Contact) -> None:
    if ttype.audience == "members" and not _is_member(db, contact):
        raise EventError(f"“{ttype.name}” tickets are for members. Choose a public ticket, or join the club.")
    if ttype.audience == "public" and _is_member(db, contact):
        raise EventError(f"“{ttype.name}” is the public price. As a member, please choose the member ticket.")


def book(db: Session, event: Event, ttype: TicketType, buyer: Contact, quantity: int = 1, *, guests: list[str] | None = None, actor: str = "public", staff: bool = False, now: datetime | None = None) -> Booking:
    """Buy places. Free tickets are booked at once; paid ones are held until the invoice is paid; a full event waitlists the buyer.

    Staff bookings skip the sales window and the audience rule (a volunteer can sell any ticket to anyone)."""
    now = now or _now()
    if ttype.event_id != event.id or not ttype.active:
        raise EventError("That ticket is not on sale")
    if quantity < 1 or quantity > MAX_PER_BOOKING:
        raise EventError(f"You can book between 1 and {MAX_PER_BOOKING} places at a time")
    if not staff:
        open_, why = sales_state(event, now)
        if not open_:
            raise EventError(why)
        check_audience(db, ttype, buyer)
    guests = [" ".join(g.split()) for g in (guests or [])]
    free = _free_for_type(db, event, ttype)
    waiting = free is not None and free < quantity
    if waiting:
        queue = availability(db, event).waiting
        if event.waiting_limit is None or queue + quantity > event.waiting_limit:
            left = free or 0
            raise EventError("Sorry, this event is sold out" if left == 0 else f"Only {left} place{'s' if left != 1 else ''} left")
    status = "waiting" if waiting else ("held" if ttype.price_minor > 0 else "booked")
    hold_until = now + timedelta(days=_setting_int(db, "event_hold_days", 3)) if status == "held" else None
    tickets = []
    for index in range(quantity):
        ticket = EventTicket(
            event_id=event.id, contact_id=buyer.id, status=status, ticket_type_id=ttype.id, holder_name=guests[index] if index < len(guests) and guests[index] else None,
            booked_by_contact_id=buyer.id, price_minor=ttype.price_minor, hold_until=hold_until,
        )
        db.add(ticket)
        tickets.append(ticket)
    db.flush()
    invoice = None
    if status == "held":
        try:
            invoice = _invoice_for(db, event, ttype, tickets, buyer, actor)
        except finance.FinanceError as exc:
            raise EventError(str(exc)) from exc
    _audit(db, actor, "event.book", event.id, ticket_type=ttype.name, quantity=quantity, status=status, buyer=buyer.id)
    if status in {"held", "booked"}:
        _confirmation(db, event, buyer, tickets, invoice)
    elif waiting:
        _tell(db, buyer, f"You are on the waiting list: {event.title}", f"Hello {_first(buyer)},\n\nThe event is full, so we have put you on the waiting list for {quantity} place{'s' if quantity != 1 else ''}. We will email you if places come free.\n\n{finance.get_setting(db, 'club_name')}", event)
    return Booking(tickets, invoice, waiting)


def _first(contact: Contact) -> str:
    return contact.name.split()[0] if contact.name else "there"


def _tell(db: Session, contact: Contact, subject: str, body: str, event: Event, calendar: bool = False) -> None:
    """Email someone about an event. With ``calendar``, the event comes as a file their phone or Outlook adds in one tap."""
    if contact.email and not contact.suppressed:
        attachment = ("event.ics", event_ics(db, event), "text/calendar") if calendar else None
        mailer.send_email(db, to=contact.email, subject=subject, body=body, related_type="event", related_id=event.id, actor="system", reply_to=finance.reply_to(db, "events"), attachment=attachment)


def ics_file(club: str, uid: str, title: str, start: datetime, end: datetime | None, location: str = "", description: str = "", url: str = "") -> bytes:
    """A one-event calendar file (.ics) for an email attachment."""
    start = _aware(start) or _now()
    finish = _aware(end) or start + timedelta(hours=2)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{_ics_text(club)}//Club office//EN", "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:{uid}",
             f"DTSTAMP:{_now().strftime('%Y%m%dT%H%M%SZ')}", f"DTSTART:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{finish.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
             f"SUMMARY:{_ics_text(title)}"]
    if location:
        lines.append(f"LOCATION:{_ics_text(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_ics_text(description)}")
    if url:
        lines.append(f"URL:{url}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def event_ics(db: Session, event: Event) -> bytes:
    base = os.getenv("CLUB_BASE_URL", "").rstrip("/")
    return ics_file(finance.get_setting(db, "club_name"), f"event-{event.id}@club-office", event.title, event.start_at, event.end_at, event.location or "", event.description or "",
                    f"{base}/whats-on/{event.id}" if base else "")


def _invoice_for(db: Session, event: Event, ttype: TicketType, tickets: list[EventTicket], payer: Contact, actor: str) -> Invoice:
    nominal = event.nominal_code or DEFAULT_NOMINAL
    lines = [
        {
            "description": f"{event.title}: {ttype.name}" + (f" ({t.holder_name})" if t.holder_name else ""), "amount_minor": ttype.price_minor, "nominal_code": nominal if db.scalar(select(NominalCode.id).where(NominalCode.code == nominal)) else None,
            "source_type": "event", "source_id": str(t.id),
        }
        for t in tickets
    ]
    invoice = finance.create_invoice(db, contact_id=payer.id, lines=lines, actor=actor, notes=f"Tickets: {event.title}", source="event", tags=f"event, {event.title}")
    finance.issue(db, invoice, actor)
    for ticket in tickets:
        ticket.invoice_id = invoice.id
    db.flush()
    return invoice


def _confirmation(db: Session, event: Event, buyer: Contact, tickets: list[EventTicket], invoice: Invoice | None) -> None:
    from app import payments

    when = event.start_at.strftime("%A %d %B %Y, %H:%M")
    lines = [f"Hello {_first(buyer)},", ""]
    if invoice is None:
        lines.append(f"You are booked: {len(tickets)} place{'s' if len(tickets) != 1 else ''} at {event.title}, {when}{', ' + event.location if event.location else ''}.")
        lines += ["", "The attached file adds it to your calendar."]
    else:
        lines += [
            f"We are holding {len(tickets)} place{'s' if len(tickets) != 1 else ''} for you at {event.title}, {when}.",
            f"Please pay invoice {invoice.reference} (£{invoice.amount_minor / 100:,.2f}) within {_setting_int(db, 'event_hold_days', 3)} days to keep them, or the places go back on sale.",
        ]
        settings = finance.all_settings(db)
        for block in (settings["payment_instructions"], settings["bank_details"]):
            if block:
                lines += ["", block]
        if payments.methods_for(db, invoice):
            lines += ["", f"Pay online: {payments.pay_link(invoice, db)}"]
    lines += ["", finance.get_setting(db, "club_name")]
    _tell(db, buyer, f"{event.title}: your {'booking' if invoice is None else 'places are held'}", "\n".join(lines), event, calendar=invoice is None)


# --------------------------------------------------------------------------- paying, cancelling, waiting lists


def confirm_on_payment(db: Session, invoice: Invoice) -> None:
    """Hook: when a ticket invoice is paid, the held places are booked."""
    held = db.scalars(select(EventTicket).where(EventTicket.invoice_id == invoice.id, EventTicket.status == "held")).all()
    if not held:
        return
    for ticket in held:
        ticket.status, ticket.hold_until = "booked", None
    event = db.get(Event, held[0].event_id)
    buyer = db.get(Contact, held[0].contact_id)
    if event and buyer:
        when = event.start_at.strftime("%A %d %B %Y, %H:%M")
        _tell(db, buyer, f"{event.title}: you are booked", f"Hello {_first(buyer)},\n\nThank you: your payment has arrived and your {len(held)} place{'s are' if len(held) != 1 else ' is'} confirmed for {event.title}, {when}. The attached file adds it to your calendar.\n\n{finance.get_setting(db, 'club_name')}", event, calendar=True)
    db.flush()


finance.PAID_HOOKS.append(confirm_on_payment)


def _release_invoice(db: Session, ticket: EventTicket, actor: str, refund: bool) -> str:
    """Take a ticket off its invoice: void it if nothing was paid, otherwise credit the ticket's price."""
    invoice = db.get(Invoice, ticket.invoice_id) if ticket.invoice_id else None
    if invoice is None or ticket.price_minor in (None, 0):
        return "no payment to return"
    if invoice.status == "paid" and refund:
        finance.credit_note(db, invoice, amount_minor=ticket.price_minor or 0, reason=f"Ticket cancelled: {ticket.id}", actor=actor)
        return f"£{(ticket.price_minor or 0) / 100:,.2f} credited"
    if invoice.status in finance.OPEN and finance.paid_total(db, invoice.id) == 0:
        others = list(db.scalars(select(EventTicket).where(EventTicket.invoice_id == invoice.id, EventTicket.status.in_(LIVE), EventTicket.id != ticket.id).order_by(EventTicket.id)))
        savepoint = db.begin_nested()
        try:
            finance.void_invoice(db, invoice, actor, "Tickets cancelled")
            if not others:
                savepoint.commit()
                return "invoice voided"
            event, payer, ttype = db.get(Event, others[0].event_id), db.get(Contact, others[0].contact_id), db.get(TicketType, others[0].ticket_type_id or 0)
            if event is None or payer is None or ttype is None:
                raise finance.FinanceError("The remaining places cannot be re-invoiced")
            _invoice_for(db, event, ttype, others, payer, actor)  # the buyer now owes only for the places they still have
            savepoint.commit()
            return "invoice re-issued for the places that remain"
        except finance.FinanceError as exc:
            savepoint.rollback()
            return f"the invoice was left as it is ({exc}); the treasurer should credit it"
    return "paid: the treasurer decides on any refund" if invoice.status == "paid" else "no payment to return"


def cancel(db: Session, ticket: EventTicket, actor: str, *, by_member: bool = False, today: date | None = None, force_refund: bool = False) -> str:
    """Cancel a place. A member may cancel until the refund cut-off; staff can always cancel. Returns what happened to the money."""
    if ticket.status == "cancelled":
        raise EventError("This place is already cancelled")
    event = db.get(Event, ticket.event_id)
    if event is None:
        raise EventError("Event not found")
    today = today or date.today()
    days_left = (event.start_at.date() - today).days
    cutoff = _setting_int(db, "event_refund_cutoff_days", 7)
    if by_member and days_left < cutoff and ticket.status == "booked" and (ticket.price_minor or 0) > 0:
        raise EventError(f"Places can only be cancelled online up to {cutoff} days before the event. Please contact the club.")
    was_live = ticket.status in LIVE
    refund = force_refund or not by_member or days_left >= cutoff
    outcome = _release_invoice(db, ticket, actor, refund) if was_live else "no payment to return"
    ticket.status, ticket.hold_until = "cancelled", None
    _audit(db, actor, "event.cancel_ticket", event.id, ticket=ticket.id, outcome=outcome)
    db.flush()
    if was_live:
        promote_waitlist(db, event, actor)
    return outcome


def promote_waitlist(db: Session, event: Event, actor: str = "system") -> list[EventTicket]:
    """Offer free places to the people waiting, in the order they asked. Paid places are held for them with an invoice."""
    promoted: list[EventTicket] = []
    waiting = db.scalars(select(EventTicket).where(EventTicket.event_id == event.id, EventTicket.status == "waiting").order_by(EventTicket.id)).all()
    for ticket in waiting:
        ttype = db.get(TicketType, ticket.ticket_type_id) if ticket.ticket_type_id else None
        if ttype is None:
            continue
        free = _free_for_type(db, event, ttype)
        if free is not None and free < 1:
            continue
        buyer = db.get(Contact, ticket.contact_id)
        if buyer is None:
            continue
        paid = (ticket.price_minor or 0) > 0
        savepoint = db.begin_nested()
        try:
            ticket.status = "held" if paid else "booked"
            ticket.hold_until = _now() + timedelta(days=_setting_int(db, "event_hold_days", 3)) if paid else None
            db.flush()
            invoice = _invoice_for(db, event, ttype, [ticket], buyer, actor) if paid else None
            savepoint.commit()
        except finance.FinanceError:
            savepoint.rollback()  # for example the books are closed: leave this person waiting rather than fail the whole run
            continue
        _confirmation(db, event, buyer, [ticket], invoice)
        promoted.append(ticket)
    return promoted


def expire_holds(db: Session, now: datetime | None = None) -> int:
    """Release places whose invoice was not paid in time, and offer them to the waiting list."""
    now = now or _now()
    expired: list[EventTicket] = []
    events: set[int] = set()
    for ticket in db.scalars(select(EventTicket).where(EventTicket.status == "held", EventTicket.hold_until.is_not(None))).all():
        until = _aware(ticket.hold_until)
        if until is None or until > now:
            continue
        invoice = db.get(Invoice, ticket.invoice_id) if ticket.invoice_id else None
        if invoice is not None and finance.paid_total(db, invoice.id) > 0:
            continue
        expired.append(ticket)
    for ticket in expired:  # cancel them all first, so an invoice shared by several tickets is voided once the last one goes
        ticket.status, ticket.hold_until = "cancelled", None
        events.add(ticket.event_id)
    db.flush()
    seen: set[int] = set()
    for ticket in expired:
        if ticket.invoice_id and ticket.invoice_id not in seen:
            seen.add(ticket.invoice_id)
            _release_invoice(db, ticket, "system", False)
    released = len(expired)
    db.flush()
    for event_id in events:
        event = db.get(Event, event_id)
        if event is not None:
            promote_waitlist(db, event, "system")
    return released


# --------------------------------------------------------------------------- attendees


@dataclass
class Attendee:
    ticket: EventTicket
    contact: Contact
    type_name: str
    member: bool


def attendees(db: Session, event: Event, statuses: tuple[str, ...] = ("held", "booked", "waiting")) -> list[Attendee]:
    types = {t.id: t.name for t in ticket_types(db, event)}
    out = []
    for ticket in db.scalars(select(EventTicket).where(EventTicket.event_id == event.id, EventTicket.status.in_(statuses)).order_by(EventTicket.status.desc(), EventTicket.id)):
        contact = db.get(Contact, ticket.contact_id)
        if contact is not None:
            out.append(Attendee(ticket, contact, types.get(ticket.ticket_type_id or 0, ""), _is_member(db, contact)))
    return out


def check_in(db: Session, ticket: EventTicket, present: bool = True) -> None:
    if ticket.status != "booked":
        raise EventError("Only a booked place can be ticked off")
    ticket.checked_in = present
    db.flush()


# --------------------------------------------------------------------------- calendar and iCal


def month_events(db: Session, year: int, month: int, published_only: bool = False) -> list[Event]:
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    stmt = select(Event).where(Event.start_at >= start - timedelta(days=1), Event.start_at < end + timedelta(days=1)).order_by(Event.start_at)
    if published_only:
        stmt = stmt.where(Event.published.is_(True))
    return list(db.scalars(stmt))


def upcoming(db: Session, limit: int = 50, published_only: bool = True, now: datetime | None = None) -> list[Event]:
    stmt = select(Event).where(Event.start_at >= (now or _now()) - timedelta(hours=6)).order_by(Event.start_at).limit(limit)
    if published_only:
        stmt = stmt.where(Event.published.is_(True))
    return list(db.scalars(stmt))


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n").replace("\r", "")


def ical(db: Session, club: str, base_url: str = "") -> str:
    """The published events as an iCalendar feed that Google, Outlook and phones can subscribe to."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:-//{_ics_text(club)}//Club office//EN", "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{_ics_text(club)}"]
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    for event in upcoming(db, limit=500, published_only=True, now=_now() - timedelta(days=60)):
        start = _aware(event.start_at) or _now()
        end = _aware(event.end_at) or start + timedelta(hours=2)
        lines += [
            "BEGIN:VEVENT", f"UID:event-{event.id}@club-office", f"DTSTAMP:{stamp}", f"DTSTART:{start.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{end.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
            f"SUMMARY:{_ics_text(event.title)}",
        ]
        if event.location:
            lines.append(f"LOCATION:{_ics_text(event.location)}")
        if event.description:
            lines.append(f"DESCRIPTION:{_ics_text(event.description)}")
        if base_url:
            lines.append(f"URL:{base_url.rstrip('/')}/whats-on/{event.id}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
