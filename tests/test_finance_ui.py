import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import select

from app import finance
from app.db import SessionLocal
from app.models import (
    BankEntry,
    Contact,
    Invoice,
    Member,
    Membership,
    MembershipType,
    NominalCode,
    Role,
)


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


def _person(name="Ann Sailor", email="ann@example.com", plan="Single Adult Sailing", year=2026) -> int:
    with SessionLocal() as db:
        member = Member(name=name, email=email, membership_type=plan)
        db.add(member)
        db.flush()
        contact = Contact(name=name, email=email, member_id=member.id)
        db.add(contact)
        db.add(Membership(member_id=member.id, plan_name=plan, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status="active"))
        if not db.scalar(select(NominalCode.id).where(NominalCode.code == "0101")):
            db.add(NominalCode(code="0101", label="Membership Fees"))
        db.commit()
        return contact.id


def _first_invoice_id() -> int:
    with SessionLocal() as db:
        return db.scalars(select(Invoice.id).order_by(Invoice.id)).first() or 0


def test_treasurer_invoice_journey(admin: TestClient) -> None:
    contact_id = _person()
    token = _csrf(admin, "/app/finance/new")
    search = admin.get("/app/finance/new?q=ann")
    assert "Ann Sailor" in search.text and f"contact_id={contact_id}" in search.text
    form = admin.get(f"/app/finance/new?contact_id={contact_id}")
    assert "Bill to" in form.text and "Ann Sailor" in form.text and "Add a line" in form.text

    bad = admin.post("/app/finance/new", data={"csrf": token, "contact_id": contact_id, "action": "issue", "desc": ["Membership", ""], "unit": ["abc", ""], "qty": ["1", ""], "nominal": ["0101", ""]}, follow_redirects=True)
    assert bad.status_code == 422 and "Unrecognised amount" in bad.text and "Bill to" in bad.text  # back on the form, with the message
    no_nominal = admin.post("/app/finance/new", data={"csrf": token, "contact_id": contact_id, "action": "issue", "desc": ["Membership"], "unit": ["10"], "qty": ["1"], "nominal": [""]})
    assert no_nominal.status_code == 422 and "needs a nominal code" in no_nominal.text
    created = admin.post(
        "/app/finance/new",
        data={"csrf": token, "contact_id": contact_id, "action": "issue", "desc": ["Membership 2027", "Berth", ""], "unit": ["108.00", "10", ""], "qty": ["1", "2", ""], "nominal": ["0101", "0101", ""]},
        follow_redirects=False,
    )
    assert created.status_code == 303
    page = admin.get(created.headers["location"])
    assert "Invoice 1001" in page.text and "£128.00" in page.text and "Record a payment" in page.text and "Add line" not in page.text
    invoice_id = _first_invoice_id()
    base = f"/app/finance/invoices/{invoice_id}"

    over = admin.post(base + "/payment", data={"csrf": token, "amount": "500", "method": "bank"}, follow_redirects=True)
    assert "more than the 128.00 outstanding" in over.text
    ok = admin.post(base + "/payment", data={"csrf": token, "amount": "28.00", "method": "bank", "reference": "FP-1", "received_on": "2026-10-01"}, follow_redirects=True)
    assert "Payment recorded" in ok.text and "part_paid" in ok.text and "£100.00" in ok.text
    dup = admin.post(base + "/payment", data={"csrf": token, "amount": "1", "method": "bank", "reference": "FP-1"}, follow_redirects=True)
    assert "already been recorded" in dup.text

    credit = admin.post(base + "/credit", data={"csrf": token, "amount": "20", "reason": "Berth not used"}, follow_redirects=True)
    assert "Credit note issued" in credit.text and "CN-0001" in credit.text and "£80.00" in credit.text

    pdf = admin.get(base + ".pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and pdf.headers["content-type"] == "application/pdf"
    emailed = admin.post(base + "/email", data={"csrf": token}, follow_redirects=True)
    assert "Invoice emailed" in emailed.text and "simulated" in emailed.text
    assert "Invoice 1001" in admin.get("/app/finance/emails").text.replace("from Margate", "").replace("Invoice 1001 from", "Invoice 1001") or "1001" in admin.get("/app/finance/emails").text

    listing = admin.get("/app/finance?q=1001")
    assert "1001" in listing.text and "Ann Sailor" in listing.text
    assert "CN-0001" in admin.get("/app/finance?filter=credit").text
    assert admin.get("/app/finance?filter=overdue").status_code == 200

    written = admin.post(base + "/write-off", data={"csrf": token, "reason": "Cannot collect"}, follow_redirects=True)
    assert "Written off" in written.text and "written_off" in written.text
    assert "Cannot collect" in written.text


def test_draft_editing_and_voiding(admin: TestClient) -> None:
    contact_id = _person()
    token = _csrf(admin, "/app/finance/new")
    admin.post("/app/finance/new", data={"csrf": token, "contact_id": contact_id, "action": "draft", "desc": ["Item"], "unit": ["10"], "qty": ["1"], "nominal": ["0101"]})
    invoice_id = _first_invoice_id()
    base = f"/app/finance/invoices/{invoice_id}"
    page = admin.get(base)
    assert "(draft)" in page.text and "Issue invoice" in page.text
    assert "Line added" in admin.post(base + "/lines", data={"csrf": token, "description": "Extra", "amount": "5.50"}, follow_redirects=True).text
    with SessionLocal() as db:
        assert db.get(Invoice, invoice_id).amount_minor == 1550
        line_id = db.execute(select(Invoice.id)).first() and db.scalars(select(finance.InvoiceLine.id).where(finance.InvoiceLine.invoice_id == invoice_id)).first()
    assert "Line removed" in admin.post(f"{base}/lines/{line_id}/delete", data={"csrf": token}, follow_redirects=True).text
    assert "Invoice issued" in admin.post(base + "/issue", data={"csrf": token}, follow_redirects=True).text
    assert "draft" in admin.post(base + "/lines", data={"csrf": token, "description": "Late", "amount": "1"}, follow_redirects=True).text  # rule message mentions draft
    assert "Invoice voided" in admin.post(base + "/void", data={"csrf": token, "reason": "Raised in error"}, follow_redirects=True).text
    assert admin.get("/app/finance/invoices/9999").status_code == 404


def test_debtors_reminders_and_exports(admin: TestClient) -> None:
    contact_id = _person()
    with SessionLocal() as db:
        invoice = finance.create_invoice(db, contact_id=contact_id, lines=[{"description": "Membership", "amount_minor": 10800, "nominal_code": "0101"}], actor="t")
        finance.issue(db, invoice, "t", issue_date=date.today() - timedelta(days=80))
        db.commit()
    token = _csrf(admin, "/app/finance/debtors")
    debtors = admin.get("/app/finance/debtors")
    assert "Ann Sailor" in debtors.text and "£108.00" in debtors.text
    assert "Ann Sailor,0.00,0.00,0.00,108.00,0.00,108.00" in admin.get("/app/finance/debtors.csv").text.replace("\r", "") or "108.00" in admin.get("/app/finance/debtors.csv").text

    reminders = admin.get("/app/finance/reminders")
    assert "ann@example.com" in reminders.text
    invoice_id = _first_invoice_id()
    sent = admin.post("/app/finance/reminders", data={"csrf": token, "invoice": str(invoice_id)}, follow_redirects=True)
    assert "1 reminder(s) sent" in sent.text
    assert "Nothing needs a reminder" in admin.get("/app/finance/reminders").text
    assert "Tick at least one" in admin.post("/app/finance/reminders", data={"csrf": token}, follow_redirects=True).text

    lines = admin.get("/app/finance/export/lines.csv")
    assert lines.status_code == 200 and "Membership Fees" in lines.text and "108.00" in lines.text and lines.headers["content-type"].startswith("text/csv")
    assert admin.get("/app/finance/export/payments.csv").text.startswith("date,invoice")
    assert admin.get("/app/finance/export/lines.csv?start=2099-01-01").text.count("\n") <= 2
    assert admin.get("/app/finance/export/lines.csv?start=nope").status_code == 400
    assert admin.get("/app/finance/export/other.csv").status_code == 404
    assert "Export for the accounts" in admin.get("/app/finance/export").text


def test_bank_upload_auto_match_manual_match_and_unmatch(admin: TestClient) -> None:
    contact_id = _person()
    other_id = _person("Bob Tiller", "bob@example.com")
    with SessionLocal() as db:
        for cid, amount in ((contact_id, 10800), (other_id, 5000)):
            inv = finance.create_invoice(db, contact_id=cid, lines=[{"description": "Fee", "amount_minor": amount}], actor="t")
            finance.issue(db, inv, "t")
        db.commit()
    token = _csrf(admin, "/app/finance/bank")
    statement = "Date,Description,Amount\n01/10/2026,FASTER PAYMENT ANN SAILOR,108.00\n02/10/2026,MYSTERY,25.00\n03/10/2026,Stationery,-5.00\n"
    up = admin.post("/app/finance/bank/upload", data={"csrf": token, "new_account": "Club current account", "new_kind": "bank"}, files={"file": ("s.csv", statement.encode(), "text/csv")}, follow_redirects=True)
    assert "3 new lines (2 money in)" in up.text and "1 matched automatically" in up.text
    again = admin.post("/app/finance/bank/upload", data={"csrf": token, "account_id": "1", "new_account": ""}, files={"file": ("s.csv", statement.encode(), "text/csv")}, follow_redirects=True)
    assert "0 new lines" in again.text and "3 already loaded" in again.text
    no_account = admin.post("/app/finance/bank/upload", data={"csrf": token}, files={"file": ("s.csv", b"x", "text/csv")}, follow_redirects=True)
    assert "Choose an account" in no_account.text
    bad_file = admin.post("/app/finance/bank/upload", data={"csrf": token, "new_account": "X"}, files={"file": ("s.csv", b"Foo,Bar\n1,2\n", "text/csv")}, follow_redirects=True)
    assert "No date column" in bad_file.text

    queue = admin.get("/app/finance/bank")
    assert "MYSTERY" in queue.text and "FASTER PAYMENT" not in queue.text
    assert "FASTER PAYMENT" in admin.get("/app/finance/bank?status=matched").text
    with SessionLocal() as db:
        mystery = db.scalar(select(BankEntry).where(BankEntry.details == "MYSTERY")).id
        matched = db.scalar(select(BankEntry).where(BankEntry.status == "matched")).id
        bob_ref = db.scalar(select(Invoice.reference).where(Invoice.contact_id == other_id))
    entry_page = admin.get(f"/app/finance/bank/entries/{mystery}")
    assert "No likely invoice" in entry_page.text
    missing = admin.post(f"/app/finance/bank/entries/{mystery}/match", data={"csrf": token, "invoice1": "99999"}, follow_redirects=True)
    assert "No invoice numbered 99999" in missing.text
    split = admin.post(f"/app/finance/bank/entries/{mystery}/match", data={"csrf": token, "invoice1": bob_ref}, follow_redirects=True)
    assert "Matched" in split.text and "part_paid" in admin.get(f"/app/finance/invoices/{_invoice_id_by_ref(bob_ref)}").text
    assert "Un-match" in admin.get(f"/app/finance/bank/entries/{mystery}").text
    undone = admin.post(f"/app/finance/bank/entries/{matched}/unmatch", data={"csrf": token}, follow_redirects=True)
    assert "Done" in undone.text and "unmatched" in undone.text
    assert admin.post(f"/app/finance/bank/entries/{matched}/ignore", data={"csrf": token}, follow_redirects=True).status_code == 200
    assert "Put back in the queue" in admin.get(f"/app/finance/bank/entries/{matched}").text
    assert "Done" in admin.post(f"/app/finance/bank/entries/{matched}/restore", data={"csrf": token}, follow_redirects=True).text
    assert "left for you" in admin.post("/app/finance/bank/auto-match", data={"csrf": token}, follow_redirects=True).text
    assert admin.post(f"/app/finance/bank/entries/{matched}/explode", data={"csrf": token}).status_code == 404


def _invoice_id_by_ref(ref: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Invoice.id).where(Invoice.reference == ref)) or 0


