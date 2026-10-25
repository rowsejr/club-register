"""Training courses, bookings ("tickets"), waiting lists, attendance and qualifications.

A booking is a place on a course. Paid courses raise an invoice through the finance service and the place is held
as ``pending_payment`` until the invoice is paid (bank matching confirms it); free courses are booked at once.
When a place is cancelled the oldest waiting person is offered it, and refunds go through credit notes, so the
treasurer never has to work them out by hand.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance, mailer
from app.models import (
    AuditLog,
    Contact,
    ContactRelation,
    Invoice,
    ParticipantProfile,
    Qualification,
    QualificationType,
    TrainingBooking,
    TrainingCourse,
)

HOLD = ("pending_payment", "booked", "attended", "passed", "failed", "no_show")  # statuses that take a seat
ACTIVE = ("pending_payment", "booked", "waitlist")  # statuses a person can still cancel
OFFER_HOURS = 48
HOLD_DAYS = 7
ADULT_AGE = 18


class TrainingError(ValueError):
    """A rule violation, safe to show to the volunteer or member."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _audit(db: Session, actor: str, action: str, target_id: Any, **details: Any) -> None:
    db.add(AuditLog(actor_email=actor, actor_role="user", action=action, target_type="training", target_id=str(target_id), details=details))


# --------------------------------------------------------------------------- courses


def save_course(
    db: Session,
    course: TrainingCourse | None,
    *,
    name: str,
    starts_at: datetime,
    ends_at: datetime,
    capacity: int,
    price_minor: int = 0,
    min_age: int | None = None,
    level: str | None = None,
    venue: str | None = None,
    description: str | None = None,
    waitlist_limit: int | None = None,
    cancel_cutoff_days: int = 7,
    qualification_type_id: int | None = None,
    nominal_code: str = "0103",
    status: str | None = None,
) -> TrainingCourse:
    name = " ".join(name.split())
    if not name:
        raise TrainingError("A course needs a name")
    if ends_at < starts_at:
        raise TrainingError("The course ends before it starts")
    if capacity < 1:
        raise TrainingError("A course needs at least one place")
    if price_minor < 0:
        raise TrainingError("A price cannot be negative")
    if min_age is not None and min_age < 0:
        raise TrainingError("Minimum age cannot be negative")
    if course is None:
        course = TrainingCourse(name=name, starts_at=starts_at, ends_at=ends_at, capacity=capacity, status="open")
        db.add(course)
    else:
        taken = seats(db, course)[0]
        if capacity < taken:
            raise TrainingError(f"{taken} places are already taken, so the capacity cannot go below that")
    course.name, course.starts_at, course.ends_at, course.capacity = name, starts_at, ends_at, capacity
    course.price_minor, course.min_age, course.nominal_code = price_minor, min_age, nominal_code or "0103"
    course.level, course.venue, course.description = (level or "").strip() or None, (venue or "").strip() or None, (description or "").strip() or None
    course.waitlist_limit, course.cancel_cutoff_days, course.qualification_type_id = waitlist_limit, cancel_cutoff_days, qualification_type_id
    if status:
        if status not in {"draft", "open", "closed", "complete"}:
            raise TrainingError("Unknown status")
        course.status = status
    db.flush()
    return course


def seats(db: Session, course: TrainingCourse) -> tuple[int, int, int]:
    """(taken, free, waiting)."""
    taken = db.scalar(select(func.count()).select_from(TrainingBooking).where(TrainingBooking.course_id == course.id, TrainingBooking.status.in_(HOLD))) or 0
    waiting = db.scalar(select(func.count()).select_from(TrainingBooking).where(TrainingBooking.course_id == course.id, TrainingBooking.status == "waitlist")) or 0
    return taken, max(0, course.capacity - taken), waiting


def roster(db: Session, course: TrainingCourse) -> list[tuple[TrainingBooking, Contact | None]]:
    rows = db.execute(
        select(TrainingBooking, Contact).outerjoin(Contact, Contact.id == TrainingBooking.contact_id).where(TrainingBooking.course_id == course.id).order_by(TrainingBooking.id)
    ).all()
    return [(b, c) for b, c in rows]


