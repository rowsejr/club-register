"""The signed, read-only feeds other tools pull: see app/feeds.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app import feeds, ratelimit
from app.db import get_db

router = APIRouter(include_in_schema=False)


@router.get("/feeds/{name}.json")
def feed(name: str, request: Request, client: str = "", ts: str = "", nonce: str = "", sig: str = "", db: Session = Depends(get_db)) -> Response:
    if not ratelimit.allow(db, f"feed:{ratelimit.hash_ip(db, ratelimit.client_ip(request))}", 120, 3600):
        db.commit()
        return JSONResponse({"error": "slow down"}, status_code=429)
    try:
        feeds.check(db, name, client, ts, nonce, sig)
    except feeds.FeedRefused as exc:
        db.commit()  # keep the rate-limit count
        return JSONResponse({"error": str(exc)}, status_code=exc.status)
    body = feeds.build(db, name)
    db.commit()
    return JSONResponse(body, headers={"Cache-Control": "no-store"})