def test_membership_billing_run_and_fees_screens(admin: TestClient) -> None:
    _person("Ann Sailor", "ann@example.com", "Single Adult Sailing")
    _person("Bob Tiller", "bob@example.com", "Odd Plan")
    token = _csrf(admin, "/app/finance/billing")
    admin.post("/app/finance/billing/period", data={"csrf": token, "year": "2027"})
    with SessionLocal() as db:
        from app import membership

        membership.sync_types(db)
        db.commit()
        period_id = membership.create_period(db, 2027).id
    page = admin.get(f"/app/finance/billing?period={period_id}")
    assert "Ann Sailor" in page.text and "£108.00" in page.text and "No fee set for this type" in page.text
    assert "2 renewals prepared" in admin.post(f"/app/finance/billing/{period_id}/prepare", data={"csrf": token}, follow_redirects=True).text
    drafts = admin.post(f"/app/finance/billing/{period_id}/bill", data={"csrf": token, "draft": "1"}, follow_redirects=True)
    assert "Tick the members to bill" in drafts.text  # nothing ticked is not "everyone"
    drafts = admin.post(f"/app/finance/billing/{period_id}/bill", data={"csrf": token, "draft": "1", "everyone": "1"}, follow_redirects=True)
    assert "1 confirmed, 1 invoiced" in drafts.text and "No fee is set for Odd Plan" in drafts.text
    with SessionLocal() as db:
        assert db.scalar(select(Invoice.status)) == "draft"
    assert "Bob Tiller" in admin.get(f"/app/finance/billing?period={period_id}").text
    assert "Marked as not renewing" in admin.post(f"/app/finance/billing/{period_id}/decline", data={"csrf": token, "member_id": "2"}, follow_redirects=True).text
    assert admin.post("/app/finance/billing/period", data={"csrf": token, "year": "1"}, follow_redirects=True).status_code == 200
    assert admin.post("/app/finance/billing/999/prepare", data={"csrf": token}).status_code == 404

    types = admin.get("/app/finance/types")
    assert "Single Adult Sailing" in types.text and "108.00" in types.text
    with SessionLocal() as db:
        type_id = db.scalar(select(MembershipType.id).where(MembershipType.name == "Odd Plan"))
    saved = admin.post("/app/finance/types", data={"csrf": token, f"fee_{type_id}": "75.5", f"active_{type_id}": "1", "new_name": "Youth", "new_fee": "20"}, follow_redirects=True)
    assert "Saved" in saved.text and "75.50" in saved.text and "Youth" in saved.text
    assert "Enter an amount" not in saved.text
    assert "Unrecognised amount" in admin.post("/app/finance/types", data={"csrf": token, f"fee_{type_id}": "lots"}, follow_redirects=True).text


