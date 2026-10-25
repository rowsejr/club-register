"""The treasurer's reports: income by nominal, receipts and payments, debtors at a date, bank balances, membership numbers.

Two bases are offered for income:

* **Invoiced**: what was billed in the period (invoice lines by issue date; credit notes reduce the nominal they credit).
* **Received**: money that reached the bank in the period, built from the bank lines themselves so it always ties to the
  statements: invoice payments (split over the invoice's nominals), income without an invoice, and expenses.

Every figure is integer pence. Nominal code ``""`` means "not allocated to a nominal yet" and is always shown, never hidden.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import banking, finance
from app.models import (
    AuditLog,
    BankAccount,
    BankAllocation,
    BankEntry,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    Membership,
    NominalCode,
    PaymentTransaction,
)

NOT_ALLOCATED = ""
NOT_ALLOCATED_LABEL = "Not yet allocated"
EXCLUDED_STATUSES = ("draft", "void", "cancelled")


# --------------------------------------------------------------------------- dates


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def fy_start_month(db: Session) -> int:
    try:
        value = int(finance.get_setting(db, "fy_start_month") or 1)
    except ValueError:
        return 1
    return value if 1 <= value <= 12 else 1


def fy_bounds(db: Session, on: date | None = None) -> tuple[date, date]:
    """The financial year containing a date. A year starting in April and containing 1 June 2026 runs 1 Apr 2026 to 31 Mar 2027."""
    on = on or date.today()
    start_month = fy_start_month(db)
    start_year = on.year if on.month >= start_month else on.year - 1
    start = date(start_year, start_month, 1)
    end_year, end_month = (start_year + 1, start_month - 1) if start_month > 1 else (start_year, 12)
    if start_month == 1:
        return start, date(start_year, 12, 31)
    return start, month_bounds(end_year, end_month)[1]


def same_period_last_year(start: date, end: date) -> tuple[date, date]:
    def back(day: date) -> date:
        try:
            return day.replace(year=day.year - 1)
        except ValueError:  # 29 February
            return day.replace(year=day.year - 1, day=28)

    return back(start), back(end)


def nominal_labels(db: Session) -> dict[str, str]:
    labels = {code: label for code, label in db.execute(select(NominalCode.code, NominalCode.label))}
    labels[NOT_ALLOCATED] = NOT_ALLOCATED_LABEL
    return labels


# --------------------------------------------------------------------------- splitting money over nominals


def _split(amount: int, weights: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Share an amount across nominals in proportion to weights; the last share takes the rounding so the parts always add up."""
    total = sum(w for _, w in weights)
    if total == 0 or not weights:
        return [(NOT_ALLOCATED, amount)]
    parts, given = [], 0
    for index, (code, weight) in enumerate(weights):
        share = amount - given if index == len(weights) - 1 else round(amount * weight / total)
        parts.append((code, share))
        given += share
    return parts


def _invoice_weights(db: Session, invoice: Invoice, lines: dict[int, list[InvoiceLine]] | None = None) -> list[tuple[str, int]]:
    """How an invoice divides over nominals. A credit note divides like the invoice it credits."""
    source = invoice
    if (invoice.kind or "invoice") == "credit_note" and invoice.credit_for_id:
        source = db.get(Invoice, invoice.credit_for_id) or invoice
    rows = lines[source.id] if lines is not None and source.id in lines else db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == source.id)).all()
    merged: dict[str, int] = defaultdict(int)
    for line in rows:
        merged[line.nominal_code or NOT_ALLOCATED] += line.amount_minor
    return [(code, amount) for code, amount in merged.items() if amount]


# --------------------------------------------------------------------------- income invoiced


