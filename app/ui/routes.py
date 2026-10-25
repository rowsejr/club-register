"""Server-rendered operator UI (Jinja2 + HTMX). Shares models and auth with the JSON API."""

from __future__ import annotations

import csv
import io
import math
import os
import re
from collections import defaultdict, deque
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import (
    applications,
    audit,
    finance,
    people,
    permissions,
    privacy,
)
from app.auth import (
    SESSION_COOKIE,
    csrf_ok,
    csrf_token,
    exchange_token,
    is_demo,
    is_dev,
    issue_magic_link,
    load_session,
    revoke_sessions,
)
from app.db import SessionLocal, get_db
from app.invoice_query import status_label
from app.models import (
    AuditLog,
    Contact,
    Invoice,
    Member,
    Membership,
    MembershipType,
    Role,
    SessionRecord,
    UserRole,
)
from app.people_filters import PeopleFilter, contacts_statement, members_statement
from app.people_filters import choices as people_choices
from app.security import now_utc

router = APIRouter(include_in_schema=False)
def _template_context(request: Request) -> dict[str, Any]:
    return {"perms": getattr(request.state, "perms", set()), "demo": is_demo()}


templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"), context_processors=[_template_context])
templates.env.filters["status_label"] = status_label
templates.env.globals["website_url"] = os.getenv("CLUB_WEBSITE_URL", "https://www.margateyachtclub.co.uk")


def invoice_badge(invoice: Any) -> Markup:
    """An invoice's status as a coloured label in words. Unpaid invoices past their due date say Overdue."""
    status = invoice.status or ""
    if status in ("issued", "part_paid") and invoice.due_date and invoice.due_date < date.today() and (invoice.kind or "invoice") == "invoice":
        return Markup('<span class="badge bad">Overdue</span>')
    return Markup('<span class="badge {}">{}</span>').format(status, status_label(status))


templates.env.filters["invoice_badge"] = invoice_badge
PAGE_SIZE = 25
OPERATORS = ("Admin", "Committee")


def _gbp(minor: int | None) -> str:
    if minor is None:
        return ""
    sign = "-" if minor < 0 else ""
    return f"{sign}£{abs(minor) / 100:,.2f}"


def _date(value: Any) -> str:
    return value.strftime("%d %b %Y") if value else ""


templates.env.filters["gbp"] = _gbp
templates.env.filters["can"] = lambda perms, needed: permissions.allows(perms, needed)
templates.env.filters["dmy"] = _date


def _local(value: Any, fmt: str = "%d %b %Y %H:%M") -> str:
    """Show a stored UTC timestamp in club (UK) local time."""
    if not value:
        return ""
    from datetime import UTC
    from zoneinfo import ZoneInfo

    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(ZoneInfo("Europe/London")).strftime(fmt)


templates.env.filters["local"] = _local
templates.env.filters["combine"] = lambda base, extra: {**base, **extra}


class LoginRequired(Exception):
    pass


class UiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"


async def login_required_handler(request: Request, _: LoginRequired) -> Response:
    if _is_htmx(request):
        return Response(status_code=401, headers={"HX-Redirect": "/login"})
    target = request.url.path
    return RedirectResponse(f"/login?{urlencode({'next': target})}" if target not in {"/", "/login"} else "/login", status_code=303)


async def ui_error_handler(request: Request, exc: UiError) -> Response:
    return templates.TemplateResponse(
        request, "error.html", {"status": exc.status, "message": exc.message, "user": None, "nav": ""}, status_code=exc.status
    )


def ui_session(*roles: str, perm: str | None = None):
    """Require a signed-in user: by base role (``roles``) or by permission (``perm``, see app/permissions.py)."""

    async def _dep(request: Request, db: Session = Depends(get_db)) -> SessionRecord:
        session, _ = load_session(db, request.cookies.get(SESSION_COOKIE))
        if session is None:
            raise LoginRequired
        perms = permissions.effective(db, session)
        request.state.perms = perms
        if perm is not None:
            if not permissions.allows(perms, perm):
                raise UiError(403, "You do not have access to this page. Ask an administrator if you need it.")
        elif roles and session.role.value not in roles:
            raise UiError(403, "You do not have access to this page.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-CSRF-Token")
            if not supplied:
                supplied = (await request.form()).get("csrf")  # type: ignore[assignment]
            if not csrf_ok(session, supplied if isinstance(supplied, str) else None):
                raise UiError(403, "Security check failed (CSRF). Reload the page and try again.")
        return session

    return _dep


