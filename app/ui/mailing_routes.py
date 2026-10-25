"""Mailing screens: compose to a group, check who will receive it, send, and see what went out."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import mailings, people_filters
from app.db import get_db
from app.models import Mailing, MailTemplate, SessionRecord
from app.ui.finance_routes import _back
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
READ = ui_session(perm="members.read")
WRITE = ui_session(perm="members.write")
AUDIENCE_KEYS = ("q", "status", "type", "age", "list", "boat", "unpaid", "member", "tag")


def _audience_from(values) -> dict[str, str]:
    return {k: str(values.get(k, "")).strip() for k in AUDIENCE_KEYS if str(values.get(k, "")).strip() not in {"", "all"}}


@router.get("/app/mailings", response_class=HTMLResponse)
def mailings_list(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    rows = db.scalars(select(Mailing).order_by(Mailing.id.desc()).limit(50)).all()
    return templates.TemplateResponse(request, "mailings.html", _ctx(session, "contacts", rows=rows, msg=msg, err=err))


@router.get("/app/mailings/new", response_class=HTMLResponse)
def mailing_new(request: Request, template: int | None = None, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    mailings.seed_templates(db)
    db.commit()
    chosen = db.get(MailTemplate, template) if template else None
    params = _audience_from(request.query_params)
    f = people_filters.PeopleFilter(**{k: v for k, v in params.items() if k in people_filters.PeopleFilter.__dataclass_fields__})
    return templates.TemplateResponse(
        request, "mailing_form.html",
        _ctx(session, "contacts", mailing=None, f=f, ch=people_filters.choices(db), subject=chosen.subject if chosen else "", body=chosen.body if chosen else "", chosen=chosen,
             templates_=db.scalars(select(MailTemplate).order_by(MailTemplate.name)).all(), fields=mailings.MERGE_FIELDS, count=len(mailings.audience(db, params))),
    )


@router.post("/app/mailings")
async def mailing_save(request: Request, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    mailing_id = int(str(form["mailing_id"])) if str(form.get("mailing_id", "")).isdigit() else None
    try:
        mailing = mailings.save_draft(db, mailing_id, subject=str(form.get("subject", "")), body=str(form.get("body", "")), audience_params=_audience_from(form), actor=session.email)
        if form.get("save_template"):
            mailings.save_template(db, str(form.get("save_template")), mailing.subject, mailing.body)
        db.commit()
    except mailings.MailingError as exc:
        db.rollback()
        return _back("/app/mailings/new", err=str(exc))
    return RedirectResponse(f"/app/mailings/{mailing.id}", status_code=303)


@router.get("/app/mailings/{mailing_id:int}", response_class=HTMLResponse)
def mailing_view(mailing_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    mailing = db.get(Mailing, mailing_id)
    if mailing is None:
        raise UiError(404, "Mailing not found")
    ready = mailings.preview(db, mailing) if mailing.status == "draft" else None
    return templates.TemplateResponse(request, "mailing_view.html", _ctx(session, "contacts", mailing=mailing, ready=ready, msg=msg, err=err))


@router.post("/app/mailings/{mailing_id:int}/send")
def mailing_send(mailing_id: int, confirm: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    mailing = db.get(Mailing, mailing_id)
    if mailing is None:
        raise UiError(404, "Mailing not found")
    if not confirm:
        return _back(f"/app/mailings/{mailing_id}", err="Tick the box to confirm: this sends the email")
    try:
        result = mailings.send(db, mailing, session.email)
        _audit(db, session, "mailing.send", "mailing", mailing_id, **result)
        db.commit()
    except mailings.MailingError as exc:
        db.rollback()
        return _back(f"/app/mailings/{mailing_id}", err=str(exc))
    return _back(f"/app/mailings/{mailing_id}", msg=f"Sent to {result['sent']} people" + (f"; {result['skipped']} left out" if result["skipped"] else ""))


@router.post("/app/mailings/{mailing_id:int}/delete")
def mailing_delete(mailing_id: int, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    mailing = db.get(Mailing, mailing_id)
    if mailing is not None and mailing.status == "draft":
        db.delete(mailing)
        db.commit()
    return _back("/app/mailings", msg="Draft deleted")
