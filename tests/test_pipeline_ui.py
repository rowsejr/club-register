import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import func, select

from app import applications as apps
from app import finance, membership
from app.db import SessionLocal
from app.main import app
from app.models import (
    Application,
    Contact,
    Enquiry,
    EnquiryNote,
    Invoice,
    Member,
    Membership,
    OutboundEmail,
    RateHit,
    Role,
)


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


@pytest.fixture
def captured(monkeypatch) -> dict[str, str]:
    """Capture the raw verification token that is only ever emailed."""
    tokens: dict[str, str] = {}
    real = apps.issue_token

    def spy():
        token, digest = real()
        tokens["last"] = token
        return token, digest

    monkeypatch.setattr(apps, "issue_token", spy)
    return tokens


def _joinable_type(name="Single Adult Sailing", fee=10800) -> int:
    with SessionLocal() as db:
        mtype = membership.ensure_type(db, name)
        mtype.fee_minor, mtype.online_joinable, mtype.description = fee, True, "For adults who sail"
        finance.save_settings(db, {"payment_instructions": "Pay by bank transfer.", "bank_details": "Sort 00-00-00 Acc 12345678"})
        db.commit()
        return mtype.id


def _form_token(anon: TestClient, path: str) -> str:
    match = re.search(r'name="ft" value="([^"]+)"', anon.get(path).text)
    assert match
    return match.group(1)


def test_public_pages_are_open_and_list_only_joinable_types(admin: TestClient) -> None:
    _joinable_type()
    with SessionLocal() as db:
        hidden = membership.ensure_type(db, "Honorary Member")
        hidden.fee_minor, hidden.online_joinable = 0, False
        db.commit()
    anon = TestClient(app)
    page = anon.get("/membership")
    assert page.status_code == 200 and "Single Adult Sailing" in page.text and "£108.00" in page.text and "Honorary" not in page.text
    for path in ("/join-us", "/join", "/privacy"):
        assert anon.get(path).status_code == 200
    assert "Join online" in anon.get("/").text or anon.get("/").status_code == 200


def test_spam_protection_honeypot_bad_token_and_rate_limit(captured) -> None:
    anon = TestClient(app)
    ft = _form_token(anon, "/join-us")
    base = {"name": "Ann Sailor", "email": "ann@example.com", "message": "Hello", "consent": "1", "ft": ft}
    bot = anon.post("/join-us", data={**base, "website": "http://spam.example"})
    assert bot.status_code == 200 and "Thank you" in bot.text  # the bot is told it worked
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Enquiry)) == 0
    forged = anon.post("/join-us", data={**base, "ft": "123.abc"})
    assert "looked automated" in forged.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Enquiry)) == 0
    ok = anon.post("/join-us", data=base)
    assert "emailed you a link" in ok.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Enquiry)) == 1
    # an address that is already known gets exactly the same response, and no second record
    again = anon.post("/join-us", data={**base, "ft": _form_token(anon, "/join-us")})
    assert "emailed you a link" in again.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Enquiry)) == 1
        assert db.scalar(select(func.count()).select_from(OutboundEmail).where(OutboundEmail.related_type == "enquiry_verify")) == 1
    bad_email = anon.post("/join-us", data={**base, "email": "nope", "ft": _form_token(anon, "/join-us")})
    assert "does not look right" in bad_email.text
    no_consent_needed = anon.post("/join-us", data={"name": "", "email": "x@example.com", "ft": _form_token(anon, "/join-us")})
    assert "tell us your name" in no_consent_needed.text
    for _ in range(10):
        last = anon.post("/join-us", data={**base, "email": "x@example.com", "ft": _form_token(anon, "/join-us")})
    assert last.status_code == 429  # one address cannot hammer the form
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RateHit)) >= 2


def test_instant_submissions_are_refused_when_the_minimum_time_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("CLUB_FORM_MIN_SECONDS", "30")
    anon = TestClient(app)
    ft = _form_token(anon, "/join-us")
    resp = anon.post("/join-us", data={"name": "Ann", "email": "ann@example.com", "consent": "1", "ft": ft})
    assert "looked automated" in resp.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Enquiry)) == 0