def invoiced_by_nominal(db: Session, start: date, end: date) -> dict[str, int]:
    """Billed in the period by nominal: lines of issued, part-paid, paid and written-off invoices; credit notes reduce what they credit."""
    totals: dict[str, int] = defaultdict(int)
    invoices = db.scalars(select(Invoice).where(Invoice.issue_date >= start, Invoice.issue_date <= end, Invoice.status.not_in(EXCLUDED_STATUSES))).all()
    ids = {i.id for i in invoices} | {i.credit_for_id for i in invoices if i.credit_for_id}
    by_invoice: dict[int, list[InvoiceLine]] = defaultdict(list)
    if ids:
        for line in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id.in_(ids))):
            by_invoice[line.invoice_id].append(line)
    for invoice in invoices:
        if (invoice.kind or "invoice") == "credit_note":
            for code, amount in _split(invoice.amount_minor, _invoice_weights(db, invoice, by_invoice)):
                totals[code] += amount
        else:
            for line in by_invoice.get(invoice.id, []):
                totals[line.nominal_code or NOT_ALLOCATED] += line.amount_minor
    return {code: amount for code, amount in totals.items() if amount}


def written_off_in(db: Session, start: date, end: date) -> int:
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "invoice.write_off", AuditLog.created_at >= _day_start(start), AuditLog.created_at < _day_start(end + timedelta(days=1))))
    return sum(int((row.details or {}).get("amount", 0)) for row in rows)


def _day_start(day: date):
    from datetime import UTC, datetime

    return datetime(day.year, day.month, day.day, tzinfo=UTC)


# --------------------------------------------------------------------------- the cash book analysis


@dataclass
class CashLine:
    entry: BankEntry
    nominal: str
    amount_minor: int
    kind: str  # invoice | income | expense | charges | transfer | unallocated | waiting


@dataclass
class CashSummary:
    start: date
    end: date
    receipts: dict[str, int] = field(default_factory=dict)  # money in, by nominal
    payments: dict[str, int] = field(default_factory=dict)  # money out, by nominal, as positive numbers
    transfers_in: int = 0
    transfers_out: int = 0
    waiting_in: int = 0  # unmatched or ignored lines: not yet categorised
    waiting_out: int = 0
    lines: int = 0

    @property
    def total_receipts(self) -> int:
        return sum(self.receipts.values())

    @property
    def total_payments(self) -> int:
        return sum(self.payments.values())

    @property
    def movement(self) -> int:
        """Net change in the bank accounts over the period: receipts - payments + transfers + what is still waiting."""
        return self.total_receipts - self.total_payments + self.transfers_in - self.transfers_out + self.waiting_in - self.waiting_out


def cash_lines(db: Session, start: date, end: date) -> list[CashLine]:
    entries = db.scalars(select(BankEntry).where(BankEntry.entry_date >= start, BankEntry.entry_date <= end).order_by(BankEntry.entry_date, BankEntry.id)).all()
    if not entries:
        return []
    ids = [e.id for e in entries]
    payments_by_entry: dict[int, list[PaymentTransaction]] = defaultdict(list)
    for payment in db.scalars(select(PaymentTransaction).where(PaymentTransaction.bank_entry_id.in_(ids), PaymentTransaction.status == "received")):
        payments_by_entry[payment.bank_entry_id].append(payment)  # type: ignore[index]
    allocations: dict[int, list[BankAllocation]] = defaultdict(list)
    for row in db.scalars(select(BankAllocation).where(BankAllocation.entry_id.in_(ids))):
        allocations[row.entry_id].append(row)
    refs = {ref for e in entries if e.status == "historical" for ref in banking.INVOICE_REF.findall(e.details or "")}
    by_reference = {inv.reference: inv for inv in db.scalars(select(Invoice).where(Invoice.reference.in_(refs)))} if refs else {}
    needed = {inv.id for inv in by_reference.values()} | {p.invoice_id for plist in payments_by_entry.values() for p in plist}
    invoices_by_id = {inv.id: inv for inv in db.scalars(select(Invoice).where(Invoice.id.in_(needed)))} if needed else {}
    lines: dict[int, list[InvoiceLine]] = defaultdict(list)
    if invoices_by_id:
        for line in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id.in_(invoices_by_id.keys()))):
            lines[line.invoice_id].append(line)

    out: list[CashLine] = []
    for entry in entries:
        if entry.status == "matched":
            for payment in payments_by_entry.get(entry.id, []):
                invoice = invoices_by_id.get(payment.invoice_id)
                weights = _invoice_weights(db, invoice, lines) if invoice else []
                out += [CashLine(entry, code, share, "invoice") for code, share in _split(payment.amount_minor, weights)]
        elif entry.status == "categorised":
            for row in allocations.get(entry.id, []):
                out.append(CashLine(entry, row.nominal_code or NOT_ALLOCATED, row.amount_minor, row.kind))
        elif entry.status == "historical":
            found = [by_reference[r] for r in banking.INVOICE_REF.findall(entry.details or "") if r in by_reference]
            if found:
                shares = _split(entry.amount_minor, [(str(inv.id), abs(inv.amount_minor)) for inv in found])
                for key, share in shares:
                    invoice = invoices_by_id[int(key)] if key.isdigit() else found[0]
                    out += [CashLine(entry, code, part, "invoice") for code, part in _split(share, _invoice_weights(db, invoice, lines))]
            else:
                out.append(CashLine(entry, NOT_ALLOCATED, entry.amount_minor, "unallocated"))
        else:  # unmatched or ignored
            out.append(CashLine(entry, NOT_ALLOCATED, entry.amount_minor, "waiting"))
    return out