def _ctx(session: SessionRecord, nav: str, **extra: Any) -> dict[str, Any]:
    return {"user": session.email, "role": session.role.value, "csrf": csrf_token(session), "nav": nav, **extra}


def _audit(db: Session, session: SessionRecord, action: str, target_type: str, target_id: Any, **details: Any) -> None:
    db.add(
        AuditLog(
            actor_email=session.email,
            actor_role=session.role.value,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
        )
    )


def _page(db: Session, stmt: Any, page: int) -> tuple[list[Any], dict[str, int]]:
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return list(rows), {"page": page, "pages": pages, "total": total}


def _clean_email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email address: {exc}") from exc


def _opt_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}' (use YYYY-MM-DD)") from exc


_PLAIN_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _csv_cell(value: Any) -> str:
    """Text a spreadsheet could run as a formula is defused; plain numbers (money, counts) are left alone so they stay numbers."""
    text = "" if value is None else str(value)
    if _PLAIN_NUMBER.fullmatch(text):
        return text
    return "'" + text if text[:1] in {"=", "+", "-", "@", "\t", "\r"} else text


def _csv_response(name: str, header: list[str], rows: list[list[Any]]) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_cell(v) for v in row])
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# --- sign-in -----------------------------------------------------------------

_login_hits: dict[str, deque[float]] = defaultdict(deque)


def _login_limited(request: Request) -> bool:
    import os
    import time

    limit = int(os.getenv("CLUB_AUTH_RATE_LIMIT", "10"))
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    hits = _login_hits[key]
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, db: Session = Depends(get_db)) -> Response:
    session, _ = load_session(db, request.cookies.get(SESSION_COOKIE))
    if session is not None:
        return RedirectResponse("/app", status_code=303)
    return templates.TemplateResponse(request, "landing.html", {"user": None, "nav": ""})


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    return templates.TemplateResponse(request, "login.html", {"user": None, "nav": "", "step": "email", "next": request.query_params.get("next", ""), "staff": request.query_params.get("staff") == "1"})


@router.post("/login", response_class=HTMLResponse)
def login_request(request: Request, email: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)) -> Response:
    if _login_limited(request):
        return templates.TemplateResponse(request, "login.html", {"user": None, "nav": "", "step": "email", "error": "Too many attempts, please wait a minute."}, status_code=429)
    dev_token = None
    try:
        token = issue_magic_link(db, _clean_email(email))
        dev_token = token if (is_dev() or is_demo()) else None
    except ValueError:
        pass  # same response as an unknown address: never reveal which emails are registered
    return templates.TemplateResponse(request, "login.html", {"user": None, "nav": "", "step": "token", "dev_token": dev_token, "next": next})


@router.post("/login/token", response_class=HTMLResponse)
def login_token(request: Request, token: str = Form(...), next: str = Form(""), db: Session = Depends(get_db)) -> Response:
    if _login_limited(request):
        return templates.TemplateResponse(request, "login.html", {"user": None, "nav": "", "step": "token", "error": "Too many attempts, please wait a minute."}, status_code=429)
    result = exchange_token(db, token.strip())
    if result is None:
        return templates.TemplateResponse(request, "login.html", {"user": None, "nav": "", "step": "token", "error": "That code is invalid or has expired.", "next": next}, status_code=400)
    session_token, _role = result
    target = next if next.startswith("/") and not next.startswith("//") else "/app"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(SESSION_COOKIE, session_token, max_age=12 * 3600, httponly=True, samesite="strict", secure=not is_dev())
    return response


@router.post("/logout")
def logout(session: SessionRecord = Depends(ui_session()), db: Session = Depends(get_db)) -> Response:
    session.revoked_at = now_utc()
    db.commit()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/operator")
def operator_redirect() -> Response:
    return RedirectResponse("/app", status_code=303)


