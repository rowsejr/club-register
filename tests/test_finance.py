from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import finance
from app.db import SessionLocal
from app.models import Contact, Invoice, Member, NominalCode, PaymentTransaction


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _contact(db, name="Ann Sailor", email="ann@example.com") -> Contact:
    member = Member(name=name, email=email)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id)
    db.add(contact)
    db.flush()
    return contact


def _issued(db, contact, *amounts, nominal="0101") -> Invoice:
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": f"Item {i}", "amount_minor": a, "nominal_code": nominal} for i, a in enumerate(amounts)], actor="t")
    return finance.issue(db, invoice, "t")


def test_totals_come_from_lines_and_lines_lock_on_issue(db) -> None:
    contact = _contact(db)
    invoice = finance.create_invoice(db, contact_id=contact.id, actor="t")
    assert invoice.status == "draft" and invoice.reference.startswith("DRAFT-")
    finance.add_line(db, invoice, description="Membership", amount_minor=10800)
    line = finance.add_line(db, invoice, description="Berth", amount_minor=2000)
    assert invoice.amount_minor == 12800
    finance.remove_line(db, invoice, line.id)
    assert invoice.amount_minor == 10800
    finance.issue(db, invoice, "t", issue_date=date(2026, 10, 1))
    assert invoice.status == "issued" and invoice.due_date == date(2026, 10, 31)
    with pytest.raises(finance.FinanceError, match="draft"):
        finance.add_line(db, invoice, description="Late", amount_minor=100)
    with pytest.raises(finance.FinanceError, match="draft"):
        finance.remove_line(db, invoice, 1)
    with pytest.raises(finance.FinanceError, match="Only a draft"):
        finance.issue(db, invoice, "t")


def test_cannot_issue_empty_or_zero_invoices(db) -> None:
    contact = _contact(db)
    empty = finance.create_invoice(db, contact_id=contact.id, actor="t")
    with pytest.raises(finance.FinanceError, match="at least one line"):
        finance.issue(db, empty, "t")
    net_zero = finance.create_invoice(db, contact_id=contact.id, actor="t", lines=[{"description": "a", "amount_minor": 500}, {"description": "b", "amount_minor": -500}])
    with pytest.raises(finance.FinanceError, match="greater than zero"):
        finance.issue(db, net_zero, "t")
    with pytest.raises(finance.FinanceError, match="zero"):
        finance.add_line(db, empty, description="x", amount_minor=0)


def test_numbers_are_sequential_continue_after_imports_and_skip_drafts(db) -> None:
    contact = _contact(db)
    db.add(Invoice(reference="1007", amount_minor=1000, status="paid", contact_id=contact.id, kind="invoice"))
    db.flush()
    first = _issued(db, contact, 1000)
    draft = finance.create_invoice(db, contact_id=contact.id, actor="t", lines=[{"description": "x", "amount_minor": 5}])
    second = _issued(db, contact, 2000)
    assert (first.reference, second.reference) == ("1008", "1009")  # the draft consumed no number
    finance.void_invoice(db, draft, "t", "not needed")
    assert draft.status == "cancelled"


def test_payment_rules(db) -> None:
    contact = _contact(db)
    invoice = _issued(db, contact, 10000)
    with pytest.raises(finance.FinanceError, match="more than"):
        finance.record_payment(db, invoice, amount_minor=10001, method="bank")
    with pytest.raises(finance.FinanceError, match="greater than zero"):
        finance.record_payment(db, invoice, amount_minor=0, method="bank")
    with pytest.raises(finance.FinanceError, match="Unknown payment method"):
        finance.record_payment(db, invoice, amount_minor=100, method="bacs")
    finance.record_payment(db, invoice, amount_minor=4000, method="Bank", reference="REF-1")
    assert invoice.status == "part_paid" and finance.outstanding(db, invoice) == 6000
    with pytest.raises(finance.FinanceError, match="already been recorded"):
        finance.record_payment(db, invoice, amount_minor=100, method="bank", reference="REF-1")
    finance.record_payment(db, invoice, amount_minor=100, method="paypal", reference="REF-1")  # same ref, other method is fine
    finance.record_payment(db, invoice, amount_minor=5900, method="bank", reference="REF-2")
    assert invoice.status == "paid" and finance.outstanding(db, invoice) == 0
    with pytest.raises(finance.FinanceError, match="paid invoice"):
        finance.record_payment(db, invoice, amount_minor=1, method="bank")
    draft = finance.create_invoice(db, contact_id=contact.id, actor="t", lines=[{"description": "x", "amount_minor": 100}])
    with pytest.raises(finance.FinanceError, match="draft invoice"):
        finance.record_payment(db, draft, amount_minor=100, method="bank")


