"""What the club website and the committee's Sheet need from the office (INTEGRATION-CHANGES.md): the version, every
member for the website, boats, event kinds, courses, the berth switch, signing in one member of a family, and members
changing their own email address."""

import importlib.util
import re
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import csrf, login
from sqlalchemy import select

from app import digests, events, feeds, people, website
from app.db import SessionLocal
from app.main import app
from app.models import (
    Boat,
    Contact,
    ContactRelation,
    Member,
    OutboundEmail,
    Role,
    SessionRecord,
    TrainingCourse,
    UserRole,
)

SITE, SHEET = "site-secret-0123456789", "sheet-secret-0123456789"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CLUB_FEED_KEYS", f"website:{SITE},sheet:{SHEET}")
    monkeypatch.setenv("CLUB_BASE_URL", "https://office.example.org")


@pytest.fixture
def family():
    """Ann holds the membership and the email address; Bob, her partner, is a member who shares it."""
    with SessionLocal() as db:
        ann = Member(name="Ann Sailor", email="ann@example.org", membership_type="Sailing Adult + Partner", renewal_status="active", active=True, expiry_date=date(2026, 12, 31))
        bob = Member(name="Bob Sailor", email=None, membership_type="Sailing Adult + Partner", renewal_status="active", active=True, expiry_date=date(2026, 12, 31))
        gone = Member(name="Old Hand", email="old@example.org", membership_type="Single Adult Sailing", renewal_status="lapsed", active=False, expiry_date=date.today() - timedelta(days=900))
        db.add_all([ann, bob, gone])
        db.flush()
        ann_c = Contact(name="Ann Sailor", email="ann@example.org", phone="07700 900111", member_id=ann.id)
        bob_c = Contact(name="Bob Sailor", email="ann@example.org", phone="07700 900222", member_id=bob.id)
        db.add_all([ann_c, bob_c, Contact(name="Old Hand", email="old@example.org", member_id=gone.id)])
        db.flush()
        db.add(ContactRelation(from_contact_id=ann_c.id, to_contact_id=bob_c.id, relation_type="partner"))
        db.add(UserRole(email="ann@example.org", role=Role.COMMITTEE, extra_roles="treasurer, bosun"))
        db.commit()
        yield {"ann": ann.id, "bob": bob.id, "gone": gone.id}


def _feed(name, client="website", secret=SITE):
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    url = f"/feeds/{name}.json?client={client}&ts={ts}&nonce={nonce}&sig={feeds.sign(secret, name, client, ts, nonce)}"
    answer = TestClient(app).get(url)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return [dict(zip(body["columns"], row, strict=True)) for row in body["rows"]], body["columns"]


def test_anyone_can_see_which_version_is_running() -> None:
    answer = TestClient(app).get("/version").json()
    assert set(answer) == {"commit", "built"} and re.fullmatch(r"[0-9a-f]{7,}|unknown", answer["commit"])
    assert (Path(__file__).parent.parent / "app" / "version.txt").read_text(encoding="utf-8").startswith("$Format:")  # git archive fills it in
    assert "app/version.txt export-subst" in (Path(__file__).parent.parent / ".gitattributes").read_text(encoding="utf-8")


def test_the_website_gets_every_current_member_with_their_hats_and_household(family) -> None:
    rows, columns = _feed("siteMembers")
    assert columns == ["Member id", "Name", "Email", "Mobile", "Membership", "Status", "Until", "Household", "Hats", "Updated"]
    by_name = {r["Name"]: r for r in rows}
    assert "Old Hand" not in by_name  # gone for more than 13 months
    ann, bob = by_name["Ann Sailor"], by_name["Bob Sailor"]
    assert (ann["Member id"], ann["Email"], ann["Mobile"], ann["Status"], ann["Until"]) == (family["ann"], "ann@example.org", "07700 900111", "active", "2026-12-31")
    assert bob["Email"] == "ann@example.org" and bob["Mobile"] == "07700 900222"  # Bob uses Ann's address
    assert ann["Household"] == bob["Household"] == min(family["ann"], family["bob"])
    assert ann["Hats"] == "Committee, Treasurer, Bosun" and bob["Hats"] == "Committee, Treasurer, Bosun"  # hats follow the address
    assert re.search(r"[+-]\d\d:\d\d$", ann["Updated"])
    flat = str(rows)
    for private in ("birth", "medical", "consent", "invoice"):
        assert private not in flat.lower()


