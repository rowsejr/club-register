"""Volunteer queues for enquiries and applications, and the member's own self-service pages."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import (
    applications,
    audit,
    finance,
    membership,
    payments,
    people,
    permissions,
    privacy,
    ratelimit,
)
from app.db import get_db
from app.models import (
    Application,
    ApplicationPerson,
    Contact,
    Enquiry,
    EnquiryNote,
    Invoice,
    Member,
    MembershipType,
    SessionRecord,
)
from app.ui.routes import (
    PAGE_SIZE,
    UiError,
    _audit,
    _ctx,
    _opt_date,
    templates,
    ui_session,
)

router = APIRouter(include_in_schema=False)
ANY_SESSION = Depends(ui_session())


def _go(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{joiner}{urlencode(params)}" if params else path, status_code=303)


def _types(db: Session) -> list[MembershipType]:
    return list(db.scalars(select(MembershipType).where(MembershipType.active.is_not(False)).order_by(MembershipType.name)))


def _paged(db: Session, stmt, page: int):  # noqa: ANN001, ANN202
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    return list(db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()), {"page": page, "pages": pages, "total": total}


# --------------------------------------------------------------------------- enquiries

ENQUIRY_TABS = [("open", "Open"), ("unverified", "Unconfirmed"), ("converted", "Became applications"), ("closed", "Closed"), ("spam", "Spam"), ("all", "All")]


@router.get("/app/enquiries", response_class=HTMLResponse)
def enquiries_page(request: Request, tab: str = "open", q: str = "", page: int = 1, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    stmt = select(Enquiry).order_by(Enquiry.id.desc())
    if tab == "open":
        stmt = stmt.where(Enquiry.status.in_(["new", "contacted"])).order_by(None).order_by(Enquiry.id)
    elif tab != "all":
        stmt = stmt.where(Enquiry.status == tab)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Enquiry.name.ilike(term), Enquiry.email.ilike(term), Enquiry.message.ilike(term)))
    rows, pager = _paged(db, stmt, page)
    counts = dict(db.execute(select(Enquiry.status, func.count()).group_by(Enquiry.status)).all())
    return templates.TemplateResponse(request, "enquiries.html", _ctx(session, "people", rows=rows, pager=pager, tab=tab, tabs=ENQUIRY_TABS, q=q, counts=counts, stale={e.id for e in applications.stale_enquiries(db)}, msg=msg, err=err))


@router.get("/app/enquiries/new", response_class=HTMLResponse)
def enquiry_new(request: Request, err: str = "", session: SessionRecord = Depends(ui_session(perm="members.write"))) -> Response:
    return templates.TemplateResponse(request, "enquiry_new.html", _ctx(session, "people", interests=applications.INTERESTS, err=err))


@router.post("/app/enquiries/new")
def enquiry_create(
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    interest: str = Form("sailing"),
    source: str = Form("phone"),
    message: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="members.write")),
    db: Session = Depends(get_db),
) -> Response:
    try:
        enquiry, _ = applications.submit_enquiry(db, name=name, email=email, phone=phone, interest=interest, message=message, source=source if source in {"phone", "walk_in", "email"} else "phone", verify=False, actor=session.email)
        db.commit()
    except applications.ApplicationError as exc:
        db.rollback()
        return _go("/app/enquiries/new", err=str(exc))
    return _go(f"/app/enquiries/{enquiry.id}", msg="Enquiry logged")


@router.get("/app/enquiries/{enquiry_id:int}", response_class=HTMLResponse)
def enquiry_detail(enquiry_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    enquiry = db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise UiError(404, "Enquiry not found")
    notes = db.scalars(select(EnquiryNote).where(EnquiryNote.enquiry_id == enquiry.id).order_by(EnquiryNote.id)).all()
    existing = applications.find_contact(db, enquiry.email)
    return templates.TemplateResponse(request, "enquiry_detail.html", _ctx(session, "people", enquiry=enquiry, notes=notes, types=_types(db), existing=existing, msg=msg, err=err))


def _enquiry_action(db: Session, enquiry_id: int, session: SessionRecord, fn, ok: str) -> RedirectResponse:  # noqa: ANN001
    enquiry = db.get(Enquiry, enquiry_id)
    if enquiry is None:
        raise UiError(404, "Enquiry not found")
    path = f"/app/enquiries/{enquiry_id}"
    try:
        result = fn(enquiry)
        db.commit()
    except applications.ApplicationError as exc:
        db.rollback()
        return _go(path, err=str(exc))
    return _go(f"/app/applications/{result.id}" if isinstance(result, Application) else path, msg=ok)


@router.post("/app/enquiries/{enquiry_id:int}/note")
def enquiry_note(enquiry_id: int, body: str = Form(""), session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    return _enquiry_action(db, enquiry_id, session, lambda e: applications.add_note(db, e, session.email, body) and None, "Note added")


@router.post("/app/enquiries/{enquiry_id:int}/assign")
def enquiry_assign(enquiry_id: int, assignee: str = Form(""), session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    return _enquiry_action(db, enquiry_id, session, lambda e: applications.assign(db, e, assignee or session.email, session.email), "Assigned")


@router.post("/app/enquiries/{enquiry_id:int}/status")
def enquiry_status(enquiry_id: int, status: str = Form(...), outcome: str = Form(""), session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    return _enquiry_action(db, enquiry_id, session, lambda e: applications.set_enquiry_status(db, e, status, session.email, outcome), "Updated")


@router.post("/app/enquiries/{enquiry_id:int}/convert")
def enquiry_convert(enquiry_id: int, membership_type: str = Form(""), session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    type_id = int(membership_type) if membership_type.isdigit() else None
    return _enquiry_action(db, enquiry_id, session, lambda e: applications.convert_enquiry(db, e, type_id, session.email), "Application started")


# --------------------------------------------------------------------------- applications

APPLICATION_TABS = [("waiting", "Waiting for a decision"), ("approved", "Awaiting first payment"), ("completed", "Members now"), ("closed", "Rejected / withdrawn"), ("unverified", "Unconfirmed"), ("all", "All")]


@router.get("/app/applications", response_class=HTMLResponse)
def applications_page(request: Request, tab: str = "waiting", page: int = 1, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    stmt = select(Application).order_by(Application.id.desc())
    if tab == "waiting":
        stmt = stmt.where(Application.status.in_(applications.PENDING_APPLICATION)).order_by(None).order_by(Application.id)
    elif tab == "closed":
        stmt = stmt.where(Application.status.in_(["rejected", "withdrawn"]))
    elif tab != "all":
        stmt = stmt.where(Application.status == tab)
    rows, pager = _paged(db, stmt, page)
    type_names = {t.id: t.name for t in db.scalars(select(MembershipType))}
    invoices = {i.id: i for i in db.scalars(select(Invoice).where(Invoice.id.in_([r.invoice_id for r in rows if r.invoice_id])))} if rows else {}
    counts = dict(db.execute(select(Application.status, func.count()).group_by(Application.status)).all())
    return templates.TemplateResponse(request, "applications.html", _ctx(session, "people", rows=rows, pager=pager, tab=tab, tabs=APPLICATION_TABS, type_names=type_names, invoices=invoices, counts=counts, msg=msg, err=err))


@router.get("/app/applications/new", response_class=HTMLResponse)
def application_new(request: Request, err: str = "", session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "application_new.html", _ctx(session, "people", types=_types(db), slots=range(4), err=err))


@router.post("/app/applications/new")
async def application_create(request: Request, session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()

    def field(name: str) -> str:
        return str(form.get(name, "")).strip()

    try:
        people = [
            applications.PersonIn(field(f"person_name_{i}"), field(f"person_role_{i}") or "partner", field(f"person_email_{i}") or None, _opt_date(field(f"person_birth_{i}")))
            for i in range(4)
            if field(f"person_name_{i}")
        ]
        data = applications.ApplicationIn(
            name=field("name"),
            email=field("email") or None,
            membership_type_id=int(field("type")) if field("type").isdigit() else None,
            phone=field("phone"),
            address=field("address"),
            birth_date=_opt_date(field("birth_date")),
            notes=field("notes"),
            people=people,
        )
        application, _ = applications.submit_application(db, data, source="staff", verify=False, actor=session.email)
        if field("approve_now") == "1":
            if not permissions.allows(permissions.effective(db, session), "applications.decide"):
                raise applications.ApplicationError("You can save this application, but only someone who decides applications can approve it")
            applications.approve(db, application, session.email, "Added by a volunteer")
        db.commit()
    except (applications.ApplicationError, ValueError) as exc:
        db.rollback()
        return _go("/app/applications/new", err=str(exc))
    return _go(f"/app/applications/{application.id}", msg="Member added" if field("approve_now") == "1" else "Application saved")


@router.get("/app/applications/{application_id:int}", response_class=HTMLResponse)
def application_detail(application_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    application = db.get(Application, application_id)
    if application is None:
        raise UiError(404, "Application not found")
    people = db.scalars(select(ApplicationPerson).where(ApplicationPerson.application_id == application.id)).all()
    invoice = db.get(Invoice, application.invoice_id) if application.invoice_id else None
    mtype = db.get(MembershipType, application.membership_type_id) if application.membership_type_id else None
    member = db.get(Member, application.member_id) if application.member_id else None
    existing = applications.find_contact(db, application.email) if not application.contact_id else None
    return templates.TemplateResponse(
        request, "application_detail.html", _ctx(session, "people", application=application, people=people, invoice=invoice, mtype=mtype, member=member, existing=existing, types=_types(db), pending=applications.PENDING_APPLICATION, msg=msg, err=err)
    )


def _application_action(db: Session, application_id: int, session: SessionRecord, fn, ok: str) -> RedirectResponse:  # noqa: ANN001
    application = db.get(Application, application_id)
    if application is None:
        raise UiError(404, "Application not found")
    path = f"/app/applications/{application_id}"
    try:
        fn(application)
        db.commit()
    except (applications.ApplicationError, finance.FinanceError) as exc:
        db.rollback()
        return _go(path, err=str(exc))
    return _go(path, msg=ok)


@router.post("/app/applications/{application_id:int}/approve")
def application_approve(application_id: int, note: str = Form(""), membership_type: str = Form(""), session: SessionRecord = Depends(ui_session(perm="applications.decide")), db: Session = Depends(get_db)) -> Response:
    type_id = int(membership_type) if membership_type.isdigit() else None
    return _application_action(db, application_id, session, lambda a: applications.approve(db, a, session.email, note, type_id), "Approved: the first invoice has been issued and emailed")


@router.post("/app/applications/{application_id:int}/reject")
def application_reject(application_id: int, note: str = Form(""), session: SessionRecord = Depends(ui_session(perm="applications.decide")), db: Session = Depends(get_db)) -> Response:
    return _application_action(db, application_id, session, lambda a: applications.reject(db, a, session.email, note), "Rejected")


@router.post("/app/applications/{application_id:int}/info")
def application_info(application_id: int, note: str = Form(""), session: SessionRecord = Depends(ui_session(perm="applications.decide")), db: Session = Depends(get_db)) -> Response:
    return _application_action(db, application_id, session, lambda a: applications.request_info(db, a, session.email, note), "Question sent")


@router.post("/app/applications/{application_id:int}/withdraw")
def application_withdraw(application_id: int, session: SessionRecord = Depends(ui_session(perm="applications.decide")), db: Session = Depends(get_db)) -> Response:
    return _application_action(db, application_id, session, lambda a: applications.withdraw(db, a, session.email), "Withdrawn")


# --------------------------------------------------------------------------- the member's own pages


def _me(db: Session, session: SessionRecord) -> tuple[Member | None, Contact | None]:
    return people.signed_in(db, session)


def _own_invoice(db: Session, session: SessionRecord, invoice_id: int) -> Invoice:
    member, contact = _me(db, session)
    invoice = db.get(Invoice, invoice_id)
    mine = invoice is not None and ((member and invoice.member_id == member.id) or (contact and invoice.contact_id == contact.id))
    if not mine or invoice is None or invoice.status not in finance.MEMBER_VISIBLE:
        raise UiError(404, "Invoice not found")
    return invoice


def member_home_context(db: Session, session: SessionRecord) -> dict:
    """Everything the member's home page shows."""
    member, contact = _me(db, session)
    if member is None:
        return {"member": None, "contact": contact, "invoices": [], "offer": None, "waiting": None, "current": None, "settings": finance.all_settings(db)}
    invoices = db.scalars(select(Invoice).where(Invoice.member_id == member.id, Invoice.status.in_(finance.MEMBER_VISIBLE)).order_by(Invoice.id.desc())).all()
    waiting = membership.awaiting_payment_invoice(db, member)
    offer = None if waiting else membership.renewal_offer(db, member)
    current = membership.latest_membership(db, member.id)
    owing = sum(finance.outstanding(db, i) for i in invoices if (i.kind or "invoice") == "invoice")
    debit = payments.active_mandate(db, contact.id) if contact is not None else None
    return {"member": member, "contact": contact, "invoices": invoices, "offer": offer, "waiting": waiting, "current": current, "owing": owing, "settings": finance.all_settings(db),
            "outstanding": {i.id: finance.outstanding(db, i) for i in invoices}, "debit": debit, "debit_offered": contact is not None and payments.direct_debit_provider() is not None}