def test_void_write_off_and_reversal(db) -> None:
    contact = _contact(db)
    unpaid = _issued(db, contact, 5000)
    with pytest.raises(finance.FinanceError, match="reason"):
        finance.void_invoice(db, unpaid, "t", " ")
    finance.void_invoice(db, unpaid, "t", "raised in error")
    assert unpaid.status == "void" and "raised in error" in unpaid.notes
    with pytest.raises(finance.FinanceError, match="Cannot record"):
        finance.record_payment(db, unpaid, amount_minor=1, method="bank")

    part = _issued(db, contact, 5000)
    finance.record_payment(db, part, amount_minor=1000, method="cash")
    with pytest.raises(finance.FinanceError, match="credit note"):
        finance.void_invoice(db, part, "t", "x")
    finance.write_off(db, part, "t", "bad debt")
    assert part.status == "written_off" and "40.00" in part.notes and finance.outstanding(db, part) == 0
    with pytest.raises(finance.FinanceError):
        finance.write_off(db, part, "t", "again")

    bounced = _issued(db, contact, 3000)
    payment = finance.record_payment(db, bounced, amount_minor=3000, method="cheque", reference="CHQ1")
    assert bounced.status == "paid"
    finance.reverse_payment(db, payment, "t", "bounced")
    assert bounced.status == "issued" and finance.outstanding(db, bounced) == 3000
    with pytest.raises(finance.FinanceError):
        finance.reverse_payment(db, payment, "t", "again")


def test_credit_notes_reduce_what_is_owed_and_cap_at_invoice_total(db) -> None:
    contact = _contact(db)
    invoice = _issued(db, contact, 10000)
    note = finance.credit_note(db, invoice, amount_minor=2500, reason="Discount agreed", actor="t")
    assert note.reference == "CN-0001" and note.amount_minor == -2500 and note.kind == "credit_note" and note.status == "paid"
    assert finance.outstanding(db, invoice) == 7500 and invoice.status == "part_paid"
    with pytest.raises(finance.FinanceError, match="exceed"):
        finance.credit_note(db, invoice, amount_minor=8000, reason="too much", actor="t")
    finance.credit_note(db, invoice, amount_minor=7500, reason="Cancelled", actor="t")
    assert invoice.status == "paid"
    # crediting an already-paid invoice leaves a refund due (credit note stays issued)
    paid = _issued(db, contact, 4000)
    finance.record_payment(db, paid, amount_minor=4000, method="bank")
    refund = finance.credit_note(db, paid, amount_minor=1000, reason="Overcharged", actor="t")
    assert refund.status == "issued" and paid.status == "paid"
    with pytest.raises(finance.FinanceError, match="credit a credit note"):
        finance.credit_note(db, note, amount_minor=1, reason="x", actor="t")


def test_aged_debtors_buckets_and_exports(db) -> None:
    db.add_all([NominalCode(code="0101", label="Membership Fees")])
    ann, bob = _contact(db), _contact(db, "Bob Tiller", "bob@example.com")
    today = date(2026, 10, 10)
    for amount, due_in, who in ((1000, 10, ann), (2000, -5, ann), (3000, -45, bob), (4000, -200, bob)):
        inv = _issued(db, who, amount)
        inv.due_date = today + timedelta(days=due_in)
    paid = _issued(db, bob, 999)
    finance.record_payment(db, paid, amount_minor=999, method="bank")
    rows = finance.aged_debtors(db, today)
    assert [r.name for r in rows] == ["Bob Tiller", "Ann Sailor"]
    assert rows[0].buckets["d31_60"] == 3000 and rows[0].buckets["d90_plus"] == 4000
    assert rows[1].buckets["not_due"] == 1000 and rows[1].buckets["d1_30"] == 2000
    assert finance.debtor_totals(rows)["total"] == 10000
    lines = finance.accounting_lines(db)
    assert len(lines) == 5 and lines[0]["nominal_label"] == "Membership Fees" and lines[0]["net"] == lines[0]["gross"] and lines[0]["vat"] == 0
    payments = finance.payment_lines(db)
    assert len(payments) == 1 and payments[0]["amount"] == 999


def test_vat_split_when_a_rate_is_set(db) -> None:
    contact = _contact(db)
    invoice = _issued(db, contact, 12000)
    invoice.tax_rate_bp = 2000
    line = finance.accounting_lines(db)[0]
    assert (line["net"], line["vat"], line["gross"]) == (10000, 2000, 12000)


def test_settings_roundtrip(db) -> None:
    assert finance.terms_days(db) == 30
    finance.save_settings(db, {"invoice_terms_days": "14", "club_name": "MYC", "bogus": "x"})
    db.flush()
    assert finance.terms_days(db) == 14 and finance.all_settings(db)["club_name"] == "MYC" and "bogus" not in finance.all_settings(db)
    contact = _contact(db)
    assert _issued(db, contact, 100).due_date == date.today() + timedelta(days=14)


# --- JSON API --------------------------------------------------------------------------------






def test_payments_persist_provenance(db) -> None:
    contact = _contact(db)
    invoice = _issued(db, contact, 1000)
    finance.record_payment(db, invoice, amount_minor=1000, method="gocardless", reference="GC1", actor="treasurer@example.com", received_on=date(2026, 9, 1))
    payment = db.scalar(select(PaymentTransaction))
    assert payment and payment.recorded_by == "treasurer@example.com" and payment.received_at.date() == date(2026, 9, 1)
