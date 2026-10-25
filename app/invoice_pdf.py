"""Invoice and credit-note PDFs."""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import finance
from app import payments as online_payments
from app.models import Contact, Invoice, InvoiceLine, PaymentTransaction


def _gbp(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}£{abs(minor) / 100:,.2f}"


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")


def render_invoice_pdf(db: Session, invoice: Invoice) -> bytes:
    settings = finance.all_settings(db)
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)).all()
    payments = db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id, PaymentTransaction.status == "received").order_by(PaymentTransaction.received_at)).all()
    is_credit = (invoice.kind or "invoice") == "credit_note"
    title = "CREDIT NOTE" if is_credit else ("DRAFT INVOICE" if invoice.status == "draft" else "INVOICE")

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=12)
    heading = ParagraphStyle("h", parent=styles["Title"], fontSize=20, alignment=0, spaceAfter=2)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title=f"{title} {invoice.reference}")
    story: list = [Paragraph(_escape(settings["club_name"]), heading)]
    if settings["club_address"]:
        story.append(Paragraph(_escape(settings["club_address"]), small))
    story += [Spacer(1, 8 * mm), Paragraph(f"<b>{title}</b>", styles["Heading2"])]

    meta = [
        ["Number", invoice.reference if invoice.status != "draft" else "(not yet issued)"],
        ["Date", invoice.issue_date.strftime("%d %B %Y") if invoice.issue_date else ""],
    ]
    if not is_credit:
        meta.append(["Due", invoice.due_date.strftime("%d %B %Y") if invoice.due_date else ""])
    left = Paragraph("<b>Bill to</b><br/>" + _escape(contact.name if contact else "") + ("<br/>" + _escape(contact.address) if contact and contact.address else ""), small)
    right = Table(meta, colWidths=[22 * mm, 45 * mm])
    right.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (0, -1), colors.grey)]))
    head = Table([[left, right]], colWidths=[95 * mm, 70 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 8 * mm)]

    rows = [["Description", "Qty", "Unit price", "Amount"]]
    for line in lines:
        qty = "" if line.quantity is None else f"{line.quantity:g}"
        unit = "" if line.unit_price_minor is None or line.quantity in (None, 1.0) else _gbp(line.unit_price_minor)
        rows.append([Paragraph(_escape(line.description), small), qty, unit, _gbp(line.amount_minor)])
    rows.append(["", "", "Total", _gbp(invoice.amount_minor)])
    paid = sum(p.amount_minor for p in payments)
    if payments and not is_credit:
        for p in payments:
            rows.append(["", "", f"Received {p.received_at.strftime('%d %b %Y')}", _gbp(-p.amount_minor)])
        rows.append(["", "", "Balance due", _gbp(max(0, invoice.amount_minor - paid) if invoice.status in finance.OPEN else 0)])
    table = Table(rows, colWidths=[88 * mm, 18 * mm, 32 * mm, 30 * mm], repeatRows=1)
    last_lines = len(lines)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b3d6e")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, last_lines), 0.25, colors.lightgrey),
                ("FONTNAME", (2, last_lines + 1), (-1, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story += [table, Spacer(1, 8 * mm)]
    if invoice.message:
        story += [Paragraph(_escape(invoice.message), small), Spacer(1, 4 * mm)]
    if invoice.status == "paid" and not is_credit:
        story.append(Paragraph("<b>PAID</b>, thank you.", styles["Normal"]))
    if not is_credit and invoice.status in finance.OPEN:
        for block in (settings["payment_instructions"], settings["bank_details"]):
            if block:
                story += [Paragraph(_escape(block), small), Spacer(1, 3 * mm)]
    if not is_credit and invoice.status in finance.OPEN and online_payments.methods_for(db, invoice):
        story += [Paragraph(f"<b>Pay online</b>: {_escape(online_payments.pay_link(invoice, db))}", small), Spacer(1, 3 * mm)]
    if not is_credit and invoice.status in finance.OPEN:
        slip = Table(
            [[Paragraph(f"<b>Remittance slip</b>: please quote invoice number <b>{_escape(invoice.reference)}</b> and return this slip with your payment.", small), f"Amount due: {_gbp(max(0, invoice.amount_minor - paid))}"]],
            colWidths=[120 * mm, 48 * mm],
        )
        slip.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.grey), ("FONTSIZE", (1, 0), (1, 0), 10), ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story += [Spacer(1, 10 * mm), slip]
    if settings["invoice_footer"]:
        story += [Spacer(1, 6 * mm), Paragraph(_escape(settings["invoice_footer"]), small)]
    doc.build(story)
    return buffer.getvalue()
