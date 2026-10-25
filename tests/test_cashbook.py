"""The cash book: bank lines that are not invoice payments become income, expenses, transfers or bank charges."""

from datetime import date

import pytest
from sqlalchemy import select

from app import banking, cashbook
from app.cashbook import CashbookError, Part
from app.db import SessionLocal
from app.models import BankAccount, BankAllocation, BankEntry, NominalCode


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add_all(
            [
                NominalCode(code="0104", label="Donations", kind="income"),
                NominalCode(code="0105", label="Bank Fees", kind="expense"),
                NominalCode(code="0201", label="Insurance", kind="expense"),
            ]
        )
        session.commit()
        yield session


@pytest.fixture
def accounts(db):
    bank = banking.get_or_create_account(db, "Bank account", kind="bank", opening_minor=100000)
    paypal = banking.get_or_create_account(db, "PayPal", kind="paypal")
    db.commit()
    return bank, paypal


def _line(db, account, amount, details="", day=date(2026, 5, 1), balance=None, n=[0]):  # noqa: B006
    n[0] += 1
    entry = BankEntry(account_id=account.id, entry_date=day, details=details, amount_minor=amount, balance_minor=balance, status="unmatched", fingerprint=f"f{n[0]}")
    db.add(entry)
    db.flush()
    return entry


def test_money_out_is_an_expense_and_the_line_is_categorised(db, accounts) -> None:
    bank, _ = accounts
    entry = _line(db, bank, -45000, "AVIVA INSURANCE")
    cashbook.categorise(db, entry, [Part("expense", -45000, "0201", "Annual premium", "Aviva")], "t@example.org")
    assert entry.status == "categorised"
    row = db.scalar(select(BankAllocation).where(BankAllocation.entry_id == entry.id))
    assert (row.kind, row.nominal_code, row.amount_minor, row.payee) == ("expense", "0201", -45000, "Aviva")


def test_a_line_can_be_split_but_the_parts_must_add_up(db, accounts) -> None:
    bank, _ = accounts
    entry = _line(db, bank, 12500, "SUNDRY CREDITS")
    with pytest.raises(CashbookError, match="add up to 100.00 but the line is 125.00"):
        cashbook.categorise(db, entry, [Part("income", 10000, "0104")], "t")
    cashbook.categorise(db, entry, [Part("income", 10000, "0104"), Part("income", 2500, "0104", "Raffle")], "t")
    assert len(cashbook.allocations_for(db, entry.id)) == 2


@pytest.mark.parametrize(
    ("amount", "part", "message"),
    [
        (-100, Part("income", -100, "0104"), "cannot be income"),
        (100, Part("expense", 100, "0201"), "not an expense"),
        (100, Part("income", -100, "0104"), "same direction"),
        (100, Part("income", 100, None), "nominal code"),
        (100, Part("income", 100, "9999"), "Unknown nominal"),
        (100, Part("transfer", 100, None, "", "", None), "other account"),
        (100, Part("banana", 100, "0104"), "Unknown kind"),
    ],
)
def test_nonsense_categories_are_refused(db, accounts, amount, part, message) -> None:
    bank, _ = accounts
    entry = _line(db, bank, amount)
    with pytest.raises(CashbookError, match=message):
        cashbook.categorise(db, entry, [part], "t")
    assert entry.status == "unmatched" and not cashbook.allocations_for(db, entry.id)


def test_a_transfer_names_the_other_account_and_has_no_nominal(db, accounts) -> None:
    bank, paypal = accounts
    entry = _line(db, bank, 25000, "PAYPAL TRANSFER")
    with pytest.raises(CashbookError, match="other account"):
        cashbook.categorise(db, entry, [Part("transfer", 25000, None, "", "", bank.id)], "t")  # the same account is not a transfer
    cashbook.categorise(db, entry, [Part("transfer", 25000, None, "", "", paypal.id)], "t")
    row = cashbook.allocations_for(db, entry.id)[0]
    assert row.transfer_account_id == paypal.id and row.nominal_code is None


