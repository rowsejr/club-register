"""Thank-you emails on payment, the PDF, the invoice detail page and the price list screen."""

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import catalogue, finance
from app.db import SessionLocal
from app.invoice_pdf import render_invoice_pdf
from app.models import Contact, FeeItem, NominalCode, OutboundEmail, Role


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0101", label="Membership"))
        session.commit()
        yield session


def _issued(db, *, email="ann@example.org", receipt=True, source="office"):
    contact = Contact(name="Ann Sailor", email=email)
    db.add(contact)
    db.flush()
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 5000, "nominal_code": "0101"}], send_receipt=receipt, source=source)
    finance.issue(db, invoice, "t@example.org")
    return invoice


def _receipts(db):
    return db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "invoice_receipt")).all()


def test_paying_in_full_sends_a_thank_you_once(db) -> None:
    from app import invoice_comms  # noqa: F401  (registers the hook, as the running app does)

    invoice = _issued(db)
    finance.record_payment(db, invoice, amount_minor=2000, method="bank")
    assert _receipts(db) == []  # a part payment is not a receipt
    finance.record_payment(db, invoice, amount_minor=3000, method="bank")
    mails = _receipts(db)
    assert len(mails) == 1 and mails[0].to_address == "ann@example.org" and "has been paid in full" in mails[0].body


@pytest.mark.parametrize("kwargs", [{"receipt": False}, {"email": None}, {"source": "imported"}])
def test_no_thank_you_when_switched_off_or_nobody_to_send_it_to(db, kwargs) -> None:
    invoice = _issued(db, **kwargs)
    finance.record_payment(db, invoice, amount_minor=5000, method="bank")
    assert _receipts(db) == []


def test_the_pdf_shows_quantity_message_and_a_remittance_slip(db) -> None:
    contact = Contact(name="Ann Sailor", email="ann@example.org")
    db.add(contact)
    db.flush()
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Kayak rack", "quantity": 2, "unit_price_minor": 3000, "nominal_code": "0101"}], message="Thank you for supporting the club")
    finance.issue(db, invoice, "t@example.org")
    pdf = render_invoice_pdf(db, invoice)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
    paid = _issued(db)
    finance.record_payment(db, paid, amount_minor=5000, method="bank")
    assert render_invoice_pdf(db, paid).startswith(b"%PDF")


def test_detail_page_offers_edit_for_drafts_and_duplicate_for_all(db) -> None:
    contact = Contact(name="Ann Sailor", email="ann@example.org")
    db.add(contact)
    db.flush()
    draft = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 100, "nominal_code": "0101"}], message="Hello there", tags="boat park", terms_days=0)
    db.commit()
    client = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    page = client.get(f"/app/finance/invoices/{draft.id}").text
    assert "Edit draft" in page and "Duplicate" in page and "Hello there" in page and "boat park" in page and "due on receipt" in page and ">Draft<" in page


def test_the_price_list_can_be_read_by_finance_and_changed_only_by_the_treasurer(db) -> None:
    catalogue.seed_starter_items(db)
    db.commit()
    treasurer = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    page = treasurer.get("/app/finance/items")
    assert page.status_code == 200 and "Gate key deposit" in page.text
    token = csrf(treasurer, "/app/finance/items")
    saved = treasurer.post("/app/finance/items", data={"csrf": token, "name": "Crane lift", "nominal": "0101", "price": "25", "unit": "per lift", "category": "club"}, follow_redirects=True)
    assert "Saved Crane lift" in saved.text
    assert db.scalar(select(FeeItem).where(FeeItem.name == "Crane lift")).unit_price_minor == 2500
    clash = treasurer.post("/app/finance/items", data={"csrf": token, "name": "Crane lift", "nominal": "0101", "price": "1"}, follow_redirects=True)
    assert "already an item" in clash.text
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    assert bosun.get("/app/finance/items").status_code == 200  # the harbourmaster can see prices, not change them
    assert bosun.post("/app/finance/items", data={"csrf": csrf(bosun), "name": "Sneaky", "price": "1"}).status_code == 403
