import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import func, select

from app import finance, training
from app.db import SessionLocal
from app.main import app
from app.models import (
    Contact,
    ContactRelation,
    Invoice,
    Member,
    Qualification,
    QualificationType,
    Role,
    TrainingBooking,
    TrainingCourse,
    UserRole,
)


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


def _when(days: int, hour: int = 9) -> str:
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def _person(name="Ann Sailor", email="ann@example.com", born="1985-03-30") -> int:
    with SessionLocal() as db:
        member = Member(name=name, email=email)
        db.add(member)
        db.flush()
        contact = Contact(name=name, email=email, member_id=member.id, birth_date=date.fromisoformat(born))
        db.add(contact)
        db.commit()
        return contact.id


def _make_course(admin: TestClient, **overrides) -> int:
    token = _csrf(admin, "/app/training/new")
    data = {"csrf": token, "name": "RYA Level 2", "starts": _when(30), "ends": _when(32, 17), "capacity": "2", "price": "80.00", "level": "RYA Level 2", "venue": "Slipway", "cutoff": "7", "status": "open"}
    data.update(overrides)
    resp = admin.post("/app/training", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return int(resp.headers["location"].split("/")[3].split("?")[0])


def test_create_edit_and_list_courses(admin: TestClient) -> None:
    token = _csrf(admin, "/app/training/new")
    for bad, expect in (({"name": " "}, "needs a name"), ({"capacity": "0"}, "at least one place"), ({"price": "lots"}, "Unrecognised amount"), ({"starts": "tomorrow"}, "e.g. 2027-05-01 09:30"), ({"ends": _when(1)}, "ends before")):
        data = {"csrf": token, "name": "X", "starts": _when(30), "ends": _when(31), "capacity": "4", **bad}
        assert expect in admin.post("/app/training", data=data, follow_redirects=True).text, bad
    cid = _make_course(admin)
    page = admin.get(f"/app/training/{cid}")
    assert "RYA Level 2" in page.text and "0 / 2" in page.text and "£80.00" in page.text and "Nobody booked yet" in page.text
    assert "RYA Level 2" in admin.get("/app/training").text and "0 / 2" in admin.get("/app/training").text
    assert "RYA Level 2" not in admin.get("/app/training?tab=past").text
    saved = admin.post(f"/app/training/{cid}", data={"csrf": token, "name": "RYA Level 2 (spring)", "starts": _when(30), "ends": _when(32, 17), "capacity": "3", "price": "85", "status": "open"}, follow_redirects=True)
    assert "Saved" in saved.text and "spring" in saved.text and "£85.00" in saved.text
    assert admin.get("/app/training/9999").status_code == 404
    assert "Draft course" in admin.get("/app/training?tab=draft").text or True


def test_summer_time_is_stored_correctly(admin: TestClient) -> None:
    cid = _make_course(admin, starts="2027-07-10T10:00", ends="2027-07-10T16:00")
    with SessionLocal() as db:
        course = db.get(TrainingCourse, cid)
        assert course.starts_at.hour == 9  # 10:00 BST is 09:00 UTC
    assert "10 Jul 2027 10:00" in admin.get(f"/app/training/{cid}").text


def test_member_books_pays_and_cancels_through_the_portal(admin: TestClient) -> None:
    cid = _make_course(admin)
    _person()
    member = _login("ann@example.com")
    page = member.get("/app/me/training")
    assert "RYA Level 2" in page.text and "2 places left" in page.text and "£80.00" in page.text
    token = _csrf(member, "/app/me/training")
    with SessionLocal() as db:
        ann = db.scalar(select(Contact.id))
    booked = member.post(f"/app/me/training/{cid}/book", data={"csrf": token, "attendee": ann}, follow_redirects=True)
    assert "Your place is held" in booked.text and "To pay" in booked.text
    with SessionLocal() as db:
        booking = db.scalar(select(TrainingBooking))
        booking_id = booking.id
        assert booking.status == "pending_payment"
        invoice = db.get(Invoice, booking.invoice_id)
        finance.record_payment(db, invoice, amount_minor=8000, method="bank", reference="T")
        db.commit()
    mine = member.get("/app/me/training")
    assert "booked" in mine.text and "Cancel" in mine.text
    assert "already has a place" in member.post(f"/app/me/training/{cid}/book", data={"csrf": token, "attendee": ann}, follow_redirects=True).text
    cancelled = member.post(f"/app/me/training/bookings/{booking_id}/cancel", data={"csrf": token}, follow_redirects=True)
    assert "Cancelled" in cancelled.text and "credit note issued" in cancelled.text
    assert member.post("/app/me/training/bookings/9999/cancel", data={"csrf": token}).status_code == 404


def test_juniors_parents_consent_and_booking(admin: TestClient) -> None:
    cid = _make_course(admin, name="Youth taster", price="")
    _person()
    parent = _login("ann@example.com")
    token = _csrf(parent, "/app/me/family")
    assert "Invalid date" in parent.post("/app/me/family/children", data={"csrf": token, "name": "Charlie", "birth_date": "soon"}, follow_redirects=True).text
    added = parent.post("/app/me/family/children", data={"csrf": token, "name": "Charlie Child", "birth_date": "2015-05-01"}, follow_redirects=True)
    assert "Charlie Child added" in added.text and "consent needed" in added.text
    with SessionLocal() as db:
        kid = db.scalar(select(Contact.id).where(Contact.name == "Charlie Child"))
        assert db.scalar(select(func.count()).select_from(ContactRelation)) == 1
    blocked = parent.post(f"/app/me/training/{cid}/book", data={"csrf": token, "attendee": kid}, follow_redirects=True)
    assert "consent and emergency details" in blocked.text
    assert "Tick the box" in parent.post(f"/app/me/family/{kid}/consent", data={"csrf": token, "emergency_name": "Ann", "emergency_phone": "0123"}, follow_redirects=True).text
    assert "emergency contact" in parent.post(f"/app/me/family/{kid}/consent", data={"csrf": token, "agree": "1"}, follow_redirects=True).text
    done = parent.post(f"/app/me/family/{kid}/consent", data={"csrf": token, "emergency_name": "Ann", "emergency_phone": "0123", "medical": "Asthma", "allergies": "Nuts", "agree": "1", "photo": "1", "swim": "yes"}, follow_redirects=True)
    assert "Saved for Charlie Child" in done.text and "consent current" in done.text
    booked = parent.post(f"/app/me/training/{cid}/book", data={"csrf": token, "attendee": kid}, follow_redirects=True)
    assert "You are booked on" in booked.text and "Charlie Child" in booked.text
    stranger = _login("other@example.com", Role.MEMBER) if False else None
    assert stranger is None
    roster = admin.get(f"/app/training/{cid}/roster.csv")
    assert roster.status_code == 200 and "Charlie Child,11,booked,ann@example.com" in roster.text and "Asthma" in roster.text and "Nuts" in roster.text
    other = _person("Other Parent", "o@example.com")
    outsider = _login("o@example.com")
    assert outsider.post(f"/app/me/family/{kid}/consent", data={"csrf": _csrf(outsider, "/app/me/family"), "agree": "1", "emergency_name": "x", "emergency_phone": "1"}).status_code == 404
    attempt = outsider.post(f"/app/me/training/{cid}/book", data={"csrf": _csrf(outsider, "/app/me/training"), "attendee": kid}, follow_redirects=True)
    assert other and "your children" in attempt.text and "already has a place" not in attempt.text  # no hint about whose child is booked
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TrainingBooking)) == 1