def test_uncategorise_puts_the_line_back_and_matched_lines_are_protected(db, accounts) -> None:
    bank, _ = accounts
    entry = _line(db, bank, -4500, "LLOYDS BANK CHARGES")
    cashbook.categorise(db, entry, [Part("charges", -4500, "0105")], "t")
    cashbook.uncategorise(db, entry, "t")
    assert entry.status == "unmatched" and not cashbook.allocations_for(db, entry.id)
    with pytest.raises(CashbookError, match="not categorised"):
        cashbook.uncategorise(db, entry, "t")
    entry.status = "matched"
    with pytest.raises(CashbookError, match="un-match it first"):
        cashbook.categorise(db, entry, [Part("charges", -4500, "0105")], "t")


def test_rules_categorise_repeat_lines_and_respect_direction_and_account(db, accounts) -> None:
    bank, paypal = accounts
    cashbook.save_rule(db, pattern="lloyds bank charges", kind="charges", nominal_code="0105", account_id=bank.id, direction="out")
    wanted = _line(db, bank, -450, "LLOYDS BANK CHARGES OCT")
    other_account = _line(db, paypal, -450, "LLOYDS BANK CHARGES OCT")
    refund = _line(db, bank, 450, "LLOYDS BANK CHARGES REFUND")
    plain = _line(db, bank, -999, "SOMETHING ELSE")
    assert cashbook.apply_rules(db, "system") == 1
    assert (wanted.status, other_account.status, refund.status, plain.status) == ("categorised", "unmatched", "unmatched", "unmatched")
    assert cashbook.apply_rules(db, "system") == 0  # nothing left to do
    from app.models import BankRule

    assert db.scalar(select(BankRule.times_used)) == 1


def test_a_rule_needs_enough_text_and_a_destination(db, accounts) -> None:
    with pytest.raises(CashbookError, match="three characters"):
        cashbook.save_rule(db, pattern="ab", kind="expense", nominal_code="0201")
    with pytest.raises(CashbookError, match="nominal code"):
        cashbook.save_rule(db, pattern="aviva", kind="expense")
    with pytest.raises(CashbookError, match="other account"):
        cashbook.save_rule(db, pattern="paypal transfer", kind="transfer")
    first = cashbook.save_rule(db, pattern="Aviva", kind="expense", nominal_code="0201")
    again = cashbook.save_rule(db, pattern="aviva", kind="expense", nominal_code="0201", payee="Aviva plc")
    assert first.id == again.id and again.payee == "Aviva plc"  # saving the same rule twice updates it


def test_the_suggested_rule_text_is_the_payee_not_the_numbers(db, accounts) -> None:
    bank, _ = accounts
    entry = _line(db, bank, -450, "DD 123456 AVIVA INSURANCE LTD 998877")
    assert cashbook.suggest_pattern(entry) == "DD AVIVA INSURANCE"


def test_seeded_expense_codes_appear_once_and_bank_fees_becomes_an_expense(db) -> None:
    db.query(NominalCode).delete()
    db.add(NominalCode(code="0105", label="Bank Fees", kind="income"))
    db.commit()
    assert cashbook.seed_codes(db) == 8
    assert cashbook.seed_codes(db) == 0
    assert db.scalar(select(NominalCode.kind).where(NominalCode.label == "Bank Fees")) == "expense"
    assert {n.code for n in cashbook.nominal_choices(db, "expense")} >= {"0105", "0201", "0208"}


def test_balance_shows_the_statement_figure_and_the_built_up_one(db, accounts) -> None:
    bank, paypal = accounts  # the bank account opened with £1,000
    _line(db, bank, 20000, "IN", date(2026, 1, 2), balance=120000)
    _line(db, bank, -5000, "OUT", date(2026, 1, 3), balance=115000)
    assert banking.balance(db, bank) == {"computed": 115000, "statement": 115000, "as_of": date(2026, 1, 3), "agrees": True, "lines": 2, "opening": 100000}
    _line(db, bank, 1000, "IN WITH A GAP", date(2026, 1, 4), balance=118000)  # the statement says 1,180: a £20 line is missing
    result = banking.balance(db, bank)
    assert result["computed"] == 116000 and result["statement"] == 118000 and not result["agrees"]
    assert banking.balance(db, bank, date(2026, 1, 2))["computed"] == 120000
    assert banking.balance(db, paypal)["statement"] is None and banking.balance(db, paypal)["agrees"]
    assert isinstance(db.get(BankAccount, bank.id), BankAccount)
