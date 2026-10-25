"""The treasurer's reports must tie to the invoices and to the bank lines."""

from datetime import UTC, date, datetime

from books_fixtures import Q1, _invoice  # noqa: F401
from sqlalchemy import select

from app import finance, reports
from app.models import AuditLog, Member, Membership


def test_invoiced_by_nominal_uses_the_issue_date_and_every_line(db, books) -> None:
    assert reports.invoiced_by_nominal(db, *Q1) == {"0101": 16800, "0102": 15500}
    assert reports.invoiced_by_nominal(db, date(2026, 2, 1), date(2026, 2, 28)) == {"0101": 10800}
    assert reports.invoiced_by_nominal(db, date(2025, 1, 1), date(2025, 12, 31)) == {}


def test_a_credit_note_reduces_the_nominals_of_the_invoice_it_credits(db, books) -> None:
    finance.credit_note(db, books["mixed"], amount_minor=1000, reason="Berth unused", actor="t")  # credits £10 of a 60/40 invoice
    db.commit()
    result = reports.invoiced_by_nominal(db, date(2026, 1, 1), date.today())  # the credit note is dated today
    assert result["0101"] + result["0102"] == 32300 - 1000 and result["0102"] == 15500 - 400  # 40% of the credit comes off boat park


def test_drafts_and_voids_are_not_income(db, books) -> None:
    finance.create_invoice(db, contact_id=books["ann"].id, lines=[{"description": "Draft", "amount_minor": 99900, "nominal_code": "0101"}], issue_date=date(2026, 3, 5))
    voided = _invoice(db, books["ann"], [("Void me", 5000, "0101")], date(2026, 3, 6))
    finance.void_invoice(db, voided, "t", "mistake")
    db.commit()
    assert sum(reports.invoiced_by_nominal(db, *Q1).values()) == 32300


def test_received_money_is_built_from_the_bank_lines_and_splits_over_nominals(db, books) -> None:
    cash = reports.cash_summary(db, *Q1)
    assert cash.receipts == {"0101": 6000 + 10800, "0102": 4000 + 11500, "0104": 2500}  # the £115 PayPal history line names the boat park invoice
    assert cash.payments == {"0201": 45000, "0105": 450}
    assert (cash.transfers_in, cash.transfers_out) == (20000, 20000)
    assert (cash.waiting_in, cash.waiting_out) == (700, 0)


def test_historical_lines_naming_an_invoice_take_that_invoices_nominal(db, books) -> None:
    lines = [ln for ln in reports.cash_lines(db, *Q1) if ln.entry.id == books["history"].id]
    assert [(ln.nominal, ln.amount_minor, ln.kind) for ln in lines] == [("0102", 11500, "invoice")]


def test_the_receipts_and_payments_account_ties_to_the_bank(db, books) -> None:
    result = reports.receipts_and_payments(db, *Q1)
    assert result.opening == 500000
    # everything that moved: receipts - payments + transfers + the line still waiting
    assert result.cash.movement == (10000 + 10800 + 2500 + 11500 + 700) - (45000 + 450) + 0
    assert result.closing_expected == result.closing_actual and result.difference == 0
    assert result.cash.waiting_in == 700  # shown, so the treasurer knows the account is not complete


def test_bank_positions_report_each_account_at_both_ends(db, books) -> None:
    positions = {p.account.name: p for p in reports.bank_positions(db, date(2026, 3, 1), date(2026, 3, 31))}
    assert positions["Bank account"].opening == 500000 + 10000 + 10800 + 2500
    assert positions["Bank account"].closing == positions["Bank account"].opening - 45000 - 450 - 20000 + 700
    assert positions["Bank account"].waiting == 1 and positions["PayPal"].closing == 20000 + 11500


