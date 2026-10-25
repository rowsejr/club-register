"""Invoicing and payments domain logic. One place for the rules, used by the UI and the JSON API.

Rules enforced here (the audit of the first prototype found every one of them missing):
* Invoice totals are derived from lines, and lines are locked once an invoice is issued.
* Invoice numbers are sequential and gap-free: a draft carries a temporary reference and only
  receives its number when issued.
* Status changes follow a state machine; payments are only accepted on open invoices, may not
  exceed what is owed, and a bank/gateway reference can only be used once per method.
* Money is integer pence throughout.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    AppSetting,
    AuditLog,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    NominalCode,
    NumberSequence,
    PaymentTransaction,
)

OPEN = ("issued", "part_paid")
MEMBER_VISIBLE = ("issued", "part_paid", "paid", "written_off")  # members never see drafts, voids or cancelled invoices
TERMINAL = ("paid", "void", "written_off", "cancelled")
PAYMENT_METHODS = ("bank", "paypal", "gocardless", "stripe", "sumup", "cash", "cheque", "card", "credit_note", "other")
DEFAULT_TERMS_DAYS = 30
DEFAULT_SETTINGS = {
    "club_name": "Margate Yacht Club",
    "club_address": "",
    "invoice_terms_days": str(DEFAULT_TERMS_DAYS),
    "payment_instructions": "",
    "bank_details": "",
    "invoice_footer": "",
    "invoice_message": "",
    "fy_start_month": "1",
    "renewal_window_months": "4",
    "lapse_after_days": "60",
    "season_invoice_mode": "combined",
    "mailing_footer": "",
    "event_hold_days": "3",
    "event_refund_cutoff_days": "7",
    "reply_to": "",
    "books_locked_until": "",
    "census_age_bands": "0-7,8-11,12-15,16-17,18-24,25-34,35-44,45-54,55-64,65-120",
}


class FinanceError(ValueError):
    """A business-rule violation, safe to show to the user."""


# --------------------------------------------------------------------------- settings


def get_setting(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row else DEFAULT_SETTINGS.get(key, "")


def all_settings(db: Session) -> dict[str, str]:
    stored = {s.key: s.value for s in db.scalars(select(AppSetting))}
    return {**DEFAULT_SETTINGS, **stored}


def save_settings(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS or key == "books_locked_until":  # the lock has its own screen, rules and audit entry
            continue
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=value.strip()))
        else:
            row.value = value.strip()


def locked_until(db: Session) -> date | None:
    """The last day of the closed financial period, or None when nothing is closed."""
    raw = get_setting(db, "books_locked_until")
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def assert_unlocked(db: Session, day: date | None, what: str) -> None:
    lock = locked_until(db)
    if lock is not None and day is not None and day <= lock:
        raise FinanceError(
            f"The books are closed up to {lock:%d %b %Y}, and {what} dated {day:%d %b %Y} falls inside that period. "
            "Date it after the lock (for a correction, use a credit note dated today), or ask an administrator to unlock the books."
        )


def set_lock(db: Session, day: date | None, actor: str, *, is_admin: bool, reason: str = "") -> None:
    """Close the books up to ``day``. Anyone with the right hat may close more of the past; only an administrator may reopen."""
    current = locked_until(db)
    if day is not None and day >= date.today():
        raise FinanceError("The lock date must be before today, so that today's payments and invoices can still be recorded")
    if (day is None and current is not None) or (day is not None and current is not None and day < current):
        if not is_admin:
            raise FinanceError("Only an administrator can reopen closed books")
        if not reason.strip():
            raise FinanceError("Give a reason for reopening the books")
    row = db.get(AppSetting, "books_locked_until")
    value = day.isoformat() if day else ""
    if row is None:
        db.add(AppSetting(key="books_locked_until", value=value))
    else:
        row.value = value
    db.add(AuditLog(actor_email=actor, actor_role="user", action="books.lock", target_type="settings", target_id="books_locked_until", details={"before": current.isoformat() if current else None, "after": value or None, "reason": reason.strip()}))
    db.flush()


def terms_days(db: Session) -> int:
    try:
        return max(0, int(get_setting(db, "invoice_terms_days")))
    except ValueError:
        return DEFAULT_TERMS_DAYS


# --------------------------------------------------------------------------- numbering


def next_number(db: Session, name: str = "invoice") -> int:
    """Next value of a gap-free sequence. The invoice sequence continues from the highest numeric
    reference already present (so earlier invoice numbers are never re-used)."""
    seq = db.get(NumberSequence, name)
    if seq is None:
        start = 1000 if name == "invoice" else 0
        if name == "invoice":
            numeric = [int(r) for (r,) in db.execute(select(Invoice.reference)) if str(r).isdigit()]
            start = max(numeric, default=1000)
        seq = NumberSequence(name=name, value=start)
        db.add(seq)
        db.flush()
    seq.value += 1
    db.flush()
    return seq.value


# --------------------------------------------------------------------------- queries


def paid_total(db: Session, invoice_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(
            PaymentTransaction.invoice_id == invoice_id, PaymentTransaction.status == "received"
        )
    ) or 0


def outstanding(db: Session, invoice: Invoice) -> int:
    if invoice.status not in OPEN:
        return 0
    return max(0, invoice.amount_minor - paid_total(db, invoice.id))


def lines_total(db: Session, invoice_id: int) -> int:
    return db.scalar(select(func.coalesce(func.sum(InvoiceLine.amount_minor), 0)).where(InvoiceLine.invoice_id == invoice_id)) or 0


def _audit(db: Session, actor: str, action: str, invoice: Invoice, **details: Any) -> None:
    db.add(AuditLog(actor_email=actor, actor_role="system" if actor == "system" else "user", action=action, target_type="invoice", target_id=str(invoice.id), details=details))


# Called when an invoice becomes fully paid (new members activate, renewals take effect). Registered by other modules.
PAID_HOOKS: list[Callable[[Session, Invoice], None]] = []


def refresh_status(db: Session, invoice: Invoice) -> None:
    """Re-derive issued / part_paid / paid from the payments received."""
    if invoice.status not in (*OPEN, "paid"):
        return
    was = invoice.status
    paid = paid_total(db, invoice.id)
    if invoice.amount_minor > 0 and paid >= invoice.amount_minor:
        invoice.status = "paid"
        if was != "paid":
            for hook in PAID_HOOKS:
                hook(db, invoice)
    elif paid > 0:
        invoice.status = "part_paid"
    else:
        invoice.status = "issued"


# --------------------------------------------------------------------------- drafts and lines


def create_invoice(
    db: Session,
    *,
    contact_id: int | None = None,
    member_id: int | None = None,
    lines: list[dict[str, Any]] | None = None,
    actor: str = "system",
    notes: str | None = None,
    source: str = "office",
    message: str | None = None,
    tags: str | None = None,
    issue_date: date | None = None,
    terms_days: int | None = None,
    send_reminders: bool = True,
    send_receipt: bool = True,
) -> Invoice:
    """Create a draft invoice (temporary reference; numbered when issued)."""
    contact: Contact | None = db.get(Contact, contact_id) if contact_id else None
    member: Member | None = db.get(Member, member_id) if member_id else None
    if contact is None and member is not None:
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
    if member is None and contact is not None and contact.member_id:
        member = db.get(Member, contact.member_id)
    if contact is None and member is None:
        raise FinanceError("An invoice needs a contact or member")
    invoice = Invoice(
        reference=f"DRAFT-{uuid.uuid4().hex[:10]}",
        amount_minor=0,
        currency="GBP",
        status="draft",
        kind="invoice",
        contact_id=contact.id if contact else None,
        member_id=member.id if member else None,
        notes=notes,
        source=source,
        tax_rate_bp=0,
        message=(message or "").strip() or None,
        tags=clean_tags(tags),
        issue_date=issue_date,
        terms_days=terms_days,
        send_reminders=send_reminders,
        send_receipt=send_receipt,
    )
    db.add(invoice)
    db.flush()
    for line in lines or []:
        add_line(db, invoice, **line)
    _audit(db, actor, "invoice.create", invoice)
    return invoice


def add_line(
    db: Session,
    invoice: Invoice,
    *,
    description: str,
    amount_minor: int | None = None,
    nominal_code: str | None = None,
    quantity: float | None = None,
    unit_price_minor: int | None = None,
    source_type: str = "manual",
    source_id: str = "",
) -> InvoiceLine:
    if invoice.status != "draft":
        raise FinanceError(f"Lines can only be changed on a draft invoice (this one is {invoice.status})")
    if amount_minor is None:
        if unit_price_minor is None:
            raise FinanceError("A line needs an amount, or a quantity and a unit price")
        amount_minor = round((quantity if quantity is not None else 1) * unit_price_minor)
    if not description.strip():
        raise FinanceError("A line needs a description")
    if amount_minor == 0:
        raise FinanceError("A line amount cannot be zero")
    if nominal_code and not db.scalar(select(NominalCode.id).where(NominalCode.code == nominal_code)) and db.scalar(select(func.count()).select_from(NominalCode)):
        raise FinanceError(f"Unknown nominal code {nominal_code}")
    line = InvoiceLine(
        invoice_id=invoice.id,
        source_type=source_type,
        source_id=source_id or str(uuid.uuid4().hex[:8]),
        description=description.strip(),
        amount_minor=amount_minor,
        nominal_code=nominal_code or None,
        quantity=quantity,
        unit_price_minor=unit_price_minor,
    )
    db.add(line)
    db.flush()
    invoice.amount_minor = lines_total(db, invoice.id)
    return line


def remove_line(db: Session, invoice: Invoice, line_id: int) -> None:
    if invoice.status != "draft":
        raise FinanceError("Lines can only be removed from a draft invoice")
    line = db.get(InvoiceLine, line_id)
    if line is None or line.invoice_id != invoice.id:
        raise FinanceError("Line not found")
    db.delete(line)
    db.flush()
    invoice.amount_minor = lines_total(db, invoice.id)


def clean_tags(value: str | None) -> str | None:
    """Tags as a tidy comma-separated string, lower-cased and de-duplicated, or None."""
    seen: list[str] = []
    for part in (value or "").replace(";", ",").split(","):
        tag = " ".join(part.split()).lower()
        if tag and tag not in seen:
            seen.append(tag)
    return ", ".join(seen) or None


def update_draft(
    db: Session,
    invoice: Invoice,
    *,
    message: str | None = None,
    tags: str | None = None,
    notes: str | None = None,
    issue_date: date | None = None,
    terms_days: int | None = None,
    send_reminders: bool = True,
    send_receipt: bool = True,
) -> Invoice:
    if invoice.status != "draft":
        raise FinanceError(f"Only a draft can be edited (this one is {invoice.status})")
    invoice.message = (message or "").strip() or None
    invoice.tags = clean_tags(tags)
    invoice.notes = (notes or "").strip() or None
    invoice.issue_date, invoice.terms_days = issue_date, terms_days
    invoice.send_reminders, invoice.send_receipt = send_reminders, send_receipt
    db.flush()
    return invoice


def replace_lines(db: Session, invoice: Invoice, lines: list[dict[str, Any]]) -> None:
    """Swap a draft's lines for a new set in one step (the builder saves the whole form at once)."""
    if invoice.status != "draft":
        raise FinanceError("Lines can only be changed on a draft invoice")
    for old in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)).all():
        db.delete(old)
    db.flush()
    invoice.amount_minor = 0
    for line in lines:
        add_line(db, invoice, **line)


