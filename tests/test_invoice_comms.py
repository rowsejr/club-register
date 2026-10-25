from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import finance, invoice_comms, mailer
from app.db import SessionLocal
from app.invoice_pdf import render_invoice_pdf
from app.models import Contact, Invoice, Member, OutboundEmail


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _invoice(db, name="Ann Sailor", email="ann@example.com", amount=10800, suppressed=False) -> Invoice:
    member = Member(name=name, email=email)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id, suppressed=suppressed, address="1 High St, Margate")
    db.add(contact)
    db.flush()
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Membership 2027", "amount_minor": amount}], actor="t")
    return finance.issue(db, invoice, "t")


def test_pdf_is_a_valid_document_for_invoices_drafts_and_credit_notes(db) -> None:
    finance.save_settings(db, {"club_name": "Margate Yacht Club", "bank_details": "Sort code 00-00-00\nAccount 12345678"})
    invoice = _invoice(db)
    finance.record_payment(db, invoice, amount_minor=3000, method="bank", reference="R1")
    pdf = render_invoice_pdf(db, invoice)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500
    draft = finance.create_invoice(db, contact_id=invoice.contact_id, lines=[{"description": "Berth <b>&</b>", "amount_minor": 500}], actor="t")
    assert render_invoice_pdf(db, draft).startswith(b"%PDF")  # markup in descriptions cannot break rendering
    note = finance.credit_note(db, invoice, amount_minor=1000, reason="Goodwill", actor="t")
    assert render_invoice_pdf(db, note).startswith(b"%PDF")


def test_sandbox_mail_is_logged_not_sent_and_invoice_email_carries_the_pdf(db) -> None:
    finance.save_settings(db, {"payment_instructions": "Pay by bank transfer quoting the invoice number.", "reply_to": "treasurer@myc.example"})
    invoice = _invoice(db)
    record = invoice_comms.send_invoice(db, invoice, "treasurer@example.com")
    assert record.status == "simulated" and record.provider == "sandbox" and record.to_address == "ann@example.com"
    assert record.attachment_name == f"Invoice-{invoice.reference}.pdf" and record.related_type == "invoice" and record.related_id == invoice.id
    assert "Dear Ann," in record.body and "quoting the invoice number" in record.body and "10,800" not in record.body and "108.00" in record.body


def test_cannot_email_drafts_or_people_without_an_address_or_who_opted_out(db) -> None:
    draft = finance.create_invoice(db, contact_id=_invoice(db).contact_id, lines=[{"description": "x", "amount_minor": 1}], actor="t")
    with pytest.raises(finance.FinanceError, match="Issue the invoice"):
        invoice_comms.send_invoice(db, draft, "t")
    with pytest.raises(finance.FinanceError, match="no email"):
        invoice_comms.send_invoice(db, _invoice(db, "Bob", None), "t")  # type: ignore[arg-type]
    with pytest.raises(finance.FinanceError, match="not to be emailed"):
        invoice_comms.send_invoice(db, _invoice(db, "Cara", "cara@example.com", suppressed=True), "t")


def test_reminders_select_overdue_open_invoices_and_respect_the_gap(db) -> None:
    today = date.today()
    late = _invoice(db, "Ann Sailor", "ann@example.com")
    late.due_date = today - timedelta(days=40)
    fresh = _invoice(db, "Bob Tiller", "bob@example.com")
    fresh.due_date = today + timedelta(days=5)
    paid = _invoice(db, "Cara Jib", "cara@example.com")
    paid.due_date = today - timedelta(days=90)
    finance.record_payment(db, paid, amount_minor=paid.amount_minor, method="bank")
    quiet = _invoice(db, "Dev Halyard", "dev@example.com", suppressed=True)
    quiet.due_date = today - timedelta(days=10)

    candidates = invoice_comms.reminder_candidates(db, today)
    assert [c.invoice.id for c in candidates] == [late.id, quiet.id]
    assert candidates[0].days_overdue == 40 and candidates[0].can_email and not candidates[1].can_email

    result = invoice_comms.send_reminders(db, [late.id, quiet.id, fresh.id, 9999], "t")
    assert result["sent"] == [late.reference] and len(result["skipped"]) == 3
    sent = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "invoice_reminder"))
    assert sent and "past its due date" in sent.body and sent.subject.startswith("Reminder:")
    assert late.id not in [c.invoice.id for c in invoice_comms.reminder_candidates(db, date.today())]  # not nagged again within the gap


def test_smtp_provider_records_failures_without_raising(db, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_EMAIL_PROVIDER", "smtp")
    monkeypatch.delenv("CLUB_SMTP_HOST", raising=False)
    record = mailer.send_email(db, to="x@example.com", subject="Hi", body="Body")
    assert record.status == "failed" and "CLUB_SMTP_HOST" in (record.error or "")

    sent: list = []
    monkeypatch.setenv("CLUB_SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(mailer, "_smtp_send", lambda message: sent.append(message))
    ok = mailer.send_email(db, to="x@example.com", subject="Hi", body="Body", attachment=("a.pdf", b"%PDF-1", "application/pdf"), reply_to="r@example.com")
    assert ok.status == "sent" and sent[0]["To"] == "x@example.com" and sent[0]["Reply-To"] == "r@example.com"
    assert sent[0].get_payload()[1].get_filename() == "a.pdf"


