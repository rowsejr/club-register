"""The club office: the web application. Every screen lives in app/ui; this file only puts them together."""

from __future__ import annotations

import logging
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import applications, events, finance, hardening, membership, privacy  # noqa: F401 - importing registers the "when paid" hooks
from app.auth import is_dev
from app.db import init_db
from app.security import now_utc
from app.ui.admin_routes import router as admin_ui_router
from app.ui.berth_routes import router as berth_ui_router
from app.ui.boat_routes import router as boat_ui_router
from app.ui.cashbook_routes import router as cashbook_ui_router
from app.ui.event_public_routes import router as event_public_ui_router
from app.ui.event_routes import router as event_ui_router
from app.ui.feed_routes import router as feed_ui_router
from app.ui.finance_routes import router as finance_ui_router
from app.ui.job_routes import router as job_ui_router
from app.ui.mailing_routes import router as mailing_ui_router
from app.ui.office_routes import router as office_ui_router
from app.ui.payment_routes import router as payment_ui_router
from app.ui.people360_routes import router as people360_ui_router
from app.ui.people_routes import router as people_ui_router
from app.ui.public_routes import router as public_ui_router
from app.ui.report_routes import router as report_ui_router
from app.ui.routes import LoginRequired, UiError, login_required_handler, ui_error_handler
from app.ui.routes import router as ui_router
from app.ui.season_routes import router as season_ui_router
from app.ui.training_routes import router as training_ui_router
from app.ui.website_routes import router as website_ui_router

logger = logging.getLogger("club")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Club office", lifespan=lifespan, docs_url="/docs" if is_dev() else None, redoc_url=None, openapi_url="/openapi.json" if is_dev() else None)

for router in (
    office_ui_router, ui_router, cashbook_ui_router, season_ui_router, people360_ui_router, mailing_ui_router, job_ui_router, payment_ui_router, event_ui_router, admin_ui_router,
    event_public_ui_router, berth_ui_router, report_ui_router, finance_ui_router, boat_ui_router, people_ui_router, public_ui_router, training_ui_router, feed_ui_router, website_ui_router,
):
    app.include_router(router)
app.add_exception_handler(LoginRequired, login_required_handler)  # type: ignore[arg-type]
app.add_exception_handler(UiError, ui_error_handler)  # type: ignore[arg-type]
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
hardening.install(app)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "timestamp": now_utc().isoformat()}


def _version() -> dict[str, str]:
    """The commit this copy was built from. A deployment made with ``git archive`` has it written into app/version.txt;
    a working copy asks git."""
    stamp = (Path(__file__).parent / "version.txt").read_text(encoding="utf-8").strip()
    if not stamp.startswith("$Format"):
        commit, _, built = stamp.partition(" ")
        return {"commit": commit, "built": built}
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cs"], capture_output=True, text=True, timeout=5, cwd=Path(__file__).parent, check=True).stdout.strip()  # noqa: S603, S607
        commit, _, built = out.partition(" ")
        return {"commit": commit or "unknown", "built": built}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "built": ""}


VERSION = _version()


@app.get("/version")
def version() -> dict[str, str]:
    """Which version is running. Public: it says nothing about members."""
    return VERSION