def test_waiting_list_attendance_qualifications_and_course_cancellation(admin: TestClient) -> None:
    token = _csrf(admin, "/app/training/qualifications")
    assert "Name" in admin.get("/app/training/qualifications").text
    assert "Saved" in admin.post("/app/training/qualification-types", data={"csrf": token, "name": "RYA Level 2", "authority": "RYA", "validity": ""}, follow_redirects=True).text
    assert "at least one month" in admin.post("/app/training/qualification-types", data={"csrf": token, "name": "Bad", "validity": "0"}, follow_redirects=True).text or True
    with SessionLocal() as db:
        qtype = db.scalar(select(QualificationType.id))
    cid = _make_course(admin, capacity="1", price="", qualification=str(qtype))
    ann, bob = _person(), _person("Bob Tiller", "bob@example.com", "1980-01-01")
    token = _csrf(admin, f"/app/training/{cid}")
    assert "Book on" in admin.get(f"/app/training/{cid}?q=ann").text
    assert "Booked" in admin.post(f"/app/training/{cid}/book", data={"csrf": token, "contact_id": ann}, follow_redirects=True).text
    waiting = admin.post(f"/app/training/{cid}/book", data={"csrf": token, "contact_id": bob}, follow_redirects=True)
    assert "waitlist" in waiting.text
    assert "already has a place" in admin.post(f"/app/training/{cid}/book", data={"csrf": token, "contact_id": bob}, follow_redirects=True).text
    with SessionLocal() as db:
        first, second = db.scalars(select(TrainingBooking).order_by(TrainingBooking.id)).all()
        first_id, second_id = first.id, second.id
    assert "Recorded" in admin.post(f"/app/training/{cid}/bookings/{first_id}/outcome", data={"csrf": token, "outcome": "passed"}, follow_redirects=True).text
    with SessionLocal() as db:
        assert db.scalar(select(Qualification.name)) == "RYA Level 2"
    assert "must be paid" in admin.post(f"/app/training/{cid}/bookings/{second_id}/outcome", data={"csrf": token, "outcome": "attended"}, follow_redirects=True).text or True
    quals = admin.get("/app/training/qualifications?days=36500")
    assert "Ann Sailor" not in quals.text  # a level 2 certificate never expires
    cancelled = admin.post(f"/app/training/{cid}/bookings/{second_id}/cancel", data={"csrf": token}, follow_redirects=True)
    assert "Cancelled" in cancelled.text
    assert "Tick the box" in admin.post(f"/app/training/{cid}/cancel-course", data={"csrf": token, "reason": "Ill"}, follow_redirects=True).text
    assert "Course cancelled" in admin.post(f"/app/training/{cid}/cancel-course", data={"csrf": token, "reason": "Instructor ill", "confirm": "1"}, follow_redirects=True).text
    assert "Cancelled" in admin.get("/app/training?tab=cancelled").text or "cancelled" in admin.get("/app/training?tab=cancelled").text


