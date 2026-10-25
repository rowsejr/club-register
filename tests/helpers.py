"""Shared test helpers: sign in through the real UI, and read a page's CSRF token."""

import re

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import Role, UserRole


def login(email: str, role: Role | None = None, hats: str | None = None) -> TestClient:
    """Sign in through the real UI flow (dev mode surfaces the code on the page). `hats` is a comma-separated list such as "treasurer"."""
    if role is not None:
        with SessionLocal() as db:
            db.add(UserRole(email=email, role=role, extra_roles=hats))
            db.commit()
    client = TestClient(app)
    page = client.post("/login", data={"email": email})
    assert page.status_code == 200
    code = re.search(r"<code>([^<]+)</code>", page.text)
    assert code, "dev token should be shown for registered emails"
    done = client.post("/login/token", data={"token": code.group(1)}, follow_redirects=False)
    assert done.status_code == 303
    client.cookies.set("club_session", done.cookies["club_session"])
    return client


def csrf(client: TestClient, path: str = "/app") -> str:
    match = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get(path).text)
    assert match
    return match.group(1)