def cash_summary(db: Session, start: date, end: date) -> CashSummary:
    summary = CashSummary(start, end)
    seen: set[int] = set()
    for line in cash_lines(db, start, end):
        seen.add(line.entry.id)
        amount = line.amount_minor
        if line.kind == "transfer":
            summary.transfers_in += max(amount, 0)
            summary.transfers_out += max(-amount, 0)
        elif line.kind == "waiting":
            summary.waiting_in += max(amount, 0)
            summary.waiting_out += max(-amount, 0)
        elif amount >= 0:
            summary.receipts[line.nominal] = summary.receipts.get(line.nominal, 0) + amount
        else:
            summary.payments[line.nominal] = summary.payments.get(line.nominal, 0) - amount
    summary.lines = len(seen)
    return summary


# --------------------------------------------------------------------------- bank balances and reconciliation


@dataclass
class BankPosition:
    account: BankAccount
    opening: int  # at the start of the period
    closing: int  # at the end of the period
    statement: int | None
    agrees: bool
    waiting: int  # lines not yet matched or categorised


def bank_positions(db: Session, start: date, end: date) -> list[BankPosition]:
    out = []
    for account in db.scalars(select(BankAccount).order_by(BankAccount.name)):
        before = banking.balance(db, account, start - timedelta(days=1))
        after = banking.balance(db, account, end)
        waiting = db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.account_id == account.id, BankEntry.status == "unmatched", BankEntry.entry_date <= end)) or 0
        out.append(BankPosition(account, before["computed"], after["computed"], after["statement"], after["agrees"], waiting))
    return out


# --------------------------------------------------------------------------- accounts month by month


@dataclass
class AccountMonth:
    month: date
    opening: int
    money_in: int
    money_out: int  # a positive number

    @property
    def closing(self) -> int:
        return self.opening + self.money_in - self.money_out


@dataclass
class AccountYear:
    account: BankAccount
    months: list[AccountMonth]


def account_months(db: Session, start: date, end: date) -> list[AccountYear]:
    """For each bank account, month by month between two dates: what it started with, what came in, what went out, what it ended with."""
    first = date(start.year, start.month, 1)
    out = []
    for account in db.scalars(select(BankAccount).order_by(BankAccount.name)):
        opening = banking.balance(db, account, first - timedelta(days=1))["computed"]
        entries = db.scalars(select(BankEntry).where(BankEntry.account_id == account.id, BankEntry.entry_date >= first, BankEntry.entry_date <= end)).all()
        months = []
        cursor = first
        while cursor <= end:
            nxt = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
            month_entries = [e for e in entries if cursor <= e.entry_date < nxt]
            row = AccountMonth(cursor, opening, sum(e.amount_minor for e in month_entries if e.amount_minor > 0), -sum(e.amount_minor for e in month_entries if e.amount_minor < 0))
            months.append(row)
            opening = row.closing
            cursor = nxt
        out.append(AccountYear(account, months))
    return out


