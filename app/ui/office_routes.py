"""The everyday office pages: home, new members, checks, requests, the chase list, the RYA census, and members' own requests."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import boats, invoice_comms, office, people, permissions
from app.db import get_db
from app.models import Contact, MemberRequest, SessionRecord
from app.ui.finance_routes import _back
from app.ui.routes import UiError, _audit, _csv_response, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
ANY = ui_session()
PEOPLE = ui_session(perm="members.read")
MONEY = ui_session(perm="finance.read")
MONEY_WRITE = ui_session(perm="finance.write")


def home_context(db: Session, session: SessionRecord, perms: set[str]) -> dict:
    is_admin = session.role.value == "Admin"
    return {"sections": office.todo(db, perms, permissions.hats_for(db, session.email), is_admin), "hats": permissions.hats_for(db, session.email)}


@router.get("/app/me", response_class=HTMLResponse)
def my_membership(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    from app.ui.people_routes import member_home_context

    return templates.TemplateResponse(request, "portal.html", _ctx(session, "me", msg=msg, err=err, kinds=office.REQUEST_KINDS, **member_home_context(db, session)))


@router.post("/app/me/requests")
def my_request(kind: str = Form(""), details: str = Form(""), session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    from app.ui.people_routes import _me

    member, contact = _me(db, session)
    if contact is None:
        raise UiError(404, "No details are linked to this sign-in")
    try:
        office.create_request(db, contact, kind, details, member_id=member.id if member else None)
        db.commit()
    except office.OfficeError as exc:
        db.rollback()
        return _back("/app/me", err=str(exc))
    role = office.ROLE_NAMES.get(office.REQUEST_KINDS[kind][1], "the club")
    return _back("/app/me", msg=f"Thank you. The {role} has been told and will be in touch.")


@router.get("/app/new-members", response_class=HTMLResponse)
def new_members(request: Request, session: SessionRecord = Depends(PEOPLE), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "new_members.html", _ctx(session, "people", p=office.pipeline(db)))


@router.get("/app/checks", response_class=HTMLResponse)
def checks(request: Request, session: SessionRecord = Depends(PEOPLE), db: Session = Depends(get_db)) -> Response:
    today = date.today()
    issues = people.data_issues(db, today)
    insurance = boats.insurance_problems(db, today)
    return templates.TemplateResponse(request, "checks.html", _ctx(session, "people", issues=issues, insurance=insurance, total=sum(len(i.contacts) + len(i.extra) for i in issues) + len(insurance)))


@router.get("/app/requests", response_class=HTMLResponse)
def requests_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    perms = request.state.perms
    if not (permissions.allows(perms, "members.write") or permissions.allows(perms, "boats.write")):
        raise UiError(403, "You do not have access to this page. Ask an administrator if you need it.")
    return templates.TemplateResponse(request, "requests.html", _ctx(session, "people", rows=office.requests_for(db, perms), done=office.requests_for(db, perms, "done")[-10:], kinds=office.REQUEST_KINDS, msg=msg, err=err))


@router.post("/app/requests/{request_id:int}/close")
def request_close(request_id: int, request: Request, status: str = Form(...), outcome: str = Form(""), session: SessionRecord = Depends(ANY), db: Session = Depends(get_db)) -> Response:
    row = db.get(MemberRequest, request_id)
    if row is None or row.kind not in office.REQUEST_KINDS:
        raise UiError(404, "Request not found")
    if not permissions.allows(request.state.perms, office.REQUEST_KINDS[row.kind][2]):
        raise UiError(403, "This request is for another volunteer")
    try:
        office.close_request(db, row, status, outcome, session.email)
        db.commit()
    except office.OfficeError as exc:
        db.rollback()
        return _back("/app/requests", err=str(exc))
    return _back("/app/requests", msg="Done, and they have been told by email" if status == "done" else "Declined, and they have been told by email")


@router.get("/app/census", response_class=HTMLResponse)
def census_page(request: Request, on: str = "", session: SessionRecord = Depends(PEOPLE), db: Session = Depends(get_db)) -> Response:
    try:
        day = date.fromisoformat(on) if on else date.today()
    except ValueError:
        day = date.today()
    return templates.TemplateResponse(request, "census.html", _ctx(session, "people", c=office.census(db, day), genders=office.GENDERS))


@router.get("/app/census.csv")
def census_csv(on: str = "", session: SessionRecord = Depends(PEOPLE), db: Session = Depends(get_db)) -> Response:
    try:
        day = date.fromisoformat(on) if on else date.today()
    except ValueError:
        day = date.today()
    c = office.census(db, day)
    rows = [[band, label, counts[key]] for band, counts in c.by_band.items() for key, label in office.GENDERS]
    rows += [[band, f"category: {cat}", counts[band]] for cat, counts in c.by_category.items() for band in counts]
    return _csv_response(f"census-{day.isoformat()}.csv", ["age band", "gender or category", "people"], rows)


@router.get("/app/finance/chase", response_class=HTMLResponse)
def chase_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(MONEY), db: Session = Depends(get_db)) -> Response:
    rows = office.chase_list(db)
    return templates.TemplateResponse(request, "chase.html", _ctx(session, "finance", rows=rows, total=sum(r.owed for r in rows), msg=msg, err=err, today=date.today()))


@router.post("/app/finance/chase/{contact_id:int}/send")
def chase_send(contact_id: int, session: SessionRecord = Depends(MONEY_WRITE), db: Session = Depends(get_db)) -> Response:
    debtor = next((d for d in office.chase_list(db) if d.contact.id == contact_id), None)
    if debtor is None:
        return _back("/app/finance/chase", err="They do not owe anything now")
    due = [i.id for i, _owed in debtor.invoices if i.due_date is None or i.due_date < date.today()] or [i.id for i, _owed in debtor.invoices]
    result = invoice_comms.send_reminders(db, due, session.email)
    _audit(db, session, "chase.send", "contact", contact_id, sent=result["sent"], skipped=result["skipped"])
    db.commit()
    if result["sent"]:
        return _back("/app/finance/chase", msg=f"Reminder emailed to {debtor.contact.name}")
    return _back("/app/finance/chase", err=f"Nothing sent to {debtor.contact.name}: " + "; ".join(result["skipped"])[:300])


@router.post("/app/finance/chase/{contact_id:int}/note")
def chase_note(contact_id: int, body: str = Form(""), session: SessionRecord = Depends(MONEY_WRITE), db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Person not found")
    try:
        office.add_chase_note(db, contact, body, session.email)
        db.commit()
    except office.OfficeError as exc:
        db.rollback()
        return _back("/app/finance/chase", err=str(exc))
    return _back("/app/finance/chase", msg="Note saved")