def duplicate(db: Session, invoice: Invoice, actor: str) -> Invoice:
    """A new draft with the same payer, lines and message: for the fee that is charged again every season."""
    if (invoice.kind or "invoice") == "credit_note":
        raise FinanceError("A credit note cannot be duplicated")
    lines = [
        {"description": line.description, "amount_minor": line.amount_minor, "nominal_code": line.nominal_code, "quantity": line.quantity, "unit_price_minor": line.unit_price_minor}
        for line in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id))
    ]
    return create_invoice(
        db, contact_id=invoice.contact_id, member_id=invoice.member_id, lines=lines, actor=actor, notes=invoice.notes, message=invoice.message, tags=invoice.tags,
        terms_days=invoice.terms_days, send_reminders=bool(invoice.send_reminders), send_receipt=bool(invoice.send_receipt),
    )


# --------------------------------------------------------------------------- lifecycle


def issue(db: Session, invoice: Invoice, actor: str, issue_date: date | None = None, terms: int | None = None) -> Invoice:
    if invoice.status != "draft":
        raise FinanceError(f"Only a draft can be issued (this one is {invoice.status})")
    count = db.scalar(select(func.count()).select_from(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id)) or 0
    if not count:
        raise FinanceError("Add at least one line before issuing")
    invoice.amount_minor = lines_total(db, invoice.id)
    if invoice.amount_minor <= 0:
        raise FinanceError("An invoice total must be greater than zero")
    issue_date = issue_date or invoice.issue_date or date.today()
    assert_unlocked(db, issue_date, "an invoice")
    invoice.reference = str(next_number(db, "invoice"))
    invoice.issue_date = issue_date
    chosen = terms if terms is not None else invoice.terms_days
    invoice.due_date = issue_date + timedelta(days=chosen if chosen is not None else terms_days(db))
    invoice.status = "issued"
    db.flush()
    _audit(db, actor, "invoice.issue", invoice, reference=invoice.reference, total=invoice.amount_minor)
    return invoice