# --------------------------------------------------------------------------- receipts and payments account


@dataclass
class ReceiptsAndPayments:
    start: date
    end: date
    opening: int
    cash: CashSummary
    closing_expected: int
    closing_actual: int
    positions: list[BankPosition]
    labels: dict[str, str]

    @property
    def difference(self) -> int:
        return self.closing_actual - self.closing_expected


def receipts_and_payments(db: Session, start: date, end: date) -> ReceiptsAndPayments:
    cash = cash_summary(db, start, end)
    positions = bank_positions(db, start, end)
    opening = sum(p.opening for p in positions)
    expected = opening + cash.movement
    return ReceiptsAndPayments(start, end, opening, cash, expected, sum(p.closing for p in positions), positions, nominal_labels(db))


# --------------------------------------------------------------------------- debtors at a date


@dataclass
class DebtorAt:
    contact_name: str
    contact_id: int | None
    invoice: Invoice
    outstanding: int
    days_overdue: int


def debtors_as_at(db: Session, as_at: date) -> list[DebtorAt]:
    """Who owed what on a given day: payments received after that day do not count, and an invoice written off later was still owed."""
    written_off_on: dict[int, date] = {}
    for row in db.scalars(select(AuditLog).where(AuditLog.action == "invoice.write_off")):
        if row.target_id.isdigit():
            written_off_on[int(row.target_id)] = row.created_at.date()
    candidates = db.scalars(select(Invoice).where(func.coalesce(Invoice.kind, "invoice") == "invoice", Invoice.issue_date <= as_at, Invoice.status.not_in(EXCLUDED_STATUSES))).all()
    ids = [i.id for i in candidates]
    paid: dict[int, int] = defaultdict(int)
    if ids:
        for payment in db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id.in_(ids), PaymentTransaction.status == "received")):
            if payment.received_at.date() <= as_at:
                paid[payment.invoice_id] += payment.amount_minor
    names = {cid: name for cid, name in db.execute(select(Contact.id, Contact.name))}
    member_names = {mid: name for mid, name in db.execute(select(Member.id, Member.name))}
    rows = []
    for invoice in candidates:
        if invoice.status == "written_off" and written_off_on.get(invoice.id, date.min) <= as_at:
            continue
        owed = invoice.amount_minor - paid.get(invoice.id, 0)
        if owed <= 0:
            continue
        due = invoice.due_date or invoice.issue_date or as_at
        name = names.get(invoice.contact_id) or member_names.get(invoice.member_id) or "(unknown)"  # type: ignore[arg-type]
        rows.append(DebtorAt(name, invoice.contact_id, invoice, owed, max(0, (as_at - due).days)))
    return sorted(rows, key=lambda r: (-r.outstanding, r.contact_name))


