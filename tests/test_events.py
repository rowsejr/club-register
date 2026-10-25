"""Events: ticket types, capacity, holds, waiting lists, refunds and duties."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import events, finance
from app.db import SessionLocal
from app.events import EventError
from app.models import Contact, EventTicket, Invoice, Member, NominalCode, OutboundEmail

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
START = datetime(2026, 12, 5, 19, 0, tzinfo=UTC)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0108", label="Open Meetings"))
        session.commit()
        yield session


def _contact(db, name, email=None, member=False):
    m = None
    if member:
        m = Member(name=name, email=email or f"{name.split()[0].lower()}@example.org", membership_type="Single Adult Sailing", renewal_status="active", active=True)
        db.add(m)
        db.flush()
    c = Contact(name=name, email=email or f"{name.split()[0].lower()}@example.org", member_id=m.id if m else None)
    db.add(c)
    db.flush()
    return c


@pytest.fixture
def dinner(db):
    event = events.save_event(db, None, title="Christmas Party", start_at=START, location="Clubhouse", bookable=True, published=True, tickets_limit=4, waiting_limit=3)
    members = events.save_ticket_type(db, event, None, name="Member", price_minor=1500, audience="members")
    public = events.save_ticket_type(db, event, None, name="Guest", price_minor=2500, audience="public")
    free = events.save_ticket_type(db, event, None, name="Child under 5", price_minor=0, audience="all")
    db.commit()
    return {"event": event, "member": members, "public": public, "free": free}


def test_an_event_needs_a_title_sane_dates_and_a_known_nominal(db) -> None:
    with pytest.raises(EventError, match="title"):
        events.save_event(db, None, title="  ", start_at=START)
    with pytest.raises(EventError, match="end before it starts"):
        events.save_event(db, None, title="X", start_at=START, end_at=START - timedelta(hours=1))
    with pytest.raises(EventError, match="Unknown nominal"):
        events.save_event(db, None, title="X", start_at=START, nominal_code="9999")
    event = events.save_event(db, None, title="Quiz night", start_at=START, tickets_limit=10)
    assert event.id and not event.bookable and not event.published


def test_the_limit_cannot_be_set_below_the_places_already_taken(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    events.book(db, dinner["event"], dinner["member"], ann, 3, now=NOW)
    with pytest.raises(EventError, match="3 places are already taken"):
        events.save_event(db, dinner["event"].id, title="Christmas Party", start_at=START, tickets_limit=2, bookable=True, published=True)


def test_ticket_types_are_validated(db, dinner) -> None:
    event = dinner["event"]
    for kwargs, message in (({"name": " ", "price_minor": 1}, "needs a name"), ({"name": "x", "price_minor": -1}, "negative"), ({"name": "x", "price_minor": 1, "audience": "robots"}, "who the ticket is for")):
        with pytest.raises(EventError, match=message):
            events.save_ticket_type(db, event, None, **kwargs)
    assert [t.name for t in events.ticket_types(db, event)] == ["Child under 5", "Member", "Guest"]  # cheapest first


def test_a_member_buys_a_paid_ticket_and_the_place_is_held_with_an_invoice(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    booking = events.book(db, dinner["event"], dinner["member"], ann, 2, guests=["Ann Sailor", "Bob Guest"], now=NOW)
    assert [t.status for t in booking.tickets] == ["held", "held"] and not booking.waiting
    invoice = booking.invoice
    assert invoice.amount_minor == 3000 and invoice.status == "issued" and invoice.source == "event" and invoice.contact_id == ann.id
    lines = [(ln.description, ln.nominal_code, ln.source_type) for ln in db.scalars(select(finance.InvoiceLine).where(finance.InvoiceLine.invoice_id == invoice.id).order_by(finance.InvoiceLine.id))]
    assert lines == [("Christmas Party: Member (Ann Sailor)", "0108", "event"), ("Christmas Party: Member (Bob Guest)", "0108", "event")]
    assert all(t.hold_until is not None for t in booking.tickets)
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "event"))
    assert mail and "holding 2 places" in mail.body and invoice.reference in mail.body


def test_paying_books_the_places_and_sends_a_confirmation(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    booking = events.book(db, dinner["event"], dinner["member"], ann, 1, now=NOW)
    finance.record_payment(db, booking.invoice, amount_minor=1500, method="bank")
    db.flush()
    assert booking.tickets[0].status == "booked" and booking.tickets[0].hold_until is None
    assert any("you are booked" in m.subject for m in db.scalars(select(OutboundEmail)))


def test_free_tickets_are_booked_at_once_without_an_invoice(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    booking = events.book(db, dinner["event"], dinner["free"], ann, 1, now=NOW)
    assert booking.tickets[0].status == "booked" and booking.invoice is None
    assert db.scalar(select(Invoice.id)) is None


def test_each_ticket_type_says_who_it_is_for(db, dinner) -> None:
    ann, guest = _contact(db, "Ann Sailor", member=True), _contact(db, "Gus Public")
    with pytest.raises(EventError, match="for members"):
        events.book(db, dinner["event"], dinner["member"], guest, 1, now=NOW)
    with pytest.raises(EventError, match="public price"):
        events.book(db, dinner["event"], dinner["public"], ann, 1, now=NOW)
    events.book(db, dinner["event"], dinner["public"], guest, 1, now=NOW)  # a non-member buys the guest ticket
    events.book(db, dinner["event"], dinner["member"], guest, 1, staff=True, now=NOW)  # a volunteer can sell any ticket to anyone


def test_the_sales_window_and_basic_limits(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    event = dinner["event"]
    with pytest.raises(EventError, match="between 1 and 8"):
        events.book(db, event, dinner["member"], ann, 9, now=NOW)
    with pytest.raises(EventError, match="closed"):
        events.book(db, event, dinner["member"], ann, 1, now=START + timedelta(hours=1))
    event.sales_close_at = START - timedelta(days=2)
    with pytest.raises(EventError, match="closed"):
        events.book(db, event, dinner["member"], ann, 1, now=START - timedelta(days=1))
    event.bookable = False
    assert events.sales_state(event, NOW) == (False, "Tickets are not on sale for this event")
    event.bookable, event.published = True, False
    assert events.sales_state(event, NOW)[1] == "This event is not published yet"


def test_a_full_event_waitlists_and_stops_when_the_list_is_full_too(db, dinner) -> None:
    event, ttype = dinner["event"], dinner["member"]
    a, b, c, d = (_contact(db, f"{n} Person", member=True) for n in ("A", "B", "C", "D"))
    events.book(db, event, ttype, a, 3, now=NOW)
    only_one = events.book(db, event, ttype, b, 1, now=NOW)
    assert not only_one.waiting
    queued = events.book(db, event, ttype, c, 2, now=NOW)
    assert queued.waiting and [t.status for t in queued.tickets] == ["waiting", "waiting"] and queued.invoice is None
    with pytest.raises(EventError, match="sold out"):
        events.book(db, event, ttype, d, 2, now=NOW)  # the waiting list holds 3, and 2 + 2 is too many
    availability = events.availability(db, event)
    assert (availability.taken, availability.free, availability.waiting, availability.sold_out) == (4, 0, 2, True)


def test_a_per_type_capacity_applies_alongside_the_event_limit(db, dinner) -> None:
    guest_type = dinner["public"]
    guest_type.capacity = 1
    a, b = _contact(db, "Gus One"), _contact(db, "Gus Two")
    events.book(db, dinner["event"], guest_type, a, 1, now=NOW)
    second = events.book(db, dinner["event"], guest_type, b, 1, now=NOW)
    assert second.waiting  # the event has room, but the guest tickets are gone: b joins the waiting list for one
    assert events.availability(db, dinner["event"]).taken == 1 and events.availability(db, dinner["event"]).waiting == 1
    member_ticket = events.book(db, dinner["event"], dinner["member"], _contact(db, "Ann Sailor", member=True), 1, now=NOW)
    assert not member_ticket.waiting  # other kinds are unaffected


def test_cancelling_before_the_cutoff_credits_a_paid_ticket_and_promotes_the_waiting_list(db, dinner) -> None:
    event, ttype = dinner["event"], dinner["member"]
    a, b = _contact(db, "Ann Sailor", member=True), _contact(db, "Bob Rower", member=True)
    full = events.book(db, event, ttype, a, 4, now=NOW)
    finance.record_payment(db, full.invoice, amount_minor=6000, method="bank")
    queued = events.book(db, event, ttype, b, 1, now=NOW)
    assert queued.waiting
    outcome = events.cancel(db, full.tickets[0], "ann@example.org", by_member=True, today=date(2026, 11, 20))
    assert outcome == "£15.00 credited"
    assert full.tickets[0].status == "cancelled" and queued.tickets[0].status == "held"
    invoice = db.get(Invoice, queued.tickets[0].invoice_id)
    assert invoice.amount_minor == 1500 and invoice.contact_id == b.id  # Bob is offered the place, with an invoice
    assert any(m.to_address == b.email and "places are held" in m.subject for m in db.scalars(select(OutboundEmail)))


def test_members_cannot_cancel_a_paid_place_inside_the_cutoff_but_staff_can(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    booking = events.book(db, dinner["event"], dinner["member"], ann, 1, now=NOW)
    finance.record_payment(db, booking.invoice, amount_minor=1500, method="bank")
    with pytest.raises(EventError, match="up to 7 days before"):
        events.cancel(db, booking.tickets[0], "ann@example.org", by_member=True, today=date(2026, 12, 1))
    assert events.cancel(db, booking.tickets[0], "sec@example.org", today=date(2026, 12, 1)) == "£15.00 credited"
    with pytest.raises(EventError, match="already cancelled"):
        events.cancel(db, booking.tickets[0], "sec@example.org")


def test_cancelling_an_unpaid_hold_voids_its_invoice_unless_other_tickets_share_it(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    two = events.book(db, dinner["event"], dinner["member"], ann, 2, now=NOW)
    assert events.cancel(db, two.tickets[0], "t") == "invoice re-issued for the places that remain"
    assert two.invoice.status == "void" and two.tickets[1].invoice_id != two.invoice.id  # the buyer now owes for one place only
    reissued = db.get(Invoice, two.tickets[1].invoice_id)
    assert reissued.status == "issued" and reissued.amount_minor == two.tickets[1].price_minor
    assert events.cancel(db, two.tickets[1], "t") == "invoice voided" and reissued.status == "void"


def test_unpaid_holds_expire_and_the_places_go_to_the_waiting_list(db, dinner) -> None:
    event, ttype = dinner["event"], dinner["member"]
    a, b = _contact(db, "Ann Sailor", member=True), _contact(db, "Bob Rower", member=True)
    held = events.book(db, event, ttype, a, 4, now=NOW)
    queued = events.book(db, event, ttype, b, 1, now=NOW)
    assert events.expire_holds(db, NOW + timedelta(days=2)) == 0  # still inside the three days
    assert events.expire_holds(db, NOW + timedelta(days=4)) == 4
    assert [t.status for t in held.tickets] == ["cancelled"] * 4 and held.invoice.status == "void"
    assert queued.tickets[0].status == "held"  # Bob moves up
    paid = events.book(db, event, ttype, a, 1, now=NOW)
    finance.record_payment(db, paid.invoice, amount_minor=1500, method="bank")
    assert events.expire_holds(db, NOW + timedelta(days=30)) == 1 and paid.tickets[0].status == "booked"  # only Bob's unpaid hold goes; paid places stay


def test_attendees_and_checking_in(db, dinner) -> None:
    ann, gus = _contact(db, "Ann Sailor", member=True), _contact(db, "Gus Public")
    events.book(db, dinner["event"], dinner["free"], ann, 1, now=NOW)
    events.book(db, dinner["event"], dinner["public"], gus, 1, now=NOW)
    rows = events.attendees(db, dinner["event"])
    assert [(a.contact.name, a.type_name, a.member, a.ticket.status) for a in rows] == [("Gus Public", "Guest", False, "held"), ("Ann Sailor", "Child under 5", True, "booked")]
    events.check_in(db, rows[1].ticket)
    assert rows[1].ticket.checked_in is True
    with pytest.raises(EventError, match="booked place"):
        events.check_in(db, rows[0].ticket)  # unpaid places cannot be ticked off






def test_the_calendar_lists_the_month_and_the_ical_feed_only_publishes_published_events(db, dinner) -> None:
    events.save_event(db, None, title="Secret committee", start_at=START + timedelta(days=1), published=False)
    db.flush()
    assert [e.title for e in events.month_events(db, 2026, 12)] == ["Christmas Party", "Secret committee"]
    assert [e.title for e in events.month_events(db, 2026, 12, published_only=True)] == ["Christmas Party"]
    feed = events.ical(db, "Margate Yacht Club, Kent", "https://myc.example.org")
    assert feed.startswith("BEGIN:VCALENDAR") and "SUMMARY:Christmas Party" in feed and "Secret committee" not in feed
    assert "DTSTART:20261205T190000Z" in feed and "LOCATION:Clubhouse" in feed and "URL:https://myc.example.org/whats-on/1" in feed and "X-WR-CALNAME:Margate Yacht Club\\, Kent" in feed
    assert feed.count("BEGIN:VEVENT") == feed.count("END:VEVENT") == 1 and "\r\n" in feed


def test_ticket_purchases_do_not_disturb_other_tickets_invoices(db, dinner) -> None:
    ann = _contact(db, "Ann Sailor", member=True)
    one = events.book(db, dinner["event"], dinner["member"], ann, 1, now=NOW)
    two = events.book(db, dinner["event"], dinner["member"], ann, 1, now=NOW)
    finance.record_payment(db, one.invoice, amount_minor=1500, method="bank")
    assert one.tickets[0].status == "booked" and two.tickets[0].status == "held"
    assert db.scalar(select(EventTicket.id).where(EventTicket.status == "held")) == two.tickets[0].id
