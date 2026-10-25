"""Club time: forms use UK local time, the database stores UTC."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")


def parse_local(value: str) -> datetime | None:
    """A browser date-time box (``2026-12-05T19:00``) or a plain date, read as UK time and returned as UTC. Blank gives None."""
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=LONDON).astimezone(UTC)
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a date and time")


def to_input(value: datetime | None) -> str:
    """A stored UTC time shown in a browser date-time box, in UK time."""
    if value is None:
        return ""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(LONDON).strftime("%Y-%m-%dT%H:%M")
