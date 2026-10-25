"""Building an invoice: price list, quantity x unit price, dates and terms, editing a draft, copying last year's invoice."""

import json
import re
from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import catalogue, finance
from app.db import SessionLocal
from app.models import (
    Contact,
    Invoice,
    InvoiceLine,
    MembershipType,
    NominalCode,
    OutboundEmail,
    Role,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add_all([NominalCode(code="0101", label="Membership Fees"), NominalCode(code="0102", label="Boat Park Fees"), NominalCode(code="0104", label="Donations")])
        session.commit()
        yield session


@pytest.fixture
def contact(db) -> Contact:
    person = Contact(name="Ann Sailor", email="ann@example.org", address="1 Quay Lane")
    db.add(person)
    db.commit()
    return person


def test_a_line_can_be_a_quantity_times_a_unit_price(db, contact) -> None:
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Kayak rack", "quantity": 2, "unit_price_minor": 3000, "nominal_code": "0102"}])
    assert invoice.amount_minor == 6000
    line = db.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
    assert (line.quantity, line.unit_price_minor, line.amount_minor) == (2, 3000, 6000)
    with pytest.raises(finance.FinanceError, match="amount, or a quantity and a unit price"):
        finance.add_line(db, invoice, description="Nothing priced")


def test_issuing_uses_the_drafts_own_date_and_terms(db, contact) -> None:
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 1000, "nominal_code": "0101"}], issue_date=date(2026, 3, 1), terms_days=7)
    finance.issue(db, invoice, "t@example.org")
    assert invoice.issue_date == date(2026, 3, 1) and invoice.due_date == date(2026, 3, 8)
    plain = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 1000, "nominal_code": "0101"}])
    finance.issue(db, plain, "t@example.org")
    assert plain.issue_date == date.today() and plain.due_date == date.today() + timedelta(days=finance.terms_days(db))


def test_tags_are_tidied(db) -> None:
    assert finance.clean_tags(" Boat Park, 2027;boat park ,, ") == "boat park, 2027"
    assert finance.clean_tags("") is None


def test_replacing_the_lines_of_a_draft_updates_the_total_and_only_a_draft(db, contact) -> None:
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Old", "amount_minor": 500, "nominal_code": "0101"}])
    finance.replace_lines(db, invoice, [{"description": "New A", "amount_minor": 700, "nominal_code": "0101"}, {"description": "New B", "amount_minor": 300, "nominal_code": "0102"}])
    assert invoice.amount_minor == 1000 and db.scalars(select(InvoiceLine.description).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)).all() == ["New A", "New B"]
    finance.issue(db, invoice, "t@example.org")
    with pytest.raises(finance.FinanceError, match="draft"):
        finance.replace_lines(db, invoice, [])


def test_duplicating_an_invoice_copies_the_lines_but_not_the_number_or_payments(db, contact) -> None:
    original = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Boat park 2026", "quantity": 1, "unit_price_minor": 11500, "nominal_code": "0102"}], message="Thanks", tags="boat park")
    finance.issue(db, original, "t@example.org")
    finance.record_payment(db, original, amount_minor=11500, method="bank")
    copy = finance.duplicate(db, original, "t@example.org")
    assert copy.status == "draft" and copy.id != original.id and copy.reference.startswith("DRAFT-")
    assert copy.amount_minor == 11500 and copy.message == "Thanks" and copy.tags == "boat park"
    credit = finance.credit_note(db, original, amount_minor=1000, reason="x", actor="t")
    with pytest.raises(finance.FinanceError, match="credit note"):
        finance.duplicate(db, credit, "t")


def test_the_price_list_gathers_club_items_membership_fees_and_tariffs(db) -> None:
    catalogue.seed_starter_items(db)
    db.add(MembershipType(name="Single Adult Sailing", fee_minor=10800, nominal_code="0101"))
    db.commit()
    labels = {o.label: o for o in catalogue.options(db, 2027)}
    assert labels["Gate key deposit"].price_minor == 1500
    assert labels["Single Adult Sailing membership 2027"].price_minor == 10800 and labels["Single Adult Sailing membership 2027"].nominal == "0101"
    assert labels["Donation"].nominal == "0104"
    assert catalogue.seed_starter_items(db) == 0  # seeding twice adds nothing


def test_an_item_needs_a_unique_name_and_a_known_nominal(db) -> None:
    catalogue.save_item(db, None, name="Crane lift", nominal_code="0102", price_minor=2500)
    with pytest.raises(catalogue.CatalogueError, match="already an item"):
        catalogue.save_item(db, None, name="Crane lift", nominal_code="0102", price_minor=1)
    with pytest.raises(catalogue.CatalogueError, match="Unknown nominal"):
        catalogue.save_item(db, None, name="Other", nominal_code="9999", price_minor=1)
    with pytest.raises(catalogue.CatalogueError, match="negative"):
        catalogue.save_item(db, None, name="Other", nominal_code="0102", price_minor=-1)


