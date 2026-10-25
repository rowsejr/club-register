"""Administrator and treasurer screens for running the system safely: the audit log and closing a financial year."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app import finance, reports, yearend
from app.db import get_db
from app.models import AuditLog, SessionRecord
from app.ui.finance_routes import _back
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
AUDIT = ui_session(perm="admin.audit")
LOCK = ui_session(perm="finance.lock")
PAGE_SIZE = 50


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text) if text.strip() else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- audit log


@router.get("/app/admin/audit", response_class=HTMLResponse)
def audit_page(
    request: Request, actor: str = "", action: str = "", target: str = "", q: str = "", start: str = "", end: str = "", page: int = 1, session: SessionRecord = Depends(AUDIT), db: Session = Depends(get_db)
) -> Response:
    stmt = select(AuditLog).order_by(AuditLog.id.desc())
    if actor.strip():
        stmt = stmt.where(func.lower(AuditLog.actor_email).like(f"%{actor.strip().lower()}%"))
    if action.strip():
        stmt = stmt.where(AuditLog.action.like(f"{action.strip()}%"))
    if target.strip():
        stmt = stmt.where(or_(AuditLog.target_type == target.strip(), AuditLog.target_id == target.strip()))
    if q.strip():
        stmt = stmt.where(cast(AuditLog.details, String).like(f"%{q.strip()}%"))
    if (d := _day(start)) is not None:
        stmt = stmt.where(AuditLog.created_at >= datetime.combine(d, time.min, tzinfo=UTC))
    if (d := _day(end)) is not None:
        stmt = stmt.where(AuditLog.created_at < datetime.combine(d + timedelta(days=1), time.min, tzinfo=UTC))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    actions = [a for (a,) in db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action))]
    params = {k: v for k, v in {"actor": actor, "action": action, "target": target, "q": q, "start": start, "end": end}.items() if v}
    return templates.TemplateResponse(request, "audit.html", _ctx(session, "admin", rows=rows, pager={"page": page, "pages": pages, "total": total}, params=params, actions=actions, f={"actor": actor, "action": action, "target": target, "q": q, "start": start, "end": end}))


# --------------------------------------------------------------------------- closing the year


def _year_bounds(db: Session, text: str) -> tuple[date, date]:
    """The financial year the screen is about: ``text`` is the year it starts in (default: the last complete year)."""
    start_month = reports.fy_start_month(db)
    if text.strip():
        if not (text.isascii() and text.isdigit() and 1990 <= int(text) <= 2100):
            raise UiError(404, "No such year")
        return reports.fy_bounds(db, date(int(text), start_month, 1))
    current_start, _ = reports.fy_bounds(db)
    return reports.fy_bounds(db, current_start - timedelta(days=1))


@router.get("/app/admin/year-end", response_class=HTMLResponse)
def year_end_page(request: Request, year: str = "", msg: str = "", err: str = "", session: SessionRecord = Depends(LOCK), db: Session = Depends(get_db)) -> Response:
    start, end = _year_bounds(db, year)
    ready = yearend.readiness(db, start, end)
    return templates.TemplateResponse(
        request, "year_end.html", _ctx(session, "finance", start=start, end=end, ready=ready, lock=finance.locked_until(db), is_admin=session.role.value == "Admin", today=date.today(), latest=date.today() - timedelta(days=1), msg=msg, err=err, sub="yearend")
    )


@router.post("/app/admin/year-end/lock")
def year_end_lock(through: str = Form(""), reason: str = Form(""), session: SessionRecord = Depends(LOCK), db: Session = Depends(get_db)) -> Response:
    day = _day(through)
    try:
        if through.strip() and day is None:
            raise finance.FinanceError("Write the date like 2026-12-31")
        finance.set_lock(db, day, session.email, is_admin=session.role.value == "Admin", reason=reason)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back("/app/admin/year-end", err=str(exc))
    return _back("/app/admin/year-end", msg=f"Books closed up to {day:%d %b %Y}" if day else "Books reopened")


@router.get("/app/admin/year-end/pack.zip")
def year_end_pack(year: str = "", session: SessionRecord = Depends(LOCK), db: Session = Depends(get_db)) -> Response:
    start, end = _year_bounds(db, year)
    if end < start:
        raise UiError(404, "No such year")
    data = yearend.pack(db, start, end)
    _audit(db, session, "yearend.pack", "settings", "year-end", start=start.isoformat(), end=end.isoformat())
    db.commit()
    return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="year-end-{start.isoformat()}-to-{end.isoformat()}.zip"'})
