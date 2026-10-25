from datetime import date

import pytest
from sqlalchemy import func, select

from app import banking, finance
from app.db import SessionLocal
from app.models import BankEntry, Contact, Invoice, Member, PaymentTransaction


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _issued(db, name: str, amount: int, email: str | None = None) -> Invoice:
    member = Member(name=name, email=email or f"{name.split()[0].lower()}@example.com")
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=member.email, member_id=member.id)
    db.add(contact)
    db.flush()
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": amount}], actor="t")
    return finance.issue(db, invoice, "t")


STATEMENT = (
    "Date,Description,Reference,Paid in,Paid out,Balance\n"
    "01/10/2026,FASTER PAYMENT FROM ANN SAILOR,,108.00,,1108.00\n"
    "02/10/2026,BACS CREDIT,INV 1002,200.00,,1308.00\n"
    "03/10/2026,Stationery,,,12.50,1295.50\n"
    "04/10/2026,UNKNOWN PAYER,,50.00,,1345.50\n"
    "04/10/2026,UNKNOWN PAYER,,50.00,,1395.50\n"
)


def test_import_statement_credits_debits_and_overlap_safety(db) -> None:
    account = banking.get_or_create_account(db, "Club current account")
    first = banking.import_statement(db, account, STATEMENT)
    assert (first.created, first.credits, first.debits, first.duplicates) == (5, 4, 1, 0)
    assert db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.status == "ignored")) == 1  # money out
    again = banking.import_statement(db, account, STATEMENT)
    assert (again.created, again.duplicates) == (0, 5)  # the two identical £50 lines are both recognised, not collapsed
    longer = banking.import_statement(db, account, STATEMENT + "05/10/2026,New,,10.00,,1405.50\n")
    assert (longer.created, longer.duplicates) == (1, 5)


def test_statement_errors_are_reported_per_row_and_header_problems_are_fatal(db) -> None:
    account = banking.get_or_create_account(db, "Club current account")
    report = banking.import_statement(db, account, "Date,Amount\nnot-a-date,5.00\n02/10/2026,\n03/10/2026,7.00\n")
    assert report.created == 1 and len(report.errors) == 2
    with pytest.raises(ValueError, match="amount"):
        banking.import_statement(db, account, "Date,Foo\n01/10/2026,1\n")
    with pytest.raises(ValueError, match="date"):
        banking.import_statement(db, account, "When,Amount\n01/10/2026,1\n")


def test_auto_match_only_when_sure(db) -> None:
    ann = _issued(db, "Ann Sailor", 10800)
    by_ref = _issued(db, "Bob Tiller", 20000)
    _issued(db, "Cara Jib", 5000)
    _issued(db, "Dev Halyard", 5000)  # same amount as Cara: an unnamed £50 must not be guessed
    by_ref.reference = "1002"
    account = banking.get_or_create_account(db, "Club current account")
    banking.import_statement(db, account, STATEMENT)
    report = banking.auto_match(db, "treasurer@example.com")
    assert report.matched == 2 and report.left == 2
    assert ann.status == "paid" and by_ref.status == "paid"
    unresolved = db.scalars(select(BankEntry).where(BankEntry.status == "unmatched")).all()
    assert [e.details for e in unresolved] == ["UNKNOWN PAYER", "UNKNOWN PAYER"]
    payment = db.scalar(select(PaymentTransaction).where(PaymentTransaction.invoice_id == ann.id))
    assert payment and payment.payment_method == "bank" and payment.received_at.date() == date(2026, 10, 1) and payment.bank_entry_id
    assert banking.auto_match(db, "t").matched == 0  # idempotent


def test_short_invoice_numbers_need_a_prefix_so_stray_digits_do_not_match(db) -> None:
    invoice = _issued(db, "Ann Sailor", 10800)
    invoice.reference = "12"
    account = banking.get_or_create_account(db, "Club current account")
    banking.import_statement(db, account, "Date,Description,Amount\n01/10/2026,PAYMENT 12 THANKS,20.00\n02/10/2026,BACS INV 12,50.00\n03/10/2026,Invoice #12 payment,10.00\n")
    entries = db.scalars(select(BankEntry).order_by(BankEntry.id)).all()
    assert [("invoice number in reference" in [r for s in banking.suggestions(db, e) for r in s.reasons]) for e in entries] == [False, True, True]


def test_name_match_needs_exact_amount_and_a_unique_best(db) -> None:
    _issued(db, "Ann Sailor", 10800)
    account = banking.get_or_create_account(db, "Club current account")
    banking.import_statement(db, account, "Date,Description,Amount\n01/10/2026,PAYMENT FROM ANN SAILOR,50.00\n")
    assert banking.auto_match(db, "t").matched == 0  # right person, wrong amount: a human decides
    entry = db.scalar(select(BankEntry))
    suggestion = banking.suggestions(db, entry)[0]
    assert "name matches" in suggestion.reasons and "part payment" in suggestion.reasons
    banking.match_one(db, entry, suggestion.invoice, "t")
    assert suggestion.invoice.status == "part_paid"


def test_split_match_unmatch_ignore_and_guards(db) -> None:
    a = _issued(db, "Ann Sailor", 10000)
    b = _issued(db, "Ann Sailor", 2500, email="ann2@example.com")
    account = banking.get_or_create_account(db, "PayPal", kind="paypal")
    banking.import_statement(db, account, "Date,Details,Amount\n01/10/2026,Ann,125.00\n02/10/2026,Misc,9.99\n")
    entry = db.scalars(select(BankEntry).order_by(BankEntry.id)).first()
    with pytest.raises(finance.FinanceError, match="add up"):
        banking.match_split(db, entry, [(a.id, 10000), (b.id, 2000)], "t")
    payments = banking.match_split(db, entry, [(a.id, 10000), (b.id, 2500)], "t")
    assert len(payments) == 2 and {p.payment_method for p in payments} == {"paypal"} and a.status == "paid" and b.status == "paid"
    assert entry.status == "matched" and len({p.bank_reference for p in payments}) == 2
    with pytest.raises(finance.FinanceError, match="already matched"):
        banking.match_one(db, entry, a, "t")
    assert banking.unmatch(db, entry, "t") == 2
    assert a.status == "issued" and b.status == "issued" and entry.status == "unmatched"
    with pytest.raises(finance.FinanceError):
        banking.unmatch(db, entry, "t")
    other = db.scalars(select(BankEntry).order_by(BankEntry.id)).all()[1]
    banking.ignore(db, other)
    assert other.status == "ignored"
    banking.restore(db, other)
    assert other.status == "unmatched"
    with pytest.raises(finance.FinanceError, match="more than"):
        banking.match_one(db, other, b, "t") if other.amount_minor > finance.outstanding(db, b) else finance.record_payment(db, b, amount_minor=999999, method="bank")


def test_match_rejects_overpayment_and_money_out(db) -> None:
    invoice = _issued(db, "Ann Sailor", 1000)
    account = banking.get_or_create_account(db, "Club current account")
    banking.import_statement(db, account, "Date,Details,Amount\n01/10/2026,x,25.00\n02/10/2026,y,-5.00\n")
    credit, debit = db.scalars(select(BankEntry).order_by(BankEntry.id)).all()
    with pytest.raises(finance.FinanceError, match="more than"):
        banking.match_one(db, credit, invoice, "t")
    assert credit.status == "unmatched"
    with pytest.raises(finance.FinanceError, match="money in"):
        banking.match_one(db, debit, invoice, "t")


