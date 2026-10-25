"""The report screens: they render, export, honour dates and keep non-finance volunteers out of money detail they cannot see."""

from datetime import date

import pytest
from helpers import login
from sqlalchemy import select

from app import cashbook
from app.cashbook import Part
from app.db import SessionLocal
from app.models import BankEntry, Role


@pytest.fixture
def treasurer(books):
    return login("tre@example.org", Role.COMMITTEE, hats="treasurer")


def test_the_committee_report_page_and_navigation(treasurer) -> None:
    page = treasurer.get("/app/finance/reports?year=2026&month=3")
    assert page.status_code == 200
    for text in ("Money report: March 2026", "In the bank", "Income invoiced", "Money received", "Money spent", "Who owes the club", "PDF for the meeting", "1 bank line still waiting", "0102 Boat Park Fees", "<svg"):
        assert text in page.text, text
    assert "← February" in page.text and "April →" in page.text


def test_bad_months_fall_back_rather_than_error(treasurer) -> None:
    assert treasurer.get("/app/finance/reports?year=2026&month=13").status_code == 200
    assert treasurer.get("/app/finance/reports?year=1&month=1").status_code == 200


def test_the_committee_pdf(treasurer) -> None:
    pdf = treasurer.get("/app/finance/reports/committee.pdf?year=2026&month=3")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"


def test_income_by_nominal_on_both_bases_and_csv(treasurer) -> None:
    invoiced = treasurer.get("/app/finance/reports/income?start=2026-01-01&end=2026-03-31&basis=invoiced")
    assert "Income invoiced" in invoiced.text and "0101 Membership Fees" in invoiced.text and "£168.00" in invoiced.text and "£155.00" in invoiced.text
    received = treasurer.get("/app/finance/reports/income?start=2026-01-01&end=2026-03-31&basis=received")
    assert "Money received" in received.text and "0104 Donations" in received.text and "Money spent" in received.text and "0201 Insurance" in received.text
    sheet = treasurer.get("/app/finance/reports/income.csv?start=2026-01-01&end=2026-03-31&basis=received").text.splitlines()
    assert sheet[0].startswith("section,nominal,label") and any(row.startswith("payments,0201,Insurance,450.00") for row in sheet)
    swapped = treasurer.get("/app/finance/reports/income?start=2026-03-31&end=2026-01-01")
    assert swapped.status_code == 200 and "01 Jan 2026 to 31 Mar 2026" in swapped.text  # dates the wrong way round are put right


def test_receipts_and_payments_ties_out_and_exports(treasurer) -> None:
    page = treasurer.get("/app/finance/reports/receipts-payments?start=2026-01-01&end=2026-03-31")
    assert "Receipts and payments account" in page.text and "Bank reconciliation" in page.text and "it adds up" in page.text and "not yet matched or categorised" in page.text
    sheet = treasurer.get("/app/finance/reports/receipts-payments.csv?start=2026-01-01&end=2026-03-31").text
    assert "opening balance,,,5000.00" in sheet and "payment,0201,Insurance,-450.00" in sheet and sheet.strip().splitlines()[-1].startswith("closing balance")


def test_debtors_at_a_date_and_membership_numbers(treasurer) -> None:
    page = treasurer.get("/app/finance/reports/debtors?as_at=2026-03-31")
    assert "Who owed the club money on 31 Mar 2026" in page.text and "Bob Rower" in page.text and "£115.00" in page.text
    assert "Nobody owed anything" in treasurer.get("/app/finance/reports/debtors?as_at=2026-02-28").text
    assert treasurer.get("/app/finance/reports/debtors.csv?as_at=2026-03-31").text.splitlines()[0] == "contact,invoice,issued,due,days_overdue,outstanding"
    assert treasurer.get("/app/finance/reports/membership").status_code == 200 and treasurer.get("/app/finance/reports/membership.csv").status_code == 200
    assert treasurer.get("/app/finance/reports/nonsense").status_code == 404


def test_the_report_changes_when_the_books_change(treasurer) -> None:
    with SessionLocal() as db:
        mystery = db.scalar(select(BankEntry).where(BankEntry.details == "MYSTERY CREDIT"))
        cashbook.categorise(db, mystery, [Part("income", 700, "0104")], "t")
        db.commit()
    page = treasurer.get("/app/finance/reports?year=2026&month=3").text
    assert "still waiting" not in page
    assert "0104 Donations" in page and date(2026, 3, 1)


@pytest.mark.parametrize("role", ["bosun", "secretary", "training"])
def test_every_volunteer_hat_can_read_the_reports_but_a_plain_member_cannot(books, role) -> None:
    assert login(f"{role}@example.org", Role.COMMITTEE, hats=role).get("/app/finance/reports").status_code == 200
    member = login("member@example.org", Role.MEMBER)
    assert member.get("/app/finance/reports").status_code in (403, 404)