# --- dashboard ---------------------------------------------------------------


@router.get("/app", response_class=HTMLResponse)
def dashboard(request: Request, session: SessionRecord = Depends(ui_session()), db: Session = Depends(get_db)) -> Response:
    if not permissions.is_operator(getattr(request.state, "perms", set())):
        from app.office import REQUEST_KINDS
        from app.ui.people_routes import member_home_context

        return templates.TemplateResponse(request, "portal.html", _ctx(session, "dashboard", msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""), kinds=REQUEST_KINDS, **member_home_context(db, session)))
    from app.ui.office_routes import home_context

    return templates.TemplateResponse(request, "home.html", _ctx(session, "dashboard", **home_context(db, session, getattr(request.state, "perms", set()))))


# --- members -----------------------------------------------------------------


def _people_filter(q: str = "", status: str = "all", type: str = "", age: str = "", list: str = "", boat: str = "", unpaid: str = "", member: str = "", tag: str = "") -> PeopleFilter:  # noqa: A002
    return PeopleFilter(q=q, status=status, type=type, age=age, list=list, boat=boat, unpaid=unpaid, member=member, tag=tag)


@router.get("/app/members", response_class=HTMLResponse)
def members_list(
    request: Request,
    page: int = 1,
    f: PeopleFilter = Depends(_people_filter),
    session: SessionRecord = Depends(ui_session(perm="members.read")),
    db: Session = Depends(get_db),
) -> Response:
    rows, pager = _page(db, members_statement(f), page)
    from app import office

    money = {m.id: office.money_status(db, db.scalar(select(Contact).where(Contact.member_id == m.id))) for m in rows}
    ctx = _ctx(session, "members", rows=rows, pager=pager, f=f, ch=people_choices(db), views=people.views_for(db, "members"), money=money)
    template = "_members_table.html" if _is_htmx(request) else "members_list.html"
    return templates.TemplateResponse(request, template, ctx, headers={"Vary": "HX-Request"})


