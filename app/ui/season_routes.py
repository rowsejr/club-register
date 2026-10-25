"""The annual cycle screens: renewal notices, season invoices, lapsing, and the extras charged to a person each season."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import catalogue, finance, membership, season
from app.db import get_db
from app.models import Contact, NominalCode, RecurringFee, SessionRecord
from app.ui.finance_routes import _back, _date_or_none, _money
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
RUN = ui_session(perm="billing.run")


def _default_year() -> int:
    today = date.today()
    return today.year + 1 if today.month >= 7 else today.year  # from the summer, "the season" means the one coming


def _ids(form, name: str) -> list[int]:
    return [int(str(v)) for v in form.getlist(name) if str(v).isdigit()]


@router.get("/app/season", response_class=HTMLResponse)
def season_page(request: Request, year: int | None = None, msg: str = "", err: str = "", session: SessionRecord = Depends(RUN), db: Session = Depends(get_db)) -> Response:
    year = year if year and 2000 <= year <= 2100 else _default_year()
    plans = season.plan(db, year)
    ctx = _ctx(
        session, "season", year=year, progress=season.progress(db, year), plans=plans, notices=season.notice_candidates(db, year), lapses=season.lapse_candidates(db, year),
        mode=finance.get_setting(db, "season_invoice_mode") or "combined", window=membership.renewal_window_months(db), msg=msg, err=err,
    )
    return templates.TemplateResponse(request, "season.html", ctx)


@router.post("/app/season/notices")
async def season_notices(request: Request, session: SessionRecord = Depends(RUN), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    year = int(str(form.get("year", _default_year())))
    ids = _ids(form, "member")
    if not ids:
        return _back(f"/app/season?year={year}", err="Tick at least one member to tell")
    result = season.send_notices(db, year, session.email, member_ids=ids)
    _audit(db, session, "season.notices", "period", year, sent=len(result["sent"]), skipped=len(result["skipped"]))
    db.commit()
    detail = f"{len(result['sent'])} notice(s) sent" + (f"; not sent: {'; '.join(result['skipped'][:4])}" if result["skipped"] else "")
    return _back(f"/app/season?year={year}", msg=detail)


@router.post("/app/season/invoices")
async def season_invoices(request: Request, session: SessionRecord = Depends(RUN), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    year = int(str(form.get("year", _default_year())))
    ids = _ids(form, "payer")
    action = str(form.get("action", "issue_email"))
    if not ids:
        return _back(f"/app/season?year={year}", err="Tick at least one payer to invoice")
    if "confirm" not in form:
        return _back(f"/app/season?year={year}", err="Tick the box to confirm: this creates invoices")
    report = season.run(db, year, session.email, payer_ids=ids, issue=action != "draft", send=action == "issue_email")
    db.commit()
    parts = [f"{len(report.invoices)} invoice(s) created", f"{report.emailed} emailed"]
    if report.not_emailed:
        parts.append(f"to post or hand over: {'; '.join(report.not_emailed[:4])}")
    if report.skipped:
        parts.append(f"not billed: {'; '.join(report.skipped[:4])}")
    return _back(f"/app/season?year={year}", msg=". ".join(parts))


@router.post("/app/season/lapse")
async def season_lapse(request: Request, session: SessionRecord = Depends(ui_session(perm="members.write")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    year = int(str(form.get("year", _default_year())))
    ids = _ids(form, "member")
    if not ids:
        return _back(f"/app/season?year={year}", err="Tick at least one member to lapse")
    if "confirm" not in form:
        return _back(f"/app/season?year={year}", err="Tick the box to confirm: lapsing ends their membership")
    result = season.lapse(db, year, ids, session.email, release_spaces="release" in form)
    db.commit()
    return _back(f"/app/season?year={year}", msg=f"{result['lapsed']} lapsed" + (f"; {result['released']} space(s) released" if result["released"] else ""))


# --------------------------------------------------------------------------- extras charged each season


def _extras(db: Session, contact_id: int) -> list[RecurringFee]:
    return list(db.scalars(select(RecurringFee).where(RecurringFee.contact_id == contact_id).order_by(RecurringFee.active.desc(), RecurringFee.name)))


@router.get("/app/contacts/{contact_id}/extras", response_class=HTMLResponse)
def extras_panel(contact_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    items = [o for o in catalogue.options(db) if o.group == "Club items"]
    return templates.TemplateResponse(
        request, "_recurring_fees.html", _ctx(session, "contacts", contact=contact, fees=_extras(db, contact_id), items=items, nominals=db.scalars(select(NominalCode).order_by(NominalCode.code)).all(), msg=msg, err=err)
    )


@router.post("/app/contacts/{contact_id}/extras")
def extras_add(
    contact_id: int,
    request: Request,
    name: str = Form(""),
    amount: str = Form(""),
    quantity: str = Form("1"),
    nominal: str = Form(""),
    ends_on: str = Form(""),
    note: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="finance.write")),
    db: Session = Depends(get_db),
) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    back = f"/app/contacts/{contact_id}"
    try:
        if not name.strip():
            raise finance.FinanceError("Say what the extra charge is for")
        minor = _money(amount)
        qty = float(quantity or 1)
        if qty <= 0 or minor <= 0:
            raise finance.FinanceError("The amount and quantity must be more than zero")
        db.add(RecurringFee(contact_id=contact_id, name=" ".join(name.split()), amount_minor=minor, quantity=qty, nominal_code=nominal or None, ends_on=_date_or_none(ends_on), note=note.strip() or None))
        _audit(db, session, "extras.add", "contact", contact_id, name=name, amount=minor)
        db.commit()
    except (finance.FinanceError, ValueError) as exc:
        db.rollback()
        return _back(back, err=str(exc))
    return _back(back, msg=f"{name.strip()} will be added to {contact.name}'s invoice each season")


@router.post("/app/extras/{fee_id}/{action}")
def extras_action(fee_id: int, action: str, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    fee = db.get(RecurringFee, fee_id)
    if fee is None or action not in {"stop", "delete"}:
        raise UiError(404, "Extra charge not found")
    back = f"/app/contacts/{fee.contact_id}"
    if action == "delete":
        db.delete(fee)
    else:
        fee.active, fee.ends_on = False, fee.ends_on or date.today()
    _audit(db, session, f"extras.{action}", "recurring_fee", fee_id)
    db.commit()
    return _back(back, msg="Done")