# --------------------------------------------------------------------------- people: age, consent, family


def age_on(contact: Contact, on: date) -> int | None:
    born = contact.birth_date
    if born is None:
        return None
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def is_minor(contact: Contact, on: date) -> bool:
    age = age_on(contact, on)
    return age is not None and age < ADULT_AGE


def children_of(db: Session, parent_id: int) -> list[Contact]:
    ids = [r.to_contact_id for r in db.scalars(select(ContactRelation).where(ContactRelation.from_contact_id == parent_id, ContactRelation.relation_type == "child"))]
    return list(db.scalars(select(Contact).where(Contact.id.in_(ids)).order_by(Contact.name))) if ids else []


def consent_current(db: Session, contact_id: int, on: date) -> bool:
    profile = db.get(ParticipantProfile, contact_id)
    return bool(profile and profile.consent_signed_at and profile.consent_valid_to and profile.consent_valid_to >= on)


def save_profile(
    db: Session,
    contact: Contact,
    *,
    signed_by: Contact | None,
    emergency_name: str,
    emergency_phone: str,
    medical: str = "",
    allergies: str = "",
    swim_ok: bool | None = None,
    photo_ok: bool = False,
    consent: bool = True,
    today: date | None = None,
) -> ParticipantProfile:
    today = today or date.today()
    if not emergency_name.strip() or not emergency_phone.strip():
        raise TrainingError("An emergency contact name and phone number are needed")
    profile = db.get(ParticipantProfile, contact.id)
    if profile is None:
        profile = ParticipantProfile(contact_id=contact.id)
        db.add(profile)
    profile.emergency_name, profile.emergency_phone = emergency_name.strip(), emergency_phone.strip()
    profile.medical, profile.allergies = medical.strip() or None, allergies.strip() or None
    profile.swim_ok, profile.photo_ok = swim_ok, photo_ok
    if consent:
        profile.consent_signed_at = _now()
        profile.consent_valid_to = date(today.year + 1, today.month, min(today.day, calendar.monthrange(today.year + 1, today.month)[1]))
        profile.signed_by_contact_id = signed_by.id if signed_by else contact.id
    db.flush()
    return profile


# --------------------------------------------------------------------------- booking


def _payer_for(db: Session, attendee: Contact, booked_by: Contact | None) -> Contact:
    return booked_by or attendee


def _invoice_for(db: Session, course: TrainingCourse, booking: TrainingBooking, attendee: Contact, payer: Contact, actor: str) -> Invoice:
    invoice = finance.create_invoice(
        db,
        contact_id=payer.id,
        lines=[{"description": f"{course.name} ({attendee.name})", "amount_minor": course.price_minor or 0, "nominal_code": course.nominal_code or "0103", "source_type": "booking", "source_id": str(booking.id)}],
        actor=actor,
        notes=f"Training booking {booking.id}",
    )
    return finance.issue(db, invoice, actor)


def _notify(db: Session, contact: Contact | None, subject: str, body: str, booking: TrainingBooking, actor: str, calendar: bool = False) -> None:
    if contact is None or not contact.email or contact.suppressed:
        return
    attachment = None
    course = db.get(TrainingCourse, booking.course_id) if calendar else None
    if course is not None:
        from app import events

        attachment = ("course.ics", events.ics_file(finance.get_setting(db, "club_name"), f"course-{course.id}@club-office", course.name, course.starts_at, course.ends_at, course.venue or "", course.description or ""), "text/calendar")
    mailer.send_email(db, to=contact.email, subject=subject, body=body, related_type="training_booking", related_id=booking.id, actor=actor, reply_to=finance.reply_to(db, "events"), attachment=attachment)


def _place(db: Session, course: TrainingCourse, booking: TrainingBooking, attendee: Contact, payer: Contact, actor: str) -> None:
    """Give a booking a seat: free courses are booked, paid ones are held until the invoice is paid."""
    booking.offered_until = None
    if (course.price_minor or 0) <= 0:
        booking.status = "booked"
        return
    booking.status = "pending_payment"
    booking.invoice_id = _invoice_for(db, course, booking, attendee, payer, actor).id


