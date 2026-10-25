"""Demo mode shows sign-in codes on the login page, but only where nothing is really emailed."""

import pytest

from app import auth
from app.db import SessionLocal
from app.models import Role, UserRole

CODE_BOX = "your code is"


@pytest.fixture(autouse=True)
def production(monkeypatch) -> None:
    """The test suite runs in dev mode, which shows codes anyway; these tests need the production behaviour."""
    monkeypatch.setenv("CLUB_ENV", "production")
    with SessionLocal() as db:
        db.add(UserRole(email="demo@example.org", role=Role.ADMIN))
        db.commit()


def _sign_in_page(client) -> str:
    return client.post("/login", data={"email": "demo@example.org"}).text


def test_production_never_shows_a_code(client) -> None:
    assert CODE_BOX not in _sign_in_page(client)


def test_demo_mode_with_sandbox_email_shows_the_code_and_a_banner(client, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_DEMO", "1")
    page = _sign_in_page(client)
    assert CODE_BOX in page and "<code>" in page
    assert "Demo site" in client.get("/login").text


def test_demo_mode_is_ignored_when_real_email_is_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_DEMO", "1")
    monkeypatch.setenv("CLUB_EMAIL_PROVIDER", "smtp")
    assert not auth.is_demo()
    assert "Demo site" not in client.get("/login").text


def test_an_unknown_address_gets_no_code_even_in_demo_mode(client, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_DEMO", "1")
    assert CODE_BOX not in client.post("/login", data={"email": "stranger@example.org"}).text