def test_debtors_at_a_date_ignore_later_payments_and_later_write_offs(db, books) -> None:
    as_at_feb = reports.debtors_as_at(db, date(2026, 2, 28))
    assert {(r.contact_name, r.outstanding) for r in as_at_feb} == set()  # £100 paid 20 Jan, £108 paid 20 Feb, the £115 invoice not yet issued
    mid_feb = reports.debtors_as_at(db, date(2026, 2, 10))
    assert [(r.contact_name, r.outstanding) for r in mid_feb] == [("Bob Rower", 10800)]  # Ann had paid on 20 Jan; Bob pays on 20 Feb
    end_march = reports.debtors_as_at(db, date(2026, 3, 31))
    assert [(r.contact_name, r.outstanding) for r in end_march] == [("Bob Rower", 11500)]
    unpaid = books["unpaid"]
    finance.write_off(db, unpaid, "t", "gone away")
    db.flush()
    row = db.scalar(select(AuditLog).where(AuditLog.action == "invoice.write_off"))
    row.created_at = datetime(2026, 5, 1, tzinfo=UTC)  # written off in May
    db.commit()
    assert [r.outstanding for r in reports.debtors_as_at(db, date(2026, 3, 31))] == [11500]  # it was still owed in March
    assert reports.debtors_as_at(db, date(2026, 6, 30)) == []


def test_debtor_buckets_age_from_the_due_date(db, books) -> None:
    rows = reports.debtors_as_at(db, date(2026, 5, 31))  # due 31 March: 61 days late
    assert reports.debtor_buckets(rows)["d61_90"] == 11500


def test_financial_year_bounds_and_last_years_period(db) -> None:
    assert reports.fy_bounds(db, date(2026, 6, 1)) == (date(2026, 1, 1), date(2026, 12, 31))
    finance.save_settings(db, {"fy_start_month": "4"})
    db.flush()
    assert reports.fy_bounds(db, date(2026, 6, 1)) == (date(2026, 4, 1), date(2027, 3, 31))
    assert reports.fy_bounds(db, date(2026, 2, 1)) == (date(2025, 4, 1), date(2026, 3, 31))
    assert reports.same_period_last_year(date(2024, 2, 29), date(2024, 2, 29)) == (date(2023, 2, 28), date(2023, 2, 28))


def test_splitting_money_over_nominals_always_adds_up() -> None:
    assert sum(share for _, share in reports._split(10000, [("a", 1), ("b", 1), ("c", 1)])) == 10000
    assert reports._split(500, []) == [("", 500)]


def test_membership_statistics_count_memberships_by_type_and_year(db) -> None:
    for i, (plan, year, status) in enumerate([("Single Adult Sailing", 2025, "active"), ("Single Adult Sailing", 2026, "active"), ("Sailing Family 2A+2C", 2026, "active"), ("Single Adult Sailing", 2026, "pending_payment")]):
        member = Member(name=f"M{i}", join_date=date(year, 3, 1))
        db.add(member)
        db.flush()
        db.add(Membership(member_id=member.id, plan_name=plan, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status=status))
    db.commit()
    stats = reports.membership_stats(db)
    assert stats["years"] == [2026, 2025] and stats["totals"] == {2026: 2, 2025: 1}
    assert {r["type"]: r["counts"] for r in stats["rows"]}["Single Adult Sailing"] == {2026: 1, 2025: 1}
    assert stats["joined"] == {2025: 1, 2026: 3}


def test_the_committee_report_pulls_it_all_together(db, books) -> None:
    report = reports.committee_report(db, 2026, 3)
    assert report["month_name"] == "March 2026" and len(report["trend"]) == 12 and report["trend"][-1]["label"] == "Mar 26"
    invoiced = {row.code: row for row in report["invoiced"]}
    assert invoiced["0102"].month == 11500 and invoiced["0102"].ytd == 15500 and invoiced["0101"].month == 0 and invoiced["0101"].ytd == 16800
    assert {row.code: row.ytd for row in report["spent"]} == {"0201": 45000, "0105": 450}
    assert report["debtors"]["total"] == 11500 and report["debtors"]["people"] == 1 and report["waiting"] == 1
    assert {p.account.name for p in report["positions"]} == {"Bank account", "PayPal"}
    last_year = reports.committee_report(db, 2025, 3)
    assert last_year["invoiced"] == [] and last_year["cash_ytd"].lines == 0

