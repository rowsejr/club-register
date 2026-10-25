"""Bank, PayPal and gateway statements: import, matching to invoices, and un-matching.

Statements are imported into ``bank_entries`` (re-uploading an overlapping statement is safe: each
line has a fingerprint). Money in is matched to open invoices and becomes a payment through the
finance service, so the same rules apply as for a payment keyed in by hand.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance
from app.csvutil import CsvTable, RowError, col, name_key, parse_date, parse_minor, read_csv
from app.models import BankAccount, BankEntry, Contact, Invoice, PaymentTransaction

ACCOUNT_KINDS = {"bank": "bank", "paypal": "paypal", "gocardless": "gocardless", "stripe": "stripe", "sumup": "sumup", "cash": "cash"}


def kind_from_name(name: str) -> str:
    lowered = name.lower()
    for word, kind in ACCOUNT_KINDS.items():
        if word in lowered:
            return kind
    return "bank"


def get_or_create_account(db: Session, name: str, kind: str | None = None, opening_minor: int = 0) -> BankAccount:
    account = db.scalar(select(BankAccount).where(func.lower(BankAccount.name) == name.strip().lower()))
    if account is None:
        account = BankAccount(name=name.strip(), kind=kind or kind_from_name(name), opening_balance_minor=opening_minor)
        db.add(account)
        db.flush()
    return account


# --------------------------------------------------------------------------- statement import

DATE_KEYS = ("date", "transactiondate", "paymentdate", "postingdate", "datetime")
REFERENCE_KEYS = ("reference", "ref", "paymentreference", "transactionreference")
DETAIL_KEYS = ("details", "description", "narrative", "memo", "transactiondescription", "note", "name")
AMOUNT_KEYS = ("amount", "value", "gross", "net")
IN_KEYS = ("paidin", "moneyin", "credit", "creditamount", "in")
OUT_KEYS = ("paidout", "moneyout", "debit", "debitamount", "out")
BALANCE_KEYS = ("balance", "runningbalance")
COUNTERPARTY_KEYS = ("contact", "counterparty", "payer", "payee", "from", "fromname")


@dataclass
class StatementReport:
    created: int = 0
    duplicates: int = 0
    credits: int = 0
    debits: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    batch: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "duplicates": self.duplicates, "credits": self.credits, "debits": self.debits, "errors": self.errors}


def _fingerprint(account_id: int, day: date, amount: int, reference: str, details: str, occurrence: int, entry_id: str = "") -> str:
    if entry_id:
        return hashlib.sha256(f"{account_id}|id|{entry_id}".encode()).hexdigest()
    return hashlib.sha256(f"{account_id}|{day}|{amount}|{reference}|{details}|{occurrence}".encode()).hexdigest()


def _row_amount(row: dict[str, str]) -> int:
    direct = col(row, *AMOUNT_KEYS)
    if direct:
        value = parse_minor(direct)
        if value is not None:
            return value
    money_in, money_out = parse_minor(col(row, *IN_KEYS)) or 0, parse_minor(col(row, *OUT_KEYS)) or 0
    if money_in or money_out:
        return money_in - abs(money_out)
    raise RowError("No amount found")


def parse_statement(table: CsvTable) -> list[tuple[int, dict[str, Any]]]:
    keys = table.keys
    if not (set(DATE_KEYS) & keys):
        raise ValueError("No date column found (expected Date)")
    if not (set(AMOUNT_KEYS) & keys or set(IN_KEYS) & keys):
        raise ValueError("No amount column found (expected Amount, or Paid in / Paid out)")
    out = []
    for row_no, row in enumerate(table.rows, start=2):
        out.append((row_no, row))
    return out


def import_statement(db: Session, account: BankAccount, text: str, source: str = "statement", batch_id: int | None = None, entry_id_keys: tuple[str, ...] = ()) -> StatementReport:
    report = StatementReport()
    table = read_csv(text)
    rows = parse_statement(table)
    seen: Counter[tuple[Any, ...]] = Counter()
    for row_no, row in rows:
        try:
            day = parse_date(col(row, *DATE_KEYS))
            if day is None:
                raise RowError("Missing date")
            amount = _row_amount(row)
        except RowError as exc:
            report.errors.append({"row": row_no, "message": str(exc)})
            continue
        reference, details = col(row, *REFERENCE_KEYS), col(row, *DETAIL_KEYS)
        seen_key = (day, amount, reference, details)
        seen[seen_key] += 1
        fingerprint = _fingerprint(account.id, day, amount, reference, details, seen[seen_key], col(row, *entry_id_keys) if entry_id_keys else "")
        if db.scalar(select(BankEntry.id).where(BankEntry.account_id == account.id, BankEntry.fingerprint == fingerprint)):
            report.duplicates += 1
            continue
        balance = parse_minor(col(row, *BALANCE_KEYS)) if col(row, *BALANCE_KEYS) else None
        entry = BankEntry(
            account_id=account.id,
            entry_date=day,
            reference=reference,
            details=details,
            counterparty=col(row, *COUNTERPARTY_KEYS) or None,
            amount_minor=amount,
            balance_minor=balance,
            status="unmatched" if amount > 0 else "ignored",
            fingerprint=fingerprint,
            source=source,
            batch_id=batch_id,
        )
        db.add(entry)
        report.created += 1
        report.credits += amount > 0
        report.debits += amount <= 0
    db.flush()
    return report


# --------------------------------------------------------------------------- matching


def _open_invoices(db: Session) -> list[Invoice]:
    return list(db.scalars(select(Invoice).where(Invoice.status.in_(finance.OPEN), func.coalesce(Invoice.kind, "invoice") == "invoice")))


def _text(entry: BankEntry) -> str:
    return f"{entry.reference} {entry.details} {entry.counterparty or ''}"


def _mentions(entry: BankEntry, reference: str) -> bool:
    """Does the statement text name this invoice number? Long numbers match on their own; short ones
    (early invoice numbers are small) need an 'INV' / 'invoice' / '#' prefix so a stray '12' does not count."""
    text = _text(entry)
    number = re.escape(reference)
    if len(reference) >= 4:
        return re.search(rf"(?<![\d.]){number}(?![\d.])", text) is not None
    return re.search(rf"(?:\binv(?:oice)?\.?\s*#?|#)\s*{number}(?![\d.])", text, re.IGNORECASE) is not None


@dataclass
class Suggestion:
    invoice: Invoice
    score: int
    reasons: list[str]
    outstanding: int
    contact_name: str


def suggestions(db: Session, entry: BankEntry, limit: int = 5) -> list[Suggestion]:
    names = {c.id: c.name for c in db.scalars(select(Contact))}
    haystack = name_key(_text(entry))
    out = []
    for invoice in _open_invoices(db):
        owed = finance.outstanding(db, invoice)
        score, reasons = 0, []
        if _mentions(entry, invoice.reference):
            score += 5
            reasons.append("invoice number in reference")
        who = names.get(invoice.contact_id or 0, "")
        parts = [p for p in name_key(who).split() if len(p) > 2]
        if parts and all(p in haystack for p in parts):
            score += 3
            reasons.append("name matches")
        if owed == entry.amount_minor:
            score += 2
            reasons.append("exact amount")
        elif 0 < entry.amount_minor < owed and score:
            reasons.append("part payment")
        if score:
            out.append(Suggestion(invoice, score, reasons, owed, who))
    return sorted(out, key=lambda s: (-s.score, s.invoice.id))[:limit]


def _method_for(account: BankAccount) -> str:
    return account.kind if account.kind in finance.PAYMENT_METHODS else "bank"


def _payment_ref(entry: BankEntry, part: int = 0) -> str:
    return f"BE{entry.id}" + (f"-{part}" if part else "")


def match_split(db: Session, entry: BankEntry, allocations: list[tuple[int, int]], actor: str) -> list[PaymentTransaction]:
    """Match a statement line to one or more invoices. The allocations must add up to the line."""
    if entry.amount_minor <= 0:
        raise finance.FinanceError("Only money in can be matched to an invoice")
    if entry.status != "unmatched":
        raise finance.FinanceError(f"This line is already {entry.status}")
    if sum(a for _, a in allocations) != entry.amount_minor:
        raise finance.FinanceError(f"Allocations add up to {sum(a for _, a in allocations) / 100:.2f} but the line is {entry.amount_minor / 100:.2f}")
    account = db.get(BankAccount, entry.account_id)
    if account is None:
        raise finance.FinanceError("Account not found")
    payments = []
    for index, (invoice_id, amount) in enumerate(allocations, start=1):
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            raise finance.FinanceError("Invoice not found")
        payments.append(
            finance.record_payment(
                db,
                invoice,
                amount_minor=amount,
                method=_method_for(account),
                reference=_payment_ref(entry, index if len(allocations) > 1 else 0),
                received_on=entry.entry_date,
                actor=actor,
                bank_entry_id=entry.id,
            )
        )
    entry.status = "matched"
    entry.matched_invoice_id = allocations[0][0] if len(allocations) == 1 else None
    entry.payment_id = payments[0].id if len(payments) == 1 else None
    return payments


def match_one(db: Session, entry: BankEntry, invoice: Invoice, actor: str) -> PaymentTransaction:
    return match_split(db, entry, [(invoice.id, entry.amount_minor)], actor)[0]


def unmatch(db: Session, entry: BankEntry, actor: str, reason: str = "un-matched") -> int:
    if entry.status != "matched":
        raise finance.FinanceError("Only a matched line can be un-matched")
    reversed_count = 0
    for payment in db.scalars(select(PaymentTransaction).where(PaymentTransaction.bank_entry_id == entry.id, PaymentTransaction.status == "received")):
        finance.reverse_payment(db, payment, actor, reason)
        reversed_count += 1
    entry.status, entry.matched_invoice_id, entry.payment_id = "unmatched", None, None
    db.flush()
    return reversed_count


def ignore(db: Session, entry: BankEntry) -> None:
    if entry.status != "unmatched":
        raise finance.FinanceError(f"This line is {entry.status}")
    entry.status = "ignored"


def restore(db: Session, entry: BankEntry) -> None:
    if entry.status != "ignored" or entry.amount_minor <= 0:
        raise finance.FinanceError("Only an ignored credit can be restored")
    entry.status = "unmatched"


@dataclass
class AutoMatchReport:
    matched: int = 0
    left: int = 0
    details: list[str] = field(default_factory=list)


def auto_match(db: Session, actor: str, account_id: int | None = None) -> AutoMatchReport:
    """Match only when we are sure: the invoice number is in the reference (and the amount fits), or the payer's
    name matches and the amount is exactly what one invoice owes. Everything else is left for a person."""
    report = AutoMatchReport()
    stmt = select(BankEntry).where(BankEntry.status == "unmatched", BankEntry.amount_minor > 0).order_by(BankEntry.entry_date, BankEntry.id)
    if account_id:
        stmt = stmt.where(BankEntry.account_id == account_id)
    for entry in db.scalars(stmt):
        ranked = suggestions(db, entry, limit=3)
        pick = None
        if ranked:
            top = ranked[0]
            by_ref = "invoice number in reference" in top.reasons and entry.amount_minor <= top.outstanding
            by_name = "name matches" in top.reasons and "exact amount" in top.reasons and (len(ranked) == 1 or ranked[1].score < top.score)
            if by_ref or by_name:
                pick = top
        if pick is None:
            report.left += 1
            continue
        savepoint = db.begin_nested()
        try:
            match_one(db, entry, pick.invoice, actor)
            savepoint.commit()
            report.matched += 1
            report.details.append(f"{entry.id} -> {pick.invoice.reference} ({', '.join(pick.reasons)})")
        except finance.FinanceError as exc:
            savepoint.rollback()
            report.left += 1
            report.details.append(f"{entry.id}: {exc}")
    return report


def balance(db: Session, account: BankAccount, on: date | None = None) -> dict[str, Any]:
    """Where an account stands: the balance the statement itself shows, and the one built from the opening balance plus every line.

    The opening balance is read from the first line when the statement carries running balances (balance before = balance - amount),
    otherwise it is the figure stored on the account. ``statement`` is None when no line carries a running balance. When both exist
    and differ, a line is missing or wrong. An account with no lines at all falls back to its recorded closing balance.
    """
    entries = db.scalars(select(BankEntry).where(BankEntry.account_id == account.id)).all()
    ordered = sorted(entries, key=_ledger_order)
    first = ordered[0] if ordered else None
    opening = account.opening_balance_minor or 0
    if first is not None and first.balance_minor is not None:
        opening = first.balance_minor - first.amount_minor
    counted = [e for e in ordered if on is None or e.entry_date <= on]
    last = counted[-1] if counted else None
    computed = opening + sum(e.amount_minor for e in counted)
    statement = last.balance_minor if last is not None else None
    if not ordered and account.closing_balance_minor is not None and on is None:
        computed = account.closing_balance_minor
    return {"computed": computed, "statement": statement, "as_of": last.entry_date if last else None, "agrees": statement is None or statement == computed, "lines": len(counted), "opening": opening}


def account_summary(db: Session) -> list[dict[str, Any]]:
    out = []
    for account in db.scalars(select(BankAccount).order_by(BankAccount.name)):
        counts = dict(db.execute(select(BankEntry.status, func.count()).where(BankEntry.account_id == account.id).group_by(BankEntry.status)).all())
        total = db.scalar(select(func.coalesce(func.sum(BankEntry.amount_minor), 0)).where(BankEntry.account_id == account.id)) or 0
        waiting = db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.account_id == account.id, BankEntry.status == "unmatched")) or 0
        out.append({"account": account, "counts": counts, "total": total, "unmatched_credits": waiting, "balance": balance(db, account)})
    return out


# --------------------------------------------------------------------------- statement order and invoice references


def _ledger_order(entry: BankEntry) -> tuple[date, int]:
    """Date, then the order the rows were loaded: within a day the bank lists entries in its own order."""
    return entry.entry_date, entry.id


INVOICE_REF = re.compile(r"Invoice\s+(\d+)")
