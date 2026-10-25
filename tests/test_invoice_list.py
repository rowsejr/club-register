"""The Finance invoice list: filters, totals over everything matched, the line-by-line view, and bulk actions."""

import io
import re
import zipfile
from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import finance
from app.db import SessionLocal
from app.models import Contact, Invoice, NominalCode, OutboundEmail, Role

TODAY = date.today()


@pytest.fixture
def books():
    """Four invoices: one paid, one unpaid and not yet due, one 40 days overdue, one draft."""
    with SessionLocal() as db:
        db.add_all([NominalCode(code="0101", label="Membership"), NominalCode(code="0102", label="Boat park")])
        ann, bob = Contact(name="Ann Sailor", email="ann@example.org"), Contact(name="Bob Rower", email="bob@example.org")
        db.add_all([ann, bob])
        db.flush()

        def make(contact, description, nominal, pounds, issued, terms, tags=None, source_type="manual"):
            invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": description, "amount_minor": pounds * 100, "nominal_code": nominal, "source_type": source_type}], issue_date=issued, terms_days=terms, tags=tags)
            finance.issue(db, invoice, "t@example.org")
            return invoice

        paid = make(ann, "Membership 2026", "0101", 100, TODAY - timedelta(days=90), 30, "renewal", "membership")
        finance.record_payment(db, paid, amount_minor=10000, method="bank")
        make(bob, "Boat park 2027", "0102", 115, TODAY, 30, "boat park", "storage")
        make(bob, "Key deposit", "0101", 15, TODAY - timedelta(days=75), 30)
        finance.create_invoice(db, contact_id=ann.id, lines=[{"description": "Draft", "amount_minor": 500, "nominal_code": "0101"}])
        db.commit()
    return login("tre@example.org", Role.COMMITTEE, hats="treasurer")


def _numbers(client, query="") -> list[str]:
    html = client.get(f"/app/finance?{query}").text
    return re.findall(r'<a href="/app/finance/invoices/\d+">([^<]+)</a>', html)


def test_default_order_is_newest_first_with_drafts_on_top(books) -> None:
    rows = _numbers(books)
    assert len(rows) == 4 and rows[0].startswith("DRAFT")
    assert rows[1] == "1002"  # issued today
    assert rows[2:] == ["1003", "1001"]  # then 75 days ago, then 90 days ago


@pytest.mark.parametrize(
    ("query", "count"),
    [("filter=open", 2), ("filter=paid", 1), ("filter=draft", 1), ("filter=overdue", 1), ("filter=late30", 1), ("nominal=0102", 1), ("source=storage", 1), ("tag=renewal", 1), ("q=Bob", 2), ("q=1001", 1)],
)
def test_filters_narrow_the_list(books, query, count) -> None:
    assert len(_numbers(books, query)) == count


def test_date_range_uses_the_issue_date(books) -> None:
    recent = (TODAY - timedelta(days=10)).isoformat()
    assert len(_numbers(books, f"date_from={recent}")) == 1  # only today's invoice; the draft has no date
    assert len(_numbers(books, f"date_to={(TODAY - timedelta(days=80)).isoformat()}")) == 1


def test_a_mistyped_date_means_no_limit_rather_than_an_error(books) -> None:
    assert books.get("/app/finance?date_from=banana").status_code == 200


def test_totals_cover_everything_matched_not_just_the_page(books) -> None:
    html = books.get("/app/finance?filter=open").text
    assert "2 invoices match" in html and "£130.00" in html  # 115 + 15 outstanding
    html = books.get("/app/finance").text
    assert "4 invoices match" in html and "£235.00" in html  # 100 + 115 + 15 + 5 draft


def test_line_by_line_view_lists_each_line_with_its_nominal(books) -> None:
    html = books.get("/app/finance?view=lines&nominal=0101").text
    assert "Membership 2026" in html and "Key deposit" in html and "Boat park 2027" not in html


def test_status_words_are_plain_english(books) -> None:
    html = books.get("/app/finance").text
    assert ">Unpaid<" in html and ">Paid<" in html and ">Draft<" in html and ">written_off<" not in html


def test_bulk_reminders_only_chase_overdue_unpaid_invoices(books) -> None:
    with SessionLocal() as db:
        ids = {i.reference: i.id for i in db.scalars(select(Invoice))}
    response = books.post("/app/finance/bulk", data={"csrf": csrf(books, "/app/finance"), "action": "remind", "invoice": [ids["1001"], ids["1002"], ids["1003"]], "back": "/app/finance"}, follow_redirects=True)
    assert "1 reminder(s) sent" in response.text and "not overdue" in response.text
    with SessionLocal() as db:
        assert db.scalars(select(OutboundEmail.related_type)).all().count("invoice_reminder") == 1
    # the list now says when it was chased
    assert TODAY.strftime("%d %b %Y") in books.get("/app/finance?filter=overdue").text


def test_bulk_pdfs_and_csv_and_the_no_selection_message(books) -> None:
    with SessionLocal() as db:
        ids = [i.id for i in db.scalars(select(Invoice).where(Invoice.status != "draft"))]
    token = csrf(books, "/app/finance")
    archive = books.post("/app/finance/bulk", data={"csrf": token, "action": "pdf", "invoice": ids})
    assert archive.headers["content-type"] == "application/zip"
    assert len(zipfile.ZipFile(io.BytesIO(archive.content)).namelist()) == 3
    sheet = books.post("/app/finance/bulk", data={"csrf": token, "action": "csv", "invoice": ids}).text.splitlines()
    assert sheet[0].startswith("number,date,due,contact,status,total,outstanding,tags") and len(sheet) == 4
    nothing = books.post("/app/finance/bulk", data={"csrf": token, "action": "email"}, follow_redirects=True)
    assert "Tick at least one invoice" in nothing.text


def test_a_member_with_no_finance_permission_cannot_use_bulk_actions(books) -> None:
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    assert secretary.post("/app/finance/bulk", data={"csrf": csrf(secretary), "action": "email", "invoice": [1]}).status_code == 403
