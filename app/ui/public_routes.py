"""Public pages: what the club offers, enquire, join online, confirm an email link, privacy notice.

These need no sign-in, so they are protected differently from the operator screens: a hidden honeypot field, a
signed form token (rejects instant and stale submissions), database-backed rate limits per address and per email,
email confirmation before anything reaches a volunteer, and an identical response whatever the outcome (nobody can
learn whether an address already belongs to a member).
"""

from __future__ import annotations

import hashlib
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import applications, finance, mailings, ratelimit
from app.db import get_db
from app.models import AuditLog, MembershipType
from app.ui.routes import UiError, templates

router = APIRouter(include_in_schema=False)


def _joinable(db: Session) -> list[MembershipType]:
    return list(db.scalars(select(MembershipType).where(MembershipType.online_joinable.is_(True), MembershipType.active.is_not(False)).order_by(MembershipType.fee_minor, MembershipType.name)))


def _page(request: Request, db: Session, template: str, **ctx) -> Response:
    settings = finance.all_settings(db)
    return templates.TemplateResponse(request, template, {"user": None, "nav": "", "club": settings["club_name"], **ctx})


def _guard(request: Request, db: Session, token: str, trap: str, email: str | None) -> str:
    """'ok' to proceed, 'silent' to pretend success without doing anything, 'retry' to show the form again."""
    ip_hash = ratelimit.hash_ip(db, ratelimit.client_ip(request))
    if not ratelimit.allow(db, f"pub:{ip_hash}", 8, 3600):
        raise UiError(429, "Too many requests from this address. Please try again later.")
    if trap.strip():
        return "silent"  # a bot filled the hidden field
    if not ratelimit.check_form_token(db, token):
        return "retry"
    if email and not ratelimit.allow(db, "mail:" + hashlib.sha256(email.lower().encode()).hexdigest()[:40], 3, 86400):
        return "silent"
    return "ok"


@router.get("/membership", response_class=HTMLResponse)
def membership_page(request: Request, db: Session = Depends(get_db)) -> Response:
    return _page(request, db, "public_membership.html", types=_joinable(db))


@router.get("/join-us", response_class=HTMLResponse)
def enquiry_form(request: Request, db: Session = Depends(get_db)) -> Response:
    token = ratelimit.issue_form_token(db)
    db.commit()
    return _page(request, db, "public_enquiry.html", ft=token, interests=applications.INTERESTS, v={})


@router.post("/join-us", response_class=HTMLResponse)
def enquiry_submit(
    request: Request,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    interest: str = Form("sailing"),
    message: str = Form(""),
    consent: str = Form(""),
    ft: str = Form(""),
    website: str = Form(""),  # honeypot: humans never see this field
    db: Session = Depends(get_db),
) -> Response:
    verdict = _guard(request, db, ft, website, email)
    if verdict == "retry":
        db.commit()
        return _page(request, db, "public_enquiry.html", ft=ratelimit.issue_form_token(db), interests=applications.INTERESTS, v={"name": name, "email": email, "phone": phone, "message": message}, error="That took too long or looked automated. Please check the details and send again.")
    if verdict == "ok":
        try:
            applications.submit_enquiry(
                db, name=name, email=email, phone=phone, interest=interest, message=message, consent=bool(consent), ip_hash=ratelimit.hash_ip(db, ratelimit.client_ip(request))
            )
        except applications.ApplicationError as exc:
            db.rollback()
            return _page(request, db, "public_enquiry.html", ft=ratelimit.issue_form_token(db), interests=applications.INTERESTS, v={"name": name, "email": email, "phone": phone, "message": message}, error=str(exc))
    db.commit()
    return _page(request, db, "public_thanks.html", heading="Thank you", message="If that address is right, we have emailed you a link to confirm it. Click it and a member of the club will be in touch.")


@router.get("/join", response_class=HTMLResponse)
def join_form(request: Request, type: int | None = None, db: Session = Depends(get_db)) -> Response:
    token = ratelimit.issue_form_token(db)
    db.commit()
    return _page(request, db, "public_join.html", ft=token, types=_joinable(db), v={"type": str(type or "")}, slots=range(4), error="")