def book(db: Session, course: TrainingCourse, attendee: Contact, *, booked_by: Contact | None = None, actor: str, staff: bool = False, today: date | None = None) -> TrainingBooking:
    today = today or date.today()
    if booked_by is not None and booked_by.id != attendee.id and not staff and attendee.id not in {c.id for c in children_of(db, booked_by.id)}:
        raise TrainingError("You can only book for yourself or your children")
    if course.status != "open":
        raise TrainingError("This course is not open for booking")
    course_day = _aware(course.starts_at).date()
    if course_day < today:
        raise TrainingError("This course has already started")
    existing = db.scalar(select(TrainingBooking).where(TrainingBooking.course_id == course.id, TrainingBooking.contact_id == attendee.id).order_by(TrainingBooking.id.desc()))
    if existing is not None and existing.status in ACTIVE + ("attended", "passed", "failed", "no_show"):
        raise TrainingError(f"{attendee.name} already has a place on this course")
    age = age_on(attendee, course_day)
    if course.min_age and age is not None and age < course.min_age:
        raise TrainingError(f"{attendee.name} is too young for this course (minimum age {course.min_age})")
    minor = is_minor(attendee, course_day)
    if minor and not consent_current(db, attendee.id, course_day):
        raise TrainingError(f"A parent or guardian must complete {attendee.name}'s consent and emergency details first")
    if minor and booked_by is None and not staff:
        raise TrainingError("A parent or guardian must book for someone under 18")
    payer = _payer_for(db, attendee, booked_by)
    taken, free, waiting = seats(db, course)
    if free <= 0 and course.waitlist_limit is not None and waiting >= course.waitlist_limit:
        raise TrainingError("This course is full and its waiting list is full too")  # refuse before anything is created
    booking = existing if existing is not None else TrainingBooking(course_id=course.id, status="waitlist")
    if existing is None:
        db.add(booking)
    booking.contact_id, booking.member_id = attendee.id, attendee.member_id
    booking.booked_by_contact_id, booking.booked_at, booking.invoice_id, booking.note = (booked_by.id if booked_by else None), _now(), None, None
    if free > 0:
        _place(db, course, booking, attendee, payer, actor)
        db.flush()
        what = "confirmed" if booking.status == "booked" else "held"
        extra = "" if booking.status == "booked" else f"\n\nYour place is held until the invoice is paid (invoice {db.get(Invoice, booking.invoice_id).reference if booking.invoice_id else ''}). Places not paid for within {HOLD_DAYS} days are released."  # type: ignore[union-attr]
        _notify(db, payer, f"{course.name}: your place is {what}", f"Hello {payer.name.split()[0]},\n\n{attendee.name} has a place on {course.name} on {_aware(course.starts_at).strftime('%d %B %Y')}.{extra}", booking, actor, calendar=booking.status == "booked")
    else:
        booking.status = "waitlist"
        db.flush()
        _notify(db, payer, f"{course.name}: you are on the waiting list", f"Hello {payer.name.split()[0]},\n\nThe course is full. {attendee.name} is number {waiting + 1} on the waiting list and we will email you if a place comes up.", booking, actor)
    _audit(db, actor, "training.book", booking.id, course=course.id, status=booking.status)
    return booking


def _release_invoice(db: Session, booking: TrainingBooking, actor: str, refund: bool) -> str:
    invoice = db.get(Invoice, booking.invoice_id) if booking.invoice_id else None
    if invoice is None:
        return ""
    if invoice.status in finance.OPEN and finance.paid_total(db, invoice.id) == 0:
        if refund:
            finance.void_invoice(db, invoice, actor, "Booking cancelled")
            return "invoice voided"
        return "fee retained (cancelled after the cutoff)"
    if invoice.status in (*finance.OPEN, "paid") and finance.paid_total(db, invoice.id) > 0:
        if refund:
            finance.credit_note(db, invoice, amount_minor=invoice.amount_minor, reason="Training booking cancelled", actor=actor)
            return "credit note issued"
        return "fee retained (cancelled after the cutoff)"
    return ""


