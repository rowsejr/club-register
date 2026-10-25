"""The treasurer's screens for non-invoice bank lines, rules and nominal codes."""

from datetime import date

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import banking
from app.db import SessionLocal
from app.models import BankAllocation, BankEntry, BankRule, NominalCode, Role


@pytest.fixture
def lines():
    with SessionLocal() as db:
        db.add_all([NominalCode(code="0104", label="Donations", kind="income"), NominalCode(code="0105", label="Bank Fees", kind="expense"), NominalCode(code="0201", label="Insurance", kind="expense")])
        bank = banking.get_or_create_account(db, "Bank account", kind="bank")
        paypal = banking.get_or_create_account(db, "PayPal", kind="paypal")
        db.flush()
        rows = [
            BankEntry(account_id=bank.id, entry_date=date(2026, 5, 1), details="LLOYDS BANK CHARGES MAY", amount_minor=-450, status="unmatched", fingerprint="a"),
            BankEntry(account_id=bank.id, entry_date=date(2026, 6, 1), details="LLOYDS BANK CHARGES JUN", amount_minor=-450, status="unmatched", fingerprint="b"),
            BankEntry(account_id=bank.id, entry_date=date(2026, 6, 2), details="CASH DEPOSIT RAFFLE AND DONATION", amount_minor=12500, status="unmatched", fingerprint="c"),
            BankEntry(account_id=bank.id, entry_date=date(2026, 6, 3), details="PAYPAL TRANSFER", amount_minor=25000, status="unmatched", fingerprint="d"),
        ]
        db.add_all(rows)
        db.commit()
        ids = [r.id for r in rows]
        ids.append(paypal.id)
    return ids


@pytest.fixture
def treasurer():
    return login("tre@example.org", Role.COMMITTEE, hats="treasurer")


def test_categorise_one_line_as_an_expense_and_save_a_rule_that_clears_its_twin(treasurer, lines) -> None:
    may, june, *_ = lines
    form = treasurer.get(f"/app/finance/bank/entries/{may}/categorise")
    assert form.status_code == 200 and "LLOYDS BANK CHARGES MAY" in form.text and "Money out" in form.text
    token = csrf(treasurer, f"/app/finance/bank/entries/{may}/categorise")
    response = treasurer.post(
        f"/app/finance/bank/entries/{may}/categorise",
        data={"csrf": token, "kind1": "charges", "nominal1": "0105", "make_rule": "1", "pattern": "LLOYDS BANK CHARGES", "rule_this_account": "1"},
        follow_redirects=True,
    )
    assert "1 other waiting line(s) categorised too" in response.text
    with SessionLocal() as db:
        assert db.get(BankEntry, may).status == "categorised" and db.get(BankEntry, june).status == "categorised"
        assert db.scalar(select(BankRule.times_used)) == 1  # only the other line was done by the rule; the first was done by hand
        assert db.scalar(select(BankAllocation.amount_minor).where(BankAllocation.entry_id == june)) == -450


def test_a_split_deposit_and_typing_spending_as_a_positive_number(treasurer, lines) -> None:
    _, _, deposit, _, _ = lines
    token = csrf(treasurer, "/app/finance/bank")
    bad = treasurer.post(f"/app/finance/bank/entries/{deposit}/categorise", data={"csrf": token, "kind1": "income", "nominal1": "0104", "amount1": "100"}, follow_redirects=True)
    assert "add up to 100.00 but the line is 125.00" in bad.text
    ok = treasurer.post(f"/app/finance/bank/entries/{deposit}/categorise", data={"csrf": token, "kind1": "income", "nominal1": "0104", "amount1": "100", "kind2": "income", "nominal2": "0104", "amount2": "25", "description2": "Raffle"}, follow_redirects=True)
    assert "Categorised" in ok.text and "Raffle" in ok.text
    with SessionLocal() as db:
        assert sorted(db.scalars(select(BankAllocation.amount_minor).where(BankAllocation.entry_id == deposit))) == [2500, 10000]


def test_a_transfer_between_accounts_and_putting_a_line_back(treasurer, lines) -> None:
    _, _, _, transfer, paypal = lines
    token = csrf(treasurer, "/app/finance/bank")
    treasurer.post(f"/app/finance/bank/entries/{transfer}/categorise", data={"csrf": token, "kind1": "transfer", "transfer1": str(paypal)}, follow_redirects=True)
    with SessionLocal() as db:
        row = db.scalar(select(BankAllocation).where(BankAllocation.entry_id == transfer))
        assert row.kind == "transfer" and row.transfer_account_id == paypal
    page = treasurer.get(f"/app/finance/bank/entries/{transfer}")
    assert "Categorised as" in page.text and "Put back in the queue" in page.text
    treasurer.post(f"/app/finance/bank/entries/{transfer}/uncategorise", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.get(BankEntry, transfer).status == "unmatched"


def test_the_bank_page_shows_balances_and_the_rules_page_manages_rules(treasurer, lines) -> None:
    page = treasurer.get("/app/finance/bank")
    assert "to deal with" in page.text and "categorised" in page.text and "/app/finance/bank/rules" in page.text
    token = csrf(treasurer, "/app/finance/bank/rules")
    saved = treasurer.post("/app/finance/bank/rules", data={"csrf": token, "pattern": "LLOYDS BANK CHARGES", "kind": "charges", "nominal": "0105", "direction": "out"}, follow_redirects=True)
    assert "2 waiting line(s) categorised" in saved.text and "LLOYDS BANK CHARGES" in saved.text
    with SessionLocal() as db:
        rule_id = db.scalar(select(BankRule.id))
    treasurer.post(f"/app/finance/bank/rules/{rule_id}/toggle", data={"csrf": token}, follow_redirects=True)
    treasurer.post(f"/app/finance/bank/rules/{rule_id}/delete", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.scalar(select(BankRule.id)) is None
    short = treasurer.post("/app/finance/bank/rules", data={"csrf": token, "pattern": "ab", "kind": "charges", "nominal": "0105"}, follow_redirects=True)
    assert "three characters" in short.text


def test_nominal_codes_can_be_added_and_hidden_by_the_treasurer_only(treasurer, lines) -> None:
    token = csrf(treasurer, "/app/finance/nominals")
    assert "Saved 0300 Fund raising" in treasurer.post("/app/finance/nominals", data={"csrf": token, "code": "0300", "label": "Fund raising", "kind": "income"}, follow_redirects=True).text
    assert "up to six digits" in treasurer.post("/app/finance/nominals", data={"csrf": token, "code": "abc", "label": "x", "kind": "income"}, follow_redirects=True).text
    treasurer.post("/app/finance/nominals", data={"csrf": token, "code": "0300", "label": "Fund raising", "kind": "income", "active": "0"}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.scalar(select(NominalCode.active).where(NominalCode.code == "0300")) is False
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    assert bosun.post("/app/finance/nominals", data={"csrf": csrf(bosun), "code": "0999", "label": "Sneaky", "kind": "income"}).status_code == 403
    assert bosun.post("/app/finance/bank/rules", data={"csrf": csrf(bosun), "pattern": "anything", "kind": "charges", "nominal": "0105"}).status_code == 403
