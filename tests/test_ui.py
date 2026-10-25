import re

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import AuditLog, Role, UserRole


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


def test_unknown_email_gets_same_page_without_code() -> None:
    c = TestClient(app)
    page = c.post("/login", data={"email": "ghost@example.com"})
    assert page.status_code == 200 and "<code>" not in page.text
    assert "sign-in code has been sent" in page.text
    assert c.post("/login", data={"email": "not-an-email"}).status_code == 200


def test_login_dashboard_logout(admin: TestClient) -> None:
    dash = admin.get("/app")
    assert dash.status_code == 200 and "What needs doing" in dash.text and "boss@example.com" in dash.text
    out = admin.post("/logout", data={"csrf": _csrf(admin)}, follow_redirects=False)
    assert out.status_code == 303
    assert admin.get("/app", follow_redirects=False).status_code == 303


def test_login_open_redirect_blocked() -> None:
    with SessionLocal() as db:
        db.add(UserRole(email="o@example.com", role=Role.COMMITTEE))
        db.commit()
    c = TestClient(app)
    code = re.search(r"<code>([^<]+)</code>", c.post("/login", data={"email": "o@example.com"}).text)
    assert code
    done = c.post("/login/token", data={"token": code.group(1), "next": "//evil.example"}, follow_redirects=False)
    assert done.headers["location"] == "/app"


def test_member_crud_search_and_csrf(admin: TestClient) -> None:
    token = _csrf(admin, "/app/members/new")
    assert admin.post("/app/members", data={"name": "A", "email": "a@example.com"}).status_code == 403  # no CSRF
    created = admin.post(
        "/app/members",
        data={"csrf": token, "name": "Ann Sailor", "email": "Ann@Example.com", "expiry_date": "2027-01-01"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    detail = admin.get(created.headers["location"])
    assert "Ann Sailor" in detail.text and "ann@example.com" in detail.text and "Saved." in detail.text

    dup = admin.post("/app/members", data={"csrf": token, "name": "Other", "email": "ann@example.com"})
    assert dup.status_code == 409 and "already exists" in dup.text
    bad = admin.post("/app/members", data={"csrf": token, "name": "X", "email": "nope"})
    assert bad.status_code == 422
    bad_date = admin.post(
        "/app/members", data={"csrf": token, "name": "X", "email": "x@example.com", "expiry_date": "01/02/2027"}
    )
    assert bad_date.status_code == 422 and "YYYY-MM-DD" in bad_date.text

    full = admin.get("/app/members?q=ann")
    partial = admin.get("/app/members?q=ann", headers={"HX-Request": "true"})
    assert "<html" in full.text and "<html" not in partial.text and "Ann Sailor" in partial.text
    assert "Ann Sailor" not in admin.get("/app/members?q=zzz").text
    assert "Ann Sailor" not in admin.get("/app/members?status=inactive").text

    member_id = created.headers["location"].split("/")[-1].split("?")[0]
    assert admin.post(f"/app/members/{member_id}/active", data={"csrf": token, "active": "0"}).status_code == 200
    assert "Ann Sailor" in admin.get("/app/members?status=inactive").text
    with SessionLocal() as db:
        actions = [a.action for a in db.scalars(select(AuditLog))]
    assert "member.create" in actions and "member.deactivate" in actions


def test_members_pagination_and_csv_injection(admin: TestClient) -> None:
    token = _csrf(admin, "/app/members/new")
    for i in range(27):
        name = "=cmd|calc" if i == 0 else f"Member {i:02d}"
        admin.post("/app/members", data={"csrf": token, "name": name, "email": f"m{i}@example.com"})
    page2 = admin.get("/app/members?page=2")
    assert "page 2 of 2" in page2.text
    assert admin.get("/app/members?page=99").status_code == 200
    csv_text = admin.get("/app/members.csv").text
    assert "'=cmd|calc" in csv_text and ",=cmd" not in csv_text and len(csv_text.strip().splitlines()) == 28


def test_html_is_escaped(admin: TestClient) -> None:
    token = _csrf(admin, "/app/contacts/new")
    admin.post("/app/contacts", data={"csrf": token, "name": "<script>alert(1)</script>"})
    page = admin.get("/app/contacts")
    assert "<script>alert(1)" not in page.text and "&lt;script&gt;" in page.text


def test_contact_save(admin: TestClient) -> None:
    token = _csrf(admin, "/app/contacts/new")
    r = admin.post(
        "/app/contacts",
        data={"csrf": token, "name": "Bo", "email": "bo@example.com", "phone": "0123", "suppressed": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = admin.get(r.headers["location"])
    assert "checked" in page.text and "0123" in page.text
    assert "bo@example.com" in admin.get("/app/contacts.csv").text


def test_member_role_cannot_use_operator_ui(admin: TestClient) -> None:
    token = _csrf(admin, "/app/members/new")
    admin.post("/app/members", data={"csrf": token, "name": "Mem", "email": "mem@example.com"})
    member = _login("mem@example.com")
    assert member.get("/app/members").status_code == 403
    assert "My membership" in member.get("/app").text
    assert member.get("/app/admin/users").status_code == 403


def test_admin_users_and_finance_pages(admin: TestClient) -> None:
    token = _csrf(admin, "/app/admin/users")
    admin.post("/app/admin/users", data={"csrf": token, "email": "Treasurer@Example.com", "role": "Committee"})
    assert "treasurer@example.com" in admin.get("/app/admin/users").text
    with SessionLocal() as db:
        own = db.scalar(select(UserRole).where(UserRole.email == "boss@example.com"))
        assert own
        own_id = own.id
    admin.post(f"/app/admin/users/{own_id}/delete", data={"csrf": token})
    assert "boss@example.com" in admin.get("/app/admin/users").text
    for path in ("/app/finance", "/app/finance?filter=overdue", "/app/events"):
        assert admin.get(path).status_code == 200
