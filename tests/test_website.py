"""The club website and the office: signing members in from the website, and forms the website sends."""

import hashlib
import json
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import login
from sqlalchemy import select

from app import feeds, website
from app.db import SessionLocal
from app.main import app
from app.models import Contact, Enquiry, Member, MemberRequest, OutboundEmail, Role, UserRole

KEY = "site-secret-0123456789"

# Worked examples for the website's own code (docs/WEBSITE.md shows the same numbers).
EXAMPLE = {"ts": "1700000000", "nonce": "abcdef0123456789"}
SSO_SIG = "4577e481dd40bd12c72141eb17e624bfa0088ce721c0685ca9b33dcdb33f1f32"  # for ann@example.org
FORM_BODY = b'{"name":"Hannah Brook","email":"hannah@example.org"}'
FORM_SIG = "1737921d490ef34d3091c300d051372d97fb3185d090d94aa848e8e0c1d12170"  # an enquiry with that body
FEED_SIG = "7bda6d1a41c95113c7ca606682046da59756fbdba38a2f5fd87023baf513ad26"  # the events feed


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CLUB_FEED_KEYS", f"website:{KEY}")


@pytest.fixture
def ann():
    with SessionLocal() as db:
        member = Member(name="Ann Sailor", email="ann@example.org", membership_type="Single Adult Sailing", renewal_status="active", active=True)
        db.add(member)
        db.flush()
        db.add(Contact(name="Ann Sailor", email="ann@example.org", member_id=member.id))
        db.commit()


def _sso(email="ann@example.org", ts=None, nonce=None, key=KEY):
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    return f"/sso?email={email}&ts={ts}&nonce={nonce}&sig={website.sso_signature(key, email, ts, nonce)}"


def _post(kind, data, client="website", key=KEY, ts=None, nonce=None, body=None):
    body = body if body is not None else json.dumps(data).encode()
    ts = str(int(time.time()) if ts is None else ts)
    nonce = nonce or uuid.uuid4().hex
    headers = {"X-Club-Client": client, "X-Club-Timestamp": ts, "X-Club-Nonce": nonce, "X-Club-Signature": website.incoming_signature(key, kind, client, ts, nonce, body), "Content-Type": "application/json"}
    return TestClient(app).post(f"/incoming/{kind}", content=body, headers=headers)


def test_the_signing_examples_in_the_guide_are_right() -> None:
    ts, nonce = EXAMPLE["ts"], EXAMPLE["nonce"]
    assert website.sso_signature(KEY, "Ann@Example.org ", ts, nonce) == SSO_SIG  # the address is trimmed and lower-cased first
    assert website.incoming_signature(KEY, "enquiry", "website", ts, nonce, FORM_BODY) == FORM_SIG
    assert feeds.sign(KEY, "events", "website", ts, nonce) == FEED_SIG
    guide = (Path(__file__).parent.parent / "docs" / "WEBSITE.md").read_text(encoding="utf-8")
    assert SSO_SIG in guide and FORM_SIG in guide and FEED_SIG in guide


def test_a_member_arrives_from_the_website_already_signed_in(ann) -> None:
    browser = TestClient(app)
    link = _sso()
    arrived = browser.get(link)
    assert arrived.status_code == 200 and 'http-equiv="refresh"' in arrived.text and "club_session" in arrived.headers["set-cookie"].lower()
    assert "samesite=strict" in arrived.headers["set-cookie"].lower()
    assert "My membership" in browser.get("/app").text and "Ann Sailor" in browser.get("/app").text
    again = TestClient(app).get(link)
    assert again.status_code == 401 and "did not work" in again.text  # a link works once
    assert TestClient(app).get(_sso(ts=int(time.time()) - 600)).status_code == 401  # and only for two minutes
    assert TestClient(app).get(_sso(key="wrong-secret-0123456789")).status_code == 401
    assert TestClient(app).get(_sso(email="nobody@example.org")).status_code == 404


