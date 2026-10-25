"""Outgoing email. Every message is logged in ``outbound_emails`` so the club can see what went out.

Providers (``CLUB_EMAIL_PROVIDER``):
* ``sandbox`` (default): nothing leaves the machine; the message is stored as ``simulated``.
* ``smtp``: sent through ``CLUB_SMTP_HOST`` / ``PORT`` / ``USER`` with the password read from the file named
  by ``CLUB_SMTP_PASSWORD_PATH``; ``CLUB_EMAIL_FROM`` sets the sender. Port 465 uses SSL, otherwise STARTTLS.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import OutboundEmail

logger = logging.getLogger("club")


def provider() -> str:
    return os.getenv("CLUB_EMAIL_PROVIDER", "sandbox").lower()


def _smtp_send(message: EmailMessage) -> None:
    host = os.getenv("CLUB_SMTP_HOST", "")
    if not host:
        raise RuntimeError("CLUB_SMTP_HOST is not set")
    port = int(os.getenv("CLUB_SMTP_PORT", "587"))
    user = os.getenv("CLUB_SMTP_USER", "")
    password_path = os.getenv("CLUB_SMTP_PASSWORD_PATH", "")
    password = Path(password_path).read_text(encoding="utf-8").strip() if password_path else ""
    client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
    with client:
        if port != 465:
            client.starttls()
        if user:
            client.login(user, password)
        client.send_message(message)


def _deliver(record: OutboundEmail, body: str, attachment: tuple[str, bytes, str] | None, reply_to: str | None) -> None:
    """Hand one recorded message to the provider and note the outcome on the record."""
    if provider() == "sandbox":
        record.status = "simulated"
        logger.warning("SANDBOX email to %s: %s", record.to_address, record.subject)
        return
    message = EmailMessage()
    message["To"], message["Subject"] = record.to_address, record.subject
    message["From"] = os.getenv("CLUB_EMAIL_FROM", "office@localhost")
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)
    if attachment:
        maintype, _, subtype = attachment[2].partition("/")
        message.add_attachment(attachment[1], maintype=maintype, subtype=subtype or "octet-stream", filename=attachment[0])
    try:
        _smtp_send(message)
        record.status, record.error = "sent", None
    except Exception as exc:  # noqa: BLE001 - any delivery failure is recorded, never raised into a request
        record.status, record.error = "failed", str(exc)[:500]
        logger.error("Email to %s failed: %s", record.to_address, exc)


def send_email(
    db: Session,
    *,
    to: str,
    subject: str,
    body: str,
    attachment: tuple[str, bytes, str] | None = None,
    related_type: str | None = None,
    related_id: int | None = None,
    actor: str | None = None,
    reply_to: str | None = None,
    stored_body: str | None = None,
) -> OutboundEmail:
    """Send (or simulate) one message and record it. ``stored_body`` lets sensitive content (sign-in codes) stay out of the log."""
    record = OutboundEmail(
        to_address=to,
        subject=subject,
        body=stored_body if stored_body is not None else body,
        attachment_name=attachment[0] if attachment else None,
        provider=provider(),
        status="queued",
        related_type=related_type,
        related_id=related_id,
        created_by=actor,
        reply_to=reply_to,
        attempts=1,
    )
    db.add(record)
    db.flush()
    _deliver(record, body, attachment, reply_to)
    db.flush()
    return record


MAX_ATTEMPTS = 3


def retry_failed(db: Session, limit: int = 50, max_attempts: int = MAX_ATTEMPTS, dry_run: bool = False) -> dict[str, int]:
    """Try again with messages that failed to send. Messages with attachments or withheld bodies cannot be rebuilt: they stay failed
    and are listed in the email log for a person to resend. A message is tried at most ``max_attempts`` times."""
    from sqlalchemy import select

    counts = {"retried": 0, "sent": 0, "gave_up": 0, "needs_a_person": 0}
    rows = db.scalars(select(OutboundEmail).where(OutboundEmail.status == "failed").order_by(OutboundEmail.id).limit(limit)).all()
    for record in rows:
        if record.attachment_name or record.related_type == "sign_in" or (record.body or "").startswith("("):
            counts["needs_a_person"] += 1
            continue
        if (record.attempts or 1) >= max_attempts:
            counts["gave_up"] += 1
            continue
        counts["retried"] += 1
        if dry_run:
            continue
        record.attempts = (record.attempts or 1) + 1
        _deliver(record, record.body, None, record.reply_to)
        counts["sent"] += record.status in {"sent", "simulated"}
    db.flush()
    return counts
