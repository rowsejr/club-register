"""A small set of books (Jan to Mar 2026) shared by the report tests: invoices, bank lines, categorised lines, a transfer and a history line."""

from datetime import date

import pytest

from app import banking, cashbook, finance
from app.cashbook import Part
from app.db import SessionLocal
from app.models import BankEntry, Contact, NominalCode

Q1 = (date(2026, 1, 1), date(2026, 3, 31))


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add_all(
            [
                NominalCode(code="0101", label="Membership Fees", kind="income"),
                NominalCode(code="0102", label="Boat Park Fees", kind="income"),
                NominalCode(code="0104", label="Donations", kind="income"),
                NominalCode(code="0105", label="Bank Fees", kind="expense"),
                NominalCode(code="0201", label="Insurance", kind="expense"),
            ]
        )
        session.commit()
        yield session


def _invoice(db, contact, lines, issued, terms=30):
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": d, "amount_minor": a, "nominal_code": n} for d, a, n in lines], issue_date=issued, terms_days=terms)
    finance.issue(db, invoice, "t")
    return invoice


@pytest.fixture
def books(db):
    """January to March 2026 for a tiny club with one bank account and PayPal."""
    ann, bob = Contact(name="Ann Sailor", email="ann@example.org"), Contact(name="Bob Rower", email="bob@example.org")
    db.add_all([ann, bob])
    db.flush()
    bank = banking.get_or_create_account(db, "Bank account", kind="bank", opening_minor=500000)
    paypal = banking.get_or_create_account(db, "PayPal", kind="paypal")
    mixed = _invoice(db, ann, [("Membership", 6000, "0101"), ("Boat park", 4000, "0102")], date(2026, 1, 10))  # £100 over two nominals
    plain = _invoice(db, bob, [("Membership", 10800, "0101")], date(2026, 2, 5))
    unpaid = _invoice(db, bob, [("Boat park", 11500, "0102")], date(2026, 3, 1))
    db.flush()

    def line(account, day, amount, details, status="unmatched", n=[0]):  # noqa: B006
        n[0] += 1
        entry = BankEntry(account_id=account.id, entry_date=day, details=details, amount_minor=amount, status=status, fingerprint=f"r{n[0]}")
        db.add(entry)
        db.flush()
        return entry

    pay_mixed = line(bank, date(2026, 1, 20), 10000, "ANN SAILOR")
    banking.match_one(db, pay_mixed, mixed, "t")
    pay_plain = line(bank, date(2026, 2, 20), 10800, "BOB ROWER")
    banking.match_one(db, pay_plain, plain, "t")
    donation = line(bank, date(2026, 2, 21), 2500, "CASH DONATION")
    cashbook.categorise(db, donation, [Part("income", 2500, "0104")], "t")
    insurance = line(bank, date(2026, 3, 2), -45000, "AVIVA")
    cashbook.categorise(db, insurance, [Part("expense", -45000, "0201")], "t")
    charges = line(bank, date(2026, 3, 3), -450, "BANK CHARGES")
    cashbook.categorise(db, charges, [Part("charges", -450, "0105")], "t")
    out = line(bank, date(2026, 3, 4), -20000, "TO PAYPAL")
    cashbook.categorise(db, out, [Part("transfer", -20000, None, "", "", paypal.id)], "t")
    came = line(paypal, date(2026, 3, 4), 20000, "FROM BANK")
    cashbook.categorise(db, came, [Part("transfer", 20000, None, "", "", bank.id)], "t")
    history = line(paypal, date(2026, 3, 10), 11500, f"Invoice {unpaid.reference} - Bob Rower", status="historical")
    waiting = line(bank, date(2026, 3, 15), 700, "MYSTERY CREDIT")
    db.commit()
    return {"mixed": mixed, "plain": plain, "unpaid": unpaid, "waiting": waiting, "history": history, "bank": bank, "paypal": paypal, "ann": ann, "bob": bob}
