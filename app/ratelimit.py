"""Rate limiting and form tokens for the public (unauthenticated) forms.

Counters live in the database so they survive restarts and work with several workers. Form tokens are signed
(HMAC) and carry the time the form was rendered, which defeats scripted instant submissions and replays of old pages.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting, RateHit


def client_ip(request: Request) -> str:
    """The caller's address. X-Forwarded-For is honoured only when the deployment says a trusted proxy sets it."""
    if os.getenv("CLUB_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _secret(db: Session) -> bytes:
    row = db.get(AppSetting, "form_secret")
    if row is None:
        row = AppSetting(key="form_secret", value=secrets.token_hex(32))
        db.add(row)
        db.flush()
    return row.value.encode()


def hash_ip(db: Session, ip: str) -> str:
    return hmac.new(_secret(db), ip.encode(), hashlib.sha256).hexdigest()


def allow(db: Session, key: str, limit: int, window_seconds: int) -> bool:
    """Count a hit against ``key``; False once more than ``limit`` hits fall inside the window."""
    now = datetime.now(UTC)
    row = db.get(RateHit, key)
    if row is None:
        db.add(RateHit(key=key, window_start=now, count=1))
        db.flush()
        return True
    start = row.window_start if row.window_start.tzinfo else row.window_start.replace(tzinfo=UTC)
    if now - start > timedelta(seconds=window_seconds):
        row.window_start, row.count = now, 1
        return True
    row.count += 1
    return row.count <= limit


def purge_old_hits(db: Session, older_than_hours: int = 48) -> None:
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)
    for row in db.scalars(select(RateHit)):
        start = row.window_start if row.window_start.tzinfo else row.window_start.replace(tzinfo=UTC)
        if start < cutoff:
            db.delete(row)


def issue_form_token(db: Session) -> str:
    stamp = str(int(time.time()))
    signature = hmac.new(_secret(db), stamp.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{stamp}.{signature}"


def check_form_token(db: Session, token: str, min_seconds: int | None = None, max_seconds: int = 7200) -> bool:
    if min_seconds is None:
        min_seconds = int(os.getenv("CLUB_FORM_MIN_SECONDS", "3"))
    try:
        stamp, signature = token.split(".", 1)
        issued = int(stamp)
    except (ValueError, AttributeError):
        return False
    expected = hmac.new(_secret(db), stamp.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(expected, signature):
        return False
    age = time.time() - issued
    return min_seconds <= age <= max_seconds