def _to_datetime(day: date | None) -> datetime:
    return datetime.combine(day or date.today(), time(12, 0), tzinfo=UTC)


def record_payment(
    db: Session,
    invoice: Invoice,
    *,
    amount_minor: int,
    method: str,
    reference: str = "",
    received_on: date | None = None,
    actor: str = "system",
    bank_entry_id: int | None = None,
) -> PaymentTransaction:
    method = (method or "").strip().lower()
    if method not in PAYMENT_METHODS:
        raise FinanceError(f"Unknown payment method '{method}'")
    if invoice.status not in OPEN:
        raise FinanceError(f"Cannot record a payment on a {invoice.status} invoice")
    if amount_minor <= 0:
        raise FinanceError("A payment must be greater than zero")
    assert_unlocked(db, received_on or date.today(), "a payment")
    owed = outstanding(db, invoice)
    if amount_minor > owed:
        raise FinanceError(f"Payment of {amount_minor / 100:.2f} is more than the {owed / 100:.2f} outstanding")
    reference = reference.strip()
    if reference and db.scalar(
        select(PaymentTransaction.id).where(
            PaymentTransaction.payment_method == method, PaymentTransaction.bank_reference == reference, PaymentTransaction.status == "received"
        )
    ):
        raise FinanceError(f"A {method} payment with reference '{reference}' has already been recorded")
    payment = PaymentTransaction(
        invoice_id=invoice.id,
        amount_minor=amount_minor,
        payment_method=method,
        bank_reference=reference,
        status="received",
        received_at=_to_datetime(received_on),
        recorded_by=actor,
        bank_entry_id=bank_entry_id,
    )
    db.add(payment)
    db.flush()
    refresh_status(db, invoice)
    _audit(db, actor, "invoice.payment", invoice, amount=amount_minor, method=method, reference=reference)
    return payment


