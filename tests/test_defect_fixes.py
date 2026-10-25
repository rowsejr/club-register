from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import func, select, text

from app import banking, finance, invoice_comms, privacy
from app.db import SessionLocal, engine
from app.models import (
    Attachment,
    Contact,
    Invoice,
    ListMembership,
    Member,
    OutboundEmail,
    Qualification,
    Role,
    SessionRecord,
    UserRole,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


def _person(db, name="Ann Sailor", email="ann@example.com") -> tuple[Member, Contact]:
    member = Member(name=name, email=email)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id, phone="0123", address="1 High St", birth_date=date(1990, 1, 1), notes="private note")
    db.add(contact)
    db.flush()
    return member, contact


def test_member_without_email_can_be_created_edited_and_listed(admin: TestClient) -> None:
    token = _csrf(admin, "/app/members/new")
    created = admin.post("/app/members", data={"csrf": token, "name": "No Email Nick", "email": ""}, follow_redirects=False)
    assert created.status_code == 303
    detail = admin.get(created.headers["location"])
    assert "No Email Nick" in detail.text
    saved = admin.post(created.headers["location"].split("?")[0], data={"csrf": token, "name": "Nick Renamed", "email": "", "membership_type": "Family"}, follow_redirects=True)
    assert "Nick Renamed" in saved.text and "Family" in saved.text




def test_billing_run_never_bills_everyone_by_accident(admin: TestClient) -> None:
    from test_finance_ui import _person as person

    person("Ann Sailor", "ann@example.com")
    with SessionLocal() as db:
        from app import membership

        membership.sync_types(db)
        period = membership.create_period(db, 2027)
        membership.prepare(db, period)
        db.commit()
        period_id = period.id
    token = _csrf(admin, "/app/finance/billing")
    refused = admin.post(f"/app/finance/billing/{period_id}/bill", data={"csrf": token}, follow_redirects=True)
    assert "Tick the members to bill" in refused.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Invoice)) == 0
    ok = admin.post(f"/app/finance/billing/{period_id}/bill", data={"csrf": token, "everyone": "1"}, follow_redirects=True)
    assert "1 invoiced" in ok.text


def test_lapsed_members_can_sign_in_to_renew_and_see_only_their_own_visible_invoices(admin: TestClient) -> None:
    with SessionLocal() as db:
        member, contact = _person(db)
        member.active, member.renewal_status = False, "lapsed"
        shown = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Membership", "amount_minor": 5000}], actor="t")
        finance.issue(db, shown, "t")
        finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Draft being prepared", "amount_minor": 999}], actor="t")  # draft
        voided = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Voided one", "amount_minor": 777}], actor="t")
        finance.issue(db, voided, "t")
        finance.void_invoice(db, voided, "t", "error")
        db.commit()
    lapsed = _login("ann@example.com")
    page = lapsed.get("/app")
    assert "£50.00" in page.text and "£9.99" not in page.text and "£7.77" not in page.text
    with SessionLocal() as db:
        ids = {i.amount_minor: i.id for i in db.scalars(select(Invoice))}
    assert lapsed.get(f"/app/me/invoices/{ids[999]}").status_code == 404
    assert lapsed.get(f"/app/me/invoices/{ids[5000]}").status_code == 200


def test_changing_or_removing_a_role_ends_that_persons_sessions(admin: TestClient) -> None:
    with SessionLocal() as db:
        db.add(UserRole(email="sec@example.com", role=Role.COMMITTEE))
        db.commit()
    secretary = _login("sec@example.com")
    assert secretary.get("/app/members").status_code == 200
    token = _csrf(admin, "/app/admin/users")
    admin.post("/app/admin/users", data={"csrf": token, "email": "sec@example.com", "role": "Member"})
    assert secretary.get("/app/members", follow_redirects=False).status_code == 303  # session revoked: must sign in again
    with SessionLocal() as db:
        assert all(s.revoked_at is not None for s in db.scalars(select(SessionRecord).where(SessionRecord.email == "sec@example.com")))