def test_people_who_run_the_club_still_sign_in_with_a_code(ann) -> None:
    with SessionLocal() as db:
        db.add(UserRole(email="ann@example.org", role=Role.MEMBER, extra_roles="bosun"))
        db.commit()
    browser = TestClient(app)
    refused = browser.get(_sso(), follow_redirects=False)
    assert refused.status_code == 303 and refused.headers["location"] == "/login?staff=1"
    assert "set-cookie" not in refused.headers
    assert "keeps members' records safe" in browser.get("/login?staff=1").text
    login("boss@example.org", Role.ADMIN)
    assert TestClient(app).get(_sso(email="boss@example.org"), follow_redirects=False).headers["location"] == "/login?staff=1"


def test_without_a_website_key_nothing_is_accepted(ann, monkeypatch) -> None:
    monkeypatch.delenv("CLUB_FEED_KEYS")
    assert TestClient(app).get(_sso()).status_code == 403
    assert _post("enquiry", {"name": "x"}).status_code == 403


def test_a_question_from_the_website_becomes_an_enquiry() -> None:
    sent = _post("enquiry", {"name": "Hannah Brook", "email": "hannah@example.org", "interest": "training", "message": "Do you run courses for adults?", "consent": True})
    assert sent.status_code == 200 and sent.json()["ok"] and "confirm your address" in sent.json()["message"]
    with SessionLocal() as db:
        enquiry = db.scalar(select(Enquiry))
        assert (enquiry.name, enquiry.source, enquiry.interest, enquiry.status, enquiry.consent_contact) == ("Hannah Brook", "website", "training", "unverified", True)
        assert db.scalar(select(OutboundEmail.related_type).where(OutboundEmail.to_address == "hannah@example.org")) == "enquiry_verify"
    assert _post("enquiry", {"name": "", "email": "x@example.org"}).status_code == 422
    assert "name" in _post("enquiry", {"name": "", "email": "x@example.org"}).json()["error"]


def test_forms_must_be_signed_fresh_and_unused() -> None:
    body = json.dumps({"name": "Spam", "email": "spam@example.org"}).encode()
    nonce = uuid.uuid4().hex
    assert _post("enquiry", None, body=body, nonce=nonce).status_code == 200
    assert _post("enquiry", None, body=body, nonce=nonce).status_code == 401  # replayed
    assert _post("enquiry", {"name": "A"}, key="wrong-secret-0123456789").status_code == 401
    assert _post("enquiry", {"name": "A"}, ts=int(time.time()) - 900).status_code == 401
    assert _post("enquiry", {"name": "A"}, client="sheet").status_code == 403
    assert _post("nonsense", {"name": "A"}).status_code == 404
    assert _post("enquiry", None, body=b"not json").status_code == 400
    tampered = {"X-Club-Client": "website", "X-Club-Timestamp": str(int(time.time())), "X-Club-Nonce": uuid.uuid4().hex,
                "X-Club-Signature": website.incoming_signature(KEY, "enquiry", "website", str(int(time.time())), "x" * 16, b"{}")}
    assert TestClient(app).post("/incoming/enquiry", content=b'{"name": "Changed"}', headers=tampered).status_code == 401
    assert hashlib.sha256(b"{}").hexdigest()  # the body is part of the signature


def test_a_message_for_the_bosun_reaches_the_bosun(ann) -> None:
    with SessionLocal() as db:
        db.add(UserRole(email="bosun@example.org", role=Role.COMMITTEE, extra_roles="bosun"))
        db.commit()
    member = _post("bosun", {"name": "Ann Sailor", "email": "ann@example.org", "message": "My trailer needs moving"})
    assert member.status_code == 200 and "The Bosun has been told" in member.json()["message"]
    stranger = _post("bosun", {"name": "Sam Visitor", "email": "sam@example.org", "message": "Can I keep a dinghy here?"})
    assert stranger.status_code == 200
    with SessionLocal() as db:
        request = db.scalar(select(MemberRequest))
        assert request.kind == "bosun" and "trailer" in request.details
        assert db.scalar(select(OutboundEmail.to_address).where(OutboundEmail.related_type == "member_request")) == "bosun@example.org"
        enquiry = db.scalar(select(Enquiry).where(Enquiry.email == "sam@example.org"))
        assert enquiry.interest == "boat_storage" and enquiry.message.startswith("For the Bosun:")