@router.get("/app/members.csv")
def members_csv(f: PeopleFilter = Depends(_people_filter), _: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    members = db.scalars(members_statement(f)).all()
    return _csv_response(
        "members.csv",
        ["id", "name", "email", "membership_number", "membership_type", "renewal_status", "payment_status", "join_date", "expiry_date", "active"],
        [[m.id, m.name, m.email, m.membership_number, m.membership_type, m.renewal_status, m.payment_status, m.join_date, m.expiry_date, m.active] for m in members],
    )


def _type_names(_request: Request | None = None) -> list[str]:
    with SessionLocal() as db:
        return [n for (n,) in db.execute(select(MembershipType.name).where(MembershipType.active.is_not(False)).order_by(MembershipType.name))]


def _start_membership(db: Session, member: Member, joined: date | None) -> None:
    """A member added by hand gets what an approved applicant gets: a membership for the year, and a number."""
    if member.renewal_status != "active" or not member.membership_type or member.membership_type == "standard":
        return
    year = (joined or date.today()).year
    db.add(Membership(member_id=member.id, plan_name=member.membership_type, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status="active"))
    member.membership_number = member.membership_number or applications.next_membership_number(db)
    member.expiry_date = member.expiry_date or date(year, 12, 31)
    member.join_date = member.join_date or date.today()


def _member_form(request: Request, session: SessionRecord, member: Member | None, values: dict[str, str], error: str = "", status: int = 200) -> Response:
    return templates.TemplateResponse(request, "member_form.html", _ctx(session, "members", member=member, v=values, error=error, types=_type_names(request)), status_code=status)


def _member_values(member: Member | None) -> dict[str, str]:
    if member is None:
        return {"name": "", "email": "", "membership_type": "", "renewal_status": "active", "payment_status": "paid", "join_date": "", "expiry_date": ""}
    return {
        "name": member.name,
        "email": member.email or "",
        "membership_type": member.membership_type,
        "renewal_status": member.renewal_status,
        "payment_status": member.payment_status,
        "join_date": member.join_date.isoformat() if member.join_date else "",
        "expiry_date": member.expiry_date.isoformat() if member.expiry_date else "",
    }


@router.get("/app/members/new", response_class=HTMLResponse)
def member_new(request: Request, session: SessionRecord = Depends(ui_session(perm="members.write"))) -> Response:
    return _member_form(request, session, None, _member_values(None))


@router.get("/app/members/{member_id}", response_class=HTMLResponse)
def member_detail(member_id: int, request: Request, saved: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    member = db.get(Member, member_id)
    if not member:
        raise UiError(404, "Member not found")
    contact = db.scalar(select(Contact).where(Contact.member_id == member_id))
    owned = Invoice.member_id == member_id
    if contact:
        owned = owned | (Invoice.contact_id == contact.id)
    invoices = db.scalars(select(Invoice).where(owned).order_by(Invoice.id.desc()).limit(10)).all()
    return templates.TemplateResponse(
        request, "member_form.html", _ctx(session, "members", member=member, v=_member_values(member), invoices=invoices, contact=contact, saved=bool(saved), types=_type_names(request))
    )


@router.post("/app/members", response_class=HTMLResponse)
@router.post("/app/members/{member_id}", response_class=HTMLResponse)
def member_save(
    request: Request,
    member_id: int | None = None,
    name: str = Form(""),
    email: str = Form(""),
    membership_type: str = Form("standard"),
    renewal_status: str = Form("active"),
    payment_status: str = Form("paid"),
    join_date: str = Form(""),
    expiry_date: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="members.write")),
    db: Session = Depends(get_db),
) -> Response:
    member = db.get(Member, member_id) if member_id else None
    if member_id and not member:
        raise UiError(404, "Member not found")
    values = {"name": name, "email": email, "membership_type": membership_type, "renewal_status": renewal_status, "payment_status": payment_status, "join_date": join_date, "expiry_date": expiry_date}
    before = audit.snapshot(member, audit.MEMBER_FIELDS) if member else {}
    try:
        if not name.strip():
            raise ValueError("Name is required")
        clean_email = _clean_email(email) if email.strip() else None  # some members have no email on file
        joined, expires = _opt_date(join_date), _opt_date(expiry_date)
        is_new = member is None
        if member is None:
            member = Member(active=True)
            db.add(member)
        member.name, member.email = name.strip(), clean_email
        member.membership_type, member.renewal_status, member.payment_status = membership_type.strip() or "standard", renewal_status.strip(), payment_status.strip()
        member.join_date, member.expiry_date = joined, expires
        db.flush()
        if is_new:
            _start_membership(db, member, joined)
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
        if contact:
            contact.name = member.name
            if clean_email:
                contact.email = clean_email
        elif not clean_email or not db.scalar(select(Contact).where(func.lower(Contact.email) == clean_email)):
            db.add(Contact(member_id=member.id, name=member.name, email=member.email))
        _audit(db, session, "member.update" if member_id else "member.create", "member", member.id, via="ui", changes=audit.changes(before, audit.snapshot(member, audit.MEMBER_FIELDS)) if before else {})
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _member_form(request, session, db.get(Member, member_id) if member_id else None, values, str(exc), 422)
    except IntegrityError:
        db.rollback()
        return _member_form(request, session, db.get(Member, member_id) if member_id else None, values, "A member with this email already exists.", 409)
    return RedirectResponse(f"/app/members/{member.id}?saved=1", status_code=303)


