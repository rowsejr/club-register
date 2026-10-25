"""Public events: the calendar, tickets for people who are not members (opens, dinners), the iCal feed, and a private link to pay."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import events, finance, payments, ratelimit
from app.db import get_db
from app.models import Contact, Event, EventTicket, OnlinePayment, TicketType
from app.ui.public_routes import _guard, _page
from app.ui.routes import UiError

router = APIRouter(include_in_schema=False)
MAX_OPEN_HOLDS_PER_EMAIL = 2


def _published(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None or not event.published:
        raise UiError(404, "Event not found")
    return event


def _small_int(text: str) -> int | None:
    """A whole number from a form field; anything else (letters, huge or non-ASCII digits) is nothing."""
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 9 else None


def _base(request: Request) -> str:
    return os.getenv("CLUB_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


@router.get("/whats-on", response_class=HTMLResponse)
def public_events(request: Request, db: Session = Depends(get_db)) -> Response:
    upcoming = events.upcoming(db, 60)
    return _page(request, db, "public_events.html", upcoming=upcoming, avail={e.id: events.availability(db, e) for e in upcoming}, on_sale={e.id: events.sales_state(e)[0] for e in upcoming})


@router.get("/events.ics")
def public_ical(request: Request, db: Session = Depends(get_db)) -> Response:
    return Response(events.ical(db, finance.get_setting(db, "club_name"), _base(request)), media_type="text/calendar", headers={"Content-Disposition": 'inline; filename="events.ics"'})


@router.get("/whats-on/{event_id:int}.ics")
def public_event_ics(event_id: int, db: Session = Depends(get_db)) -> Response:
    """One event as a file that adds it to a phone or computer calendar."""
    event = _published(db, event_id)
    return Response(events.event_ics(db, event), media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="event-{event.id}.ics"'})


@router.get("/whats-on/{event_id:int}", response_class=HTMLResponse)
def public_event(event_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    event = _published(db, event_id)
    token = ratelimit.issue_form_token(db)
    db.commit()
    open_, why = events.sales_state(event)
    types = [t for t in events.ticket_types(db, event, True) if t.audience in {"all", "public"}]
    member_types = [t for t in events.ticket_types(db, event, True) if t.audience == "members"]
    return _page(request, db, "public_event.html", event=event, types=types, member_types=member_types, avail=events.availability(db, event), open_=open_, why=why, ft=token, v={}, error="")


@router.post("/whats-on/{event_id:int}/book", response_class=HTMLResponse)
async def public_book(event_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    event = _published(db, event_id)
    form = await request.form()

    def field(name: str) -> str:
        return str(form.get(name, "")).strip()

    verdict = _guard(request, db, field("ft"), field("website"), field("email"))
    values = {k: field(k) for k in ("name", "email", "phone", "type_id", "quantity", "guests")}
    types = [t for t in events.ticket_types(db, event, True) if t.audience in {"all", "public"}]
    member_types = [t for t in events.ticket_types(db, event, True) if t.audience == "members"]
    open_, why = events.sales_state(event)

    def again(message: str, status: int = 200) -> Response:
        db.commit()
        page = _page(request, db, "public_event.html", event=event, types=types, member_types=member_types, avail=events.availability(db, event), open_=open_, why=why, ft=ratelimit.issue_form_token(db), v=values, error=message)
        page.status_code = status
        return page

    if verdict == "retry":
        return again("That took too long or looked automated. Please check the details and send again.")
    if verdict == "silent":
        db.commit()
        return _page(request, db, "public_thanks.html", heading="Thank you", message="If everything was in order you will get an email shortly.")
    ttype = db.get(TicketType, _small_int(values["type_id"]) or 0)
    if not values["name"] or "@" not in values["email"]:
        return again("Please give your name and a valid email address: we send your booking there.")
    if not form.get("consent"):
        return again("Please agree to the privacy notice so we can keep your booking.")
    if ttype is None or ttype not in types:
        return again("Please choose a ticket.")
    quantity = _small_int(values["quantity"]) or 1
    email = values["email"].lower()
    contact = db.scalar(select(Contact).where(func.lower(Contact.email) == email).order_by(Contact.id))
    if contact is not None and events._is_member(db, contact):  # a lapsed member may buy at the public price
        return again("That email address belongs to a member. Please sign in to book at the member price.")
    if contact is None:
        contact = Contact(name=" ".join(values["name"].split())[:255], email=email, phone=values["phone"][:80] or None, notes="Bought tickets on the public events page")
        db.add(contact)
        db.flush()
    held = db.scalar(select(func.count(func.distinct(EventTicket.invoice_id))).where(EventTicket.contact_id == contact.id, EventTicket.status == "held")) or 0
    if held >= MAX_OPEN_HOLDS_PER_EMAIL:
        return again("You already have unpaid tickets waiting. Please pay for those, or wait for them to expire, before booking more.")
    try:
        booking = events.book(db, event, ttype, contact, quantity, guests=[g for g in values["guests"].splitlines() if g.strip()], actor="public")
    except events.EventError as exc:
        db.rollback()
        return again(str(exc))
    db.commit()
    link = f"/i/{payments.guest_token(db, booking.invoice.id)}" if booking.invoice else ""
    return _page(request, db, "public_booked.html", event=event, booking=booking, link=link)


# --------------------------------------------------------------------------- paying without an account


@router.get("/i/{token}", response_class=HTMLResponse)
def guest_invoice(token: str, request: Request, msg: str = "", err: str = "", db: Session = Depends(get_db)) -> Response:
    invoice = payments.invoice_for_guest_token(db, token)
    if invoice is None:
        raise UiError(404, "This link is not valid")
    lines = db.scalars(select(finance.InvoiceLine).where(finance.InvoiceLine.invoice_id == invoice.id).order_by(finance.InvoiceLine.id)).all()
    return _page(
        request, db, "guest_invoice.html", invoice=invoice, lines=lines, owed=finance.outstanding(db, invoice), settings=finance.all_settings(db), methods=payments.methods_for(db, invoice), mode=payments.mode(), token=token, msg=msg, err=err,
    )


@router.post("/i/{token}/pay")
def guest_pay(token: str, request: Request, method: str = Form(...), db: Session = Depends(get_db)) -> Response:
    invoice = payments.invoice_for_guest_token(db, token)
    if invoice is None:
        raise UiError(404, "This link is not valid")
    ip_hash = ratelimit.hash_ip(db, ratelimit.client_ip(request))
    if not ratelimit.allow(db, f"guestpay:{ip_hash}", 20, 3600):
        raise UiError(429, "Too many attempts. Please try again later.")
    try:
        _, url = payments.start(db, invoice, method, base_url=_base(request), back=f"{_base(request)}/i/{token}")
        db.commit()
    except payments.PaymentError as exc:
        db.rollback()
        return RedirectResponse(f"/i/{token}?err={str(exc)[:200]}", status_code=303)
    return RedirectResponse(url, status_code=303)


__all__ = ["router", "OnlinePayment"]
