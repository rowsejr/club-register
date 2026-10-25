from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import finance, training
from app.db import SessionLocal
from app.models import (
    Contact,
    ContactRelation,
    Invoice,
    Member,
    OutboundEmail,
    Qualification,
    TrainingBooking,
)

TODAY = date(2026, 10, 10)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _person(db, name="Ann Sailor", email="ann@example.com", born=date(1985, 3, 30), member=True) -> Contact:
    member_id = None
    if member:
        m = Member(name=name, email=email)
        db.add(m)
        db.flush()
        member_id = m.id
    contact = Contact(name=name, email=email, member_id=member_id, birth_date=born)
    db.add(contact)
    db.flush()
    return contact


def _course(db, *, price=8000, capacity=2, days_ahead=30, **kw) -> training.TrainingCourse:
    start = datetime.combine(TODAY + timedelta(days=days_ahead), datetime.min.time(), tzinfo=UTC).replace(hour=9)
    values = {"name": "RYA Level 2", "starts_at": start, "ends_at": start + timedelta(days=2), "capacity": capacity, "price_minor": price}
    values.update(kw)
    return training.save_course(db, None, **values)


def test_course_validation_and_capacity_floor(db) -> None:
    with pytest.raises(training.TrainingError, match="name"):
        _course(db, name=" ")
    with pytest.raises(training.TrainingError, match="ends before"):
        _course(db, ends_at=datetime(2020, 1, 1, tzinfo=UTC))
    with pytest.raises(training.TrainingError, match="at least one place"):
        _course(db, capacity=0)
    with pytest.raises(training.TrainingError, match="negative"):
        _course(db, price=-1)
    course = _course(db, capacity=2)
    training.book(db, course, _person(db), actor="t", today=TODAY)
    training.book(db, course, _person(db, "Bob", "b@example.com"), actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="already taken"):
        training.save_course(db, course, name="RYA Level 2", starts_at=course.starts_at, ends_at=course.ends_at, capacity=1)
    with pytest.raises(training.TrainingError):
        training.save_course(db, course, name="x", starts_at=course.starts_at, ends_at=course.ends_at, capacity=1, status="weird")


def test_paid_booking_holds_a_place_until_the_invoice_is_paid(db) -> None:
    course = _course(db)
    ann = _person(db)
    booking = training.book(db, course, ann, actor="ann@example.com", today=TODAY)
    assert booking.status == "pending_payment" and booking.contact_id == ann.id and booking.member_id == ann.member_id
    invoice = db.get(Invoice, booking.invoice_id)
    assert invoice.status == "issued" and invoice.amount_minor == 8000 and invoice.contact_id == ann.id
    line = db.scalar(select(finance.InvoiceLine).where(finance.InvoiceLine.invoice_id == invoice.id))
    assert line.nominal_code == "0103" and "RYA Level 2 (Ann Sailor)" in line.description
    assert training.seats(db, course) == (1, 1, 0)
    held = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "training_booking"))
    assert held and "held" in held.subject and "7 days" in held.body
    finance.record_payment(db, invoice, amount_minor=8000, method="bank", reference="T1")
    assert booking.status == "booked"
    assert db.scalar(select(OutboundEmail).where(OutboundEmail.subject.like("%confirmed%")))


def test_free_courses_book_immediately_without_an_invoice(db) -> None:
    course = _course(db, price=0)
    booking = training.book(db, course, _person(db), actor="t", today=TODAY)
    assert booking.status == "booked" and booking.invoice_id is None and db.scalar(select(func.count()).select_from(Invoice)) == 0


def test_booking_rules(db) -> None:
    ann = _person(db)
    closed = _course(db, status="closed")
    with pytest.raises(training.TrainingError, match="not open"):
        training.book(db, closed, ann, actor="t", today=TODAY)
    started = _course(db, days_ahead=-1)
    with pytest.raises(training.TrainingError, match="already started"):
        training.book(db, started, ann, actor="t", today=TODAY)
    course = _course(db, min_age=16)
    kid = _person(db, "Kid", None, born=date(2015, 5, 1), member=False)
    with pytest.raises(training.TrainingError, match="too young"):
        training.book(db, course, kid, actor="t", today=TODAY)
    training.book(db, course, ann, actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="already has a place"):
        training.book(db, course, ann, actor="t", today=TODAY)


