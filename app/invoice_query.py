"""Finding invoices: the filters on the Finance list, shared by the screen, the line-by-line view, bulk actions and exports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app import finance
from app.models import Contact, Invoice, InvoiceLine, Member, OutboundEmail

STATUS_FILTERS = [
    ("all", "All"),
    ("open", "Unpaid"),
    ("overdue", "Overdue"),
    ("late30", "30+ days late"),
    ("draft", "Drafts"),
    ("paid", "Paid"),
    ("written_off", "Written off"),
    ("void", "Void"),
    ("credit", "Credit notes"),
]
STATUS_LABELS = {"draft": "Draft", "issued": "Unpaid", "part_paid": "Part paid", "paid": "Paid", "written_off": "Written off", "void": "Void", "cancelled": "Cancelled"}


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", (status or "").replace("_", " ").capitalize())


@dataclass
class InvoiceFilter:
    filter: str = "all"
    q: str = ""
    date_from: date | None = None
    date_to: date | None = None
    nominal: str = ""
    source: str = ""
    tag: str = ""
    view: str = "invoices"

    def as_params(self) -> dict[str, str]:
        out = {"filter": self.filter, "q": self.q, "nominal": self.nominal, "source": self.source, "tag": self.tag, "view": self.view}
        if self.date_from:
            out["date_from"] = self.date_from.isoformat()
        if self.date_to:
            out["date_to"] = self.date_to.isoformat()
        return {k: v for k, v in out.items() if v and not (k == "filter" and v == "all") and not (k == "view" and v == "invoices")}


def _conditions(f: InvoiceFilter, today: date, lines: bool = False) -> list:
    unpaid = Invoice.status.in_(finance.OPEN)
    out: list = []
    if f.filter == "overdue":
        out += [unpaid, Invoice.due_date < today]
    elif f.filter == "late30":
        out += [unpaid, Invoice.due_date < today - timedelta(days=30)]
    elif f.filter == "open":
        out.append(unpaid)
    elif f.filter in {"draft", "paid", "written_off", "void"}:
        out.append(Invoice.status == f.filter)
    elif f.filter == "credit":
        out.append(Invoice.kind == "credit_note")
    if f.q.strip():
        term = f"%{f.q.strip()}%"
        out.append(or_(Invoice.reference.ilike(term), Contact.name.ilike(term), Member.name.ilike(term)))
    if f.date_from:
        out.append(Invoice.issue_date >= f.date_from)
    if f.date_to:
        out.append(Invoice.issue_date <= f.date_to)
    if lines:  # the line view already has the lines in hand
        if f.nominal:
            out.append(InvoiceLine.nominal_code == f.nominal)
        if f.source:
            out.append(InvoiceLine.source_type == f.source)
    else:
        if f.nominal:
            out.append(exists().where(InvoiceLine.invoice_id == Invoice.id, InvoiceLine.nominal_code == f.nominal))
        if f.source:
            out.append(exists().where(InvoiceLine.invoice_id == Invoice.id, InvoiceLine.source_type == f.source))
    if f.tag.strip():
        out.append(Invoice.tags.ilike(f"%{f.tag.strip().lower()}%"))
    return out


def invoices_statement(f: InvoiceFilter, today: date | None = None) -> Select:
    stmt = (
        select(Invoice, func.coalesce(Contact.name, Member.name))
        .outerjoin(Contact, Contact.id == Invoice.contact_id)
        .outerjoin(Member, Member.id == Invoice.member_id)
        .order_by(Invoice.issue_date.desc().nulls_first(), Invoice.id.desc())
    )
    for condition in _conditions(f, today or date.today()):
        stmt = stmt.where(condition)
    return stmt


def lines_statement(f: InvoiceFilter, today: date | None = None) -> Select:
    stmt = (
        select(InvoiceLine, Invoice, func.coalesce(Contact.name, Member.name))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .outerjoin(Contact, Contact.id == Invoice.contact_id)
        .outerjoin(Member, Member.id == Invoice.member_id)
        .order_by(Invoice.issue_date.desc().nulls_first(), Invoice.id.desc(), InvoiceLine.id)
    )
    for condition in _conditions(f, today or date.today(), lines=True):
        stmt = stmt.where(condition)
    return stmt


def totals(db: Session, f: InvoiceFilter, today: date | None = None) -> dict[str, int]:
    """Invoiced and outstanding for everything the filter matches (not only the current page)."""
    matched = invoices_statement(f, today).order_by(None).subquery()
    billed = db.scalar(select(func.coalesce(func.sum(matched.c.amount_minor), 0)).where(matched.c.status != "void")) or 0
    ids = [row[0] for row in db.execute(select(matched.c.id).where(matched.c.status.in_(finance.OPEN)))]
    owed = sum(finance.outstanding(db, inv) for inv in db.scalars(select(Invoice).where(Invoice.id.in_(ids)))) if ids else 0
    return {"count": db.scalar(select(func.count()).select_from(matched)) or 0, "billed": billed, "outstanding": owed}


def last_reminders(db: Session, invoice_ids: list[int]) -> dict[int, date]:
    """When each invoice was last chased by email."""
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(OutboundEmail.related_id, func.max(OutboundEmail.created_at))
        .where(OutboundEmail.related_type == "invoice_reminder", OutboundEmail.related_id.in_(invoice_ids), OutboundEmail.status.in_(["sent", "simulated"]))
        .group_by(OutboundEmail.related_id)
    )
    return {related: created.date() for related, created in rows if related is not None}


def sources(db: Session) -> list[str]:
    return [s for (s,) in db.execute(select(InvoiceLine.source_type).distinct().order_by(InvoiceLine.source_type)) if s]
