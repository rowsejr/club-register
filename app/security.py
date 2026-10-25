from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC)


def issue_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def expiry_after(minutes: int) -> datetime:
    return now_utc() + timedelta(minutes=minutes)
