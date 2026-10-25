"""Closing the books, the year-end pack, and the audit viewer with before and after values."""

import io
import zipfile
from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import cashbook, finance
from app.db import SessionLocal
from app.models import AuditLog, BankAccount, BankEntry, Contact, Invoice, Member, NominalCode, Role

TODAY = date.today()
LAST_YEAR_END = date(TODAY.year - 1, 12, 31)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0101", label="Membership"))
        member = Member(name="Ann Sailor", email="ann@example.org", membership_type="Single", renewal_status="active", active=True)
        session.add(member)
        session.flush()
        session.add(Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id))
        session.commit()
        yield session


def _issued(db, when: date, amount=10000):
    contact = db.scalar(select(Contact))
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Subs", "amount_minor": amount, "nominal_code": "0101"}])
    finance.issue(db, invoice, "t", issue_date=when)
    db.commit()
    return invoice


def test_nothing_dated_inside_a_closed_period_can_change(db) -> None:
    invoice = _issued(db, date(TODAY.year - 1, 6, 1))
    other = _issued(db, date(TODAY.year - 1, 7, 1))
    payment = finance.record_payment(db, other, amount_minor=5000, method="bank", received_on=date(TODAY.year - 1, 8, 1), actor="t")
    draft = finance.create_invoice(db, contact_id=db.scalar(select(Contact.id)), lines=[{"description": "Late", "amount_minor": 100, "nominal_code": "0101"}])
    db.commit()
    finance.set_lock(db, LAST_YEAR_END, "treasurer", is_admin=False)
    db.commit()
    assert finance.locked_until(db) == LAST_YEAR_END
    with pytest.raises(finance.FinanceError, match="books are closed up to"):
        finance.record_payment(db, invoice, amount_minor=1000, method="bank", received_on=date(TODAY.year - 1, 12, 30), actor="t")
    with pytest.raises(finance.FinanceError, match="books are closed"):
        finance.void_invoice(db, invoice, "t", "oops")
    with pytest.raises(finance.FinanceError, match="books are closed"):
        finance.reverse_payment(db, payment, "t", "bounced")
    with pytest.raises(finance.FinanceError, match="books are closed"):
        finance.issue(db, draft, "t", issue_date=LAST_YEAR_END)
    # corrections and new work are still possible
    finance.record_payment(db, invoice, amount_minor=1000, method="bank", received_on=TODAY, actor="t")
    finance.credit_note(db, invoice, amount_minor=500, reason="correction", actor="t")
    finance.issue(db, draft, "t", issue_date=TODAY)


def test_bank_lines_in_a_closed_period_cannot_be_recategorised_and_rules_skip_them(db) -> None:
    account = BankAccount(name="Bank account", kind="bank")
    db.add(account)
    db.flush()
    old = BankEntry(account_id=account.id, entry_date=LAST_YEAR_END - timedelta(days=3), details="AVIVA", reference="", amount_minor=-4500, status="unmatched", fingerprint="o1", source="demo")
    new = BankEntry(account_id=account.id, entry_date=TODAY, details="AVIVA", reference="", amount_minor=-4500, status="unmatched", fingerprint="n1", source="demo")
    db.add_all([old, new, NominalCode(code="0201", label="Insurance", kind="expense")])
    db.flush()
    cashbook.save_rule(db, pattern="AVIVA", kind="expense", nominal_code="0201", account_id=account.id, direction="out")
    finance.set_lock(db, LAST_YEAR_END, "t", is_admin=False)
    assert cashbook.apply_rules(db, "t") == 1 and old.status == "unmatched" and new.status == "categorised"
    with pytest.raises(cashbook.CashbookError, match="books are closed"):
        cashbook.categorise(db, old, [cashbook.Part("expense", -4500, "0201")], "t")


def test_only_an_administrator_can_reopen_and_the_lock_is_audited(db) -> None:
    finance.set_lock(db, LAST_YEAR_END, "treasurer", is_admin=False)
    finance.set_lock(db, LAST_YEAR_END + timedelta(days=30), "treasurer", is_admin=False)  # closing more is fine
    with pytest.raises(finance.FinanceError, match="Only an administrator"):
        finance.set_lock(db, LAST_YEAR_END, "treasurer", is_admin=False)
    with pytest.raises(finance.FinanceError, match="Only an administrator"):
        finance.set_lock(db, None, "treasurer", is_admin=False)
    with pytest.raises(finance.FinanceError, match="reason"):
        finance.set_lock(db, None, "admin", is_admin=True)
    for bad in (TODAY, TODAY + timedelta(days=1)):
        with pytest.raises(finance.FinanceError, match="before today"):
            finance.set_lock(db, bad, "admin", is_admin=True)
    finance.set_lock(db, None, "admin", is_admin=True, reason="examiner found an error")
    db.commit()
    assert finance.locked_until(db) is None
    logged = [(a.actor_email, a.details["before"], a.details["after"]) for a in db.scalars(select(AuditLog).where(AuditLog.action == "books.lock"))]
    assert logged[-1] == ("admin", (LAST_YEAR_END + timedelta(days=30)).isoformat(), None)


