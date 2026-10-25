"""Reading spreadsheets saved as CSV: tolerant headers, UK dates and money in pounds."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
DATE_FORMATS = ("%d-%b-%y", "%d %b %Y", "%d-%b-%Y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d %B %Y", "%d-%m-%Y")


class RowError(Exception):
    """A row that cannot be read; it is skipped and reported."""


def norm(header: str) -> str:
    """Headers compare ignoring case, spaces and punctuation."""
    return re.sub(r"[^a-z0-9]", "", header.lower())


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise RowError(f"Unrecognised date '{value}'")


def parse_time(value: str) -> time | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(value, fmt).time()  # noqa: DTZ007
        except ValueError:
            continue
    raise RowError(f"Unrecognised time '{value}'")


def parse_minor(value: str) -> int | None:
    """'£1,234.50' -> 123450 pence."""
    cleaned = (value or "").replace("£", "").replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int((Decimal(cleaned) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise RowError(f"Unrecognised amount '{value}'") from exc


def name_key(name: str) -> str:
    """Names compare ignoring case and stray spaces."""
    return " ".join((name or "").split()).casefold()


def london_to_utc(day: date, at: time | None) -> datetime:
    return datetime.combine(day, at or time(0, 0), tzinfo=LONDON).astimezone(UTC)


@dataclass
class CsvTable:
    headers: list[str]
    rows: list[dict[str, str]]  # keys are normalised headers
    raw: list[list[str]] = field(default_factory=list)

    @property
    def keys(self) -> set[str]:
        return {norm(h) for h in self.headers}


def read_csv(text: str) -> CsvTable:
    text = text.lstrip("﻿")
    if not text.strip():
        raise ValueError("The file is empty")
    first_line = text.splitlines()[0]
    delimiter = max(",;\t", key=first_line.count) if any(d in first_line for d in ",;\t") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    headers = [h.strip() for h in next(reader)]
    keyed = [norm(h) for h in headers]
    rows, raw_rows = [], []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue
        rows.append({k: (v or "").strip() for k, v in zip(keyed, raw, strict=False)})
        raw_rows.append([(v or "").strip() for v in raw])
    return CsvTable(headers, rows, raw_rows)


def col(row: dict[str, str], *aliases: str) -> str:
    for alias in aliases:
        value = row.get(alias, "")
        if value:
            return value
    return ""