def test_juniors_need_a_guardian_booker_and_current_consent(db) -> None:
    parent, other = _person(db), _person(db, "Other Parent", "o@example.com")
    kid = _person(db, "Charlie Child", None, born=date(2015, 5, 1), member=False)
    db.add(ContactRelation(from_contact_id=parent.id, to_contact_id=kid.id, relation_type="child"))
    course = _course(db)
    assert [c.name for c in training.children_of(db, parent.id)] == ["Charlie Child"]
    with pytest.raises(training.TrainingError, match="consent and emergency"):
        training.book(db, course, kid, booked_by=parent, actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="emergency contact"):
        training.save_profile(db, kid, signed_by=parent, emergency_name="", emergency_phone="")
    training.save_profile(db, kid, signed_by=parent, emergency_name="Ann", emergency_phone="0123", medical="Asthma", photo_ok=True, today=TODAY)
    assert training.consent_current(db, kid.id, date(2027, 6, 1)) and not training.consent_current(db, kid.id, date(2028, 1, 1))
    with pytest.raises(training.TrainingError, match="parent or guardian must book"):
        training.book(db, course, kid, actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="your children"):
        training.book(db, course, kid, booked_by=other, actor="t", today=TODAY)
    booking = training.book(db, course, kid, booked_by=parent, actor="t", today=TODAY)
    assert booking.booked_by_contact_id == parent.id and db.get(Invoice, booking.invoice_id).contact_id == parent.id  # the parent is billed
    assert booking.member_id is None  # a child need not be a member
    staff = _course(db, name="Staff booked")
    assert training.book(db, staff, kid, booked_by=None, actor="sec", staff=True, today=TODAY).status == "pending_payment"


def test_full_courses_use_a_waiting_list_that_promotes_in_order(db) -> None:
    course = _course(db, capacity=1, waitlist_limit=1)
    first, second, third = _person(db), _person(db, "Bob", "b@example.com"), _person(db, "Cara", "c@example.com")
    held = training.book(db, course, first, actor="t", today=TODAY)
    waiting = training.book(db, course, second, actor="t", today=TODAY)
    assert waiting.status == "waitlist" and waiting.invoice_id is None
    with pytest.raises(training.TrainingError, match="waiting list is full"):
        training.book(db, course, third, actor="t", today=TODAY)
    assert training.seats(db, course) == (1, 0, 1)
    result = training.cancel(db, held, "ann", today=TODAY)
    assert "invoice voided" in result and held.status == "cancelled"
    assert waiting.status == "pending_payment" and waiting.invoice_id and waiting.offered_until is not None
    assert db.scalar(select(OutboundEmail).where(OutboundEmail.subject.like("%a place has come up%")))
    assert training.seats(db, course) == (1, 0, 0)
    again = training.book(db, course, first, actor="t", today=TODAY)  # the cancelled person can come back (and waits)
    assert again.id == held.id and again.status == "waitlist"


def test_cancellation_refunds_follow_the_cutoff(db) -> None:
    near = _course(db, days_ahead=3, cancel_cutoff_days=7)
    far = _course(db, name="Far", days_ahead=40, cancel_cutoff_days=7)
    ann, bob = _person(db), _person(db, "Bob", "b@example.com")
    late = training.book(db, near, ann, actor="t", today=TODAY)
    early = training.book(db, far, bob, actor="t", today=TODAY)
    finance.record_payment(db, db.get(Invoice, late.invoice_id), amount_minor=8000, method="bank", reference="L")
    finance.record_payment(db, db.get(Invoice, early.invoice_id), amount_minor=8000, method="bank", reference="E")
    assert "fee retained" in training.cancel(db, late, "t", today=TODAY)
    assert db.get(Invoice, late.invoice_id).status == "paid"  # no credit: too late
    assert "credit note issued" in training.cancel(db, early, "t", today=TODAY)
    assert db.get(Invoice, early.invoice_id).status == "paid" and db.scalar(select(func.count()).select_from(Invoice).where(Invoice.kind == "credit_note")) == 1
    with pytest.raises(training.TrainingError, match="cannot be cancelled"):
        training.cancel(db, early, "t", today=TODAY)


