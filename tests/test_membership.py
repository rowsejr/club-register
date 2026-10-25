from datetime import date

import pytest
from sqlalchemy import func, select

from app import finance, membership
from app.db import SessionLocal
from app.models import Contact, Invoice, InvoiceLine, Member, Membership, MembershipType


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _member(db, name, plan, status="active", year=2026, email=None) -> Member:
    member = Member(name=name, email=email or f"{name.split()[0].lower()}@example.com", membership_type=plan, active=status == "active")
    db.add(member)
    db.flush()
    db.add(Contact(name=name, email=member.email, member_id=member.id))
    db.add(Membership(member_id=member.id, plan_name=plan, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status=status))
    db.flush()
    return member


def test_sync_types_creates_one_per_plan_and_applies_known_fees(db) -> None:
    _member(db, "Ann One", "Single Adult Sailing")
    _member(db, "Bob Two", "Single Adult Sailing")
    _member(db, "Cara Three", "Honorary Member")
    assert membership.sync_types(db) == 2
    types = {t.name: t for t in db.scalars(select(MembershipType))}
    assert types["Single Adult Sailing"].fee_minor == 10800 and types["Honorary Member"].fee_minor == 0
    types["Single Adult Sailing"].fee_minor = 12000  # a treasurer's edit survives a later sync
    membership.sync_types(db)
    assert types["Single Adult Sailing"].fee_minor == 12000


def test_prepare_preview_and_billing_run_are_idempotent(db) -> None:
    ann = _member(db, "Ann One", "Single Adult Sailing")
    _member(db, "Bob Two", "Sailing Adult + Partner")
    _member(db, "Cara Three", "Honorary Member")
    _member(db, "Dev Four", "Single Adult Sailing", status="resigned")
    _member(db, "Eve Five", "Mystery Plan")
    membership.sync_types(db)
    db.scalar(select(MembershipType).where(MembershipType.name == "Honorary Member")).fee_minor = 0
    period = membership.create_period(db, 2027)

    rows = {r.member.name: r for r in membership.preview(db, period)}
    assert set(rows) == {"Ann One", "Bob Two", "Cara Three", "Eve Five"}  # the resigned member is not due
    assert rows["Ann One"].fee_minor == 10800 and rows["Eve Five"].note == "No fee set for this type"

    assert membership.prepare(db, period).prepared == 4
    assert membership.prepare(db, period).prepared == 0

    report = membership.run_billing(db, period, "treasurer@example.com")
    assert report.confirmed == 3 and report.invoiced == 2  # honorary is free; Eve has no type
    assert any("Eve Five" in s for s in report.skipped)
    invoices = db.scalars(select(Invoice).order_by(Invoice.id)).all()
    assert [i.amount_minor for i in invoices] == [10800, 15300] and all(i.status == "issued" and i.reference.isdigit() for i in invoices)
    line = db.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoices[0].id))
    assert line and line.nominal_code == "0101" and "2027" in line.description

    ann_2027 = db.scalar(select(Membership).where(Membership.member_id == ann.id, Membership.starts_on == date(2027, 1, 1)))
    assert ann_2027 and ann_2027.status == "active" and ann.expiry_date == date(2027, 12, 31)

    again = membership.run_billing(db, period, "treasurer@example.com")
    assert again.confirmed == 0 and again.invoiced == 0
    assert db.scalar(select(func.count()).select_from(Invoice)) == 2  # nothing billed twice
    assert db.scalar(select(func.count()).select_from(Membership).where(Membership.starts_on == date(2027, 1, 1))) == 3


def test_billing_selected_members_only_and_draft_mode(db) -> None:
    ann = _member(db, "Ann One", "Single Adult Sailing")
    bob = _member(db, "Bob Two", "Single Adult Sailing")
    membership.sync_types(db)
    period = membership.create_period(db, 2027)
    membership.prepare(db, period)
    report = membership.run_billing(db, period, "t", member_ids=[ann.id], issue=False)
    assert report.invoiced == 1
    invoice = db.scalar(select(Invoice))
    assert invoice and invoice.status == "draft" and invoice.reference.startswith("DRAFT-")
    assert db.scalar(select(Membership).where(Membership.member_id == bob.id, Membership.starts_on == date(2027, 1, 1))) is None
    finance.issue(db, invoice, "t")  # the draft can be reviewed, then issued


def test_decline_resigns_the_member(db) -> None:
    ann = _member(db, "Ann One", "Single Adult Sailing")
    membership.sync_types(db)
    period = membership.create_period(db, 2027)
    membership.prepare(db, period)
    renewal = membership.preview(db, period)[0].renewal
    membership.decline(db, renewal)
    assert ann.active is False and ann.renewal_status == "resigned"
    assert membership.run_billing(db, period, "t").confirmed == 0


def test_missing_fee_is_reported_not_billed(db) -> None:
    _member(db, "Ann One", "Odd Plan")
    membership.ensure_type(db, "Odd Plan")
    period = membership.create_period(db, 2027)
    membership.prepare(db, period)
    report = membership.run_billing(db, period, "t")
    assert report.invoiced == 0 and "No fee is set" in report.skipped[0]
    assert db.scalar(select(func.count()).select_from(Invoice)) == 0


