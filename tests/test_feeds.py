"""The signed read-only feeds for the committee's Sheet and the website: signing, refusals, replays and what each feed holds."""

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

SHEET, SITE, WRONG, SHORT = "sheet-secret-0123456789", "site-secret-0123456789", "wrong-secret-0123456789", "short"

# The same example is checked by the Sheet's own tests (sheet/tests/sign.test.js), so both sides sign identically.
VECTOR = {"secret": "test-secret-0123456789", "name": "members", "client": "sheet", "ts": "1700000000", "nonce": "abcdef0123456789",
          "sig": "9257ecfd5a118024972c84a9bd6fb6d2a8da02e07c802d328903b37ca6f2f3a6"}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CLUB_FEED_KEYS", f"sheet:{SHEET}, website:{SITE}")
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


def _url(name, client="sheet", secret=SHEET, ts=None, nonce=None):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    return f"/feeds/{name}.json?client={client}&ts={ts}&nonce={nonce}&sig={feeds.sign(secret, name, client, ts, nonce)}"


def test_the_signature_matches_the_shared_example() -> None:
    assert feeds.sign(VECTOR["secret"], VECTOR["name"], VECTOR["client"], VECTOR["ts"], VECTOR["nonce"]) == VECTOR["sig"]


def test_only_fresh_correctly_signed_requests_from_the_right_tool_are_answered(club) -> None:
    web = TestClient(app)
    good = _url("members")
    assert web.get(good).status_code == 200
    assert web.get(good).status_code == 401  # the same address cannot be used twice
    assert web.get(_url("members", ts=int(time.time()) - 900)).status_code == 401  # too old
    assert web.get(_url("members", secret=WRONG)).status_code == 401
    assert web.get(_url("members", client="website", secret=SITE)).status_code == 403  # the website may not read the members list
    assert web.get(_url("directory")).status_code == 403  # and the Sheet may not read the directory
    assert web.get(_url("nonsense")).status_code == 404
    assert web.get("/feeds/members.json").status_code in {401, 403}


def test_no_keys_means_no_feeds(club, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_FEED_KEYS", f"sheet:{SHORT}")  # a secret too short to be safe counts as none
    assert TestClient(app).get(_url("members", secret=SHORT)).status_code == 403


def test_the_committee_feeds_hold_what_the_sheet_shows(club) -> None:
    web = TestClient(app)
    members = web.get(_url("members")).json()
    assert members["columns"] == ["Name", "Number", "Membership", "Status", "Joined", "Until", "Money"]
    assert members["rows"][0][:4] == ["Ann Sailor", "7", "Single Adult Sailing", "active"] and members["rows"][0][-1] == "Overdue £108.00"
    assert "ann@example.org" not in str(members) and "07700" not in str(members)  # no contact details in the committee copy
    chase = web.get(_url("chase")).json()
    assert chase["rows"][0][:3] == ["Ann Sailor", 108.0, 15]
    census = web.get(_url("census")).json()
    assert "Female" in census["columns"] and any(row[-1] == 1 and row[1] == 1 for row in census["rows"])
    money = dict(web.get(_url("money")).json()["rows"])
    assert money["Owed to the club"] == 108.0 and money["Active members"] == 2
    assert web.get(_url("payments")).json()["rows"] == []
    assert web.get(_url("bookings")).json()["columns"][0] == "Event"


def test_the_website_feeds_respect_peoples_choices(club) -> None:
    web = TestClient(app)
    directory = web.get(_url("directory", client="website", secret=SITE)).json()
    assert directory["rows"] == [["Ann Sailor", "Single Adult Sailing", "ann@example.org", "07700 900123"]]  # only the member who agreed
    boats = web.get(_url("boats", client="website", secret=SITE)).json()
    assert boats["rows"][0][1:] == ["Puffin", "Laser", "123456", 1100, "Ann Sailor"]
    coming = web.get(_url("events", client="website", secret=SITE)).json()
    row = dict(zip(coming["columns"], coming["rows"][0], strict=True))
    assert row["Title"] == "Laying-up Supper" and row["Member price (£)"] == 25.0 and row["Visitor price (£)"] == 30.0 and row["Places left"] == 40
    assert row["Book at"].startswith("https://office.example.org/whats-on/")


def test_used_request_ids_are_forgotten_after_a_day(club) -> None:
    from app.models import WebhookEvent

    with SessionLocal() as db:
        db.add(WebhookEvent(provider="feed:sheet", event_id="old-nonce-123", received_at=datetime.now(UTC) - timedelta(days=2)))
        db.commit()
    TestClient(app).get(_url("money"))
    with SessionLocal() as db:
        assert db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == "old-nonce-123")) is None
        assert db.scalar(select(Event.id)) is not None