def reverse_payment(db: Session, payment: PaymentTransaction, actor: str, reason: str) -> None:
    """Mark a payment reversed (bounced cheque, returned direct debit) and re-open the invoice."""
    if payment.status != "received":
        raise FinanceError("Only a received payment can be reversed")
    if payment.payment_method == "credit_note":
        raise FinanceError("A credit applied from a credit note cannot be reversed")
    assert_unlocked(db, payment.received_at.date(), "the payment")
    invoice = db.get(Invoice, payment.invoice_id)
    payment.status = "reversed"
    db.flush()
    if invoice is not None:
        if invoice.status == "paid":
            invoice.status = "part_paid"
        refresh_status(db, invoice)
        _audit(db, actor, "invoice.payment_reversed", invoice, amount=payment.amount_minor, reason=reason)


def void_invoice(db: Session, invoice: Invoice, actor: str, reason: str) -> Invoice:
    if not reason.strip():
        raise FinanceError("Give a reason for voiding")
    if invoice.status == "draft":
        invoice.status = "cancelled"
    elif invoice.status in OPEN:
        assert_unlocked(db, invoice.issue_date, "the invoice")
        if paid_total(db, invoice.id) > 0:
            raise FinanceError("This invoice has payments; issue a credit note instead of voiding")
        invoice.status = "void"
    else:
        raise FinanceError(f"A {invoice.status} invoice cannot be voided")
    invoice.notes = f"{invoice.notes + chr(10) if invoice.notes else ''}Voided: {reason.strip()}"
    _audit(db, actor, "invoice.void", invoice, reason=reason)
    db.flush()
    return invoice