def cancel(db: Session, booking: TrainingBooking, actor: str, *, today: date | None = None, force_refund: bool = False) -> str:
    today = today or date.today()
    if booking.status not in ACTIVE:
        raise TrainingError(f"A {booking.status.replace('_', ' ')} booking cannot be cancelled")
    course = db.get(TrainingCourse, booking.course_id)
    if course is None:
        raise TrainingError("Course not found")
    cutoff = _aware(course.starts_at).date() - timedelta(days=course.cancel_cutoff_days or 0)
    refund = force_refund or today <= cutoff or booking.status == "waitlist"
    held = booking.status in HOLD
    result = _release_invoice(db, booking, actor, refund)
    booking.status = "cancelled"
    booking.note = result or None
    db.flush()
    _audit(db, actor, "training.cancel", booking.id, refund=refund, result=result)
    if held:
        promote_waitlist(db, course, actor)
    return result


def promote_waitlist(db: Session, course: TrainingCourse, actor: str = "system") -> list[TrainingBooking]:
    """Offer free places to the oldest waiting bookings."""
    promoted: list[TrainingBooking] = []
    while True:
        _, free, _ = seats(db, course)
        if free <= 0:
            break
        nxt = db.scalars(select(TrainingBooking).where(TrainingBooking.course_id == course.id, TrainingBooking.status == "waitlist").order_by(TrainingBooking.id)).first()
        if nxt is None:
            break
        attendee = db.get(Contact, nxt.contact_id) if nxt.contact_id else None
        payer = db.get(Contact, nxt.booked_by_contact_id) if nxt.booked_by_contact_id else attendee
        if attendee is None or payer is None:
            nxt.status = "cancelled"
            db.flush()
            continue
        _place(db, course, nxt, attendee, payer, actor)
        nxt.offered_until = _now() + timedelta(hours=OFFER_HOURS) if nxt.status == "pending_payment" else None
        db.flush()
        _notify(db, payer, f"{course.name}: a place has come up", f"Hello {payer.name.split()[0]},\n\nA place is now available for {attendee.name} on {course.name}. {'It is confirmed.' if nxt.status == 'booked' else f'It is held for you for {OFFER_HOURS} hours: please pay the invoice to keep it.'}", nxt, actor)
        promoted.append(nxt)
    return promoted


def expire_unpaid(db: Session, now: datetime | None = None, hold_days: int = HOLD_DAYS) -> int:
    """Release places whose invoice has stayed unpaid (a job for the scheduler). Returns how many were released."""
    now = now or _now()
    released = 0
    for booking in db.scalars(select(TrainingBooking).where(TrainingBooking.status == "pending_payment")):
        invoice = db.get(Invoice, booking.invoice_id) if booking.invoice_id else None
        limit = _aware(booking.offered_until) if booking.offered_until else _aware(booking.booked_at or now) + timedelta(days=hold_days)
        if invoice is None or invoice.status not in finance.OPEN or finance.paid_total(db, invoice.id) > 0 or limit > now:
            continue
        cancel(db, booking, "system", force_refund=True)
        booking.note = "Released: not paid in time"
        released += 1
    return released


def confirm_on_payment(db: Session, invoice: Invoice) -> None:
    """Hook: a paid training invoice confirms the place."""
    booking = db.scalar(select(TrainingBooking).where(TrainingBooking.invoice_id == invoice.id, TrainingBooking.status == "pending_payment"))
    if booking is None:
        return
    booking.status, booking.offered_until = "booked", None
    db.flush()
    course = db.get(TrainingCourse, booking.course_id)
    attendee = db.get(Contact, booking.contact_id) if booking.contact_id else None
    payer = db.get(Contact, booking.booked_by_contact_id) if booking.booked_by_contact_id else attendee
    if course and attendee:
        _notify(db, payer, f"{course.name}: your place is confirmed", f"Thank you: your payment has arrived and {attendee.name}'s place on {course.name} on {_aware(course.starts_at).strftime('%d %B %Y')} is confirmed.", booking, "system", calendar=True)


finance.PAID_HOOKS.append(confirm_on_payment)