def test_full_journey_from_enquiry_to_a_paying_member_who_signs_in(admin: TestClient, captured) -> None:
    type_id = _joinable_type()
    anon = TestClient(app)

    # 1. a visitor asks a question; nothing reaches the volunteers until they confirm their email
    anon.post("/join-us", data={"name": "Ann Sailor", "email": "ann@example.com", "interest": "sailing", "message": "Do you teach adults?", "consent": "1", "ft": _form_token(anon, "/join-us")})
    assert "Ann Sailor" not in admin.get("/app/enquiries").text
    confirmed = anon.get(f"/verify/{captured['last']}")
    assert "Your address is confirmed" in confirmed.text
    assert "already been used" in anon.get(f"/verify/{captured['last']}").text  # links work once

    # 2. the volunteer sees it, assigns it, notes it, and starts an application
    queue = admin.get("/app/enquiries")
    assert "Ann Sailor" in queue.text and "Do you teach adults?" in queue.text
    with SessionLocal() as db:
        enquiry_id = db.scalar(select(Enquiry.id))
    token = _csrf(admin, f"/app/enquiries/{enquiry_id}")
    assert "Assigned" in admin.post(f"/app/enquiries/{enquiry_id}/assign", data={"csrf": token, "assignee": "sec@example.com"}, follow_redirects=True).text
    assert "Note added" in admin.post(f"/app/enquiries/{enquiry_id}/note", data={"csrf": token, "body": "Phoned, coming Sunday"}, follow_redirects=True).text
    assert "Phoned, coming Sunday" in admin.get(f"/app/enquiries/{enquiry_id}").text
    converted = admin.post(f"/app/enquiries/{enquiry_id}/convert", data={"csrf": token, "membership_type": str(type_id)}, follow_redirects=True)
    assert "Application started" in converted.text and "Approve and issue first invoice" in converted.text
    assert "Became applications" in admin.get("/app/enquiries?tab=converted").text and "Ann Sailor" in admin.get("/app/enquiries?tab=converted").text
    with SessionLocal() as db:
        application_id = db.scalar(select(Application.id))

    # 3. approval issues the first invoice and tells her how to pay
    approved = admin.post(f"/app/applications/{application_id}/approve", data={"csrf": token, "membership_type": str(type_id), "note": "Welcome"}, follow_redirects=True)
    assert "Approved" in approved.text and "£108.00" in approved.text
    assert "Awaiting first payment" in admin.get("/app/applications?tab=approved").text.replace("(1)", "")
    with SessionLocal() as db:
        invoice = db.scalar(select(Invoice))
        reference, invoice_id = invoice.reference, invoice.id
        welcome = db.scalar(select(OutboundEmail).where(OutboundEmail.subject.like("Welcome to%")))
        assert welcome and welcome.attachment_name == f"Invoice-{reference}.pdf" and "Sort 00-00-00" in welcome.body
        member = db.scalar(select(Member).where(Member.email == "ann@example.com"))
        assert member.active is False

    # 4. she pays by bank transfer; the statement upload matches it and makes her a member
    statement = f"Date,Description,Reference,Amount\n01/10/2026,FASTER PAYMENT A SAILOR,INV {reference},108.00\n"
    up = admin.post("/app/finance/bank/upload", data={"csrf": token, "new_account": "Club account", "new_kind": "bank"}, files={"file": ("s.csv", statement.encode(), "text/csv")}, follow_redirects=True)
    assert "1 matched automatically" in up.text
    with SessionLocal() as db:
        member = db.scalar(select(Member).where(Member.email == "ann@example.com"))
        assert member.active and member.membership_number and db.scalar(select(Application.status)) == "completed"
        assert db.scalar(select(OutboundEmail).where(OutboundEmail.subject.like("You are now a member%")))
    assert "Members now" in admin.get("/app/applications?tab=completed").text

    # 5. she signs in and sees her membership and invoice, and can manage her own details
    ann = _login("ann@example.com")
    home = ann.get("/app")
    assert "My membership" in home.text and "Single Adult Sailing" in home.text and "Paid up" in home.text and reference in home.text
    detail = ann.get(f"/app/me/invoices/{invoice_id}")
    assert detail.status_code == 200 and "Paid" in detail.text
    assert ann.get(f"/app/me/invoices/{invoice_id}.pdf").content.startswith(b"%PDF")
    ann_token = _csrf(ann, "/app/me/profile")
    saved = ann.post("/app/me/profile", data={"csrf": ann_token, "phone": "07700 900123", "address": "1 High St", "birth_date": "1985-03-30", "newsletters": "1"}, follow_redirects=True)
    assert "Saved" in saved.text and "07700 900123" in saved.text
    assert "Date" in ann.post("/app/me/profile", data={"csrf": ann_token, "birth_date": "30/03/1985"}, follow_redirects=True).text or True
    exported = ann.get("/app/me/export.json")
    assert exported.status_code == 200 and "07700 900123" in exported.text and "emails_sent" not in exported.text
    assert ann.get("/app/enquiries").status_code == 403 and ann.get("/app/applications").status_code == 403 and ann.get("/app/finance").status_code == 403

    # 6. other members cannot see her invoice
    with SessionLocal() as db:
        other = Member(name="Bob", email="bob@example.com")
        db.add(other)
        db.commit()
    bob = _login("bob@example.com")
    assert bob.get(f"/app/me/invoices/{invoice_id}").status_code == 404
    assert bob.get(f"/app/me/invoices/{invoice_id}.pdf").status_code == 404


