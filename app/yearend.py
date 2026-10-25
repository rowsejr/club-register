"""Closing a financial year: what is still open, the year-end pack for the examiner, and the lock itself (see finance.set_lock)."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance, reports
from app.models import BankEntry, Invoice, Member


@dataclass
class Readiness:
    start: date
    end: date
    unmatched_lines: int
    drafts: int
    open_invoices: int
    ready: bool


def readiness(db: Session, start: date, end: date) -> Readiness:
    unmatched = db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.entry_date >= start, BankEntry.entry_date <= end, BankEntry.status.in_(["unmatched", "ignored"]))) or 0
    drafts = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.status == "draft", Invoice.issue_date >= start, Invoice.issue_date <= end)) or 0
    open_invoices = len(reports.debtors_as_at(db, end))
    return Readiness(start, end, unmatched, drafts, open_invoices, unmatched == 0 and drafts == 0)


_PLAIN_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _safe(cell):
    """Text a spreadsheet could run as a formula is defused; numbers stay numbers."""
    if isinstance(cell, str) and cell and not _PLAIN_NUMBER.fullmatch(cell) and cell[0] in "=+-@\t\r":
        return "'" + cell
    return cell


def _csv(header: list[str], rows: list[list]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe(c) for c in row])
    return out.getvalue()


def pack(db: Session, start: date, end: date) -> bytes:
    """A zip of plain CSVs an independent examiner can open in a spreadsheet: sales, receipts, the cash book, debtors and members."""
    files: dict[str, str] = {}
    lines = finance.accounting_lines(db, start, end)
    files["sales-invoice-lines.csv"] = _csv(
        ["date", "document", "type", "contact", "description", "nominal_code", "nominal_label", "gross", "status"],
        [[r["date"], r["document"], r["type"], r["contact"], r["description"], r["nominal_code"], r["nominal_label"], f"{r['gross'] / 100:.2f}", r["status"]] for r in lines],
    )
    payments = [r for r in finance.payment_lines(db, start, end) if r["method"] != "credit_note"]  # a credit note is not money received
    files["payments-received.csv"] = _csv(
        ["date", "invoice", "contact", "method", "reference", "amount", "recorded_by"],
        [[r["date"], r["invoice"], r["contact"], r["method"], r["reference"], f"{r['amount'] / 100:.2f}", r["recorded_by"]] for r in payments],
    )
    cash = reports.cash_lines(db, start, end)
    files["cash-book.csv"] = _csv(
        ["date", "account_id", "details", "nominal_code", "kind", "amount"],
        [[c.entry.entry_date.isoformat(), c.entry.account_id, c.entry.details or "", c.nominal, c.kind, f"{c.amount_minor / 100:.2f}"] for c in cash],
    )
    debtors = reports.debtors_as_at(db, end)
    files["debtors-at-year-end.csv"] = _csv(
        ["contact", "invoice", "issued", "due", "outstanding", "days_overdue"],
        [[d.contact_name, d.invoice.reference, d.invoice.issue_date.isoformat() if d.invoice.issue_date else "", d.invoice.due_date.isoformat() if d.invoice.due_date else "", f"{d.outstanding / 100:.2f}", d.days_overdue] for d in debtors],
    )
    income = reports.invoiced_by_nominal(db, start, end)
    labels = reports.nominal_labels(db)
    files["income-by-nominal.csv"] = _csv(["nominal_code", "label", "invoiced"], [[code, labels.get(code, ""), f"{amount / 100:.2f}"] for code, amount in sorted(income.items())])
    members = db.scalars(select(Member).order_by(Member.name)).all()
    files["members-now.csv"] = _csv(["name", "membership_type", "status", "expiry"], [[m.name, m.membership_type, m.renewal_status, m.expiry_date.isoformat() if m.expiry_date else ""] for m in members])
    receipts = sum(r["amount"] for r in payments)
    files["README.txt"] = (
        f"{finance.get_setting(db, 'club_name')}: year-end pack for {start:%d %b %Y} to {end:%d %b %Y}\n"
        f"Created {datetime.now(UTC):%d %b %Y %H:%M} UTC from the club office.\n\n"
        f"Invoice lines: {len(lines)}  Payments received: {len(payments)} totalling {receipts / 100:,.2f}\n"
        f"Cash book lines: {len(cash)}  Debtors at year end: {len(debtors)} totalling {sum(d.outstanding for d in debtors) / 100:,.2f}\n\n"
        "Every figure is in pounds. Debtors are as at the last day of the period. Nothing here is edited: it is read straight from the ledger.\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()
