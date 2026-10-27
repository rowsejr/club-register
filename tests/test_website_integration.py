"""What the club website needs from the office (INTEGRATION-CHANGES.md and the later one-email-per-person decision): the version, every
member for the website, boats, event kinds, courses, the berth switch, signing a member in, members changing their
own email, one email per person, committee posts and the narrower access to the books."""

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

from app import digests, events, feeds, people, permissions, website
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

SITE = "site-secret-0123456789"


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("CLUB_FEED_KEYS", f"website:{SITE}")
    monkeypatch.setenv("CLUB_BASE_URL", "https://office.example.org")


@pytest.fixture
def family():
    """Ann holds the membership; Bob, her partner, is a member in his own right with his own address (Phase 2, D6:
    one email per person — nobody shares an address any more)."""
    with SessionLocal() as db:
        ann = Member(name="Ann Sailor", email="ann@example.org", membership_type="Sailing Adult + Partner", renewal_status="active", active=True, expiry_date=date(2026, 12, 31))
        bob = Member(name="Bob Sailor", email="bob@example.org", membership_type="Sailing Adult + Partner", renewal_status="active", active=True, expiry_date=date(2026, 12, 31))
        gone = Member(name="Old Hand", email="old@example.org", membership_type="Single Adult Sailing", renewal_status="lapsed", active=False, expiry_date=date.today() - timedelta(days=900))
        db.add_all([ann, bob, gone])
        db.flush()
        ann_c = Contact(name="Ann Sailor", email="ann@example.org", phone="07700 900111", member_id=ann.id)
        bob_c = Contact(name="Bob Sailor", email="bob@example.org", phone="07700 900222", member_id=bob.id)
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
    assert columns == ["Member id", "Name", "Email", "Mobile", "Membership", "Status", "Until", "Household", "Hats", "Updated", "Committee post", "External ref"]
    by_name = {r["Name"]: r for r in rows}
    assert "Old Hand" not in by_name  # gone for more than 13 months
    ann, bob = by_name["Ann Sailor"], by_name["Bob Sailor"]
    assert (ann["Member id"], ann["Email"], ann["Mobile"], ann["Status"], ann["Until"]) == (family["ann"], "ann@example.org", "07700 900111", "active", "2026-12-31")
    assert bob["Email"] == "bob@example.org" and bob["Mobile"] == "07700 900222"  # Bob has his own address (Phase 2, D6)
    assert ann["Household"] == bob["Household"] == min(family["ann"], family["bob"])  # grouped by the family link, not a shared address
    assert ann["Hats"] == "Committee, Treasurer, Bosun" and bob["Hats"] == ""  # a role belongs to the one address that holds it
    assert re.search(r"[+-]\d\d:\d\d$", ann["Updated"])
    assert ann["Committee post"] == "" and ann["External ref"] == ""
    flat = str(rows)
    for private in ("birth", "medical", "consent", "invoice"):
        assert private not in flat.lower()


def test_hats_are_named_as_the_website_expects() -> None:
    assert feeds.hats_label(None) == ""
    assert feeds.hats_label(UserRole(email="a", role=Role.ADMIN, extra_roles="")) == "Admin"
    assert feeds.hats_label(UserRole(email="a", role=Role.MEMBER, extra_roles="social,training,race_box")) == "Social, Training, Race Box"


def test_the_boats_feed_keeps_its_first_columns_and_adds_owner_ids_and_a_ref(family) -> None:
    with SessionLocal() as db:
        db.add_all([
            Boat(name="Puffin", boat_class="Laser", sail_number="1", owner_member_id=family["ann"], insurance_expiry=date(2027, 3, 1), external_ref="old-42"),
            Boat(name="Club Wayfarer", boat_class="Wayfarer", sail_number="2", is_club_boat=True),
            Boat(name="Sold Boat", boat_class="Topper", sail_number="3", owner_member_id=family["bob"], status="archived"),
        ])
        db.commit()
    rows, columns = _feed("boats")
    assert columns[:6] == ["Id", "Name", "Class", "Sail number", "Portsmouth number", "Owner"]
    assert columns[6:] == ["Boat id", "Owner member id", "Club boat", "Status", "Insurance until", "Updated", "External ref"]
    assert not any("space" in c.lower() or "berth" in c.lower() or "location" in c.lower() for c in columns)
    by_name = {r["Name"]: r for r in rows}
    assert (by_name["Puffin"]["Owner member id"], by_name["Puffin"]["Club boat"], by_name["Puffin"]["Insurance until"], by_name["Puffin"]["External ref"]) == (family["ann"], "no", "2027-03-01", "old-42")
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


def _sso(email, key=SITE):
    ts, nonce = str(int(time.time())), uuid.uuid4().hex
    return f"/sso?email={email}&ts={ts}&nonce={nonce}&sig={website.sso_signature(key, email, ts, nonce)}"


