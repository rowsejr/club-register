"""Online payment screens and the provider webhooks."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import finance, payments, ratelimit
from app.db import get_db
from app.models import Contact, OnlinePayment, PaymentMandate, SessionRecord
from app.ui.people_routes import _own_invoice
from app.ui.routes import UiError, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
ANY = ui_session()


def _base(request: Request) -> str:
    return os.getenv("CLUB_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")


@router.post("/app/me/invoices/{invoice_id:int}/pay")
def start_payment(invoice_id: int, request: Request, method: str = Form(...), session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    invoice = _own_invoice(db, session, invoice_id)
    try:
        payment, url = payments.start(db, invoice, method, base_url=_base(request))
        db.commit()
    except payments.PaymentError as exc:
        db.rollback()
        return RedirectResponse(f"/app/me/invoices/{invoice_id}?err={str(exc)[:200]}", status_code=303)
    del payment
    return RedirectResponse(url, status_code=303)


@router.get("/pay/{token}", response_class=HTMLResponse)
def sandbox_page(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """The pretend provider page for demos. Switched off (404) anywhere that is not a development or demo deployment."""
    if payments.mode() != "sandbox":
        raise UiError(404, "Not found")
    payment = db.scalar(select(OnlinePayment).where(OnlinePayment.token == token, OnlinePayment.provider == "sandbox"))
    if payment is None:
        raise UiError(404, "Not found")
    invoice = db.get(finance.Invoice, payment.invoice_id)
    return templates.TemplateResponse(request, "pay_sandbox.html", {"user": None, "nav": "", "payment": payment, "invoice": invoice, "status": payment.status, "club": finance.get_setting(db, "club_name")})


@router.post("/pay/{token}", response_class=HTMLResponse)
def sandbox_confirm(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    if payments.mode() != "sandbox":
        raise UiError(404, "Not found")
    try:
        payments.sandbox_pay(db, token)
        db.commit()
    except payments.PaymentError as exc:
        db.rollback()
        raise UiError(404, str(exc)) from exc
    payment = db.scalar(select(OnlinePayment).where(OnlinePayment.token == token))
    if payment is None:
        return RedirectResponse("/app", status_code=303)
    invoice = db.get(finance.Invoice, payment.invoice_id)
    contact = db.get(Contact, invoice.contact_id) if invoice and invoice.contact_id else None
    if contact is not None and contact.member_id is None:  # someone without an account goes back to their private invoice link
        return RedirectResponse(f"/i/{payments.guest_token(db, payment.invoice_id)}?msg=Payment+received.+Thank+you", status_code=303)
    return RedirectResponse(f"/app/me/invoices/{payment.invoice_id}?msg=Payment+received.+Thank+you", status_code=303)


async def _webhook(request: Request, db: Session, provider: str, verify, parse) -> Response:
    ip_hash = ratelimit.hash_ip(db, ratelimit.client_ip(request))
    if not ratelimit.allow(db, f"webhook:{provider}:{ip_hash}", 600, 60):
        return JSONResponse({"error": "slow down"}, status_code=429)
    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    if not verify(headers, body):
        db.commit()  # keep the rate-limit count
        return JSONResponse({"error": "bad signature"}, status_code=400)  # nothing in the body is trusted or read
    try:
        events = parse(body)
    except (ValueError, KeyError):
        return JSONResponse({"error": "unreadable"}, status_code=400)
    outcomes = [payments.apply(db, provider, event, _base(request)) for event in events]
    db.commit()
    return JSONResponse({"ok": True, "handled": len(events), "outcomes": outcomes})


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _webhook(request, db, "stripe", payments.verify_stripe, payments.parse_stripe)


@router.post("/webhooks/gocardless")
async def gocardless_webhook(request: Request, db: Session = Depends(get_db)) -> Response:
    return await _webhook(request, db, "gocardless", payments.verify_gocardless, payments.parse_gocardless)


@router.get("/app/finance/invoices/{invoice_id:int}/online", response_class=HTMLResponse)
def online_panel(invoice_id: int, request: Request, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    invoice = db.get(finance.Invoice, invoice_id)
    if invoice is None:
        raise UiError(404, "Invoice not found")
    attempts = db.scalars(select(OnlinePayment).where(OnlinePayment.invoice_id == invoice.id).order_by(OnlinePayment.id.desc())).all()
    mandate = db.scalar(select(PaymentMandate).where(PaymentMandate.contact_id == invoice.contact_id, PaymentMandate.status == "active")) if invoice.contact_id else None
    return templates.TemplateResponse(request, "_online_payments.html", _ctx(session, "finance", invoice=invoice, attempts=attempts, mandate=mandate, methods=payments.methods_for(db, invoice), link=payments.pay_link(invoice), mode=payments.mode()))

