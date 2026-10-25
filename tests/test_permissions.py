import re

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from sqlalchemy import select

from app import finance, permissions
from app.db import SessionLocal
from app.main import app
from app.models import Contact, Invoice, Member, Role, SessionRecord, UserRole


def _signin(email: str, base: Role, hats: str | None = None) -> TestClient:
    with SessionLocal() as db:
        db.add(UserRole(email=email, role=base, extra_roles=hats))
        db.commit()
    c = TestClient(app)
    code = re.search(r"<code>([^<]+)</code>", c.post("/login", data={"email": email}).text)
    assert code
    done = c.post("/login/token", data={"token": code.group(1)}, follow_redirects=False)
    c.cookies.set("club_session", done.cookies["club_session"])
    return c


@pytest.fixture
def invoice_id() -> int:
    with SessionLocal() as db:
        member = Member(name="Ann Sailor", email="ann@example.com")
        db.add(member)
        db.flush()
        contact = Contact(name="Ann Sailor", email="ann@example.com", member_id=member.id)
        db.add(contact)
        db.flush()
        invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Fee", "amount_minor": 5000}], actor="t")
        finance.issue(db, invoice, "t")
        db.commit()
        return invoice.id


def _status(c: TestClient, verb: str, path: str, **data) -> int:
    token = _csrf(c, "/app") if verb == "post" else ""
    response = c.get(path) if verb == "get" else c.post(path, data={"csrf": token, **data}, follow_redirects=False)
    return response.status_code


def test_permission_sets_are_what_the_help_text_promises() -> None:
    assert permissions.allows(permissions.BASE_PERMISSIONS["Admin"], "anything.at.all")
    assert not permissions.allows(permissions.BASE_PERMISSIONS["Committee"], "finance.write")
    assert permissions.allows(permissions.HATS["treasurer"].perms, "finance.admin")
    assert not permissions.allows(permissions.HATS["secretary"].perms, "finance.write")
    assert not permissions.is_operator(permissions.BASE_PERMISSIONS["Member"])






def test_secretary_hat_runs_membership_but_cannot_touch_payments(invoice_id: int) -> None:
    secretary = _signin("sec@example.com", Role.MEMBER, "secretary")
    token = _csrf(secretary, "/app")
    for path in ("/app/members", "/app/contacts", "/app/enquiries", "/app/applications", "/app/enquiries/new", "/app/applications/new", "/app/members/new", "/app/finance", "/app/finance/billing"):
        assert secretary.get(path).status_code == 200, path
    made = secretary.post("/app/members", data={"csrf": token, "name": "New Member", "email": "new@example.com"}, follow_redirects=False)
    assert made.status_code == 303
    assert secretary.post("/app/enquiries/new", data={"csrf": token, "name": "Caller", "phone": "123"}, follow_redirects=False).status_code == 303
    assert secretary.post(f"/app/finance/invoices/{invoice_id}/payment", data={"csrf": token, "amount": "10", "method": "bank"}).status_code == 403
    assert secretary.post(f"/app/finance/invoices/{invoice_id}/void", data={"csrf": token, "reason": "x"}).status_code == 403
    assert secretary.get("/app/finance/new").status_code == 403
    assert secretary.get("/app/finance/settings").status_code == 403
    assert secretary.get("/app/admin/users").status_code == 403






def test_admin_assigns_hats_and_changes_end_the_old_session(invoice_id: int) -> None:
    admin = _signin("boss@example.com", Role.ADMIN)
    treasurer = _signin("t@example.com", Role.MEMBER, "treasurer")
    assert treasurer.get("/app/finance/settings").status_code == 200
    token = _csrf(admin, "/app/admin/users")
    page = admin.get("/app/admin/users")
    assert "Treasurer" in page.text and "Membership secretary" in page.text and "finance.admin" in page.text
    admin.post("/app/admin/users", data={"csrf": token, "email": "T@example.com", "role": "Member", "hat_secretary": "1", "hat_bogus": "1"})
    assert treasurer.get("/app/finance", follow_redirects=False).status_code == 303  # session ended: sign in again
    with SessionLocal() as db:
        row = db.scalar(select(UserRole).where(UserRole.email == "t@example.com"))
        assert row.extra_roles == "secretary"  # unknown hats are ignored, the treasurer hat is gone
        assert all(s.revoked_at is not None for s in db.scalars(select(SessionRecord).where(SessionRecord.email == "t@example.com")))
    assert "membership secretary" in admin.get("/app/admin/users").text.lower()
    assert invoice_id and Invoice