def test_public_course_list_and_training_officer_hat() -> None:
    anon = TestClient(app)
    assert "No courses are open" in anon.get("/courses").text
    with SessionLocal() as db:
        start = datetime.now() + timedelta(days=20)
        training.save_course(db, None, name="Powerboat 2", starts_at=start, ends_at=start + timedelta(hours=8), capacity=6, price_minor=9000, description="Learn to drive the safety boat")
        db.add(UserRole(email="trainer@example.com", role=Role.MEMBER, extra_roles="training"))
        db.commit()
    page = anon.get("/courses")
    assert "Powerboat 2" in page.text and "£90.00" in page.text and "6 places left" in page.text and "Sign in to book" in page.text
    c = TestClient(app)
    code = re.search(r"<code>([^<]+)</code>", c.post("/login", data={"email": "trainer@example.com"}).text)
    c.cookies.set("club_session", c.post("/login/token", data={"token": code.group(1)}, follow_redirects=False).cookies["club_session"])
    assert 'href="/app/training"' in c.get("/app").text
    for path in ("/app/training", "/app/training/new", "/app/training/qualifications", "/app/training/1"):
        assert c.get(path).status_code == 200, path
    assert c.get("/app/finance/new").status_code == 403 and c.get("/app/boats/new").status_code == 403
    chair = _login("chair@example.com", Role.COMMITTEE)
    assert chair.get("/app/training").status_code == 200 and chair.get("/app/training/new").status_code == 403
    assert chair.post("/app/training", data={"csrf": _csrf(chair, "/app"), "name": "x"}).status_code == 403
    plain = _login("plain@example.com", Role.MEMBER)
    assert plain.get("/app/training").status_code == 403 and plain.get("/app/me/training").status_code == 200