def test_the_sheet_gets_members_without_email_or_mobile(family) -> None:
    rows, columns = _feed("siteMembers", client="sheet", secret=SHEET)
    assert columns == ["Member id", "Name", "Membership", "Status", "Until", "Household", "Hats", "Updated"]
    assert "ann@example.org" not in str(rows) and "07700" not in str(rows)


def test_hats_are_named_as_the_website_expects() -> None:
    assert feeds.hats_label(None) == ""
    assert feeds.hats_label(UserRole(email="a", role=Role.ADMIN, extra_roles="")) == "Admin"
    assert feeds.hats_label(UserRole(email="a", role=Role.MEMBER, extra_roles="social,training,racing")) == "Social, Training"


def test_the_boats_feed_keeps_its_first_columns_and_adds_owner_ids(family) -> None:
    with SessionLocal() as db:
        db.add_all([
            Boat(name="Puffin", boat_class="Laser", sail_number="1", owner_member_id=family["ann"], insurance_expiry=date(2027, 3, 1)),
            Boat(name="Club Wayfarer", boat_class="Wayfarer", sail_number="2", is_club_boat=True),
            Boat(name="Sold Boat", boat_class="Topper", sail_number="3", owner_member_id=family["bob"], status="archived"),
        ])
        db.commit()
    rows, columns = _feed("boats")
    assert columns[:6] == ["Id", "Name", "Class", "Sail number", "Portsmouth number", "Owner"]
    assert columns[6:] == ["Boat id", "Owner member id", "Club boat", "Status", "Insurance until", "Updated"]
    assert not any("space" in c.lower() or "berth" in c.lower() or "location" in c.lower() for c in columns)
    by_name = {r["Name"]: r for r in rows}
    assert (by_name["Puffin"]["Owner member id"], by_name["Puffin"]["Club boat"], by_name["Puffin"]["Insurance until"]) == (family["ann"], "no", "2027-03-01")
    assert (by_name["Club Wayfarer"]["Owner member id"], by_name["Club Wayfarer"]["Club boat"]) == ("", "yes")
    assert by_name["Sold Boat"]["Status"] == "archived" and by_name["Puffin"]["Boat id"] == by_name["Puffin"]["Id"]
    assert re.search(r"[+-]\d\d:\d\d$", by_name["Puffin"]["Updated"])


def test_events_have_a_fixed_kind_and_say_whether_the_website_shows_them() -> None:
    soon = datetime.now(UTC) + timedelta(days=5)
    with SessionLocal() as db:
        events.save_event(db, None, title="Supper", start_at=soon, published=True, event_type="Dinner and dance")
        events.save_event(db, None, title="AGM", start_at=soon, published=True, event_type="meeting", website_show=False)
        db.commit()
    rows, columns = _feed("events")
    assert columns[-1] == "Website show"
    by_title = {r["Title"]: r for r in rows}
    assert (by_title["Supper"]["Kind"], by_title["Supper"]["Website show"]) == ("social", "yes")
    assert (by_title["AGM"]["Kind"], by_title["AGM"]["Website show"]) == ("meeting", "no")
    assert set(events.EVENT_KINDS) == {"social", "racing", "training", "meeting", "open_day", "other"}
    assert events.event_kind("Open meeting") == "meeting" and events.event_kind("") == "other" and events.event_kind("Open day") == "open_day"


def test_the_event_form_saves_the_website_choice() -> None:
    social = login("soc@example.org", Role.COMMITTEE, hats="social")
    form = social.get("/app/events/new").text
    assert 'name="website_show"' in form and '<select name="event_type">' in form
    social.post("/app/events", data={"csrf": csrf(social, "/app/events/new"), "title": "Quiz night", "start": "2030-01-10T19:00", "event_type": "social"}, follow_redirects=True)
    with SessionLocal() as db:
        quiz = db.scalar(select(events.Event).where(events.Event.title == "Quiz night"))
        assert quiz.website_show is False and quiz.event_type == "social"  # the box was not ticked


