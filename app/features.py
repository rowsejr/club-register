"""Parts of the office a club can switch off. Switching off only hides screens: the data stays as it is."""

from __future__ import annotations

import os

from fastapi import HTTPException

OFF = {"off", "0", "false", "no"}


def berths_on() -> bool:
    """Berths, dinghy park spaces, space allocation, the waiting list and space-based storage billing.
    ``CLUB_FEATURE_BERTHS=off`` hides them; boat park fees can still go on invoices as ordinary items."""
    return os.getenv("CLUB_FEATURE_BERTHS", "on").strip().lower() not in OFF


def require_berths() -> None:
    """A route dependency: these pages do not exist while berths are switched off."""
    if not berths_on():
        raise HTTPException(status_code=404, detail="Not found")