def test_bank_linked_and_credit_note_payments_cannot_be_reversed_from_the_invoice(admin: TestClient) -> None:
    with SessionLocal() as db:
        _, contact = _person(db)
        invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 10000}], actor="t")
        finance.issue(db, invoice, "t")
        account = banking.get_or_create_account(db, "Current")
        banking.import_statement(db, account, "Date,Description,Amount\n01/10/2026,PAYMENT,40.00\n")
        entry = banking.match_one(db, db.scalar(select(banking.BankEntry)), invoice, "t")
        credit = finance.credit_note(db, invoice, amount_minor=1000, reason="Goodwill", actor="t")
        credit_payment = db.scalar(select(finance.PaymentTransaction).where(finance.PaymentTransaction.payment_method == "credit_note"))
        db.commit()
        bank_payment_id, credit_payment_id, entry_id = entry.id, credit_payment.id, entry.bank_entry_id
        assert credit.status == "paid"
    token = _csrf(admin, "/app/finance")
    blocked = admin.post(f"/app/finance/payments/{bank_payment_id}/reverse", data={"csrf": token, "reason": "x"}, follow_redirects=True)
    assert "un-match the line" in blocked.text and f"/bank/entries/{entry_id}" in str(blocked.url)
    credit_blocked = admin.post(f"/app/finance/payments/{credit_payment_id}/reverse", data={"csrf": token, "reason": "x"}, follow_redirects=True)
    assert "cannot be reversed" in credit_blocked.text


def test_reminder_failures_are_reported_not_counted_as_sent(db, monkeypatch) -> None:
    _, contact = _person(db)
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 100}], actor="t")
    finance.issue(db, invoice, "t", issue_date=date(2020, 1, 1))
    monkeypatch.setenv("CLUB_EMAIL_PROVIDER", "smtp")
    monkeypatch.delenv("CLUB_SMTP_HOST", raising=False)
    result = invoice_comms.send_reminders(db, [invoice.id], "t")
    assert result["sent"] == [] and "email failed" in result["skipped"][0]
    assert db.scalar(select(OutboundEmail.status).where(OutboundEmail.related_type == "invoice_reminder")) == "failed"


def test_erase_anonymises_everywhere_but_keeps_financial_records(admin: TestClient) -> None:
    with SessionLocal() as db:
        member, contact = _person(db)
        db.add(Attachment(owner_contact_id=contact.id, filename="id.pdf", uri="https://example.com/id.pdf"))
        db.add(Qualification(contact_id=contact.id, name="PB2"))
        db.add(UserRole(email="ann@example.com", role=Role.COMMITTEE))
        invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 5000}], actor="t")
        finance.issue(db, invoice, "t")
        invoice_comms.send_invoice(db, invoice, "t")
        db.commit()
        cid, mid, iid = contact.id, member.id, invoice.id
    exported = admin.get(f"/app/contacts/{cid}/export.json")
    assert exported.status_code == 200 and "ann@example.com" in exported.text and "private note" in exported.text and "PB2" in exported.text
    token = _csrf(admin, f"/app/contacts/{cid}")
    assert "Type ERASE" in admin.post(f"/app/contacts/{cid}/erase", data={"csrf": token, "confirm": "no"}, follow_redirects=True).text
    admin.post(f"/app/contacts/{cid}/erase", data={"csrf": token, "confirm": "ERASE"})
    with SessionLocal() as db:
        contact, member = db.get(Contact, cid), db.get(Member, mid)
        assert contact.name.startswith("Erased person") and contact.email is None and contact.phone is None and contact.address is None and contact.birth_date is None and contact.notes == ""
        assert member.email is None and member.active is False and member.name.startswith("Erased person")
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0 and db.scalar(select(func.count()).select_from(Qualification)) == 0
        assert db.scalar(select(func.count()).select_from(UserRole).where(UserRole.email == "ann@example.com")) == 0
        mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "invoice"))
        assert mail.to_address == "erased@invalid" and mail.body == "(erased)"
        invoice = db.get(Invoice, iid)
        assert invoice.amount_minor == 5000 and invoice.contact_id == cid  # the financial record survives
        assert db.scalar(select(func.count()).select_from(ListMembership)) == 0
        assert privacy.export_person(db, contact_id=cid)["contact"]["name"].startswith("Erased person")
        assert privacy.erase_person(db, contact_id=cid, actor="t")["contact"] == 1  # safe to repeat
    assert admin.get("/app/contacts/9999/export.json").status_code == 404






def test_sqlite_runs_in_wal_mode_with_indexes() -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        names = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))}
    assert {"ix_invoices_status_due", "ix_bank_entries_account_status", "ix_payments_invoice"} <= names