def write_off(db: Session, invoice: Invoice, actor: str, reason: str) -> Invoice:
    if invoice.status not in OPEN:
        raise FinanceError("Only an unpaid invoice can be written off")
    if not reason.strip():
        raise FinanceError("Give a reason for the write-off")
    owed = outstanding(db, invoice)
    invoice.status = "written_off"
    invoice.notes = f"{invoice.notes + chr(10) if invoice.notes else ''}Written off {owed / 100:.2f}: {reason.strip()}"
    _audit(db, actor, "invoice.write_off", invoice, amount=owed, reason=reason)
    db.flush()
    return invoice


def credit_note(db: Session, invoice: Invoice, *, amount_minor: int, reason: str, actor: str) -> Invoice:
    """Issue a credit note against an invoice and, where money is still owed, apply it."""
    if invoice.kind == "credit_note":
        raise FinanceError("Cannot credit a credit note")
    if invoice.status not in (*OPEN, "paid"):
        raise FinanceError(f"A {invoice.status} invoice cannot be credited")
    if amount_minor <= 0:
        raise FinanceError("A credit must be greater than zero")
    if not reason.strip():
        raise FinanceError("Give a reason for the credit")
    already = db.scalar(select(func.coalesce(func.sum(-Invoice.amount_minor), 0)).where(Invoice.credit_for_id == invoice.id, Invoice.kind == "credit_note")) or 0
    if amount_minor + already > invoice.amount_minor:
        raise FinanceError(f"Credits would exceed the invoice total ({already / 100:.2f} already credited)")
    reference = f"CN-{next_number(db, 'credit_note'):04d}"
    note = Invoice(
        reference=reference,
        amount_minor=-amount_minor,
        currency=invoice.currency,
        status="issued",
        kind="credit_note",
        credit_for_id=invoice.id,
        contact_id=invoice.contact_id,
        member_id=invoice.member_id,
        issue_date=date.today(),
        notes=reason.strip(),
        source="office",
        tax_rate_bp=invoice.tax_rate_bp,
    )
    db.add(note)
    db.flush()
    db.add(InvoiceLine(invoice_id=note.id, source_type="credit", source_id=str(invoice.id), description=f"Credit against {invoice.reference}: {reason.strip()}", amount_minor=-amount_minor))
    owed = outstanding(db, invoice)
    apply = min(amount_minor, owed)
    if apply > 0:
        db.add(
            PaymentTransaction(
                invoice_id=invoice.id, amount_minor=apply, payment_method="credit_note", bank_reference=reference, status="received", received_at=_to_datetime(None), recorded_by=actor
            )
        )
        db.flush()
        refresh_status(db, invoice)
    note.status = "paid" if apply == amount_minor else "issued"  # an unapplied remainder is a refund due
    _audit(db, actor, "invoice.credit_note", invoice, credit_note=reference, amount=amount_minor, applied=apply)
    return note


# --------------------------------------------------------------------------- reports


