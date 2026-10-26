"""Signing in from the club website, and forms the website sends: see app/website.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app import applications, auth, ratelimit, website
from app.db import get_db
from app.feeds import FeedRefused
from app.ui.routes import templates

router = APIRouter(include_in_schema=False)


def _limited(request: Request, db: Session, what: str, limit: int) -> bool:
    return not ratelimit.allow(db, f"{what}:{ratelimit.hash_ip(db, ratelimit.client_ip(request))}", limit, 3600)


def _notice(request: Request, heading: str, message: str, status: int) -> Response:
    return templates.TemplateResponse(request, "public_thanks.html", {"user": None, "nav": "", "heading": heading, "message": message, "sign_in": True}, status_code=status)


@router.get("/sso", response_class=HTMLResponse)
def sign_in_from_website(request: Request, email: str = "", ts: str = "", nonce: str = "", sig: str = "", member_id: str = "", next: str = "/app", db: Session = Depends(get_db)) -> Response:
    if _limited(request, db, "sso", 60):
        db.commit()
        return _notice(request, "Please wait a minute", "Too many sign-in attempts. Please try again shortly.", 429)
    try:
        role, chosen = website.check_sso(db, email, ts, nonce, sig, member_id)
    except website.StaffMustSignIn:
        db.commit()
        return RedirectResponse("/login?staff=1", status_code=303)
    except FeedRefused as exc:
        db.commit()
        return _notice(request, "That sign-in link did not work", str(exc), exc.status)
    cookie = auth.start_session(db, email, role, member_id=chosen)
    db.commit()
    target = next if next.startswith("/app") else "/app"
    # The session cookie is "SameSite=Strict", so the last step must start on this site: a page that moves itself on.
    page = templates.TemplateResponse(request, "sso_continue.html", {"user": None, "nav": "", "target": target})
    page.set_cookie(auth.SESSION_COOKIE, cookie, max_age=auth.SESSION_HOURS * 3600, httponly=True, samesite="strict", secure=not auth.is_dev())
    return page


@router.post("/incoming/{kind}")
async def form_from_website(kind: str, request: Request, db: Session = Depends(get_db)) -> Response:
    if _limited(request, db, "incoming", 200):
        db.commit()
        return JSONResponse({"ok": False, "error": "slow down"}, status_code=429)
    body = await request.body()
    try:
        data = website.check_incoming(db, kind, {k.lower(): v for k, v in request.headers.items()}, body)
        thanks = website.receive(db, kind, data)
    except FeedRefused as exc:
        db.commit()  # keep the rate-limit count and the used request id
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)
    except applications.ApplicationError as exc:
        db.commit()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    db.commit()
    return JSONResponse({"ok": True, "message": thanks})
