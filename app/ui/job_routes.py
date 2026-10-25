"""Admin screen for the scheduled jobs: what runs, when it last ran, and a button to rehearse or run one now."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import jobs
from app.db import get_db
from app.models import SessionRecord
from app.ui.finance_routes import _back
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
ADMIN = ui_session(perm="admin.jobs")


@router.get("/app/admin/jobs", response_class=HTMLResponse)
def jobs_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ADMIN), db: Session = Depends(get_db)) -> Response:
    today = date.today()
    rows = [{"job": j, "last": jobs.last_real_run(db, j.name), "due": jobs.is_due(j, today, jobs.last_real_run(db, j.name))} for j in jobs.JOBS.values()]
    return templates.TemplateResponse(request, "admin_jobs.html", _ctx(session, "admin", rows=rows, runs=jobs.recent_runs(db), msg=msg, err=err))


@router.post("/app/admin/jobs/{name}/run")
def job_run(name: str, mode: str = Form("rehearse"), session: SessionRecord = Depends(ADMIN), db: Session = Depends(get_db)) -> Response:
    job = jobs.JOBS.get(name)
    if job is None:
        raise UiError(404, "Unknown job")
    dry = mode != "now"
    record = jobs.run_job(db, job, date.today(), dry, triggered_by=session.email)
    _audit(db, session, "job.run", "job", name, dry_run=dry, status=record.status)
    db.commit()
    return _back("/app/admin/jobs", msg=f"{job.label}: {'rehearsal' if dry else 'run'} {record.status}", err="" if record.status == "ok" else str(record.summary.get("error", "")))