@dataclass
class DebtorRow:
    contact_id: int | None
    name: str
    buckets: dict[str, int] = field(default_factory=lambda: dict.fromkeys(("not_due", "d1_30", "d31_60", "d61_90", "d90_plus"), 0))
    invoices: list[tuple[int, str, int, int]] = field(default_factory=list)  # (id, reference, outstanding, days overdue)

    @property
    def total(self) -> int:
        return sum(self.buckets.values())


def _bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "not_due"
    if days_overdue <= 30:
        return "d1_30"
    if days_overdue <= 60:
        return "d31_60"
    if days_overdue <= 90:
        return "d61_90"
    return "d90_plus"


def aged_debtors(db: Session, today: date | None = None) -> list[DebtorRow]:
    today = today or date.today()
    rows: dict[int | None, DebtorRow] = {}
    names = {c.id: c.name for c in db.scalars(select(Contact))}
    for invoice in db.scalars(select(Invoice).where(Invoice.status.in_(OPEN), func.coalesce(Invoice.kind, "invoice") == "invoice")):
        owed = outstanding(db, invoice)
        if owed <= 0:
            continue
        due = invoice.due_date or invoice.issue_date or today
        days = (today - due).days
        row = rows.setdefault(invoice.contact_id, DebtorRow(invoice.contact_id, names.get(invoice.contact_id or 0, "Unknown")))
        row.buckets[_bucket(days)] += owed
        row.invoices.append((invoice.id, invoice.reference, owed, max(days, 0)))
    return sorted(rows.values(), key=lambda r: (-r.total, r.name))


def debtor_totals(rows: list[DebtorRow]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        for key, value in row.buckets.items():
            totals[key] += value
    totals["total"] = sum(totals.values())
    return dict(totals)


def accounting_lines(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    """One row per invoice line, ready for the treasurer's spreadsheet or accounts package."""
    labels = {n.code: n.label for n in db.scalars(select(NominalCode))}
    stmt = select(InvoiceLine, Invoice, Contact.name).join(Invoice, Invoice.id == InvoiceLine.invoice_id).outerjoin(Contact, Contact.id == Invoice.contact_id)
    stmt = stmt.where(Invoice.status.in_([*OPEN, "paid", "written_off"]))
    if start:
        stmt = stmt.where(Invoice.issue_date >= start)
    if end:
        stmt = stmt.where(Invoice.issue_date <= end)
    out = []
    for line, invoice, name in db.execute(stmt.order_by(Invoice.issue_date, Invoice.id, InvoiceLine.id)).all():
        gross = line.amount_minor
        rate = invoice.tax_rate_bp or 0
        net = round(gross * 10000 / (10000 + rate)) if rate else gross
        out.append(
            {
                "date": invoice.issue_date.isoformat() if invoice.issue_date else "",
                "document": invoice.reference,
                "type": invoice.kind or "invoice",
                "contact": name or "",
                "description": line.description,
                "nominal_code": line.nominal_code or "",
                "nominal_label": labels.get(line.nominal_code or "", ""),
                "net": net,
                "vat": gross - net,
                "gross": gross,
                "status": invoice.status,
            }
        )
    return out


def payment_lines(db: Session, start: date | None = None, end: date | None = None) -> list[dict[str, Any]]:
    stmt = select(PaymentTransaction, Invoice.reference, Contact.name).join(Invoice, Invoice.id == PaymentTransaction.invoice_id).outerjoin(Contact, Contact.id == Invoice.contact_id)
    stmt = stmt.where(PaymentTransaction.status == "received")
    if start:
        stmt = stmt.where(PaymentTransaction.received_at >= datetime.combine(start, time.min, tzinfo=UTC))
    if end:
        stmt = stmt.where(PaymentTransaction.received_at <= datetime.combine(end, time.max, tzinfo=UTC))
    return [
        {
            "date": p.received_at.date().isoformat(),
            "invoice": ref,
            "contact": name or "",
            "method": p.payment_method,
            "reference": p.bank_reference,
            "amount": p.amount_minor,
            "recorded_by": p.recorded_by or "",
        }
        for p, ref, name in db.execute(stmt.order_by(PaymentTransaction.received_at, PaymentTransaction.id)).all()
    ]