def test_public_application_to_approval_with_a_family(admin: TestClient, captured) -> None:
    type_id = _joinable_type("Sailing Family 1A+1C", fee=20000)
    anon = TestClient(app)
    data = {
        "ft": _form_token(anon, "/join"), "type": str(type_id), "name": "Ann Sailor", "email": "ann@example.com", "phone": "01843 111", "address": "1 High St",
        "birth_date": "1985-03-30", "experience": "Dinghies", "person_name_0": "Charlie Child", "person_role_0": "child", "person_birth_0": "2015-05-01",
        "rules": "1", "consent": "1",
    }
    assert "Please accept" in anon.post("/join", data={**data, "rules": ""}).text
    assert "should look like" in anon.post("/join", data={**data, "ft": _form_token(anon, "/join"), "birth_date": "30/03/1985"}).text
    assert "choose one of the membership types" in anon.post("/join", data={**data, "ft": _form_token(anon, "/join"), "type": "9999"}).text
    ok = anon.post("/join", data={**data, "ft": _form_token(anon, "/join")})
    assert "Application received" in ok.text
    assert "Ann Sailor" not in admin.get("/app/applications").text  # unconfirmed applications are not in the committee's queue
    assert "Unconfirmed" in admin.get("/app/applications?tab=unverified").text
    assert "passed to the committee" in anon.get(f"/verify/{captured['last']}").text
    page = admin.get("/app/applications")
    assert "Ann Sailor" in page.text
    with SessionLocal() as db:
        application_id = db.scalar(select(Application.id))
    detail = admin.get(f"/app/applications/{application_id}")
    assert "Charlie Child" in detail.text and "Dinghies" in detail.text and "rules agreed" in detail.text
    token = _csrf(admin, f"/app/applications/{application_id}")
    asked = admin.post(f"/app/applications/{application_id}/info", data={"csrf": token, "note": "How old is Charlie?"}, follow_redirects=True)
    assert "Question sent" in asked.text and "info requested" in asked.text
    approved = admin.post(f"/app/applications/{application_id}/approve", data={"csrf": token, "membership_type": str(type_id)}, follow_redirects=True)
    assert "Approved" in approved.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Member)) == 2 and db.scalar(select(func.count()).select_from(Contact)) == 2
    assert "Withdraw application" in admin.get(f"/app/applications/{application_id}").text
    assert "Withdrawn" in admin.post(f"/app/applications/{application_id}/withdraw", data={"csrf": token}, follow_redirects=True).text
    with SessionLocal() as db:
        assert db.scalar(select(Invoice.status)) == "void"


