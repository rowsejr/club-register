"""Event screens: staff calendar and tickets, the public events page and non-member purchase, members' tickets."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import csrf, login
from sqlalchemy import select

from app import events, jobs
from app.db import SessionLocal
from app.main import app
from app.models import (
    Contact,
    Event,
    EventTicket,
    Invoice,
    Member,
    NominalCode,
    OutboundEmail,
    Role,
    TicketType,
)

FUTURE = datetime.now(UTC) + timedelta(days=40)


def _local_input(dt: datetime) -> str:
    from app.localtime import to_input

    return to_input(dt)


@pytest.fixture
def social():
    with SessionLocal() as db:
        db.add(NominalCode(code="0108", label="Open Meetings"))
        db.commit()
    return login("social@example.org", Role.COMMITTEE, hats="social")


def _create(social, **extra):
    data = {
        "csrf": csrf(social, "/app/events/new"),
        "title": "Club Dinner",
        "start": _local_input(FUTURE),
        "location": "Clubhouse",
        "bookable": "1",
        "published": "1",
        "limit": "3",
        "waiting": "2",
        "description": "Three courses",
        **extra,
    }
    response = social.post("/app/events", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text[:300]
    return int(re.search(r"/app/events/(\d+)", response.headers["location"]).group(1))


def _types(social, event_id):
    token = csrf(social, f"/app/events/{event_id}")
    for name, price, audience in (
        ("Member", "15", "members"),
        ("Guest", "25", "public"),
        ("Child", "0", "all"),
    ):
        social.post(
            f"/app/events/{event_id}/types",
            data={"csrf": token, "name": name, "price": price, "audience": audience},
            follow_redirects=True,
        )


def test_the_social_secretary_creates_an_event_and_ticket_types(social) -> None:
    page = social.get("/app/events/new")
    assert page.status_code == 200 and "Tickets can be bought" in page.text
    event_id = _create(social)
    _types(social, event_id)
    detail = social.get(f"/app/events/{event_id}")
    for text in (
        "Club Dinner",
        "members only",
        "the public",
        "£25.00",
        "free",
        
        "Who is coming",
        "Wrap-up",
    ):
        assert text in detail.text, text
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        assert event.tickets_limit == 3 and event.bookable and event.published
        assert [t.name for t in db.scalars(select(TicketType).order_by(TicketType.id))] == [
            "Member",
            "Guest",
            "Child",
        ]


def test_bad_event_forms_come_back_with_the_message(social) -> None:
    token = csrf(social, "/app/events/new")
    bad = social.post(
        "/app/events", data={"csrf": token, "title": "", "start": _local_input(FUTURE)}
    )
    assert bad.status_code == 422 and "needs a title" in bad.text
    nodate = social.post("/app/events", data={"csrf": token, "title": "X"})
    assert nodate.status_code == 422 and "when the event starts" in nodate.text
    notnum = social.post(
        "/app/events",
        data={"csrf": token, "title": "X", "start": _local_input(FUTURE), "limit": "lots"},
    )
    assert notnum.status_code == 422 and "not a whole number" in notnum.text


def test_events_open_on_whats_coming_up_with_a_month_view_too(social) -> None:
    _create(social)
    coming = social.get("/app/events")
    assert coming.status_code == 200 and "Club Dinner" in coming.text and "Calendar feed" in coming.text and "cal-event" not in coming.text
    month = social.get(f"/app/events?year={FUTURE.year}&month={FUTURE.month}&view=month")
    assert "Club Dinner" in month.text and "cal-event" in month.text
    assert social.get("/app/events?year=1&month=99&view=month").status_code == 200


def test_the_public_pages_show_published_events_only_and_the_ical_feed(social) -> None:
    _create(social)
    _create(social, title="Private committee", published="")
    anonymous = TestClient(app)
    listing = anonymous.get("/whats-on")
    assert (
        listing.status_code == 200
        and "Club Dinner" in listing.text
        and "Private committee" not in listing.text
        and "What&#39;s on" in anonymous.get("/membership").text
        or "What's on" in anonymous.get("/membership").text
    )
    with SessionLocal() as db:
        secret = db.scalar(select(Event.id).where(Event.title == "Private committee"))
    assert anonymous.get(f"/whats-on/{secret}").status_code == 404
    feed = anonymous.get("/events.ics")
    assert (
        feed.headers["content-type"].startswith("text/calendar")
        and "SUMMARY:Club Dinner" in feed.text
        and "Private committee" not in feed.text
    )


def _public_book(client, event_id, type_id, **fields):
    page = client.get(f"/whats-on/{event_id}")
    ft = re.search(r'name="ft" value="([^"]+)"', page.text).group(1)
    data = {
        "ft": ft,
        "website": "",
        "type_id": type_id,
        "quantity": "2",
        "name": "Gus Visitor",
        "email": "gus@example.org",
        "consent": "1",
        "guests": "Gus Visitor\nGreta Visitor",
        **fields,
    }
    return client.post(f"/whats-on/{event_id}/book", data=data)


def test_a_non_member_buys_tickets_receives_an_invoice_and_pays_through_a_private_link(
    social, monkeypatch
) -> None:
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "sandbox")
    event_id = _create(social)
    _types(social, event_id)
    with SessionLocal() as db:
        guest_type = db.scalar(select(TicketType.id).where(TicketType.name == "Guest"))
    visitor = TestClient(app)
    page = visitor.get(f"/whats-on/{event_id}")
    assert (
        "Guest" in page.text and "£25.00" in page.text and "Member ·" not in page.text
    )  # a visitor is not shown members-only tickets
    done = _public_book(visitor, event_id, guest_type)
    assert (
        done.status_code == 200 and "We are holding 2 places" in done.text and "£50.00" in done.text
    )
    link = re.search(r'href="(/i/[^"]+)"', done.text).group(1)
    with SessionLocal() as db:
        contact = db.scalar(select(Contact).where(Contact.email == "gus@example.org"))
        assert contact and contact.member_id is None
        assert [t.status for t in db.scalars(select(EventTicket).order_by(EventTicket.id))] == [
            "held",
            "held",
        ]
        mail = db.scalar(
            select(OutboundEmail).where(
                OutboundEmail.to_address == "gus@example.org", OutboundEmail.related_type == "event"
            )
        )
        assert "/i/" in mail.body and "holding 2 places" in mail.body
    invoice_page = visitor.get(link)
    assert (
        "invoice" in invoice_page.text.lower()
        and "£50.00" in invoice_page.text
        and "Pay £50.00 by Direct Debit" in invoice_page.text
    )
    started = visitor.post(f"{link}/pay", data={"method": "card"}, follow_redirects=False)
    assert started.status_code == 303 and "/pay/" in started.headers["location"]
    token = started.headers["location"].rsplit("/", 1)[1]
    paid = visitor.post(f"/pay/{token}", follow_redirects=False)
    assert paid.status_code == 303 and paid.headers["location"].startswith("/i/")
    with SessionLocal() as db:
        assert [t.status for t in db.scalars(select(EventTicket).order_by(EventTicket.id))] == [
            "booked",
            "booked",
        ]
        assert any("you are booked" in m.subject for m in db.scalars(select(OutboundEmail)))
    assert "Paid. Thank you." in visitor.get(link).text
    assert (
        visitor.get("/i/1.badbadbadbadbadbadbadbad").status_code == 404
        and visitor.get("/i/garbage").status_code == 404
    )


def test_public_purchase_refuses_spam_members_and_the_wrong_ticket(social) -> None:
    event_id = _create(social)
    _types(social, event_id)
    with SessionLocal() as db:
        guest_type = db.scalar(select(TicketType.id).where(TicketType.name == "Guest"))
        member_type = db.scalar(select(TicketType.id).where(TicketType.name == "Member"))
        member = Member(
            name="Ann Sailor",
            email="ann@example.org",
            membership_type="Single Adult Sailing",
            renewal_status="active",
            active=True,
        )
        db.add(member)
        db.flush()
        db.add(Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id))
        db.commit()
    visitor = TestClient(app)
    assert (
        "belongs to a member"
        in _public_book(
            visitor, event_id, guest_type, email="ann@example.org", name="Ann Sailor"
        ).text
    )
    assert (
        "Please choose a ticket" in _public_book(visitor, event_id, member_type).text
    )  # members-only tickets are not for sale to the public
    assert "privacy notice" in _public_book(visitor, event_id, guest_type, consent="").text
    assert "valid email" in _public_book(visitor, event_id, guest_type, email="nonsense").text
    bot = visitor.post(
        f"/whats-on/{event_id}/book",
        data={
            "ft": "x",
            "website": "http://spam",
            "type_id": guest_type,
            "name": "Bot",
            "email": "bot@example.org",
            "consent": "1",
        },
    )
    assert "Thank you" in bot.text
    with SessionLocal() as db:
        assert (
            db.scalar(select(Contact.id).where(Contact.email == "bot@example.org")) is None
            and db.scalar(select(EventTicket.id)) is None
        )


def test_a_full_event_takes_a_waiting_list_then_says_sold_out(social) -> None:
    event_id = _create(social, limit="2", waiting="1")
    _types(social, event_id)
    with SessionLocal() as db:
        guest_type = db.scalar(select(TicketType.id).where(TicketType.name == "Guest"))
    first = _public_book(
        TestClient(app), event_id, guest_type, quantity="2", email="a1@example.org", name="A One"
    )
    assert "holding 2 places" in first.text
    waiting = _public_book(
        TestClient(app), event_id, guest_type, quantity="1", email="b1@example.org", name="B One"
    )
    assert "You are on the waiting list" in waiting.text
    full = _public_book(
        TestClient(app), event_id, guest_type, quantity="1", email="c1@example.org", name="C One"
    )
    assert "sold out" in full.text.lower()
    assert "sold out" in TestClient(app).get(f"/whats-on/{event_id}").text.lower()


def test_staff_add_bookings_cancel_and_export_the_list(social) -> None:
    event_id = _create(social)
    _types(social, event_id)
    with SessionLocal() as db:
        contact = Contact(name="Walk In", email="walkin@example.org")
        db.add(contact)
        db.commit()
        cid, child = contact.id, db.scalar(select(TicketType.id).where(TicketType.name == "Child"))
    token = csrf(social, f"/app/events/{event_id}")
    added = social.post(
        f"/app/events/{event_id}/attendees",
        data={"csrf": token, "contact_id": cid, "type_id": child, "quantity": 2},
        follow_redirects=True,
    )
    assert "2 place(s) booked for Walk In" in added.text and "Walk In" in added.text
    with SessionLocal() as db:
        ticket_id = db.scalar(select(EventTicket.id))
    assert (
        "Done"
        in social.post(
            f"/app/events/{event_id}/tickets/{ticket_id}/checkin",
            data={"csrf": token},
            follow_redirects=True,
        ).text
    )
    sheet = social.get(f"/app/events/{event_id}/attendees.csv").text.splitlines()
    assert (
        sheet[0].startswith("buyer,for,email,ticket,who,status,price,arrived")
        and sheet[1].startswith("Walk In")
        and sheet[1].endswith("yes")
    )
    cancelled = social.post(
        f"/app/events/{event_id}/tickets/{ticket_id}/cancel",
        data={"csrf": token},
        follow_redirects=True,
    )
    assert "no payment to return".capitalize() in cancelled.text
    assert (
        "Choose a contact"
        in social.post(
            f"/app/events/{event_id}/attendees",
            data={"csrf": token, "contact_id": 99999, "type_id": child},
            follow_redirects=True,
        ).text
    )
    assert (
        social.post(
            f"/app/events/{event_id}/tickets/99999/cancel", data={"csrf": token}
        ).status_code
        == 404
    )




@pytest.fixture
def member_ann():
    with SessionLocal() as db:
        member = Member(
            name="Ann Sailor",
            email="ann@example.org",
            membership_type="Single Adult Sailing",
            renewal_status="active",
            active=True,
        )
        db.add(member)
        db.flush()
        db.add(Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id))
        db.commit()
    return login("ann@example.org")


def test_notices_documents_wrapup_and_copying(social) -> None:
    event_id = _create(social)
    _types(social, event_id)
    token = csrf(social, f"/app/events/{event_id}")
    social.post(
        f"/app/events/{event_id}/notices",
        data={"csrf": token, "message": "Bring a dish"},
        follow_redirects=True,
    )
    assert (
        "https://"
        in social.post(
            f"/app/events/{event_id}/documents",
            data={"csrf": token, "title": "Menu", "uri": "javascript:alert(1)"},
            follow_redirects=True,
        ).text
    )  # refused: only web links
    social.post(
        f"/app/events/{event_id}/documents",
        data={"csrf": token, "title": "Menu", "uri": "https://example.org/menu.pdf"},
        follow_redirects=True,
    )
    social.post(
        f"/app/events/{event_id}/wrapup",
        data={"csrf": token, "tally": "62 came", "summary": "Great", "actions": "More chairs"},
        follow_redirects=True,
    )
    page = social.get(f"/app/events/{event_id}").text
    assert (
        "Bring a dish" in page
        and "Menu" in page
        and "62 came" in page
        and 'href="javascript' not in page
    )
    copied = social.post(
        f"/app/events/{event_id}/duplicate", data={"csrf": token, "weeks": 2}, follow_redirects=True
    )
    assert "Copied as a draft" in copied.text and "Member" in copied.text
    with SessionLocal() as db:
        original, copy = db.scalars(select(Event).order_by(Event.id)).all()
        assert (
            (copy.start_at - original.start_at) == timedelta(weeks=2)
            and not copy.published
            and copy.tickets_limit == original.tickets_limit
        )


def test_a_member_buys_a_ticket_pays_and_cancels(social, member_ann) -> None:
    event_id = _create(social)
    _types(social, event_id)
    page = member_ann.get("/app/me/events")
    assert (
        page.status_code == 200
        and "Club Dinner" in page.text
        and "Member £15.00" in page.text
        and "Guest" not in page.text.split("Buy tickets")[1].split("Coming up")[0]
    )
    with SessionLocal() as db:
        member_type = db.scalar(select(TicketType.id).where(TicketType.name == "Member"))
    token = csrf(member_ann, "/app/me/events")
    booked = member_ann.post(
        f"/app/me/events/{event_id}/book",
        data={"csrf": token, "type_id": member_type, "quantity": 2},
        follow_redirects=False,
    )
    assert booked.status_code == 303 and "/app/me/invoices/" in booked.headers["location"]
    assert "pay below" in member_ann.get(booked.headers["location"]).text
    mine = member_ann.get("/app/me/events")
    assert "held" in mine.text and "pay now" in mine.text
    with SessionLocal() as db:
        ticket_id = db.scalar(select(EventTicket.id))
    assert (
        "Cancelled"
        in member_ann.post(
            f"/app/me/tickets/{ticket_id}/cancel", data={"csrf": token}, follow_redirects=True
        ).text
    )
    other = login("bob@example.org", Role.MEMBER)
    assert (
        other.post(
            f"/app/me/tickets/{ticket_id}/cancel", data={"csrf": csrf(other, "/app")}
        ).status_code
        == 404
    )


def test_only_events_people_can_write_but_committee_can_look(social) -> None:
    event_id = _create(social)
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    assert (
        bosun.get("/app/events").status_code == 200
        and bosun.get(f"/app/events/{event_id}").status_code == 200
    )
    assert bosun.get("/app/events/new").status_code == 403
    assert (
        bosun.post(
            f"/app/events/{event_id}/types", data={"csrf": csrf(bosun), "name": "x", "price": "1"}
        ).status_code
        == 403
    )
    social_again = login("social2@example.org", Role.COMMITTEE, hats="social")
    assert social_again.get("/app/events/new").status_code == 200
    member = login("m@example.org", Role.MEMBER)
    assert member.get("/app/events").status_code in (403, 404)


def test_jobs_release_unpaid_holds(social) -> None:
    event_id = _create(social)
    _types(social, event_id)
    with SessionLocal() as db:
        member = Member(
            name="Ann Sailor",
            email="ann@example.org",
            membership_type="x",
            renewal_status="active",
            active=True,
        )
        db.add(member)
        db.flush()
        contact = Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id)
        db.add(contact)
        db.flush()
        event = db.get(Event, event_id)
        events.book(
            db,
            event,
            db.scalar(select(TicketType).where(TicketType.name == "Member")),
            contact,
            1,
            now=datetime.now(UTC),
        )
        db.commit()
        assert jobs.expire_event_holds(db, datetime.now(UTC).date(), False) == {"released": 0}
        assert jobs.expire_event_holds(
            db, datetime.now(UTC).date() + timedelta(days=10), False
        ) == {"released": 1}
        assert db.scalar(select(Invoice.status)) == "void"
