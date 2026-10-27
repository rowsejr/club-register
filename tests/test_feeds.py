"""The signed read-only feeds for the club website: signing, refusals, replays and what each feed holds.

Phase 2 (D3/D4) retired the Google Sheet client: the website now feeds its own Sheet, and money never leaves the
office, so ``website`` is the only client these feeds know."""

import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import events, feeds, finance
from app.db import SessionLocal
from app.main import app
from app.models import Boat, Contact, Event, Member, NominalCode

SITE, WRONG, SHORT = "site-secret-0123456789", "wrong-secret-0123456789", "short"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CLUB_FEED_KEYS", f"website:{SITE}")
    monkeypatch.setenv("CLUB_BASE_URL", "https://office.example.org")


@pytest.fixture
def club():
    with SessionLocal() as db:
        db.add(NominalCode(code="0101", label="Membership"))
        member = Member(name="Ann Sailor", email="ann@example.org", membership_type="Single Adult Sailing", renewal_status="active", active=True, membership_number=7)
        db.add(member)
        db.flush()
        ann = Contact(name="Ann Sailor", email="ann@example.org", phone="07700 900123", member_id=member.id, gender="female", birth_date=date(1980, 1, 1), directory_consent=True)
        quiet = Member(name="Quiet Person", email="quiet@example.org", membership_type="Single Adult Sailing", renewal_status="active", active=True)
        db.add_all([ann, quiet])
        db.flush()
        db.add(Contact(name="Quiet Person", email="quiet@example.org", phone="07700 900999", member_id=quiet.id))
        db.add(Boat(name="Puffin", boat_class="Laser", sail_number="123456", portsmouth_number=1100, owner_contact_id=ann.id))
        invoice = finance.create_invoice(db, contact_id=ann.id, lines=[{"description": "Subs", "amount_minor": 10800, "nominal_code": "0101"}])
        finance.issue(db, invoice, "t", issue_date=date.today() - timedelta(days=45), terms=30)
        event = events.save_event(db, None, title="Laying-up Supper", start_at=datetime.now(UTC) + timedelta(days=10), location="Clubhouse", published=True, bookable=True, tickets_limit=40)
        events.save_ticket_type(db, event, None, name="Member", price_minor=2500, audience="members")
        events.save_ticket_type(db, event, None, name="Guest", price_minor=3000, audience="public")
        db.commit()
        yield


def _url(name, client="website", secret=SITE, ts=None, nonce=None):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    return f"/feeds/{name}.json?client={client}&ts={ts}&nonce={nonce}&sig={feeds.sign(secret, name, client, ts, nonce)}"


def test_only_fresh_correctly_signed_requests_from_the_right_tool_are_answered(club) -> None:
    web = TestClient(app)
    good = _url("siteMembers")
    assert web.get(good).status_code == 200
    assert web.get(good).status_code == 401  # the same address cannot be used twice
    assert web.get(_url("siteMembers", ts=int(time.time()) - 900)).status_code == 401  # too old
    assert web.get(_url("siteMembers", secret=WRONG)).status_code == 401
    assert web.get(_url("siteMembers", client="sheet", secret=SITE)).status_code == 403  # the Sheet client was retired in Phase 2
    assert web.get(_url("nonsense")).status_code == 404
    assert web.get("/feeds/siteMembers.json").status_code in {401, 403}


def test_no_keys_means_no_feeds(club, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_FEED_KEYS", f"website:{SHORT}")  # a secret too short to be safe counts as none
    assert TestClient(app).get(_url("siteMembers", secret=SHORT)).status_code == 403


def test_the_website_feeds_respect_peoples_choices(club) -> None:
    web = TestClient(app)
    directory = web.get(_url("directory")).json()
    assert directory["rows"] == [["Ann Sailor", "Single Adult Sailing", "ann@example.org", "07700 900123"]]  # only the member who agreed
    boats = web.get(_url("boats")).json()
    assert boats["rows"][0][1:6] == ["Puffin", "Laser", "123456", 1100, "Ann Sailor"]  # the first six columns never change
    coming = web.get(_url("events")).json()
    row = dict(zip(coming["columns"], coming["rows"][0], strict=True))
    assert row["Title"] == "Laying-up Supper" and row["Member price (£)"] == 25.0 and row["Visitor price (£)"] == 30.0 and row["Places left"] == 40
    assert row["Book at"].startswith("https://office.example.org/whats-on/")
    members = web.get(_url("siteMembers")).json()
    by_name = {r["Name"]: r for r in [dict(zip(members["columns"], row, strict=True)) for row in members["rows"]]}
    assert by_name["Ann Sailor"]["Email"] == "ann@example.org" and by_name["Quiet Person"]["Email"] == "quiet@example.org"


def test_used_request_ids_are_forgotten_after_a_day(club) -> None:
    from app.models import WebhookEvent

    with SessionLocal() as db:
        db.add(WebhookEvent(provider="feed:website", event_id="old-nonce-123", received_at=datetime.now(UTC) - timedelta(days=2)))
        db.commit()
    TestClient(app).get(_url("siteMembers"))
    with SessionLocal() as db:
        assert db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == "old-nonce-123")) is None
        assert db.scalar(select(Event.id)) is not None


def test_money_never_leaves_the_office(club) -> None:
    """Phase 2, D3: no who-owes, payments or money-summary feed exists at all, for any client."""
    for name in ("chase", "payments", "money", "members", "bookings", "census"):
        assert name not in feeds.FEEDS, name
        assert TestClient(app).get(_url(name)).status_code == 404