def test_settings_feed_the_invoice_and_emails_are_logged(admin: TestClient) -> None:
    token = _csrf(admin, "/app/finance/settings")
    saved = admin.post("/app/finance/settings", data={"csrf": token, "club_name": "MYC", "invoice_terms_days": "14", "bank_details": "Sort 00-00-00", "reply_to": "t@example.com"}, follow_redirects=True)
    assert "Saved" in saved.text and 'value="MYC"' in saved.text and "Sort 00-00-00" in saved.text
    contact_id = _person()
    created = admin.post("/app/finance/new", data={"csrf": token, "contact_id": contact_id, "action": "issue", "desc": ["Fee"], "unit": ["10"], "qty": ["1"], "nominal": ["0101"]}, follow_redirects=True)
    assert (date.today() + timedelta(days=14)).strftime("%d %b %Y") in created.text
    log = admin.get("/app/finance/emails")
    assert "Email provider" in log.text and "sandbox" in log.text


def test_finance_screens_need_login_csrf_and_an_operator_role(admin: TestClient) -> None:
    anon = TestClient(__import__("app.main", fromlist=["app"]).app, follow_redirects=False)
    for path in ("/app/finance", "/app/finance/new", "/app/finance/debtors", "/app/finance/bank", "/app/finance/billing", "/app/finance/types", "/app/finance/settings", "/app/finance/export/lines.csv", "/app/finance/invoices/1.pdf"):
        assert anon.get(path).status_code == 303, path
    assert admin.post("/app/finance/settings", data={"club_name": "x"}).status_code == 403  # no CSRF token

    token = _csrf(admin, "/app/members/new")
    admin.post("/app/members", data={"csrf": token, "name": "Mem", "email": "mem@example.com"})
    member = _login("mem@example.com")
    assert member.get("/app/finance").status_code == 403
    assert member.get("/app/finance/invoices/1.pdf").status_code == 403
    assert re.search(r"My account", member.get("/app").text)