def debtor_buckets(rows: list[DebtorAt]) -> dict[str, int]:
    buckets = {"not_due": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90_plus": 0}
    for row in rows:
        key = "not_due" if row.days_overdue == 0 else "d1_30" if row.days_overdue <= 30 else "d31_60" if row.days_overdue <= 60 else "d61_90" if row.days_overdue <= 90 else "d90_plus"
        buckets[key] += row.outstanding
    return buckets


# --------------------------------------------------------------------------- membership numbers


def membership_stats(db: Session) -> dict:
    """Memberships by type and year (the yearly summary), plus people joining and leaving each year."""
    table: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    years: set[int] = set()
    for plan, starts, status in db.execute(select(Membership.plan_name, Membership.starts_on, Membership.status)):
        if status in {"active", "lapsed", "ended"}:
            table[plan][starts.year] += 1
            years.add(starts.year)
    ordered_years = sorted(years, reverse=True)
    joined = {y: n for y, n in db.execute(select(func.strftime("%Y", Member.join_date), func.count()).where(Member.join_date.is_not(None)).group_by(func.strftime("%Y", Member.join_date))) if y}
    return {
        "years": ordered_years,
        "rows": [{"type": plan, "counts": {y: table[plan].get(y, 0) for y in ordered_years}} for plan in sorted(table)],
        "totals": {y: sum(table[plan].get(y, 0) for plan in table) for y in ordered_years},
        "joined": {int(y): n for y, n in joined.items()},
    }


# --------------------------------------------------------------------------- the committee report


def monthly_series(db: Session, end_year: int, end_month: int, months: int = 12) -> list[dict]:
    """Invoiced and received for each of the last N months, for the trend chart."""
    points = []
    year, month = end_year, end_month
    for _ in range(months):
        start, end = month_bounds(year, month)
        points.append({"label": f"{calendar.month_abbr[month]} {str(year)[2:]}", "invoiced": sum(invoiced_by_nominal(db, start, end).values()), "received": cash_summary(db, start, end).total_receipts})
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return points[::-1]


@dataclass
class Compare:
    """One row of a this-period-against-last-year table."""

    code: str
    label: str
    month: int
    month_last_year: int
    ytd: int
    ytd_last_year: int


def compare_by_nominal(db: Session, current: dict[str, int], previous: dict[str, int], year_to_date: dict[str, int], year_to_date_previous: dict[str, int]) -> list[Compare]:
    labels = nominal_labels(db)
    codes = sorted(set(current) | set(previous) | set(year_to_date) | set(year_to_date_previous), key=lambda c: (c == NOT_ALLOCATED, c))
    return [Compare(c, labels.get(c, c), current.get(c, 0), previous.get(c, 0), year_to_date.get(c, 0), year_to_date_previous.get(c, 0)) for c in codes]


def committee_report(db: Session, year: int, month: int) -> dict:
    """Everything the committee wants on one page for a month."""
    m_start, m_end = month_bounds(year, month)
    fy_start, _ = fy_bounds(db, m_start)
    ly_m = same_period_last_year(m_start, m_end)
    ly_y = same_period_last_year(fy_start, m_end)
    invoiced = compare_by_nominal(db, invoiced_by_nominal(db, m_start, m_end), invoiced_by_nominal(db, *ly_m), invoiced_by_nominal(db, fy_start, m_end), invoiced_by_nominal(db, *ly_y))
    cash_m, cash_ly_m = cash_summary(db, m_start, m_end), cash_summary(db, *ly_m)
    cash_y, cash_ly_y = cash_summary(db, fy_start, m_end), cash_summary(db, *ly_y)
    received = compare_by_nominal(db, cash_m.receipts, cash_ly_m.receipts, cash_y.receipts, cash_ly_y.receipts)
    spent = compare_by_nominal(db, cash_m.payments, cash_ly_m.payments, cash_y.payments, cash_ly_y.payments)
    debtors = debtors_as_at(db, m_end)
    stats = membership_stats(db)
    return {
        "year": year,
        "month": month,
        "month_name": f"{calendar.month_name[month]} {year}",
        "period": (m_start, m_end),
        "fy_start": fy_start,
        "invoiced": invoiced,
        "received": received,
        "spent": spent,
        "cash_month": cash_m,
        "cash_ytd": cash_y,
        "positions": bank_positions(db, m_start, m_end),
        "debtors": {"total": sum(r.outstanding for r in debtors), "people": len({r.contact_id for r in debtors}), "buckets": debtor_buckets(debtors), "top": debtors[:8]},
        "waiting": db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.status == "unmatched", BankEntry.entry_date <= m_end)) or 0,
        "members": {"active": db.scalar(select(func.count()).select_from(Membership).where(Membership.status == "active", Membership.starts_on <= m_end, Membership.ends_on >= m_end)) or 0, "stats": stats},
        "trend": monthly_series(db, year, month),
    }