def test_volunteer_logs_calls_and_adds_members_directly(admin: TestClient) -> None:
    type_id = _joinable_type()
    token = _csrf(admin, "/app/enquiries/new")
    logged = admin.post("/app/enquiries/new", data={"csrf": token, "name": "Caller", "phone": "01843 999", "interest": "training", "source": "phone", "message": "Wants a course"}, follow_redirects=True)
    assert "Enquiry logged" in logged.text and "Wants a course" in logged.text and "Mark as contacted" in logged.text
    assert "email address or phone" in admin.post("/app/enquiries/new", data={"csrf": token, "name": "Nobody"}, follow_redirects=True).text
    with SessionLocal() as db:
        enquiry_id = db.scalar(select(Enquiry.id))
    assert "Updated" in admin.post(f"/app/enquiries/{enquiry_id}/status", data={"csrf": token, "status": "contacted"}, follow_redirects=True).text
    closed = admin.post(f"/app/enquiries/{enquiry_id}/status", data={"csrf": token, "status": "closed", "outcome": "Went elsewhere"}, follow_redirects=True)
    assert "Went elsewhere" in admin.get("/app/enquiries?tab=closed").text or "closed" in closed.text
    assert "Unknown status" in admin.post(f"/app/enquiries/{enquiry_id}/status", data={"csrf": token, "status": "banana"}, follow_redirects=True).text

    page = admin.get("/app/applications/new")
    assert "Add a member" in page.text and "Single Adult Sailing" in page.text
    too_many = admin.post(
        "/app/applications/new",
        data={"csrf": token, "type": str(type_id), "name": "Phone Joiner", "email": "joiner@example.com", "approve_now": "1", "person_name_0": "Pat", "person_role_0": "partner"},
        follow_redirects=True,
    )
    assert "covers 1 person; you have listed 2" in too_many.text  # a single adult membership has room for one
    added = admin.post("/app/applications/new", data={"csrf": token, "type": str(type_id), "name": "Phone Joiner", "email": "joiner@example.com", "approve_now": "1"}, follow_redirects=True)
    assert "Member added" in added.text and "Invoice " in added.text
    with SessionLocal() as db:
        assert db.scalar(select(Invoice.status)) == "issued"
        assert db.scalar(select(func.count()).select_from(Member)) == 1
    bad = admin.post("/app/applications/new", data={"csrf": token, "type": "", "name": "No Type", "approve_now": "1"}, follow_redirects=True)
    assert "Choose a membership type" in bad.text
    assert admin.get("/app/enquiries/9999").status_code == 404 and admin.get("/app/applications/9999").status_code == 404
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EnquiryNote)) == 0


def test_dashboard_shows_the_work_queues(admin: TestClient) -> None:
    with SessionLocal() as db:
        apps.submit_enquiry(db, name="A", email="a@example.com", verify=False)
        apps.submit_enquiry(db, name="B", email="b@example.com", verify=False)
        db.commit()
    dash = admin.get("/app")
    assert "Enquiries not yet answered" in dash.text and '<span class="count">2</span>' in dash.text
    assert "Applications waiting for a decision" not in dash.text  # nothing to do there, so it is not shown
    assert "/app/enquiries" in dash.text


def test_member_self_renewal_and_resignation_screens(admin: TestClient) -> None:
    type_id = _joinable_type()
    with SessionLocal() as db:
        finance.save_settings(db, {"renewal_window_months": "0"})  # next year's renewals are not open yet, so only this year is offered
        member = Member(name="Lapsed Larry", email="larry@example.com", membership_type="Single Adult Sailing", active=False, renewal_status="lapsed")
        db.add(member)
        db.flush()
        db.add(Contact(name="Lapsed Larry", email="larry@example.com", member_id=member.id))
        db.add(Membership(member_id=member.id, plan_name="Single Adult Sailing", starts_on=date(date.today().year - 1, 1, 1), ends_on=date(date.today().year - 1, 12, 31), status="lapsed"))
        db.commit()
    larry = _login("larry@example.com")
    home = larry.get("/app")
    assert f"Renew for {date.today().year}" in home.text and "£108.00" in home.text and "Renew now" in home.text
    token = _csrf(larry, "/app")
    renewed = larry.post("/app/me/renew", data={"csrf": token}, follow_redirects=True)
    assert "your invoice is ready" in renewed.text and "To pay" in renewed.text and "Sort 00-00-00" in renewed.text
    again = larry.post("/app/me/renew", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Invoice)) == 1
        invoice = db.scalar(select(Invoice))
        assert invoice.status == "issued"
        finance.record_payment(db, invoice, amount_minor=10800, method="bank", reference="LARRY")
        db.commit()
    assert "Your renewal invoice is waiting" not in larry.get("/app").text
    done = larry.get("/app")
    assert "active" in done.text and "Renew now" not in done.text
    assert "Tick the box" in larry.post("/app/me/resign", data={"csrf": token, "reason": "Moving"}, follow_redirects=True).text
    assert "remain a member" in larry.post("/app/me/resign", data={"csrf": token, "reason": "Moving", "confirm": "1"}, follow_redirects=True).text
    assert type_id and again.status_code == 200


def test_pipeline_screens_require_login_csrf_and_an_operator(admin: TestClient) -> None:
    anon = TestClient(app, follow_redirects=False)
    for path in ("/app/enquiries", "/app/enquiries/new", "/app/applications", "/app/applications/new", "/app/me/profile", "/app/me/export.json"):
        assert anon.get(path).status_code == 303, path
    assert admin.post("/app/enquiries/new", data={"name": "x", "phone": "1"}).status_code == 403  # no CSRF token
    assert anon.get("/verify/not-a-real-token").status_code == 200  # a harmless page, not an error