def test_courses_have_their_own_feed() -> None:
    soon = datetime.now(UTC) + timedelta(days=10)
    with SessionLocal() as db:
        db.add(TrainingCourse(name="Start Sailing", starts_at=soon, ends_at=soon + timedelta(hours=6), capacity=6, price_minor=9500, status="open", venue="Slipway", min_age=8))
        db.add(TrainingCourse(name="Draft Course", starts_at=soon, ends_at=soon, capacity=6, status="draft"))
        db.commit()
    rows, columns = _feed("courses")
    assert columns == ["Course id", "Title", "Starts", "Ends", "Where", "Level", "Price (£)", "Places left", "Minimum age", "Book at"]
    assert [r["Title"] for r in rows] == ["Start Sailing"]
    assert rows[0]["Price (£)"] == 95.0 and rows[0]["Places left"] == 6 and rows[0]["Book at"] == f"https://office.example.org/courses#course-{rows[0]['Course id']}"


def test_berths_can_be_switched_off_without_losing_anything(monkeypatch) -> None:
    with SessionLocal() as db:
        db.add(Boat(name="Puffin", boat_class="Laser", sail_number="1"))
        db.commit()
        boat_id = db.scalar(select(Boat.id))
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    assert "Waiting list" in bosun.get("/app/boats").text and bosun.get("/app/storage").status_code == 200  # on by default
    monkeypatch.setenv("CLUB_FEATURE_BERTHS", "off")
    page = bosun.get("/app/boats").text
    assert "Waiting list" not in page and "Spaces" not in page and "Space now" not in page and "No space" not in page
    for path in ("/app/storage", "/app/storage/waiting", "/app/storage/season", "/app/storage/tariffs", "/app/storage/billing"):
        assert bosun.get(path).status_code == 404, path
    assert "Give a space" not in bosun.get(f"/app/boats/{boat_id}").text and "Puffin" in bosun.get(f"/app/boats/{boat_id}").text
    with SessionLocal() as db:
        assert [line.text for line in digests.bosun(db, date.today()).lines] == ["boats with expired, expiring or unknown insurance"]


def _sso(email, member_id="", key=SITE):
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    query = f"email={email}&ts={ts}&nonce={nonce}&sig={website.sso_signature(key, email, ts, nonce, member_id)}"
    return f"/sso?{query}&member_id={member_id}" if member_id else f"/sso?{query}"


def test_a_family_member_who_shares_an_address_signs_in_as_themselves(family) -> None:
    with SessionLocal() as db:
        db.execute(UserRole.__table__.delete())  # Ann is not staff for this test
        db.commit()
    browser = TestClient(app)
    assert browser.get(_sso("ann@example.org", str(family["bob"]))).status_code == 200
    home = browser.get("/app").text
    assert "Bob Sailor" in home and "Ann Sailor" not in home
    with SessionLocal() as db:
        assert db.scalar(select(SessionRecord.member_id).order_by(SessionRecord.id.desc())) == family["bob"]
    plain = TestClient(app)
    plain.get(_sso("ann@example.org"))
    assert "Ann Sailor" in plain.get("/app").text  # without member_id: the member who holds the address
    assert TestClient(app).get(_sso("ann@example.org", str(family["gone"]))).status_code == 404  # not that member's address
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    signed_for_ann = website.sso_signature(SITE, "ann@example.org", ts, nonce, str(family["ann"]))
    swapped = f"/sso?email=ann@example.org&ts={ts}&nonce={nonce}&sig={signed_for_ann}&member_id={family['bob']}"
    assert TestClient(app).get(swapped).status_code == 401  # the member id is part of the signature


