"""Bookings land in people's calendars, and medical notes stay with the people running the course."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from helpers import login
from sqlalchemy import select

from app import events, training
from app.db import SessionLocal
from app.main import app
from app.models import Contact, OutboundEmail, ParticipantProfile, Role, TrainingCourse

SOON = datetime.now(UTC).replace(microsecond=0) + timedelta(days=14)


def test_a_free_booking_comes_with_a_calendar_file() -> None:
    with SessionLocal() as db:
        event = events.save_event(db, None, title="Open Meeting", start_at=SOON, end_at=SOON + timedelta(hours=2), location="Clubhouse", published=True, bookable=True, tickets_limit=30)
        ttype = events.save_ticket_type(db, event, None, name="Place", price_minor=0, audience="all")
        buyer = Contact(name="Ann Sailor", email="ann@example.org")
        db.add(buyer)
        db.flush()
        events.book(db, event, ttype, buyer, 1, actor="public")
        db.commit()
        mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "event"))
        assert mail.attachment_name == "event.ics" and "adds it to your calendar" in mail.body
        ics = events.event_ics(db, event).decode()
        assert "BEGIN:VEVENT" in ics and "SUMMARY:Open Meeting" in ics and f"DTSTART:{SOON.strftime('%Y%m%dT%H%M%SZ')}" in ics and "LOCATION:Clubhouse" in ics
        event_id = event.id
    web = TestClient(app)
    download = web.get(f"/whats-on/{event_id}.ics")
    assert download.status_code == 200 and download.headers["content-type"].startswith("text/calendar") and "Open Meeting" in download.text
    assert "Add to my calendar" in web.get(f"/whats-on/{event_id}").text
    assert web.get("/whats-on/999.ics").status_code == 404


def test_a_confirmed_course_place_comes_with_a_calendar_file() -> None:
    with SessionLocal() as db:
        course = TrainingCourse(name="Powerboat Level 2", starts_at=SOON, ends_at=SOON + timedelta(hours=8), capacity=6, price_minor=0, status="open", venue="Slipway")
        person = Contact(name="Bob Rower", email="bob@example.org")
        db.add_all([course, person])
        db.flush()
        booking = training.book(db, course, person, booked_by=person, actor="bob@example.org")
        db.commit()
        assert booking.status == "booked"
        mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "training_booking"))
        assert mail.attachment_name == "course.ics"


def test_medical_notes_on_the_roster_are_only_for_those_running_courses() -> None:
    with SessionLocal() as db:
        course = TrainingCourse(name="Start Sailing", starts_at=SOON, ends_at=SOON + timedelta(hours=6), capacity=6, price_minor=0, status="open")
        person = Contact(name="Kit Sailor", email="kit@example.org")
        db.add_all([course, person])
        db.flush()
        db.add(ParticipantProfile(contact_id=person.id, medical="Asthma: inhaler in bag", emergency_name="Pat Sailor", emergency_phone="07700 900111"))
        training.book(db, course, person, booked_by=person, actor="kit@example.org")
        db.commit()
        course_id = course.id
    committee = login("c@example.org", Role.COMMITTEE).get(f"/app/training/{course_id}/roster.csv").text
    assert "Pat Sailor" in committee and "Asthma" not in committee and "medical" not in committee.splitlines()[0]
    officer = login("t@example.org", Role.COMMITTEE, hats="training").get(f"/app/training/{course_id}/roster.csv").text
    assert "Asthma: inhaler in bag" in officer and officer.splitlines()[0].endswith("medical,allergies")
