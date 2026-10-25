"""Training: course management and rosters for volunteers; booking, family and consent pages for members; a public course list."""

from __future__ import annotations

import csv
import io
import math
from datetime import UTC, date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import finance, training
from app.csvutil import RowError, london_to_utc, parse_minor
from app.db import get_db
from app.models import (
    Contact,
    ContactRelation,
    Invoice,
    ParticipantProfile,
    QualificationType,
    SessionRecord,
    TrainingBooking,
    TrainingCourse,
)
from app.ui.routes import (
    PAGE_SIZE,
    UiError,
    _audit,
    _csv_cell,
    _ctx,
    _opt_date,
    templates,
    ui_session,
)

router = APIRouter(include_in_schema=False)
READ = Depends(ui_session(perm="training.read"))
WRITE = Depends(ui_session(perm="training.write"))
ANY = Depends(ui_session())
LONDON = ZoneInfo("Europe/London")


def _go(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{joiner}{urlencode(params)}" if params else path, status_code=303)


def _money(value: str) -> int:
    value = value.strip()
    if not value:
        return 0
    try:
        minor = parse_minor(value)
    except RowError as exc:
        raise training.TrainingError(str(exc)) from exc
    return minor or 0


def _dt(value: str) -> datetime:
    try:
        naive = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise training.TrainingError("Enter the date and time, e.g. 2027-05-01 09:30") from exc
    return london_to_utc(naive.date(), naive.time())


def _local_input(value: datetime | None) -> str:
    if value is None:
        return ""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(LONDON).strftime("%Y-%m-%dT%H:%M")


def _int_or_none(value: str) -> int | None:
    return int(value) if value.strip().isdigit() else None


def _course(db: Session, course_id: int) -> TrainingCourse:
    course = db.get(TrainingCourse, course_id)
    if course is None:
        raise UiError(404, "Course not found")
    return course


def _course_values(name, starts, ends, capacity, price, min_age, level, venue, description, waitlist, cutoff, qualification, status="") -> dict:  # noqa: ANN001
    return {
        "name": name,
        "starts_at": _dt(starts),
        "ends_at": _dt(ends or starts),
        "capacity": int(capacity) if capacity.strip().isdigit() else 0,
        "price_minor": _money(price),
        "min_age": _int_or_none(min_age),
        "level": level,
        "venue": venue,
        "description": description,
        "waitlist_limit": _int_or_none(waitlist),
        "cancel_cutoff_days": int(cutoff) if cutoff.strip().isdigit() else 7,
        "qualification_type_id": _int_or_none(qualification),
        "status": status or None,
    }


# --------------------------------------------------------------------------- volunteers

TABS = [("upcoming", "Upcoming"), ("past", "Past"), ("draft", "Drafts"), ("cancelled", "Cancelled"), ("all", "All")]


@router.get("/app/training", response_class=HTMLResponse)
def training_list(request: Request, tab: str = "upcoming", page: int = 1, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    now = datetime.now(UTC)
    stmt = select(TrainingCourse).order_by(TrainingCourse.starts_at)
    if tab == "upcoming":
        stmt = stmt.where(TrainingCourse.starts_at >= now, func.coalesce(TrainingCourse.status, "open").in_(["open", "closed"]))
    elif tab == "past":
        stmt = stmt.where(TrainingCourse.starts_at < now).order_by(None).order_by(TrainingCourse.starts_at.desc())
    elif tab in {"draft", "cancelled"}:
        stmt = stmt.where(TrainingCourse.status == tab)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    courses = db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    rows = [{"course": c, "seats": training.seats(db, c)} for c in courses]
    return templates.TemplateResponse(request, "training_list.html", _ctx(session, "training", rows=rows, tab=tab, tabs=TABS, pager={"page": page, "pages": pages, "total": total}, msg=msg, err=err, sub="courses"))


@router.get("/app/training/new", response_class=HTMLResponse)
def course_new(request: Request, err: str = "", session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "course_new.html", _ctx(session, "training", course=None, qtypes=db.scalars(select(QualificationType).order_by(QualificationType.name)).all(), local=_local_input, err=err, msg="", sub="new"))


@router.post("/app/training")
def course_create(
    name: str = Form(""), starts: str = Form(""), ends: str = Form(""), capacity: str = Form(""), price: str = Form(""), min_age: str = Form(""), level: str = Form(""), venue: str = Form(""),
    description: str = Form(""), waitlist: str = Form(""), cutoff: str = Form("7"), qualification: str = Form(""), status: str = Form("open"), session: SessionRecord = WRITE, db: Session = Depends(get_db),
) -> Response:
    try:
        course = training.save_course(db, None, **_course_values(name, starts, ends, capacity, price, min_age, level, venue, description, waitlist, cutoff, qualification, status))
        _audit(db, session, "training.course_create", "course", course.id)
        db.commit()
    except training.TrainingError as exc:
        db.rollback()
        return _go("/app/training/new", err=str(exc))
    return _go(f"/app/training/{course.id}", msg="Course created")


@router.get("/app/training/{course_id:int}", response_class=HTMLResponse)
def course_detail(course_id: int, request: Request, q: str = "", msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    course = _course(db, course_id)
    rows = training.roster(db, course)
    invoices = {b.invoice_id: db.get(Invoice, b.invoice_id) for b, _ in rows if b.invoice_id}
    matches: list[Contact] = []
    if q.strip():
        term = f"%{q.strip()}%"
        matches = list(db.scalars(select(Contact).where(or_(Contact.name.ilike(term), Contact.email.ilike(term))).order_by(Contact.name).limit(15)).all())
    return templates.TemplateResponse(
        request, "course_detail.html",
        _ctx(session, "training", course=course, seats=training.seats(db, course), roster=rows, invoices=invoices, matches=matches, q=q, qtypes=db.scalars(select(QualificationType).order_by(QualificationType.name)).all(), local=_local_input,
             age_on=training.age_on, course_day=course.starts_at.date(), msg=msg, err=err, sub="courses"),
    )


def _course_action(db: Session, course_id: int, fn, ok: str) -> RedirectResponse:  # noqa: ANN001
    course = _course(db, course_id)
    path = f"/app/training/{course_id}"
    try:
        fn(course)
        db.commit()
    except (training.TrainingError, finance.FinanceError, ValueError) as exc:
        db.rollback()
        return _go(path, err=str(exc))
    return _go(path, msg=ok)


@router.post("/app/training/{course_id:int}")
def course_save(
    course_id: int, name: str = Form(""), starts: str = Form(""), ends: str = Form(""), capacity: str = Form(""), price: str = Form(""), min_age: str = Form(""), level: str = Form(""), venue: str = Form(""),
    description: str = Form(""), waitlist: str = Form(""), cutoff: str = Form("7"), qualification: str = Form(""), status: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db),
) -> Response:
    def save(course: TrainingCourse) -> None:
        training.save_course(db, course, **_course_values(name, starts, ends, capacity, price, min_age, level, venue, description, waitlist, cutoff, qualification, status))
        _audit(db, session, "training.course_update", "course", course.id)

    return _course_action(db, course_id, save, "Saved")


@router.post("/app/training/{course_id:int}/book")
def staff_book(course_id: int, contact_id: int = Form(...), booked_by: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def book(course: TrainingCourse) -> None:
        attendee = db.get(Contact, contact_id)
        if attendee is None:
            raise training.TrainingError("Contact not found")
        payer = db.get(Contact, int(booked_by)) if booked_by.strip().isdigit() else None
        training.book(db, course, attendee, booked_by=payer, actor=session.email, staff=True)

    return _course_action(db, course_id, book, "Booked")


@router.post("/app/training/{course_id:int}/bookings/{booking_id:int}/cancel")
def staff_cancel(course_id: int, booking_id: int, session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def cancel(course: TrainingCourse) -> None:
        booking = db.get(TrainingBooking, booking_id)
        if booking is None or booking.course_id != course.id:
            raise training.TrainingError("Booking not found")
        result = training.cancel(db, booking, session.email)
        if result:
            db.add(training.AuditLog(actor_email=session.email, actor_role="user", action="training.cancel_result", target_type="training", target_id=str(booking.id), details={"result": result}))

    return _course_action(db, course_id, cancel, "Cancelled")


@router.post("/app/training/{course_id:int}/bookings/{booking_id:int}/outcome")
def booking_outcome(course_id: int, booking_id: int, outcome: str = Form(...), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def mark(course: TrainingCourse) -> None:
        booking = db.get(TrainingBooking, booking_id)
        if booking is None or booking.course_id != course.id:
            raise training.TrainingError("Booking not found")
        training.record_outcome(db, booking, outcome, session.email)

    return _course_action(db, course_id, mark, "Recorded")


@router.post("/app/training/{course_id:int}/cancel-course")
def course_cancel(course_id: int, reason: str = Form(""), confirm: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    if confirm != "1":
        return _go(f"/app/training/{course_id}", err="Tick the box to confirm cancelling the whole course")
    return _course_action(db, course_id, lambda c: training.cancel_course(db, c, session.email, reason), "Course cancelled and everyone refunded")


@router.get("/app/training/{course_id:int}/roster.csv")
def roster_csv(course_id: int, session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    course = _course(db, course_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["name", "age", "status", "email", "phone", "booked_by", "emergency_contact", "emergency_phone", "medical", "allergies"])
    for booking, contact in training.roster(db, course):
        profile = db.get(ParticipantProfile, contact.id) if contact else None
        payer = db.get(Contact, booking.booked_by_contact_id) if booking.booked_by_contact_id else None
        reach = payer or contact  # a parent is the person to ring about a child
        age = training.age_on(contact, course.starts_at.date()) if contact else None
        values = (
            contact.name if contact else "",
            age if age is not None else "",
            booking.status,
            reach.email if reach else "",
            reach.phone if reach else "",
            payer.name if payer else "",
            profile.emergency_name if profile else "",
            profile.emergency_phone if profile else "",
            profile.medical if profile else "",
            profile.allergies if profile else "",
        )
        writer.writerow([_csv_cell(v) for v in values])
    return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="roster-{course.id}.csv"'})


@router.get("/app/training/qualifications", response_class=HTMLResponse)
def qualifications_page(request: Request, days: int = 90, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(
        request, "qualifications.html",
        _ctx(session, "training", expiring=training.expiring_qualifications(db, max(1, days)), qtypes=db.scalars(select(QualificationType).order_by(QualificationType.name)).all(), days=max(1, days), msg=msg, err=err, sub="qualifications"),
    )


@router.post("/app/training/qualification-types")
def qualification_type_save(name: str = Form(""), authority: str = Form(""), validity: str = Form(""), instructor: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    try:
        training.save_qualification_type(db, name, authority, _int_or_none(validity), instructor == "1")
        db.commit()
    except training.TrainingError as exc:
        db.rollback()
        return _go("/app/training/qualifications", err=str(exc))
    return _go("/app/training/qualifications", msg="Saved")


# --------------------------------------------------------------------------- members


def _me(db: Session, session: SessionRecord) -> Contact | None:
    return db.scalar(select(Contact).where(func.lower(Contact.email) == session.email.lower()).order_by(Contact.id))


@router.get("/app/me/training", response_class=HTMLResponse)
def me_training(request: Request, msg: str = "", err: str = "", session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    me = _me(db, session)
    now = datetime.now(UTC)
    courses = db.scalars(select(TrainingCourse).where(TrainingCourse.starts_at >= now, TrainingCourse.status == "open").order_by(TrainingCourse.starts_at)).all()
    people = ([me] if me else []) + (training.children_of(db, me.id) if me else [])
    mine: list[tuple[TrainingBooking, TrainingCourse, Contact | None]] = []
    if people:
        ids = [p.id for p in people]
        for booking in db.scalars(select(TrainingBooking).where(TrainingBooking.contact_id.in_(ids)).order_by(TrainingBooking.id.desc())):
            course = db.get(TrainingCourse, booking.course_id)
            if course:
                mine.append((booking, course, db.get(Contact, booking.contact_id) if booking.contact_id else None))
    return templates.TemplateResponse(
        request, "me_training.html", _ctx(session, "dashboard", courses=[(c, training.seats(db, c)) for c in courses], people=people, mine=mine, invoices={b.invoice_id: db.get(Invoice, b.invoice_id) for b, _, _ in mine if b.invoice_id}, me=me, msg=msg, err=err)
    )


@router.post("/app/me/training/{course_id:int}/book")
def me_book(course_id: int, attendee: int = Form(...), session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    me = _me(db, session)
    course = db.get(TrainingCourse, course_id)
    person = db.get(Contact, attendee)
    if me is None or course is None or person is None:
        raise UiError(404, "Not found")
    try:
        booking = training.book(db, course, person, booked_by=me, actor=session.email)
        db.commit()
    except training.TrainingError as exc:
        db.rollback()
        return _go("/app/me/training", err=str(exc))
    if booking.status == "waitlist":
        return _go("/app/me/training", msg="The course is full: you are on the waiting list and we will email you if a place comes up")
    if booking.invoice_id:
        return _go(f"/app/me/invoices/{booking.invoice_id}", msg="Your place is held: pay the invoice below to confirm it")
    return _go("/app/me/training", msg="You are booked on")


@router.post("/app/me/training/bookings/{booking_id:int}/cancel")
def me_cancel(booking_id: int, session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    me = _me(db, session)
    booking = db.get(TrainingBooking, booking_id)
    family = {me.id} | {c.id for c in training.children_of(db, me.id)} if me else set()
    if booking is None or booking.contact_id not in family:
        raise UiError(404, "Booking not found")
    try:
        result = training.cancel(db, booking, session.email)
        db.commit()
    except (training.TrainingError, finance.FinanceError) as exc:
        db.rollback()
        return _go("/app/me/training", err=str(exc))
    return _go("/app/me/training", msg="Cancelled" + (f" ({result})" if result else ""))


@router.get("/app/me/family", response_class=HTMLResponse)
def me_family(request: Request, msg: str = "", err: str = "", session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    me = _me(db, session)
    if me is None:
        raise UiError(404, "No details are linked to this sign-in")
    kids = training.children_of(db, me.id)
    today = date.today()
    return templates.TemplateResponse(
        request, "me_family.html",
        _ctx(session, "dashboard", me=me, people=[(c, db.get(ParticipantProfile, c.id), training.consent_current(db, c.id, today), training.age_on(c, today)) for c in [me, *kids]], msg=msg, err=err),
    )


@router.post("/app/me/family/children")
def me_add_child(name: str = Form(""), birth_date: str = Form(""), session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    me = _me(db, session)
    if me is None:
        raise UiError(404, "No details are linked to this sign-in")
    try:
        born = _opt_date(birth_date)
        if not name.strip() or born is None:
            raise ValueError("Enter your child's name and date of birth (YYYY-MM-DD)")
    except ValueError as exc:
        return _go("/app/me/family", err=str(exc))
    child = Contact(name=" ".join(name.split()), birth_date=born)
    db.add(child)
    db.flush()
    db.add(ContactRelation(from_contact_id=me.id, to_contact_id=child.id, relation_type="child"))
    _audit(db, session, "family.add_child", "contact", child.id)
    db.commit()
    return _go("/app/me/family", msg=f"{child.name} added: please complete their consent and emergency details below")


@router.post("/app/me/family/{contact_id:int}/consent")
def me_consent(
    contact_id: int, emergency_name: str = Form(""), emergency_phone: str = Form(""), medical: str = Form(""), allergies: str = Form(""), swim: str = Form(""), photo: str = Form(""), agree: str = Form(""),
    session: SessionRecord = ANY, db: Session = Depends(get_db),
) -> Response:
    me = _me(db, session)
    if me is None or contact_id not in ({me.id} | {c.id for c in training.children_of(db, me.id)}):
        raise UiError(404, "Not found")
    person = db.get(Contact, contact_id)
    if person is None:
        raise UiError(404, "Not found")
    if agree != "1":
        return _go("/app/me/family", err="Tick the box to give consent")
    try:
        training.save_profile(db, person, signed_by=me, emergency_name=emergency_name, emergency_phone=emergency_phone, medical=medical, allergies=allergies, swim_ok={"yes": True, "no": False}.get(swim), photo_ok=photo == "1")
        _audit(db, session, "family.consent", "contact", person.id)
        db.commit()
    except training.TrainingError as exc:
        db.rollback()
        return _go("/app/me/family", err=str(exc))
    return _go("/app/me/family", msg=f"Saved for {person.name}")


# --------------------------------------------------------------------------- public


@router.get("/courses", response_class=HTMLResponse)
def public_courses(request: Request, db: Session = Depends(get_db)) -> Response:
    courses = db.scalars(select(TrainingCourse).where(TrainingCourse.starts_at >= datetime.now(UTC), TrainingCourse.status == "open").order_by(TrainingCourse.starts_at)).all()
    return templates.TemplateResponse(request, "public_courses.html", {"user": None, "nav": "", "club": finance.get_setting(db, "club_name"), "rows": [(c, training.seats(db, c)) for c in courses]})