def test_staff_never_sign_in_from_the_website_even_through_a_family_member(family) -> None:
    for link in (_sso("ann@example.org"), _sso("ann@example.org", str(family["bob"]))):
        refused = TestClient(app).get(link, follow_redirects=False)
        assert refused.status_code == 303 and refused.headers["location"] == "/login?staff=1"


def test_members_change_their_own_email_once_they_confirm_it(family) -> None:
    with SessionLocal() as db:
        db.execute(UserRole.__table__.delete())
        db.commit()
    ann = login("ann@example.org")
    token = csrf(ann, "/app/me/profile")
    assert "Change my email address" in ann.get("/app/me/profile").text
    asked = ann.post("/app/me/email", data={"csrf": token, "new_email": "Ann.New@Example.org"}, follow_redirects=True)
    assert "We have emailed a link to ann.new@example.org" in asked.text
    with SessionLocal() as db:
        assert db.get(Member, family["ann"]).email == "ann@example.org"  # nothing changes yet
        sent = db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "email_change")).all()
        assert {m.to_address for m in sent} == {"ann.new@example.org", "ann@example.org"}  # the old address is told too
        assert "not stored" in [m for m in sent if m.to_address == "ann.new@example.org"][0].body
        member, contact = db.get(Member, family["ann"]), db.scalar(select(Contact).where(Contact.member_id == family["ann"]))
        link = people.request_email_change(db, member, contact, "ann.new@example.org", "ann@example.org")
        db.commit()
    web = TestClient(app)
    page = web.get(f"/me/email/{link}")
    assert "Yes, change it" in page.text
    with SessionLocal() as db:
        assert db.get(Member, family["ann"]).email == "ann@example.org"  # opening the link alone changes nothing
    done = web.post(f"/me/email/{link}")
    assert "Your email address is now <strong>ann.new@example.org</strong>" in done.text
    with SessionLocal() as db:
        assert db.get(Member, family["ann"]).email == "ann.new@example.org"
        assert db.scalar(select(Contact.email).where(Contact.member_id == family["ann"])) == "ann.new@example.org"
    assert ann.get("/app", follow_redirects=False).status_code in {302, 303, 401}  # the old sign-in has ended
    assert web.post(f"/me/email/{link}").status_code == 400  # a link works once
    rows, _ = _feed("siteMembers")
    assert {r["Name"]: r["Email"] for r in rows}["Ann Sailor"] == "ann.new@example.org"


def test_email_changes_are_refused_when_they_should_be(family) -> None:
    with SessionLocal() as db:
        old, old_contact = db.get(Member, family["gone"]), db.scalar(select(Contact).where(Contact.member_id == family["gone"]))
        with pytest.raises(people.EmailChangeError, match="does not look like"):
            people.request_email_change(db, old, old_contact, "not an address", "old@example.org")
        with pytest.raises(people.EmailChangeError, match="Another member"):
            people.request_email_change(db, old, old_contact, "Ann@example.org", "old@example.org")
        with pytest.raises(people.EmailChangeError, match="already your"):
            people.request_email_change(db, old, old_contact, "old@example.org", "old@example.org")
        bob, bob_contact = db.get(Member, family["bob"]), db.scalar(select(Contact).where(Contact.member_id == family["bob"]))
        with pytest.raises(people.EmailChangeError, match="administrator"):
            people.request_email_change(db, bob, bob_contact, "bob@example.org", "ann@example.org")  # the shared address is a volunteer's


def test_the_cpanel_start_up_file_serves_the_office() -> None:
    pytest.importorskip("a2wsgi")
    path = Path(__file__).parent.parent / "deploy" / "cpanel" / "passenger_wsgi.py"
    spec = importlib.util.spec_from_file_location("passenger_wsgi", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replies = []
    body = b"".join(module.application({"REQUEST_METHOD": "GET", "PATH_INFO": "/health", "QUERY_STRING": "", "SERVER_NAME": "x", "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1",
                                        "wsgi.url_scheme": "http", "wsgi.input": __import__("io").BytesIO(b""), "wsgi.errors": __import__("sys").stderr}, lambda status, headers, exc=None: replies.append(status)))
    assert replies[0].startswith("200") and b'"status":"ok"' in body