@router.post("/app/me/renew")
def me_renew(session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    member, _ = _me(db, session)
    if member is None:
        raise UiError(404, "No membership is linked to this sign-in")
    try:
        invoice = membership.self_renew(db, member, session.email)
        _audit(db, session, "membership.self_renew", "member", member.id, invoice=invoice.id if invoice else None)
        db.commit()
    except (membership.MembershipError, finance.FinanceError) as exc:
        db.rollback()
        return _go("/app", err=str(exc))
    return _go(f"/app/me/invoices/{invoice.id}" if invoice else "/app", msg="Thank you: your invoice is ready below" if invoice else "Renewed")


@router.post("/app/me/resign")
def me_resign(reason: str = Form(""), confirm: str = Form(""), session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    member, _ = _me(db, session)
    if member is None:
        raise UiError(404, "No membership is linked to this sign-in")
    if confirm != "1":
        return _go("/app", err="Tick the box to confirm you do not want to renew")
    membership.resign_at_expiry(db, member, reason, session.email)
    db.commit()
    return _go("/app", msg="We have noted that you will not renew. You remain a member until your membership ends.")


@router.get("/app/me/invoices/{invoice_id:int}", response_class=HTMLResponse)
def me_invoice(invoice_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    invoice = _own_invoice(db, session, invoice_id)
    lines = db.scalars(select(finance.InvoiceLine).where(finance.InvoiceLine.invoice_id == invoice.id).order_by(finance.InvoiceLine.id)).all()
    return templates.TemplateResponse(request, "me_invoice.html", _ctx(session, "dashboard", invoice=invoice, lines=lines, outstanding=finance.outstanding(db, invoice), settings=finance.all_settings(db), msg=msg, err=err, methods=payments.methods_for(db, invoice), mode=payments.mode()))


@router.get("/app/me/invoices/{invoice_id:int}.pdf")
def me_invoice_pdf(invoice_id: int, session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    from app.invoice_pdf import render_invoice_pdf

    invoice = _own_invoice(db, session, invoice_id)
    return Response(render_invoice_pdf(db, invoice), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="invoice-{invoice.reference}.pdf"'})


@router.get("/app/me/profile", response_class=HTMLResponse)
def me_profile(request: Request, msg: str = "", err: str = "", session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    member, contact = _me(db, session)
    if contact is None:
        raise UiError(404, "No details are linked to this sign-in")
    return templates.TemplateResponse(request, "me_profile.html", _ctx(session, "dashboard", member=member, contact=contact, msg=msg, err=err))


@router.post("/app/me/profile")
def me_profile_save(
    phone: str = Form(""), address: str = Form(""), birth_date: str = Form(""), newsletters: str = Form(""), gender: str = Form(""), emergency_name: str = Form(""),
    emergency_phone: str = Form(""), photo_consent: str = Form(""), directory_consent: str = Form(""), session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db),
) -> Response:
    _, contact = _me(db, session)
    if contact is None:
        raise UiError(404, "No details are linked to this sign-in")
    try:
        born = _opt_date(birth_date)
    except ValueError as exc:
        return _go("/app/me/profile", err=str(exc))
    before = audit.snapshot(contact, audit.CONTACT_FIELDS)
    contact.phone, contact.address, contact.birth_date = phone.strip()[:80] or None, address.strip() or None, born
    contact.gender = gender if gender in {"female", "male", "other"} else None
    contact.emergency_name, contact.emergency_phone = emergency_name.strip()[:255] or None, emergency_phone.strip()[:80] or None
    choices = (newsletters != "1", photo_consent == "1", directory_consent == "1")
    if choices != (contact.suppressed, bool(contact.photo_consent), bool(contact.directory_consent)) or contact.consents_updated_at is None:
        contact.consents_updated_at, contact.consents_by = datetime.now(UTC), session.email
    contact.suppressed, contact.photo_consent, contact.directory_consent = choices
    _audit(db, session, "member.self_edit", "contact", contact.id, changes=audit.changes(before, audit.snapshot(contact, audit.CONTACT_FIELDS)))
    db.commit()
    return _go("/app/me/profile", msg="Saved")


@router.post("/app/me/email")
def me_email_change(new_email: str = Form(""), session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    member, contact = _me(db, session)
    if member is None:
        raise UiError(404, "No membership is linked to this sign-in")
    if not ratelimit.allow(db, f"email-change:{member.id}", 5, 3600):
        db.commit()
        return _go("/app/me/profile", err="Please wait an hour before asking again")
    try:
        people.request_email_change(db, member, contact, new_email, session.email)
    except people.EmailChangeError as exc:
        db.rollback()
        return _go("/app/me/profile", err=str(exc))
    db.commit()
    return _go("/app/me/profile", msg=f"We have emailed a link to {new_email.strip().lower()}. Your address changes when you open it.")


@router.get("/me/email/{token}", response_class=HTMLResponse)
def me_email_confirm_page(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    """Opening the link only shows a button: some email programs open links to scan them, which must not change anything."""
    change = people.pending_email_change(db, token)
    return templates.TemplateResponse(request, "email_change.html", {"user": None, "nav": "", "change": change, "token": token, "done": None, "error": ""})


@router.post("/me/email/{token}", response_class=HTMLResponse)
def me_email_confirm(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        _member, new_email = people.confirm_email_change(db, token)
    except people.EmailChangeError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "email_change.html", {"user": None, "nav": "", "change": None, "token": token, "done": None, "error": str(exc)}, status_code=400)
    db.commit()
    return templates.TemplateResponse(request, "email_change.html", {"user": None, "nav": "", "change": None, "token": token, "done": new_email, "error": ""})


@router.get("/app/me/export.json")
def me_export(session: SessionRecord = ANY_SESSION, db: Session = Depends(get_db)) -> Response:
    member, contact = _me(db, session)
    if member is None and contact is None:
        raise UiError(404, "Nothing is linked to this sign-in")
    data = privacy.export_person(db, contact_id=contact.id if contact else None, member_id=member.id if member else None)
    data.pop("emails_sent", None)
    _audit(db, session, "privacy.self_export", "member", member.id if member else contact.id if contact else 0)
    db.commit()
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="my-data.json"'})

