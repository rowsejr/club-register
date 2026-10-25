"""The demo copy of the database must keep the numbers and lose every personal detail."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import anonymise
from app.db import DATABASE_URL, SessionLocal
from app.models import (
    AppSetting,
    BankAccount,
    BankEntry,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    NominalCode,
    ParticipantProfile,
    Role,
    UserRole,
)

REAL_NAME, REAL_EMAIL, REAL_PHONE = "Hesper Quillfeather", "hesper.q@realmail.test", "07911 123456"  # scan: allow (a made-up number that only looks real)


@pytest.fixture
def source() -> Path:
    with SessionLocal() as db:
        member = Member(name=REAL_NAME, email=REAL_EMAIL)
        db.add(member)
        db.flush()
        contact = Contact(
            member_id=member.id,
            name=REAL_NAME,
            email=REAL_EMAIL,
            phone=REAL_PHONE,
            address="1 Real Street",
            birth_date=date(1980, 5, 5),
            notes="allergic",
        )
        db.add(contact)
        db.flush()
        invoice = Invoice(
            reference="7",
            amount_minor=12345,
            status="paid",
            contact_id=contact.id,
            notes=f"Chased {REAL_NAME} on the phone",
        )
        db.add(invoice)
        db.flush()
        db.add(
            InvoiceLine(
                invoice_id=invoice.id,
                source_type="imported",
                source_id="1",
                description=f"Membership 2026 for {REAL_NAME}",
                amount_minor=12345,
            )
        )
        account = BankAccount(name="Bank account", closing_balance_minor=12345)
        db.add(account)
        db.flush()
        db.add(
            BankEntry(
                account_id=account.id, entry_date=date(2026, 1, 2), reference=REAL_NAME, details=f"Invoice 7 - {REAL_NAME}", counterparty=REAL_NAME, amount_minor=12345,
                balance_minor=12345, status="historical", fingerprint="abc",
            )
        )  # fmt: skip
        db.add(
            ParticipantProfile(
                contact_id=contact.id,
                emergency_name="Corvin Harrowgate",
                emergency_phone="07922 654321",  # scan: allow (a made-up number that only looks real)
                medical="asthma",
                allergies="nuts",
            )
        )
        db.add(
            UserRole(
                email="real.treasurer@realmail.test", role=Role.COMMITTEE, extra_roles="treasurer"  # scan: allow
            )
        )
        db.add(AppSetting(key="form_secret", value="5ec2e7" * 10))
        db.commit()
    return Path(DATABASE_URL.removeprefix("sqlite:///"))


def _all_text(path: Path) -> str:
    return path.read_bytes().decode("latin-1").lower()


def test_no_real_detail_survives_and_the_numbers_do(source, tmp_path) -> None:
    out = tmp_path / "demo.db"
    report = anonymise.anonymise(source, out, seed=1)
    assert report.clean and not report.warnings
    raw = _all_text(out)
    for secret in (
        REAL_NAME,
        REAL_EMAIL,
        "07911 123456",  # scan: allow (a made-up number that only looks real)
        "1 real street",
        "corvin",
        "harrowgate",
        "asthma",
        "nuts",
        "realmail",
    ):
        assert secret.lower() not in raw, secret
    conn = sqlite3.connect(out)
    assert conn.execute("select count(*), sum(amount_minor) from invoices").fetchone() == (1, 12345)
    assert conn.execute("select sum(amount_minor) from invoice_lines").fetchone() == (12345,)
    assert conn.execute("select closing_balance_minor from bank_accounts").fetchone() == (12345,)
    assert conn.execute("select details from bank_entries").fetchone() == (
        "Invoice 7",
    )  # kept, so the ledger-vs-invoice check still works
    assert conn.execute("select medical, allergies from participant_profiles").fetchone() == (
        None,
        None,
    )
    assert conn.execute("select email from contacts").fetchone()[0].endswith("@example.org")
    conn.close()


def test_one_person_gets_one_pseudonym_everywhere(source, tmp_path) -> None:
    out = tmp_path / "demo.db"
    anonymise.anonymise(source, out, seed=1)
    conn = sqlite3.connect(out)
    member, contact, payer = (
        conn.execute(q).fetchone()[0]
        for q in (
            "select name from members",
            "select name from contacts",
            "select counterparty from bank_entries",
        )
    )
    assert member == contact == payer != REAL_NAME
    assert (
        conn.execute("select description from invoice_lines")
        .fetchone()[0]
        .startswith("Membership 2026 for")
    )
    conn.close()


def test_sign_ins_are_replaced_with_fictional_accounts_for_each_role(source, tmp_path) -> None:
    out = tmp_path / "demo.db"
    report = anonymise.anonymise(source, out, seed=1)
    conn = sqlite3.connect(out)
    emails = {row[0] for row in conn.execute("select email from user_roles")}
    assert "real.treasurer@realmail.test" not in emails  # scan: allow
    assert {
        "admin@example.org",
        "treasurer@example.org",
        "secretary@example.org",
        "bosun@example.org",
        "training@example.org",
    } <= emails
    assert {email for email, _ in report.accounts} == emails
    conn.close()


def test_a_leak_the_scan_finds_deletes_the_copy(source, tmp_path) -> None:
    with SessionLocal() as db:
        db.add(
            NominalCode(code="9999", label=f"Fund for {REAL_NAME}")
        )  # a column the anonymiser does not know to clean
        db.commit()
    out = tmp_path / "demo.db"
    report = anonymise.anonymise(source, out, seed=1)
    assert not report.clean and "nominal_codes.label" in report.leaks
    assert not out.exists()


def test_newer_tables_are_cleaned_too(source, tmp_path) -> None:
    import app.models as m

    with SessionLocal() as db:
        contact = db.scalar(select(m.Contact))
        contact.phones_extra, contact.addresses_extra = "07911 654321", "9 Real Road"  # scan: allow (a made-up number that only looks real)
        db.add_all(
            [
                m.RecurringFee(contact_id=contact.id, name=f"Locker for {REAL_NAME}", amount_minor=1000, note="Corvin Harrowgate pays"),
                m.Mailing(subject=f"Hello {REAL_NAME}", body="x", audience={}, skipped=[f"{REAL_NAME}: no email"]),
                m.JobRun(job="weekly_digests", summary={"told": REAL_EMAIL}),
                m.BankRule(pattern=REAL_NAME.upper(), kind="expense", nominal_code="0201", payee=REAL_NAME),
                m.SavedView(scope="members", name="x", query=f"q={REAL_NAME}"),
            ]
        )
        db.commit()
    out = tmp_path / "demo.db"
    report = anonymise.anonymise(source, out, seed=1)
    assert report.clean
    raw = _all_text(out)
    for secret in (REAL_NAME, "07911 654321", "9 real road", "corvin", "harrowgate"):  # scan: allow (a made-up number that only looks real)
        assert secret.lower() not in raw, secret
    conn = sqlite3.connect(out)
    assert conn.execute("select count(*) from mailings").fetchone() == (0,) and conn.execute("select count(*) from job_runs").fetchone() == (0,)
    assert conn.execute("select phones_extra from contacts").fetchone()[0].startswith("07700 900")
    conn.close()


def test_it_refuses_to_overwrite_or_to_anonymise_in_place(source, tmp_path) -> None:
    with pytest.raises(ValueError):
        anonymise.anonymise(source, source)
    existing = tmp_path / "demo.db"
    existing.write_text("x")
    with pytest.raises(FileExistsError):
        anonymise.anonymise(source, existing)
    assert existing.read_text() == "x"