def test_a_member_signs_in_from_the_website(family) -> None:
    with SessionLocal() as db:
        db.execute(UserRole.__table__.delete())  # neither Ann nor Bob is staff for this test
        db.commit()
    browser = TestClient(app)
    assert browser.get(_sso("bob@example.org")).status_code == 200
    assert "Bob Sailor" in browser.get("/app").text
    with SessionLocal() as db:
        assert db.scalar(select(SessionRecord.email)) == "bob@example.org"
    assert TestClient(app).get(_sso("nobody@example.org")).status_code == 404
    assert TestClient(app).get(_sso("bob@example.org", key="wrong-secret-0123456789")).status_code == 401


def test_a_stale_member_id_from_an_old_website_build_is_simply_ignored(family) -> None:
    """Phase 1 let /sso name one member of a family that shared an address; Phase 2 removed that (D6: addresses are
    unique). A website still sending the old ``member_id`` parameter is just ignored, not refused."""
    with SessionLocal() as db:
        db.execute(UserRole.__table__.delete())
        db.commit()
    link = _sso("bob@example.org") + f"&member_id={family['ann']}"
    assert TestClient(app).get(link).status_code == 200
    with pytest.raises(TypeError):
        website.sso_signature(SITE, "bob@example.org", "1", "n", "2")  # the parameter is gone, not just optional


def test_staff_never_sign_in_from_the_website(family) -> None:
    refused = TestClient(app).get(_sso("ann@example.org"), follow_redirects=False)
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
        with pytest.raises(people.EmailChangeError, match="administrator"):
            # Ann's own address is a volunteer's (from the fixture's UserRole), so even she cannot self-serve it
            people.request_email_change(db, db.get(Member, family["ann"]), None, "new@example.org", "ann@example.org")


def test_one_email_address_belongs_to_one_person(family) -> None:
    """Phase 2, D6: admin screens refuse to give someone an address another person already holds."""
    with SessionLocal() as db:
        with pytest.raises(people.EmailChangeError, match="Another"):
            people.ensure_unique_email(db, "ann@example.org")
        ann_contact_id = db.scalar(select(Contact.id).where(Contact.member_id == family["ann"]))
        people.ensure_unique_email(db, "ann@example.org", current="ann@example.org", exclude_member_id=family["ann"], exclude_contact_id=ann_contact_id)  # unchanged: no clash
    admin = login("boss@example.org", Role.ADMIN)
    token = csrf(admin, "/app/contacts/new")
    clash = admin.post("/app/contacts", data={"csrf": token, "name": "New Person", "email": "bob@example.org"}, follow_redirects=True)
    assert "Another" in clash.text and "already uses" in clash.text
    token2 = csrf(admin, f"/app/members/{family['ann']}")
    unchanged = admin.post(f"/app/members/{family['ann']}", data={"csrf": token2, "name": "Ann Sailor", "email": "ann@example.org", "expiry_date": "2026-12-31"}, follow_redirects=True)
    assert unchanged.status_code == 200 and "Saved" in unchanged.text  # keeping your own address is never a clash


def test_an_administrator_sets_a_committee_post_but_nobody_else_can(family) -> None:
    admin = login("boss@example.org", Role.ADMIN)
    form = admin.get(f"/app/members/{family['ann']}").text
    assert 'name="committee_post"' in form and "Treasurer" in form
    token = csrf(admin, f"/app/members/{family['ann']}")
    admin.post(f"/app/members/{family['ann']}", data={"csrf": token, "name": "Ann Sailor", "email": "ann@example.org", "expiry_date": "2026-12-31", "committee_post": "Treasurer"}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.get(Member, family["ann"]).committee_post == "Treasurer"
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    page = secretary.get(f"/app/members/{family['bob']}").text
    assert 'name="committee_post"' not in page  # not an administrator: cannot set it
    token2 = csrf(secretary, f"/app/members/{family['bob']}")
    secretary.post(f"/app/members/{family['bob']}", data={"csrf": token2, "name": "Bob Sailor", "email": "bob@example.org", "expiry_date": "2026-12-31", "committee_post": "Commodore"}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.get(Member, family["bob"]).committee_post is None  # the post in the POST body was ignored
    rows, _ = _feed("siteMembers")
    assert {r["Name"]: r["Committee post"] for r in rows}["Ann Sailor"] == "Treasurer"


def test_the_books_are_seen_only_by_the_treasurer_and_the_administrator() -> None:
    assert "finance.read" not in permissions.BASE_PERMISSIONS["Committee"]
    for hat in ("secretary", "bosun", "social", "training"):
        assert "finance.read" not in permissions.HATS[hat].perms, hat
    assert "finance.read" in permissions.HATS["treasurer"].perms
    assert permissions.HATS["race_box"].perms == frozenset()


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
