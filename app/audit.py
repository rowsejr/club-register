"""What changed, for the audit log: a record's fields before and after an edit, kept as plain text so the viewer can show them."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

MEMBER_FIELDS = ("name", "email", "membership_type", "renewal_status", "payment_status", "join_date", "expiry_date", "active", "committee_post")
CONTACT_FIELDS = ("name", "email", "phone", "address", "birth_date", "notes", "suppressed", "gender", "emergency_name", "emergency_phone", "photo_consent", "directory_consent")
EVENT_FIELDS = ("title", "start_at", "end_at", "location", "event_type", "bookable", "published", "tickets_limit", "waiting_limit", "nominal_code")


def plain(value: Any) -> Any:
    if isinstance(value, datetime):  # the database hands back naive times, a fresh edit has an offset: compare them as UTC
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {f: plain(getattr(obj, f, None)) for f in fields}


def changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Only the fields whose value differs: {"email": {"before": "a@x", "after": "b@x"}}."""
    return {k: {"before": before.get(k), "after": v} for k, v in after.items() if before.get(k) != v}
