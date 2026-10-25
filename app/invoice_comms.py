"""Emailing invoices and payment reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance, mailer, payments
from app.invoice_pdf import render_invoice_pdf
from app.models import Application, Contact, Invoice, OutboundEmail

REMINDER_GAP_DAYS = 14


@dataclass
class ReminderCandidate:
    invoice: Invoice
    contact: Contact
    days_overdue: int
    outstanding: int
    last_reminded: date | None

    @property
    def can_email(self) -> bool:
        return bool(self.contact.email) and not self.contact.suppressed


def _contact_for(db: Session, invoice: Invoice) -> Contact | None:
    return db.get(Contact, invoice.contact_id) if invoice.contact_id else None


def send_invoice(db: Session, invoice: Invoice, actor: str, reminder: bool = False) -> OutboundEmail:
    contact = _contact_for(db, invoice)
    if contact is None or not contact.email:
        raise finance.FinanceError("This contact has no email address")
    if contact.suppressed:
        raise finance.FinanceError("This contact has asked not to be emailed")
    if invoice.status == "draft":
        raise finance.FinanceError("Issue the invoice before emailing it")
    settings = finance.all_settings(db)
    owed = finance.outstanding(db, invoice)
    club = settings["club_name"]
    kind = "Credit note" if invoice.kind == "credit_note" else "Invoice"
    if reminder:
        days = max(0, (date.today() - (invoice.due_date or date.today())).days)
        subject = f"Reminder: {kind.lower()} {invoice.reference} from {club}"
        intro = f"Our records show {kind.lower()} {invoice.reference} for £{invoice.amount_minor / 100:,.2f} is {days} day{'s' if days != 1 else ''} past its due date, with £{owed / 100:,.2f} outstanding."
    else:
        subject = f"{kind} {invoice.reference} from {club}"
        intro = f"Please find attached {kind.lower()} {invoice.reference} for £{abs(invoice.amount_minor) / 100:,.2f}."
        if invoice.due_date and invoice.kind != "credit_note":
            intro += f" It is due by {invoice.due_date.strftime('%d %B %Y')}."
    parts = [f"Dear {contact.name.split()[0] if contact.name else 'member'},", "", intro]
    for block in (settings["payment_instructions"], settings["bank_details"]):
        if block and invoice.status in finance.OPEN:
            parts += ["", block]
    online = payments.methods_for(db, invoice)
    if online:
        parts += ["", f"You can also pay online by {' or '.join(m.label for m in online)} (Direct Debit costs the club least, so please choose it if you can): {payments.pay_link(invoice, db)}"]
    parts += ["", "If you have already paid, thank you, and please ignore this message.", "", club]
    pdf = render_invoice_pdf(db, invoice)
    record = mailer.send_email(
        db,
        to=contact.email,
        subject=subject,
        body="\n".join(parts),
        attachment=(f"{kind.replace(' ', '-')}-{invoice.reference}.pdf", pdf, "application/pdf"),
        related_type="invoice_reminder" if reminder else "invoice",
        related_id=invoice.id,
        actor=actor,
        reply_to=settings["reply_to"] or None,
    )
    return record


def reminder_candidates(db: Session, today: date | None = None, min_days_overdue: int = 1) -> list[ReminderCandidate]:
    today = today or date.today()
    recent: dict[int, datetime] = {}
    for related_id, created in db.execute(
        select(OutboundEmail.related_id, OutboundEmail.created_at).where(OutboundEmail.related_type == "invoice_reminder", OutboundEmail.status.in_(["sent", "simulated"]))
    ):
        if related_id is not None and (related_id not in recent or created > recent[related_id]):
            recent[related_id] = created
    out = []
    for invoice in db.scalars(select(Invoice).where(Invoice.status.in_(finance.OPEN), func.coalesce(Invoice.kind, "invoice") == "invoice")):
        due = invoice.due_date or invoice.issue_date
        if due is None:
            continue
        days = (today - due).days
        contact = _contact_for(db, invoice)
        if days < min_days_overdue or contact is None:
            continue
        last = recent.get(invoice.id)
        last_day = last.date() if last else None
        if last_day and (today - last_day).days < REMINDER_GAP_DAYS:
            continue
        out.append(ReminderCandidate(invoice, contact, days, finance.outstanding(db, invoice), last_day))
    return sorted(out, key=lambda c: -c.days_overdue)


def send_reminders(db: Session, invoice_ids: list[int], actor: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"sent": [], "skipped": []}
    for invoice_id in invoice_ids:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None or invoice.status not in finance.OPEN:
            result["skipped"].append(f"#{invoice_id}: not open")
            continue
        if (invoice.due_date or invoice.issue_date or date.today()) >= date.today():
            result["skipped"].append(f"{invoice.reference}: not overdue")
            continue
        try:
            record = send_invoice(db, invoice, actor, reminder=True)
            if record.status == "failed":
                result["skipped"].append(f"{invoice.reference}: email failed ({record.error})")
            else:
                result["sent"].append(invoice.reference)
        except finance.FinanceError as exc:
            result["skipped"].append(f"{invoice.reference}: {exc}")
    return result



def send_receipt(db: Session, invoice: Invoice) -> None:
    """Hook: thank the payer when an invoice is paid in full (unless the invoice says not to, or the welcome email covers it)."""
    if not invoice.send_receipt or (invoice.kind or "invoice") != "invoice" or invoice.source == "imported":
        return
    if db.scalar(select(Application.id).where(Application.invoice_id == invoice.id)):
        return  # a new member's first payment already sends the welcome email
    contact = _contact_for(db, invoice)
    if contact is None or not contact.email or contact.suppressed:
        return
    settings = finance.all_settings(db)
    club = settings["club_name"]
    mailer.send_email(
        db,
        to=contact.email,
        subject=f"Thank you: invoice {invoice.reference} has been paid",
        body=f"Dear {contact.name.split()[0] if contact.name else 'member'},\n\nThank you: invoice {invoice.reference} for £{invoice.amount_minor / 100:,.2f} has been paid in full. No further action is needed.\n\n{club}",
        related_type="invoice_receipt",
        related_id=invoice.id,
        actor="system",
        reply_to=settings["reply_to"] or None,
    )


finance.PAID_HOOKS.append(send_receipt)