def cancel_course(db: Session, course: TrainingCourse, actor: str, reason: str) -> int:
    """Cancel the whole course: everyone is cancelled and refunded in full."""
    if course.status == "cancelled":
        raise TrainingError("Already cancelled")
    if not reason.strip():
        raise TrainingError("Give a reason (it is emailed to everyone booked)")
    course.status = "cancelled"
    count = 0
    for booking, attendee in roster(db, course):
        if booking.status not in ACTIVE:
            continue
        payer = db.get(Contact, booking.booked_by_contact_id) if booking.booked_by_contact_id else attendee
        result = _release_invoice(db, booking, actor, True)
        booking.status, booking.note = "cancelled", result or "course cancelled"
        db.flush()
        _notify(db, payer, f"{course.name} has been cancelled", f"We are sorry: {course.name} has been cancelled.\n\n{reason.strip()}\n\n{('Refund: ' + result) if result else ''}", booking, actor)
        count += 1
    _audit(db, actor, "training.cancel_course", course.id, reason=reason, bookings=count)
    return count


# --------------------------------------------------------------------------- attendance and qualifications


def _add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def save_qualification_type(db: Session, name: str, authority: str = "", validity_months: int | None = None, is_instructor: bool = False) -> QualificationType:
    name = " ".join(name.split())
    if not name:
        raise TrainingError("A qualification needs a name")
    if validity_months is not None and validity_months < 1:
        raise TrainingError("Validity must be at least one month (leave blank if it never expires)")
    existing = db.scalar(select(QualificationType).where(func.lower(QualificationType.name) == name.lower()))
    qtype = existing or QualificationType(name=name)
    if existing is None:
        db.add(qtype)
    qtype.authority, qtype.validity_months, qtype.is_instructor = authority.strip() or None, validity_months, is_instructor
    db.flush()
    return qtype


def issue_qualification(db: Session, contact: Contact, qtype: QualificationType, on: date | None = None, booking: TrainingBooking | None = None, certificate_no: str | None = None) -> Qualification:
    on = on or date.today()
    existing = db.scalar(select(Qualification).where(Qualification.contact_id == contact.id, Qualification.type_id == qtype.id, Qualification.issued_on == on))
    if existing is not None:
        return existing
    qualification = Qualification(
        contact_id=contact.id,
        name=qtype.name,
        issued_on=on,
        expires_on=_add_months(on, qtype.validity_months) if qtype.validity_months else None,
        authority=qtype.authority,
        type_id=qtype.id,
        certificate_no=certificate_no,
        source_booking_id=booking.id if booking else None,
        level="Yes",
    )
    db.add(qualification)
    db.flush()
    return qualification


def record_outcome(db: Session, booking: TrainingBooking, outcome: str, actor: str, today: date | None = None) -> Qualification | None:
    if outcome not in {"attended", "no_show", "passed", "failed"}:
        raise TrainingError("Unknown outcome")
    if booking.status not in {"booked", "attended", "no_show", "passed", "failed"}:
        raise TrainingError(f"A {booking.status.replace('_', ' ')} booking cannot be marked: it must be paid and confirmed first")
    booking.status = outcome
    db.flush()
    _audit(db, actor, "training.outcome", booking.id, outcome=outcome)
    if outcome != "passed":
        return None
    course = db.get(TrainingCourse, booking.course_id)
    attendee = db.get(Contact, booking.contact_id) if booking.contact_id else None
    qtype = db.get(QualificationType, course.qualification_type_id) if course and course.qualification_type_id else None
    if attendee is None or qtype is None:
        return None
    return issue_qualification(db, attendee, qtype, today, booking)


@dataclass
class ExpiringQualification:
    qualification: Qualification
    contact: Contact
    days_left: int


def expiring_qualifications(db: Session, within_days: int = 60, today: date | None = None) -> list[ExpiringQualification]:
    today = today or date.today()
    out = []
    for q, c in db.execute(
        select(Qualification, Contact).join(Contact, Contact.id == Qualification.contact_id).where(Qualification.expires_on.is_not(None), Qualification.expires_on <= today + timedelta(days=within_days)).order_by(Qualification.expires_on)
    ).all():
        out.append(ExpiringQualification(q, c, (q.expires_on - today).days if q.expires_on else 0))
    return out