@pytest.fixture
def treasurer(db, contact):
    return login("tre@example.org", Role.COMMITTEE, hats="treasurer")


def _post(client, path, contact_id=None, **extra):
    data = {"csrf": csrf(client, "/app/finance/new"), "action": "draft", "desc": ["Boat park fee"], "qty": ["2"], "unit": ["57.50"], "nominal": ["0102"], **extra}
    if contact_id:
        data["contact_id"] = contact_id
    return client.post(path, data=data, follow_redirects=False)


def test_the_builder_page_carries_the_price_list_and_a_running_total(treasurer, contact, db) -> None:
    catalogue.seed_starter_items(db)
    db.commit()
    page = treasurer.get(f"/app/finance/new?contact_id={contact.id}")
    assert page.status_code == 200 and "Ann Sailor" in page.text and "Gate key deposit" in page.text
    payload = re.search(r'<script type="application/json" id="catalogue">(.*?)</script>', page.text, re.S).group(1)
    assert any(item["label"] == "Gate key deposit" for item in json.loads(payload))
    assert 'id="grand"' in page.text and "/static/invoice-builder.js" in page.text
    assert treasurer.get("/static/invoice-builder.js").status_code == 200


def test_issue_and_email_in_one_step(treasurer, contact, db) -> None:
    response = _post(treasurer, "/app/finance/new", contact.id, action="issue_email", issue_date="2027-01-10", terms_days="14", message="Thanks for your support", tags="Boat Park")
    assert response.status_code == 303
    invoice = db.scalar(select(Invoice))
    db.refresh(invoice)
    assert invoice.status == "issued" and invoice.amount_minor == 11500 and invoice.due_date == date(2027, 1, 24)
    assert invoice.message == "Thanks for your support" and invoice.tags == "boat park"
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "invoice"))
    assert mail and mail.to_address == "ann@example.org" and mail.attachment_name == f"Invoice-{invoice.reference}.pdf"


def test_an_invalid_form_comes_back_with_everything_typed_in(treasurer, contact) -> None:
    response = _post(treasurer, "/app/finance/new", contact.id, unit=["abc"], desc=["Kept text"])
    assert response.status_code == 422 and "Kept text" in response.text and "Unrecognised amount" in response.text


def test_preview_saves_a_draft_and_opens_the_pdf(treasurer, contact, db) -> None:
    response = _post(treasurer, "/app/finance/new", contact.id, action="preview")
    invoice = db.scalar(select(Invoice))
    assert response.status_code == 303 and response.headers["location"].startswith(f"/app/finance/invoices/{invoice.id}.pdf")
    assert treasurer.get(response.headers["location"]).content.startswith(b"%PDF")


def test_a_draft_can_be_edited_and_then_issued_but_an_issued_invoice_cannot(treasurer, contact, db) -> None:
    _post(treasurer, "/app/finance/new", contact.id)
    invoice = db.scalar(select(Invoice))
    page = treasurer.get(f"/app/finance/invoices/{invoice.id}/edit")
    assert page.status_code == 200 and 'value="Boat park fee"' in page.text and 'value="57.50"' in page.text
    saved = _post(treasurer, f"/app/finance/invoices/{invoice.id}/edit", action="issue", desc=["Boat park fee", "Key"], qty=["1", "1"], unit=["100", "15"], nominal=["0102", "0101"])
    assert saved.status_code == 303
    db.refresh(invoice)
    assert invoice.status == "issued" and invoice.amount_minor == 11500
    locked = treasurer.get(f"/app/finance/invoices/{invoice.id}/edit", follow_redirects=False)
    assert locked.status_code == 303 and "locked" in locked.headers["location"].replace("+", " ")


def test_duplicate_makes_an_editable_draft(treasurer, contact, db) -> None:
    _post(treasurer, "/app/finance/new", contact.id, action="issue")
    original = db.scalar(select(Invoice))
    copy = treasurer.post(f"/app/finance/invoices/{original.id}/duplicate", data={"csrf": csrf(treasurer, "/app/finance/new")}, follow_redirects=False)
    assert copy.status_code == 303 and copy.headers["location"].endswith("/edit?msg=Copied+as+a+new+draft%3A+change+what+you+need%2C+then+issue+it")
    assert db.scalar(select(Invoice).where(Invoice.status == "draft")) is not None


def test_contact_search_finds_by_name_email_or_phone(treasurer, contact) -> None:
    assert "Ann Sailor" in treasurer.get("/app/finance/new/search?q=ann").text
    assert "No contacts match" in treasurer.get("/app/finance/new/search?q=zzz").text
