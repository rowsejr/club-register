"""Event screens for volunteers: the calendar and an event's tickets; and members' own tickets."""

from __future__ import annotations

import calendar as pycalendar
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, events, finance, localtime
from app.db import get_db
from app.models import (
    Contact,
    Event,
    EventDocument,
    EventNotice,
    EventTicket,
    EventWrapUp,
    Member,
    NominalCode,
    SessionRecord,
    TicketType,
)
from app.ui.finance_routes import _back, _csv_response, _money
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
READ = ui_session(perm="events.read")
WRITE = ui_session(perm="events.write")
ANY = ui_session()


def _event(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise UiError(404, "Event not found")
    return event


def _month(year: int | None, month: int | None) -> tuple[int, int]:
    today = date.today()
    y, m = year or today.year, month or today.month
    return (y, m) if 1 <= m <= 12 and 2000 <= y <= 2100 else (today.year, today.month)


# --------------------------------------------------------------------------- calendar


@router.get("/app/events", response_class=HTMLResponse)
def calendar_page(request: Request, year: int | None = None, month: int | None = None, view: str = "month", msg: str = "", err: str = "", session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    y, m = _month(year, month)
    month_events = events.month_events(db, y, m)
    by_day: dict[int, list[Event]] = {}
    for event in month_events:
        local = event.start_at.astimezone(localtime.LONDON) if event.start_at.tzinfo else event.start_at
        if (local.year, local.month) == (y, m):
            by_day.setdefault(local.day, []).append(event)
    weeks = pycalendar.Calendar(firstweekday=0).monthdayscalendar(y, m)
    prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    ctx = _ctx(
        session, "events", year=y, month=m, month_name=pycalendar.month_name[m], weeks=weeks, by_day=by_day, today=date.today(), prev=(prev_y, prev_m), nxt=(next_y, next_m),
        month_events=[e for evs in by_day.values() for e in evs], view=view, avail={e.id: events.availability(db, e) for e in month_events}, msg=msg, err=err,
    )
    return templates.TemplateResponse(request, "events.html", ctx)


def _form_values(event: Event | None) -> dict:
    if event is None:
        return {"title": "", "start": "", "end": "", "location": "", "event_type": "", "description": "", "bookable": False, "limit": "", "waiting": "", "published": False, "closes": "", "nominal": "", "group": "", "notes": ""}
    return {
        "title": event.title, "start": localtime.to_input(event.start_at), "end": localtime.to_input(event.end_at), "location": event.location or "", "event_type": event.event_type or "", "description": event.description or "",
        "bookable": bool(event.bookable), "limit": "" if event.tickets_limit is None else event.tickets_limit, "waiting": "" if event.waiting_limit is None else event.waiting_limit,
        "published": bool(event.published), "closes": localtime.to_input(event.sales_close_at), "nominal": event.nominal_code or "", "group": event.group_name or "", "notes": event.notes or "",
    }


@router.get("/app/events/new", response_class=HTMLResponse)
def event_new(request: Request, start: str = "", session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    values = _form_values(None)
    values["start"] = start
    return templates.TemplateResponse(request, "event_form.html", _ctx(session, "events", event=None, v=values, nominals=db.scalars(select(NominalCode).order_by(NominalCode.code)).all()))


def _int_or_none(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    if not text.isdigit():
        raise events.EventError(f"'{text}' is not a whole number")
    return int(text)


@router.post("/app/events")
@router.post("/app/events/{event_id:int}")
def event_save(
    request: Request,
    event_id: int | None = None,
    title: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    location: str = Form(""),
    event_type: str = Form(""),
    description: str = Form(""),
    bookable: str = Form(""),
    limit: str = Form(""),
    waiting: str = Form(""),
    published: str = Form(""),
    closes: str = Form(""),
    nominal: str = Form(""),
    group: str = Form(""),
    notes: str = Form(""),
    session: SessionRecord = Depends(WRITE),
    db: Session = Depends(get_db),
) -> Response:
    values = {"title": title, "start": start, "end": end, "location": location, "event_type": event_type, "description": description, "bookable": bool(bookable), "limit": limit, "waiting": waiting, "published": bool(published), "closes": closes, "nominal": nominal, "group": group, "notes": notes}
    try:
        starts = localtime.parse_local(start)
        if starts is None:
            raise events.EventError("Say when the event starts")
        before = audit.snapshot(db.get(Event, event_id), audit.EVENT_FIELDS) if event_id and db.get(Event, event_id) else {}
        event = events.save_event(
            db, event_id, title=title, start_at=starts, end_at=localtime.parse_local(end), location=location, event_type=event_type, description=description, bookable=bool(bookable),
            tickets_limit=_int_or_none(limit), waiting_limit=_int_or_none(waiting), published=bool(published), sales_close_at=localtime.parse_local(closes), nominal_code=nominal, group_name=group, notes=notes,
        )
        _audit(db, session, "event.save", "event", event.id, title=event.title, changes=audit.changes(before, audit.snapshot(event, audit.EVENT_FIELDS)) if before else {})
        db.commit()
    except (events.EventError, ValueError) as exc:
        db.rollback()
        existing = db.get(Event, event_id) if event_id else None
        return templates.TemplateResponse(
            request, "event_form.html", _ctx(session, "events", event=existing, v=values, error=str(exc), nominals=db.scalars(select(NominalCode).order_by(NominalCode.code)).all()), status_code=422
        )
    return _back(f"/app/events/{event.id}", msg="Saved")


# --------------------------------------------------------------------------- one event


@router.get("/app/events/{event_id:int}", response_class=HTMLResponse)
def event_detail(event_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    event = _event(db, event_id)
    attendee_rows = events.attendees(db, event)
    invoices = {a.ticket.invoice_id: db.get(finance.Invoice, a.ticket.invoice_id) for a in attendee_rows if a.ticket.invoice_id}
    ctx = _ctx(
        session, "events", event=event, v=_form_values(event), types=events.ticket_types(db, event), avail=events.availability(db, event), attendees=attendee_rows, invoices=invoices,
        notices=db.scalars(select(EventNotice).where(EventNotice.event_id == event.id).order_by(EventNotice.id.desc())).all(),
        documents=db.scalars(select(EventDocument).where(EventDocument.event_id == event.id)).all(), wrap=db.scalar(select(EventWrapUp).where(EventWrapUp.event_id == event.id)),
        nominals=db.scalars(select(NominalCode).order_by(NominalCode.code)).all(), sales=events.sales_state(event), msg=msg, err=err,
    )
    return templates.TemplateResponse(request, "event_detail.html", ctx)


@router.post("/app/events/{event_id:int}/types")
def type_save(event_id: int, type_id: str = Form(""), name: str = Form(""), price: str = Form("0"), audience: str = Form("all"), capacity: str = Form(""), active: str = Form("1"), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    event = _event(db, event_id)
    try:
        events.save_ticket_type(db, event, int(type_id) if type_id.isdigit() else None, name=name, price_minor=_money(price or "0"), audience=audience, capacity=_int_or_none(capacity), active=active == "1")
        _audit(db, session, "event.ticket_type", "event", event_id, name=name)
        db.commit()
    except (events.EventError, finance.FinanceError) as exc:
        db.rollback()
        return _back(f"/app/events/{event_id}", err=str(exc))
    return _back(f"/app/events/{event_id}", msg="Ticket type saved")


@router.post("/app/events/{event_id:int}/attendees")
def attendee_add(event_id: int, contact_id: int = Form(...), type_id: int = Form(...), quantity: int = Form(1), guests: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    event, contact, ttype = _event(db, event_id), db.get(Contact, contact_id), db.get(TicketType, type_id)
    if contact is None or ttype is None:
        return _back(f"/app/events/{event_id}", err="Choose a contact (by their contact number) and a ticket type")
    try:
        booking = events.book(db, event, ttype, contact, quantity, guests=[g for g in guests.splitlines() if g.strip()], actor=session.email, staff=True)
        db.commit()
    except events.EventError as exc:
        db.rollback()
        return _back(f"/app/events/{event_id}", err=str(exc))
    return _back(f"/app/events/{event_id}", msg=f"{len(booking.tickets)} place(s) {'on the waiting list' if booking.waiting else 'booked'} for {contact.name}" + (f"; invoice {booking.invoice.reference}" if booking.invoice else ""))


@router.post("/app/events/{event_id:int}/tickets/{ticket_id:int}/{action}")
def ticket_action(event_id: int, ticket_id: int, action: str, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    ticket = db.get(EventTicket, ticket_id)
    if ticket is None or ticket.event_id != event_id or action not in {"cancel", "checkin", "uncheck"}:
        raise UiError(404, "Ticket not found")
    try:
        if action == "cancel":
            note = events.cancel(db, ticket, session.email)
        else:
            events.check_in(db, ticket, action == "checkin")
            note = "Done"
        db.commit()
    except events.EventError as exc:
        db.rollback()
        return _back(f"/app/events/{event_id}", err=str(exc))
    return _back(f"/app/events/{event_id}", msg=note[:1].upper() + note[1:])


@router.get("/app/events/{event_id:int}/attendees.csv")
def attendees_csv(event_id: int, session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    event = _event(db, event_id)
    rows = [
        [a.contact.name, a.ticket.holder_name or "", a.contact.email or "", a.type_name, "member" if a.member else "public", a.ticket.status, f"{(a.ticket.price_minor or 0) / 100:.2f}", "yes" if a.ticket.checked_in else ""]
        for a in events.attendees(db, event, ("held", "booked", "waiting"))
    ]
    return _csv_response(f"attendees-{event.id}.csv", ["buyer", "for", "email", "ticket", "who", "status", "price", "arrived"], rows)


# --------------------------------------------------------------------------- notices, documents, wrap-up


@router.post("/app/events/{event_id:int}/notices")
def notice_add(event_id: int, message: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    _event(db, event_id)
    if message.strip():
        db.add(EventNotice(event_id=event_id, message=message.strip()))
        db.commit()
    return _back(f"/app/events/{event_id}", msg="Notice added" if message.strip() else "")


@router.post("/app/events/{event_id:int}/documents")
def document_add(event_id: int, title: str = Form(""), uri: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    _event(db, event_id)
    if not title.strip() or not uri.strip().lower().startswith(("http://", "https://")):
        return _back(f"/app/events/{event_id}", err="A document needs a title and a web address starting with https://")
    db.add(EventDocument(event_id=event_id, title=title.strip(), uri=uri.strip()))
    db.commit()
    return _back(f"/app/events/{event_id}", msg="Document linked")


@router.post("/app/events/{event_id:int}/wrapup")
def wrapup_save(event_id: int, tally: str = Form(""), summary: str = Form(""), actions: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    _event(db, event_id)
    wrap = db.scalar(select(EventWrapUp).where(EventWrapUp.event_id == event_id)) or EventWrapUp(event_id=event_id)
    wrap.tally_notes, wrap.result_summary, wrap.actions = tally.strip(), summary.strip(), actions.strip()
    db.add(wrap)
    db.commit()
    return _back(f"/app/events/{event_id}", msg="Wrap-up saved")


@router.post("/app/events/{event_id:int}/duplicate")
def event_duplicate(event_id: int, weeks: int = Form(1), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    from datetime import timedelta

    source = _event(db, event_id)
    shift = timedelta(weeks=max(1, min(weeks, 52)))
    copy = events.save_event(
        db, None, title=source.title, start_at=source.start_at + shift, end_at=(source.end_at + shift) if source.end_at else None, location=source.location or "", event_type=source.event_type or "",
        description=source.description or "", bookable=bool(source.bookable), tickets_limit=source.tickets_limit, waiting_limit=source.waiting_limit, published=False, nominal_code=source.nominal_code or "", group_name=source.group_name or "",
    )
    for ttype in events.ticket_types(db, source):
        events.save_ticket_type(db, copy, None, name=ttype.name, price_minor=ttype.price_minor, audience=ttype.audience, capacity=ttype.capacity, active=ttype.active)
    _audit(db, session, "event.duplicate", "event", copy.id, source=source.id)
    db.commit()
    return _back(f"/app/events/{copy.id}", msg="Copied as a draft: check the date, then publish it")


# --------------------------------------------------------------------------- members: my events and tickets


def _me(db: Session, session: SessionRecord) -> tuple[Member | None, Contact | None]:
    from sqlalchemy import func

    member = db.scalar(select(Member).where(func.lower(Member.email) == session.email.lower()))
    contact = db.scalar(select(Contact).where(Contact.member_id == member.id)) if member else db.scalar(select(Contact).where(func.lower(Contact.email) == session.email.lower()))
    return member, contact


@router.get("/app/me/events", response_class=HTMLResponse)
def me_events(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    member, contact = _me(db, session)
    upcoming = [e for e in events.upcoming(db, 50) if e.bookable]
    open_ = {e.id: events.sales_state(e)[0] for e in upcoming}
    audiences = {"all", "members"} if contact is not None and events._is_member(db, contact) else {"all", "public"}  # a lapsed member pays the public price
    types = {e.id: [t for t in events.ticket_types(db, e, True) if t.audience in audiences] for e in upcoming}
    mine = list(db.execute(select(EventTicket, Event).join(Event, Event.id == EventTicket.event_id).where(EventTicket.contact_id == (contact.id if contact else 0), EventTicket.status != "cancelled").order_by(Event.start_at)))
    all_events = events.upcoming(db, 30)
    return templates.TemplateResponse(
        request, "me_events.html", _ctx(session, "dashboard", upcoming=upcoming, open_=open_, types=types, mine=mine, all_events=all_events, avail={e.id: events.availability(db, e) for e in upcoming}, msg=msg, err=err)
    )


@router.post("/app/me/events/{event_id:int}/book")
def me_book(event_id: int, type_id: int = Form(...), quantity: int = Form(1), guests: str = Form(""), session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    _, contact = _me(db, session)
    event, ttype = _event(db, event_id), db.get(TicketType, type_id)
    if contact is None or ttype is None:
        raise UiError(404, "Not found")
    try:
        booking = events.book(db, event, ttype, contact, quantity, guests=[g for g in guests.splitlines() if g.strip()], actor=session.email)
        db.commit()
    except events.EventError as exc:
        db.rollback()
        return _back("/app/me/events", err=str(exc))
    if booking.invoice is not None:
        return RedirectResponse(f"/app/me/invoices/{booking.invoice.id}?msg=Your+places+are+held:+please+pay+below", status_code=303)
    return _back("/app/me/events", msg="You are on the waiting list" if booking.waiting else "You are booked")


@router.post("/app/me/tickets/{ticket_id:int}/cancel")
def me_cancel(ticket_id: int, session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    _, contact = _me(db, session)
    ticket = db.get(EventTicket, ticket_id)
    if contact is None or ticket is None or ticket.contact_id != contact.id:
        raise UiError(404, "Ticket not found")
    try:
        outcome = events.cancel(db, ticket, session.email, by_member=True)
        db.commit()
    except events.EventError as exc:
        db.rollback()
        return _back("/app/me/events", err=str(exc))
    return _back("/app/me/events", msg=f"Cancelled ({outcome})")