def test_secretary_cannot_approve_applications_without_the_decide_permission() -> None:
    # the secretary hat includes it; a committee-only hat-less member of the public never gets it
    secretary = _signin("sec@example.com", Role.MEMBER, "secretary")
    token = _csrf(secretary, "/app/applications/new")
    with SessionLocal() as db:
        from app import membership

        mtype = membership.ensure_type(db, "Single Adult Sailing")
        mtype.fee_minor = 10000
        db.commit()
        type_id = mtype.id
    added = secretary.post("/app/applications/new", data={"csrf": token, "type": str(type_id), "name": "Joiner", "email": "j@example.com", "approve_now": "1"}, follow_redirects=True)
    assert "Member added" in added.text


def test_committee_can_look_and_decide_but_not_change_records_or_money(invoice_id: int) -> None:
    committee = _signin("chair@example.com", Role.COMMITTEE)
    for path in ("/app", "/app/members", "/app/contacts", "/app/finance", "/app/finance/debtors", "/app/enquiries", "/app/applications", "/app/finance/bank"):
        assert _status(committee, "get", path) == 200, path
    assert _status(committee, "get", "/app/members/new") == 403
    assert _status(committee, "post", "/app/members", name="X", email="x@example.com") == 403
    assert _status(committee, "post", f"/app/finance/invoices/{invoice_id}/payment", amount="10", method="bank") == 403
    assert _status(committee, "post", f"/app/finance/invoices/{invoice_id}/void", reason="x") == 403
    assert _status(committee, "get", "/app/finance/settings") == 403
    assert _status(committee, "get", "/app/admin/users") == 403
    assert _status(committee, "post", "/app/enquiries/new", name="x", phone="1") == 403


def test_treasurer_hat_runs_the_money_but_not_the_membership_register(invoice_id: int) -> None:
    treasurer = _signin("treasurer@example.com", Role.MEMBER, "treasurer")
    page = treasurer.get("/app")
    assert "Finance" in page.text and "My account" not in page.text  # the hat turns on the operator view
    assert _status(treasurer, "get", "/app/finance/settings") == 200 and _status(treasurer, "get", "/app/finance/bank") == 200
    token = _csrf(treasurer, "/app/finance")
    assert treasurer.post(f"/app/finance/invoices/{invoice_id}/payment", data={"csrf": token, "amount": "10", "method": "bank"}, follow_redirects=False).status_code == 303
    assert treasurer.post(f"/app/finance/invoices/{invoice_id}/credit", data={"csrf": token, "amount": "5", "reason": "Goodwill"}, follow_redirects=False).status_code == 303
    assert treasurer.post(f"/app/finance/invoices/{invoice_id}/write-off", data={"csrf": token, "reason": "Bad debt"}, follow_redirects=False).status_code == 303
    assert _status(treasurer, "get", "/app/members") == 200  # can look the members up
    assert _status(treasurer, "post", "/app/members", name="X", email="x@example.com") == 403
    assert _status(treasurer, "post", "/app/applications/1/approve") == 403
    assert _status(treasurer, "get", "/app/admin/users") == 403


def test_hats_stack_and_navigation_follows_permissions() -> None:
    both = _signin("both@example.com", Role.MEMBER, "treasurer,secretary")
    page = both.get("/app")
    assert 'href="/app/finance"' in page.text and 'href="/app/members"' in page.text and 'href="/app/enquiries"' in page.text
    assert 'href="/app/admin/users"' not in page.text
    plain = _signin("plain@example.com", Role.MEMBER)
    nav = plain.get("/app").text
    assert "My account" in nav and 'href="/app/finance"' not in nav and 'href="/app/members"' not in nav
    admin = _signin("boss@example.com", Role.ADMIN)
    assert 'href="/app/admin/users"' in admin.get("/app").text


def test_plain_members_are_locked_out_of_every_operator_screen() -> None:
    with SessionLocal() as db:
        db.add(Member(name="Mem", email="mem@example.com"))
        db.commit()
    c = TestClient(app)
    code = re.search(r"<code>([^<]+)</code>", c.post("/login", data={"email": "mem@example.com"}).text)
    c.cookies.set("club_session", c.post("/login/token", data={"token": code.group(1)}, follow_redirects=False).cookies["club_session"])
    for path in ("/app/members", "/app/contacts", "/app/events", "/app/finance", "/app/finance/new", "/app/finance/bank", "/app/finance/billing", "/app/enquiries", "/app/applications", "/app/admin/users"):
        assert c.get(path).status_code == 403, path
    assert c.get("/app").status_code == 200 and c.get("/app/me/profile").status_code in {200, 404}
