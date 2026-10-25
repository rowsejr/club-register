"""Regression tests for defects found in the independent review of M6 to M8."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import login
from sqlalchemy import select

from app import audit, backup, events, finance, jobs, privacy, yearend
from app.db import DATABASE_URL, SessionLocal
from app.main import app
from app.models import (
    AuditLog,
    Contact,
    Event,
    EventTicket,
    Invoice,
    Member,
    NominalCode,
    Role,
    TicketType,
)

NOW = datetime.now(UTC)
START = NOW + timedelta(days=30)
YESTERDAY = date.today() - timedelta(days=1)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0108", label="Open Meetings"))
        session.add(NominalCode(code="0101", label="Membership"))
        session.commit()
        yield session


def _contact(db, name, member=False, active=True):
    m = None
    if member:
        m = Member(name=name, email=f"{name.split()[0].lower()}@example.org", membership_type="Single", renewal_status="active" if active else "lapsed", active=active)
        db.add(m)
        db.flush()
    c = Contact(name=name, email=f"{name.split()[0].lower()}@example.org", member_id=m.id if m else None)
    db.add(c)
    db.flush()
    return c


def _event(db, limit=2, waiting=5, price=2500):
    event = events.save_event(db, None, title="Dinner", start_at=START, bookable=True, published=True, tickets_limit=limit, waiting_limit=waiting)
    ttype = events.save_ticket_type(db, event, None, name="Guest", price_minor=price, audience="all")
    db.commit()
    return event, ttype


# --------------------------------------------------------------------------- events


def test_a_closed_period_does_not_break_hold_expiry_or_cancelling(db) -> None:
    event, ttype = _event(db)
    ann = _contact(db, "Ann Sailor")
    held = events.book(db, event, ttype, ann, 1, now=NOW - timedelta(days=5))
    finance.assert_unlocked(db, None, "x")
    finance.set_lock(db, YESTERDAY, "t", is_admin=True)
    # the invoice was issued today, so it is open; make it look old by locking past its date instead
    held.invoice.issue_date = YESTERDAY - timedelta(days=3)
    db.commit()
    assert events.expire_holds(db, NOW) == 1  # the place is released even though the invoice cannot be voided
    assert held.tickets[0].status == "cancelled" and held.invoice.status == "issued"
    again = events.book(db, event, ttype, _contact(db, "Bob Rower"), 1, now=NOW)
    again.invoice.issue_date = YESTERDAY - timedelta(days=1)
    outcome = events.cancel(db, again.tickets[0], "t")
    assert "left as it is" in outcome and again.tickets[0].status == "cancelled"


def test_cancelling_one_of_several_unpaid_places_reduces_what_the_buyer_owes(db) -> None:
    event, ttype = _event(db, limit=4)
    booking = events.book(db, event, ttype, _contact(db, "Ann Sailor"), 2, now=NOW)
    assert booking.invoice.amount_minor == 5000
    events.cancel(db, booking.tickets[0], "t")
    live = db.get(EventTicket, booking.tickets[1].id)
    new_invoice = db.get(Invoice, live.invoice_id)
    assert booking.invoice.status == "void" and new_invoice.amount_minor == 2500 and new_invoice.status == "issued"
    finance.record_payment(db, new_invoice, amount_minor=2500, method="bank")
    assert live.status == "booked" and new_invoice.status == "paid"  # the one place they kept is confirmed by the new invoice


def test_raising_the_limit_gives_the_waiting_list_their_places_first(db) -> None:
    event, ttype = _event(db, limit=2)
    a, w, b = _contact(db, "Ann Sailor"), _contact(db, "Wendy Waits"), _contact(db, "Bob New")
    events.book(db, event, ttype, a, 2, now=NOW)
    queued = events.book(db, event, ttype, w, 1, now=NOW)
    assert queued.waiting
    events.save_event(db, event.id, title="Dinner", start_at=START, bookable=True, published=True, tickets_limit=10, waiting_limit=5)
    assert queued.tickets[0].status == "held"  # promoted before anyone new can buy
    assert events.book(db, event, ttype, b, 1, now=NOW).tickets[0].status == "held"
    capped = events.save_ticket_type(db, event, None, name="Limited", price_minor=0, audience="all", capacity=1)
    first = events.book(db, event, capped, _contact(db, "Cy One"), 1, now=NOW)
    second = events.book(db, event, capped, _contact(db, "Di Two"), 1, now=NOW)
    assert first.tickets[0].status == "booked" and second.waiting
    events.save_ticket_type(db, event, capped.id, name="Limited", price_minor=0, audience="all", capacity=2)
    assert second.tickets[0].status == "booked"


def test_a_lapsed_member_can_buy_at_the_public_price_on_the_public_page_and_when_signed_in(db) -> None:
    event, ttype = _event(db)
    public_only = events.save_ticket_type(db, event, None, name="Public", price_minor=3000, audience="public")
    db.commit()
    _contact(db, "Lena Lapsed", member=True, active=False)
    db.commit()
    page = TestClient(app).get(f"/whats-on/{event.id}")
    token = re.search(r'name="ft" value="([^"]+)"', page.text).group(1)
    done = TestClient(app).post(f"/whats-on/{event.id}/book", data={"ft": token, "website": "", "type_id": public_only.id, "quantity": "1", "name": "Lena Lapsed", "email": "lena@example.org", "consent": "1"})
    assert "holding 1 place" in done.text and "belongs to a member" not in done.text
    member_client = login("lena@example.org")  # a lapsed member can still sign in
    assert "Public" in member_client.get("/app/me/events").text


def test_garbage_in_the_public_booking_form_is_an_error_message_not_a_crash(db) -> None:
    event, ttype = _event(db)
    client = TestClient(app, raise_server_exceptions=False)
    token = re.search(r'name="ft" value="([^"]+)"', client.get(f"/whats-on/{event.id}").text).group(1)
    for type_id, quantity in (("9" * 30, "1"), (str(ttype.id), "²"), ("٣", "1"), (str(ttype.id), "9" * 30)):
        r = client.post(f"/whats-on/{event.id}/book", data={"ft": token, "website": "", "type_id": type_id, "quantity": quantity, "name": "X Y", "email": "x@example.org", "consent": "1"})
        assert r.status_code == 200, (type_id, quantity)


# --------------------------------------------------------------------------- racing










# --------------------------------------------------------------------------- books




def test_erasing_a_person_also_scrubs_their_audit_trail(db) -> None:
    contact = _contact(db, "Ann Sailor", member=True)
    db.add(AuditLog(actor_email="a", actor_role="x", action="contact.update", target_type="contact", target_id=str(contact.id), details={"changes": {"phone": {"before": "0777", "after": "0888"}}}))
    db.commit()
    privacy.erase_person(db, contact_id=contact.id, actor="admin")
    db.commit()
    rows = list(db.scalars(select(AuditLog).where(AuditLog.action == "contact.update")))
    assert rows[0].details == {"erased": True}


def test_the_pack_does_not_count_a_credit_note_as_money_received(db) -> None:
    contact = _contact(db, "Ann Sailor")
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Subs", "amount_minor": 8000, "nominal_code": "0101"}])
    finance.issue(db, invoice, "t")
    finance.record_payment(db, invoice, amount_minor=3000, method="bank")
    finance.credit_note(db, invoice, amount_minor=5000, reason="goodwill", actor="t")
    db.commit()
    import io
    import zipfile

    data = zipfile.ZipFile(io.BytesIO(yearend.pack(db, date.today() - timedelta(days=30), date.today())))
    payments = data.read("payments-received.csv").decode()
    assert "30.00" in payments and "50.00" not in payments and "totalling 30.00" in data.read("README.txt").decode()


def test_an_unchanged_event_time_is_not_reported_as_a_change() -> None:
    before = audit.snapshot(type("E", (), {"start_at": datetime(2026, 12, 5, 19, 0)})(), ("start_at",))
    after = audit.snapshot(type("E", (), {"start_at": datetime(2026, 12, 5, 19, 0, tzinfo=UTC)})(), ("start_at",))
    assert audit.changes(before, after) == {}


def test_year_end_rejects_silly_years() -> None:
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    for bad in ("0", "99999", "²", "abc"):
        assert treasurer.get(f"/app/admin/year-end?year={bad}").status_code == 404, bad
        assert treasurer.get(f"/app/admin/year-end/pack.zip?year={bad}").status_code == 404, bad
    assert treasurer.get("/app/admin/year-end?year=2025").status_code == 200


# --------------------------------------------------------------------------- hardening


def test_a_backup_in_a_folder_with_odd_characters_still_verifies(tmp_path) -> None:
    with SessionLocal() as db:
        db.add(Member(name="Ann", email="a@example.org", membership_type="Single", renewal_status="active", active=True))
        db.commit()
    odd = tmp_path / "bk#1 and 100%"
    snapshot = backup.backup(DATABASE_URL, odd)
    assert backup.verify(snapshot)["ok"]


def test_a_failing_offsite_copy_does_not_stop_old_backups_being_pruned(monkeypatch, tmp_path) -> None:
    folder = tmp_path / "b"
    folder.mkdir()
    monkeypatch.setenv("CLUB_BACKUP_DIR", str(folder))
    for n in range(20):
        (folder / f"club-2026010{n % 10}-0000{n:02d}.db").write_bytes(b"x")
    monkeypatch.setenv("CLUB_BACKUP_COMMAND", "definitely-not-a-command {file}")
    with SessionLocal() as db:
        db.add(Member(name="Ann", email="a@example.org", membership_type="Single", renewal_status="active", active=True))
        db.commit()
        run = jobs.run_job(db, jobs.JOBS["nightly_backup"], date.today(), False)
    assert run.status == "failed" and len(list(folder.glob("club-*"))) <= 15


def test_monitors_may_use_head_and_error_pages_keep_their_security_headers(monkeypatch) -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert client.head("/healthz").status_code == 200 and client.head("/readyz").status_code in (200, 503)
    from app import hardening

    monkeypatch.setattr(hardening, "_aware", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    crashed = client.get("/readyz", headers={"accept": "text/html"})
    assert crashed.status_code == 500 and "script-src 'self'" in crashed.headers["content-security-policy"] and crashed.headers["x-frame-options"] == "DENY"
    assert TicketType and Event  # imported for the fixtures above
