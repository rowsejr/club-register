"""CSV exports must not let a cell run as a spreadsheet formula, but must keep money and counts as numbers."""

import pytest

from app.ui.routes import _csv_cell


@pytest.mark.parametrize("text", ["=HYPERLINK(\"http://x\")", "+1+1", "@SUM(A1)", "-cmd|' /C calc'!A0", "\tTabbed", "-1x"])
def test_formula_like_text_is_defused(text) -> None:
    assert _csv_cell(text) == "'" + text


@pytest.mark.parametrize("value", [-450.0, "-450.00", "168.00", 12, "0", None, "Ann Sailor"])
def test_numbers_and_ordinary_text_are_untouched(value) -> None:
    assert _csv_cell(value) == ("" if value is None else str(value))