def test_unpaid_places_expire_and_pass_to_the_next_person(db) -> None:
    course = _course(db, capacity=1)
    ann, bob = _person(db), _person(db, "Bob", "b@example.com")
    held = training.book(db, course, ann, actor="t", today=TODAY)
    waiting = training.book(db, course, bob, actor="t", today=TODAY)
    assert training.expire_unpaid(db, now=datetime.now(UTC)) == 0  # still inside the hold window
    assert training.expire_unpaid(db, now=datetime.now(UTC) + timedelta(days=8)) == 1
    assert held.status == "cancelled" and "not paid in time" in held.note and db.get(Invoice, held.invoice_id).status == "void"
    assert waiting.status == "pending_payment"


def test_cancelling_a_course_refunds_everyone(db) -> None:
    course = _course(db, days_ahead=2)
    ann, bob = _person(db), _person(db, "Bob", "b@example.com")
    paid, unpaid = training.book(db, course, ann, actor="t", today=TODAY), training.book(db, course, bob, actor="t", today=TODAY)
    finance.record_payment(db, db.get(Invoice, paid.invoice_id), amount_minor=8000, method="bank", reference="P")
    with pytest.raises(training.TrainingError, match="Give a reason"):
        training.cancel_course(db, course, "t", " ")
    assert training.cancel_course(db, course, "t", "Instructor ill") == 2
    assert course.status == "cancelled" and paid.status == unpaid.status == "cancelled"
    assert db.get(Invoice, unpaid.invoice_id).status == "void" and db.scalar(select(func.count()).select_from(Invoice).where(Invoice.kind == "credit_note")) == 1  # refunded even inside the cutoff
    assert db.scalar(select(func.count()).select_from(OutboundEmail).where(OutboundEmail.subject.like("%has been cancelled"))) == 2
    with pytest.raises(training.TrainingError, match="Already"):
        training.cancel_course(db, course, "t", "again")


def test_outcomes_issue_qualifications_that_expire(db) -> None:
    qtype = training.save_qualification_type(db, "RYA Level 2", "RYA", validity_months=None)
    first_aid = training.save_qualification_type(db, "First Aid", "St John", validity_months=36)
    assert training.save_qualification_type(db, "first aid", "St John", 24).id == first_aid.id and first_aid.validity_months == 24
    with pytest.raises(training.TrainingError, match="at least one month"):
        training.save_qualification_type(db, "Bad", validity_months=0)
    course = _course(db, price=0, qualification_type_id=first_aid.id)
    ann = _person(db)
    booking = training.book(db, course, ann, actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="Unknown outcome"):
        training.record_outcome(db, booking, "banana", "t")
    assert training.record_outcome(db, booking, "attended", "t") is None
    issued = training.record_outcome(db, booking, "passed", "t", today=date(2026, 10, 31))
    assert issued and issued.name == "First Aid" and issued.expires_on == date(2028, 10, 31) and issued.source_booking_id == booking.id and issued.type_id == first_aid.id
    assert training.record_outcome(db, booking, "passed", "t", today=date(2026, 10, 31)) .id == issued.id  # idempotent
    soon = training.issue_qualification(db, ann, qtype, date(2026, 1, 1))
    assert soon.expires_on is None
    expiring = training.expiring_qualifications(db, 800, date(2026, 10, 10))
    assert [e.qualification.name for e in expiring] == ["First Aid"] and expiring[0].days_left == (date(2028, 10, 31) - date(2026, 10, 10)).days
    pending = training.book(db, _course(db, name="Paid", price=100), _person(db, "Bob", "b@example.com"), actor="t", today=TODAY)
    with pytest.raises(training.TrainingError, match="must be paid"):
        training.record_outcome(db, pending, "attended", "t")
    assert db.scalar(select(func.count()).select_from(Qualification)) == 2 and db.scalar(select(func.count()).select_from(ContactRelation)) == 0
    assert db.scalar(select(func.count()).select_from(TrainingBooking)) == 2
