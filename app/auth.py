from __future__ import annotations

import hashlib
import hmac
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import mailer
from app.models import AuthMagicLink, Member, Role, SessionRecord, UserRole
from app.security import expiry_after, hash_token, issue_token, now_utc

logger = logging.getLogger("club")
SESSION_COOKIE = "club_session"
SESSION_HOURS = 12


def is_dev() -> bool:
    return os.getenv("CLUB_ENV", "production").lower() in {"dev", "development", "test"}


def is_demo() -> bool:
    """A demo on fictional data: sign-in codes are shown on the login page because nothing is ever emailed.

    Needs CLUB_DEMO=1 *and* sandbox email, so it can never show a code for a deployment that really sends mail.
    """
    return os.getenv("CLUB_DEMO", "") == "1" and mailer.provider() == "sandbox"


def is_expired(value: datetime) -> bool:
    now = now_utc()
    if value.tzinfo is None:
        return value <= now.replace(tzinfo=None)
    return value <= now


def role_for_email(db: Session, email: str) -> Role | None:
    """Role allowed to sign in as this email, or None if the address is unknown."""
    email = email.lower()
    explicit = db.scalar(select(UserRole).where(func.lower(UserRole.email) == email))
    if explicit:
        return explicit.role
    # Lapsed and pending members may sign in: they need the portal to renew or pay.
    member = db.scalar(select(Member).where(func.lower(Member.email) == email))
    return Role.MEMBER if member else None


def issue_magic_link(db: Session, email: str) -> str | None:
    """Create a single-use sign-in token and 'email' it.

    The token is returned only so dev mode can surface it; callers must never
    return it to clients in production.
    """
    email = email.lower()
    role = role_for_email(db, email)
    if role is None:
        return None
    token, token_hash = issue_token()
    db.add(AuthMagicLink(token_hash=token_hash, email=email, role=role, expires_at=expiry_after(20)))
    db.commit()
    mailer.send_email(
        db,
        to=email,
        subject="Your club sign-in code",
        body=f"Your single-use sign-in code is:\n\n{token}\n\nIt expires in 20 minutes. If you did not ask for it, ignore this message.",
        stored_body="(sign-in code not stored)",
        related_type="sign_in",
    )
    if mailer.provider() == "sandbox":
        logger.warning("SANDBOX email: magic-link token for %s: %s", email, token)
    db.commit()
    return token


def revoke_sessions(db: Session, email: str) -> int:
    """End every live session for an address (used when a role is removed or changed)."""
    count = 0
    for session in db.scalars(select(SessionRecord).where(func.lower(SessionRecord.email) == email.lower(), SessionRecord.revoked_at.is_(None))):
        session.revoked_at = now_utc()
        count += 1
    return count


def purge_sessions(db: Session) -> None:
    cutoff = now_utc() - timedelta(days=7)
    for model in (SessionRecord, AuthMagicLink):
        db.execute(delete(model).where(model.expires_at < cutoff), execution_options={"synchronize_session": False})


def start_session(db: Session, email: str, role: Role, member_id: int | None = None) -> str:
    """Sign someone in: returns the cookie value. Only the hash is stored. ``member_id`` picks one member of a family
    that shares an email address; without it the member holding that address is the one signed in."""
    session_token, session_hash = issue_token()
    db.add(SessionRecord(token_hash=session_hash, email=email.lower(), role=role, expires_at=expiry_after(SESSION_HOURS * 60), member_id=member_id))
    purge_sessions(db)
    return session_token


def exchange_token(db: Session, token: str) -> tuple[str, Role] | None:
    record = db.scalar(select(AuthMagicLink).where(AuthMagicLink.token_hash == hash_token(token)))
    if not record or record.consumed_at is not None or is_expired(record.expires_at):
        return None
    record.consumed_at = now_utc()
    session_token = start_session(db, record.email, record.role)
    db.commit()
    return session_token, record.role


def is_staff(db: Session, email: str) -> bool:
    """Anyone with more than a member's own page: an administrator, a committee member, or a member wearing a hat."""
    explicit = db.scalar(select(UserRole).where(func.lower(UserRole.email) == email.lower()))
    return explicit is not None and (explicit.role != Role.MEMBER or bool((explicit.extra_roles or "").strip()))


def load_session(db: Session, raw: str | None) -> tuple[SessionRecord | None, str]:
    """Return (session, reason); reason is '' when the session is valid."""
    if not raw:
        return None, "missing"
    session = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == hash_token(raw)))
    if not session or session.revoked_at is not None or is_expired(session.expires_at):
        return None, "invalid"
    return session, ""


def csrf_token(session: SessionRecord) -> str:
    """Per-session CSRF token; unguessable without the session's stored token hash."""
    return hmac.new(session.token_hash.encode(), b"club-csrf", hashlib.sha256).hexdigest()


def csrf_ok(session: SessionRecord, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(csrf_token(session), supplied or "")