@router.post("/join", response_class=HTMLResponse)
async def join_submit(request: Request, db: Session = Depends(get_db)) -> Response:
    form = await request.form()

    def field(name: str) -> str:
        return str(form.get(name, "")).strip()

    verdict = _guard(request, db, field("ft"), field("website"), field("email"))
    values = {k: field(k) for k in ("name", "email", "phone", "address", "birth_date", "experience", "type")}

    def again(message: str) -> Response:
        db.commit()
        return _page(request, db, "public_join.html", ft=ratelimit.issue_form_token(db), types=_joinable(db), v=values, slots=range(4), error=message)

    if verdict == "retry":
        return again("That took too long or looked automated. Please check the details and send again.")
    if verdict == "ok":
        try:
            birth = date.fromisoformat(values["birth_date"]) if values["birth_date"] else None
        except ValueError:
            return again("Date of birth should look like 1985-03-30")
        people = []
        for index in range(4):
            person_name = field(f"person_name_{index}")
            if person_name:
                try:
                    person_birth = date.fromisoformat(field(f"person_birth_{index}")) if field(f"person_birth_{index}") else None
                except ValueError:
                    return again(f"{person_name}: date of birth should look like 2015-05-01")
                people.append(applications.PersonIn(person_name, field(f"person_role_{index}") or "partner", field(f"person_email_{index}") or None, person_birth))
        try:
            data = applications.ApplicationIn(
                name=values["name"],
                email=values["email"],
                membership_type_id=int(values["type"]) if values["type"].isdigit() else None,
                phone=values["phone"],
                address=values["address"],
                birth_date=birth,
                experience=values["experience"],
                people=people,
                rules_agreed=field("rules") == "1",
                consent=field("consent") == "1",
                consent_marketing=field("marketing") == "1",
            )
            applications.submit_application(db, data, ip_hash=ratelimit.hash_ip(db, ratelimit.client_ip(request)))
        except applications.ApplicationError as exc:
            db.rollback()
            return again(str(exc))
    db.commit()
    return _page(request, db, "public_thanks.html", heading="Application received", message="If that address is right, we have emailed you a link to confirm it. Once you click it, your application goes to the committee, and we will email you when it has been decided.")


@router.get("/verify/{token}", response_class=HTMLResponse)
def verify_link(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    ip_hash = ratelimit.hash_ip(db, ratelimit.client_ip(request))
    if not ratelimit.allow(db, f"verify:{ip_hash}", 20, 3600):
        raise UiError(429, "Too many attempts. Please try again later.")
    if applications.verify_application(db, token):
        db.commit()
        return _page(request, db, "public_thanks.html", heading="Thank you", message="Your application has been passed to the committee. We will email you when it has been decided.")
    if applications.verify_enquiry(db, token):
        db.commit()
        return _page(request, db, "public_thanks.html", heading="Thank you", message="Your address is confirmed. A member of the club will be in touch soon.")
    db.commit()
    return _page(request, db, "public_thanks.html", heading="This link has already been used or has expired", message="If you still want to get in touch, please use the form again.")


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request, db: Session = Depends(get_db)) -> Response:
    return _page(request, db, "public_privacy.html")


@router.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    contact = mailings.contact_for_token(db, token)
    if contact is None:
        return _page(request, db, "public_thanks.html", heading="This link is not valid", message="It may be damaged or out of date. Reply to any email from the club and we will stop them by hand.")
    return _page(request, db, "public_unsubscribe.html", token=token, first=contact.name.split()[0] if contact.name else "there", done=bool(contact.suppressed))


@router.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_do(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    ip_hash = ratelimit.hash_ip(db, ratelimit.client_ip(request))
    if not ratelimit.allow(db, f"unsub:{ip_hash}", 30, 3600):
        raise UiError(429, "Too many attempts. Please try again later.")
    contact = mailings.contact_for_token(db, token)
    if contact is None:
        return _page(request, db, "public_thanks.html", heading="This link is not valid", message="It may be damaged or out of date. Reply to any email from the club and we will stop them by hand.")
    contact.suppressed = True
    db.add(AuditLog(actor_email=contact.email or "public", actor_role="public", action="contact.unsubscribe", target_type="contact", target_id=str(contact.id), details={}))
    db.commit()
    return _page(request, db, "public_thanks.html", heading="You are unsubscribed", message="We will not send you club news or mailings any more. You will still get emails about your own invoices and membership. If you change your mind, just tell the membership secretary.")
