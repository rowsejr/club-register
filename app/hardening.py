"""Production hardening: security headers, friendly error pages, and the health checks a monitor can watch."""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.db import get_db
from app.models import JobRun

logger = logging.getLogger("club")

# Everything the pages need is served from this site: no inline scripts, no outside scripts, styles or frames.
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
MESSAGES = {
    400: "That request did not look right.",
    401: "Please sign in to see that.",
    403: "You do not have access to that. Ask an administrator if you need it.",
    404: "We could not find that page.",
    405: "That is not something this page can do.",
    422: "Something in what was sent was not valid. Please go back and check it.",
    429: "Too many requests. Please wait a little and try again.",
}
STALE_JOBS = timedelta(hours=3)
STALE_BACKUP = timedelta(hours=36)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=UTC)


def _page(request: Request, status: int, message: str, extra: dict | None = None) -> Response:
    from app.ui.routes import templates

    return templates.TemplateResponse(request, "error.html", {"status": status, "message": message, "user": None, "nav": "", **(extra or {})}, status_code=status)


def secure(response: Response, path: str) -> Response:
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    if not path.startswith(("/docs", "/openapi", "/redoc")):
        headers.setdefault("Content-Security-Policy", CSP)
    if path.startswith(("/app", "/login", "/i/", "/pay/")):
        headers.setdefault("Cache-Control", "no-store")  # pages with people's details are not kept by browsers or proxies
    if os.getenv("CLUB_ENV") == "production":
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        return secure(await call_next(request), request.url.path)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if not _wants_html(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
        return _page(request, exc.status_code, MESSAGES.get(exc.status_code, "Something went wrong."))

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> Response:
        if not _wants_html(request):
            return JSONResponse({"detail": jsonable(exc.errors())}, status_code=422)
        return _page(request, 422, MESSAGES[422])

    @app.exception_handler(Exception)
    async def crashed(request: Request, exc: Exception) -> Response:
        reference = uuid.uuid4().hex[:8]
        logger.error("unhandled error ref=%s %s %s", reference, request.method, request.url.path, exc_info=exc)
        if not _wants_html(request):
            return secure(JSONResponse({"detail": "Internal server error", "reference": reference}, status_code=500), request.url.path)
        return secure(_page(request, 500, f"Something went wrong on our side. It has been logged (reference {reference}). If it keeps happening, tell the club with that reference."), request.url.path)

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def healthz() -> dict:
        """The process is up. A monitor that only wants to know the site answers uses this."""
        return {"status": "ok"}

    @app.api_route("/readyz", methods=["GET", "HEAD"], include_in_schema=False)
    def readyz(strict: int = 0, db: Session = Depends(get_db)) -> Response:
        """The site can reach its database, its timer is running and last night's backup happened. ``?strict=1`` turns a warning into a 503 so a monitor can page someone."""
        now = datetime.now(UTC)
        problems: list[str] = []
        try:
            db.execute(text("select 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "down", "problems": ["the database cannot be reached"]}, status_code=503)
        from app.jobs import last_tick

        ran = [t for t in (_aware(db.scalar(select(func.max(JobRun.started_at)).where(JobRun.dry_run.is_(False)))), _aware(last_tick(db))) if t is not None]
        last_job = max(ran) if ran else None  # the hourly runner notes every call, even when nothing was due
        last_backup = _aware(db.scalar(select(func.max(JobRun.finished_at)).where(JobRun.job == "nightly_backup", JobRun.status == "ok", JobRun.dry_run.is_(False))))
        if last_job is None or now - last_job > STALE_JOBS:
            problems.append("the scheduled jobs have not run in the last 3 hours")
        if last_backup is None or now - last_backup > STALE_BACKUP:
            problems.append("there has been no successful backup in the last 36 hours")
        body = {"status": "degraded" if problems else "ok", "problems": problems, "last_job_run": last_job.isoformat() if last_job else None, "last_backup": last_backup.isoformat() if last_backup else None}
        return JSONResponse(body, status_code=503 if problems and strict else 200)


def jsonable(errors) -> list:  # noqa: ANN001
    return [{k: (str(v) if k == "ctx" else v) for k, v in e.items() if k != "input"} for e in errors]