@router.post("/app/members/{member_id}/active")
def member_set_active(member_id: int, active: str = Form(...), session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    member = db.get(Member, member_id)
    if not member:
        raise UiError(404, "Member not found")
    was = member.active
    member.active = active == "1"
    _audit(db, session, "member.activate" if member.active else "member.deactivate", "member", member.id, via="ui", changes={"active": {"before": was, "after": member.active}})
    db.commit()
    return RedirectResponse(f"/app/members/{member_id}?saved=1", status_code=303)


# --- contacts ----------------------------------------------------------------


@router.get("/app/contacts", response_class=HTMLResponse)
def contacts_list(request: Request, page: int = 1, f: PeopleFilter = Depends(_people_filter), session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    rows, pager = _page(db, contacts_statement(f), page)
    template = "_contacts_table.html" if _is_htmx(request) else "contacts_list.html"
    return templates.TemplateResponse(request, template, _ctx(session, "contacts", rows=rows, pager=pager, f=f, ch=people_choices(db), views=people.views_for(db, "contacts")), headers={"Vary": "HX-Request"})


@router.get("/app/contacts.csv")
def contacts_csv(f: PeopleFilter = Depends(_people_filter), _: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    contacts = db.scalars(contacts_statement(f)).all()
    return _csv_response(
        "contacts.csv",
        ["id", "member_id", "name", "email", "phone", "address", "birth_date", "suppressed", "notes"],
        [[c.id, c.member_id, c.name, c.email, c.phone, c.address, c.birth_date, c.suppressed, c.notes] for c in contacts],
    )


def _contact_values(contact: Contact | None) -> dict[str, Any]:
    if contact is None:
        return {"name": "", "email": "", "phone": "", "address": "", "birth_date": "", "notes": "", "suppressed": False}
    return {
        "name": contact.name,
        "email": contact.email or "",
        "phone": contact.phone or "",
        "address": contact.address or "",
        "birth_date": contact.birth_date.isoformat() if contact.birth_date else "",
        "notes": contact.notes or "",
        "suppressed": contact.suppressed,
    }


@router.get("/app/contacts/new", response_class=HTMLResponse)
def contact_new(request: Request, session: SessionRecord = Depends(ui_session(perm="members.write"))) -> Response:
    return templates.TemplateResponse(request, "contact_form.html", _ctx(session, "contacts", contact=None, v=_contact_values(None)))


@router.get("/app/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail(contact_id: int, request: Request, saved: str = "", err: str = "", msg: str = "", session: SessionRecord = Depends(ui_session(perm="members.read")), db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id)
    if not contact:
        raise UiError(404, "Contact not found")
    person = people.person360(db, contact)
    from app import office

    return templates.TemplateResponse(
        request,
        "contact_form.html",
        _ctx(session, "contacts", contact=contact, v=_contact_values(contact), saved=bool(saved), err=err, msg=msg, p=person, known_tags=people.all_tags(db), all_lists=[lst for lst, _ in people.lists_with_counts(db)],
             money=office.money_status(db, contact)),
    )


@router.post("/app/contacts", response_class=HTMLResponse)
@router.post("/app/contacts/{contact_id}", response_class=HTMLResponse)
def contact_save(
    request: Request,
    contact_id: int | None = None,
    name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    birth_date: str = Form(""),
    notes: str = Form(""),
    suppressed: str = Form(""),
    gender: str = Form(""),
    emergency_name: str = Form(""),
    emergency_phone: str = Form(""),
    photo_consent: str = Form(""),
    directory_consent: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="members.write")),
    db: Session = Depends(get_db),
) -> Response:
    contact = db.get(Contact, contact_id) if contact_id else None
    if contact_id and not contact:
        raise UiError(404, "Contact not found")
    values = {"name": name, "email": email, "phone": phone, "address": address, "birth_date": birth_date, "notes": notes, "suppressed": bool(suppressed), "gender": gender,
              "emergency_name": emergency_name, "emergency_phone": emergency_phone, "photo_consent": bool(photo_consent), "directory_consent": bool(directory_consent)}
    before = audit.snapshot(contact, audit.CONTACT_FIELDS) if contact else {}
    try:
        if not name.strip():
            raise ValueError("Name is required")
        clean_email = _clean_email(email) if email.strip() else None
        born = _opt_date(birth_date)
        if contact is None:
            contact = Contact(name=name.strip())
            db.add(contact)
        contact.name, contact.email, contact.phone = name.strip(), clean_email, phone.strip() or None
        contact.address, contact.birth_date, contact.notes = address.strip() or None, born, notes
        contact.gender = gender if gender in {"female", "male", "other"} else None
        contact.emergency_name, contact.emergency_phone = emergency_name.strip()[:255] or None, emergency_phone.strip()[:80] or None
        choices = (bool(suppressed), bool(photo_consent), bool(directory_consent))
        if contact.id is None or choices != (bool(contact.suppressed), bool(contact.photo_consent), bool(contact.directory_consent)):
            contact.consents_updated_at, contact.consents_by = datetime.now(UTC), f"{session.email} (on their behalf)"
        contact.suppressed, contact.photo_consent, contact.directory_consent = choices
        db.flush()
        _audit(db, session, "contact.update" if contact_id else "contact.create", "contact", contact.id, via="ui", changes=audit.changes(before, audit.snapshot(contact, audit.CONTACT_FIELDS)) if before else {})
        db.commit()
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(request, "contact_form.html", _ctx(session, "contacts", contact=db.get(Contact, contact_id) if contact_id else None, v=values, error=str(exc)), status_code=422)
    return RedirectResponse(f"/app/contacts/{contact.id}?saved=1", status_code=303)


@router.get("/app/contacts/{contact_id}/export.json")
def contact_export(contact_id: int, session: SessionRecord = Depends(ui_session("Admin")), db: Session = Depends(get_db)) -> Response:
    import json

    try:
        data = privacy.export_person(db, contact_id=contact_id)
    except finance.FinanceError as exc:
        raise UiError(404, str(exc)) from exc
    _audit(db, session, "privacy.export", "contact", contact_id)
    db.commit()
    return Response(json.dumps(data, indent=2, default=str), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="contact-{contact_id}.json"'})


@router.post("/app/contacts/{contact_id}/erase")
def contact_erase(contact_id: int, confirm: str = Form(""), session: SessionRecord = Depends(ui_session("Admin")), db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    if confirm.strip() != "ERASE":
        return RedirectResponse(f"/app/contacts/{contact_id}?{urlencode({'err': 'Type ERASE to confirm'})}", status_code=303)
    privacy.erase_person(db, contact_id=contact_id, actor=session.email)
    db.commit()
    return RedirectResponse(f"/app/contacts/{contact_id}?saved=1", status_code=303)


# --- admin -------------------------------------------------------------------


@router.get("/app/admin/users", response_class=HTMLResponse)
def admin_users(request: Request, msg: str = "", session: SessionRecord = Depends(ui_session("Admin")), db: Session = Depends(get_db)) -> Response:
    users = db.scalars(select(UserRole).order_by(UserRole.email)).all()
    return templates.TemplateResponse(request, "admin_users.html", _ctx(session, "admin", users=users, msg=msg, hats=permissions.HATS, effective={u.id: sorted(permissions.BASE_PERMISSIONS.get(u.role.value, set()) | {p for h in permissions.hats_for(db, u.email) for p in permissions.HATS[h].perms}) for u in users}))


@router.post("/app/admin/users")
async def admin_user_add(request: Request, session: SessionRecord = Depends(ui_session("Admin")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    try:
        clean = _clean_email(str(form.get("email", "")))
        new_role = Role(str(form.get("role", "")))
    except ValueError:
        return RedirectResponse("/app/admin/users?msg=Invalid+email+or+role", status_code=303)
    hats = ",".join(h for h in permissions.HATS if form.get(f"hat_{h}") == "1")
    existing = db.scalar(select(UserRole).where(func.lower(UserRole.email) == clean))
    if existing:
        existing.role, existing.extra_roles = new_role, hats or None
        revoke_sessions(db, clean)  # the old access must not outlive the change
    else:
        db.add(UserRole(email=clean, role=new_role, extra_roles=hats or None))
    _audit(db, session, "user_role.set", "user_role", clean, role=new_role.value, hats=hats)
    db.commit()
    return RedirectResponse("/app/admin/users?msg=Saved", status_code=303)


@router.post("/app/admin/users/{user_id}/delete")
def admin_user_delete(user_id: int, session: SessionRecord = Depends(ui_session("Admin")), db: Session = Depends(get_db)) -> Response:
    record = db.get(UserRole, user_id)
    if record and record.email.lower() == session.email.lower():
        return RedirectResponse("/app/admin/users?msg=You+cannot+remove+your+own+role", status_code=303)
    if record:
        _audit(db, session, "user_role.remove", "user_role", record.email)
        revoke_sessions(db, record.email)
        db.delete(record)
        db.commit()
    return RedirectResponse("/app/admin/users?msg=Removed", status_code=303)


__all__ = ["LoginRequired", "UiError", "login_required_handler", "router", "ui_error_handler"]