def test_the_lock_cannot_be_changed_through_the_general_settings_form(db) -> None:
    finance.save_settings(db, {"books_locked_until": "2030-01-01", "club_name": "X"})
    db.flush()
    assert finance.locked_until(db) is None and finance.get_setting(db, "club_name") == "X"


def test_year_end_screen_pack_and_lock_through_the_ui(db) -> None:
    _issued(db, date(TODAY.year - 1, 6, 1), 12345)
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    page = treasurer.get(f"/app/admin/year-end?year={TODAY.year - 1}")
    assert page.status_code == 200 and "Before you close the year" in page.text and "Reopen" not in page.text
    pack = treasurer.get(f"/app/admin/year-end/pack.zip?year={TODAY.year - 1}")
    assert pack.status_code == 200 and pack.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(pack.content)).namelist()
    assert {"sales-invoice-lines.csv", "payments-received.csv", "cash-book.csv", "debtors-at-year-end.csv", "income-by-nominal.csv", "members-now.csv", "README.txt"} <= set(names)
    sales = zipfile.ZipFile(io.BytesIO(pack.content)).read("sales-invoice-lines.csv").decode()
    assert "123.45" in sales
    token = csrf(treasurer, "/app/admin/year-end")
    closed = treasurer.post("/app/admin/year-end/lock", data={"csrf": token, "through": LAST_YEAR_END.isoformat()}, follow_redirects=True)
    assert "Books closed up to" in closed.text
    blocked = treasurer.post("/app/admin/year-end/lock", data={"csrf": token, "through": (LAST_YEAR_END - timedelta(days=100)).isoformat()}, follow_redirects=True)
    assert "Only an administrator" in blocked.text
    assert "Write the date" in treasurer.post("/app/admin/year-end/lock", data={"csrf": token, "through": "soon"}, follow_redirects=True).text
    admin = login("admin@example.org", Role.ADMIN)
    assert "Reopen" in admin.get("/app/admin/year-end").text
    reopened = admin.post("/app/admin/year-end/lock", data={"csrf": csrf(admin, "/app/admin/year-end"), "through": "", "reason": "fix"}, follow_redirects=True)
    assert "Books reopened" in reopened.text
    secretary = login("s@example.org", Role.COMMITTEE, hats="secretary")
    assert secretary.get("/app/admin/year-end").status_code == 403 and secretary.get("/app/admin/year-end/pack.zip").status_code == 403


def test_the_pack_defuses_formulae_in_text_cells(db) -> None:
    from app import yearend

    assert yearend._safe("=1+1") == "'=1+1" and yearend._safe("-12.50") == "-12.50" and yearend._safe("") == "" and yearend._safe("plain") == "plain"


def test_audit_viewer_filters_and_shows_before_and_after(db) -> None:
    admin = login("admin@example.org", Role.ADMIN)
    member_id = db.scalar(select(Member.id))
    token = csrf(admin, f"/app/members/{member_id}")
    admin.post(f"/app/members/{member_id}", data={"csrf": token, "name": "Ann Sailor", "email": "ann.new@example.org", "membership_type": "Family", "renewal_status": "active", "payment_status": "paid"}, follow_redirects=True)
    page = admin.get("/app/admin/audit?action=member")
    assert page.status_code == 200 and "member.update" in page.text and "ann.new@example.org" in page.text and "ann@example.org" in page.text and "Single" in page.text and "Family" in page.text and 'class="old"' in page.text
    assert "Nothing matches" in admin.get("/app/admin/audit?actor=nobody@example.org").text
    assert "member.update" in admin.get("/app/admin/audit?q=Family").text
    assert "Nothing matches" in admin.get("/app/admin/audit?q=zzzzz").text
    assert admin.get("/app/admin/audit?start=2999-01-01").status_code == 200 and admin.get("/app/admin/audit?start=garbage&page=99").status_code == 200
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    assert treasurer.get("/app/admin/audit").status_code == 403


def test_contact_and_settings_edits_record_what_changed(db) -> None:
    admin = login("admin@example.org", Role.ADMIN)
    contact_id = db.scalar(select(Contact.id))
    admin.post(f"/app/contacts/{contact_id}", data={"csrf": csrf(admin, f"/app/contacts/{contact_id}"), "name": "Ann Sailor", "email": "ann@example.org", "phone": "0777", "notes": ""}, follow_redirects=True)
    admin.post("/app/finance/settings", data={"csrf": csrf(admin, "/app/finance/settings"), "club_name": "Margate Yacht Club (MYC)"}, follow_redirects=True)
    db.expire_all()
    rows = {a.action: a.details for a in db.scalars(select(AuditLog).where(AuditLog.action.in_(["contact.update", "finance.settings"])))}
    assert rows["contact.update"]["changes"]["phone"] == {"before": None, "after": "0777"}
    assert rows["finance.settings"]["changes"]["club_name"]["after"] == "Margate Yacht Club (MYC)"
    assert Invoice  # imported for the fixtures above
