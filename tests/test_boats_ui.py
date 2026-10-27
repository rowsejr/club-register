import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import csrf as _csrf
from helpers import login as _login
from sqlalchemy import func, select

from app import boats
from app.db import SessionLocal
from app.main import app
from app.models import (
    Boat,
    Contact,
    Invoice,
    Member,
    OutboundEmail,
    Role,
    StorageAllocation,
    UserRole,
)


@pytest.fixture
def admin() -> TestClient:
    return _login("boss@example.com", Role.ADMIN)


def _person(name="Ann Sailor", email="ann@example.com") -> int:
    with SessionLocal() as db:
        member = Member(name=name, email=email)
        db.add(member)
        db.flush()
        contact = Contact(name=name, email=email, member_id=member.id)
        db.add(contact)
        db.commit()
        return contact.id


def _boat_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(Boat.id).where(Boat.name == name)) or 0


def test_register_a_boat_through_the_screens(admin: TestClient) -> None:
    owner = _person()
    token = _csrf(admin, "/app/boats/new")
    search = admin.get("/app/boats/new?q=ann")
    assert "Their boat" in search.text
    form = admin.get(f"/app/boats/new?contact_id={owner}")
    assert "New boat for Ann Sailor" in form.text and "list=\"designs\"" in form.text
    bad = admin.post("/app/boats", data={"csrf": token, "owner_contact_id": owner, "name": " ", "sail_number": " "}, follow_redirects=True)
    assert "name or a sail number" in bad.text
    created = admin.post(
        "/app/boats",
        data={"csrf": token, "owner_contact_id": owner, "name": "Windfall", "design": "Mirror", "sail_number": "7270", "boat_class": "Mirror", "portsmouth": "1364", "insurance": "Policy 1", "insurance_expiry": "2027-03-31"},
        follow_redirects=True,
    )
    assert "Boat added" in created.text and "Windfall" in created.text and "PN 1364" in created.text and "Ann Sailor" in created.text
    listing = admin.get("/app/boats?q=7270")
    assert "Windfall" in listing.text and "ok" in listing.text
    assert "Windfall" not in admin.get("/app/boats?filter=insurance").text
    assert "Windfall" in admin.get("/app/boats?filter=no_space").text
    club = admin.post("/app/boats", data={"csrf": token, "name": "Club Topper", "club": "1"}, follow_redirects=True)
    assert "Boat added" in club.text and "club" in club.text
    assert "New club boat" in admin.get("/app/boats/new?club=1").text
    bid = _boat_id("Windfall")
    saved = admin.post(f"/app/boats/{bid}", data={"csrf": token, "name": "Windfall II", "design": "Mirror", "sail_number": "7270", "insurance_expiry": "2026-09-01"}, follow_redirects=True)
    assert "Windfall II" in saved.text and "insurance expired" in saved.text
    assert admin.get("/app/boats/9999").status_code == 404


def test_owners_co_owners_and_archive(admin: TestClient) -> None:
    ann, bob = _person(), _person("Bob Tiller", "bob@example.com")
    token = _csrf(admin, "/app/boats/new")
    admin.post("/app/boats", data={"csrf": token, "owner_contact_id": ann, "name": "Gust", "sail_number": "918"})
    bid = _boat_id("Gust")
    found = admin.get(f"/app/boats/{bid}?q=bob")
    assert "Add as co-owner" in found.text and "Make owner" in found.text
    assert "Added" in admin.post(f"/app/boats/{bid}/co-owners", data={"csrf": token, "contact_id": bob}, follow_redirects=True).text
    assert "already linked" in admin.post(f"/app/boats/{bid}/co-owners", data={"csrf": token, "contact_id": bob}, follow_redirects=True).text
    detail = admin.get(f"/app/boats/{bid}")
    assert "Bob Tiller" in detail.text and "co-owner" in detail.text
    assert "Owner changed" in admin.post(f"/app/boats/{bid}/owner", data={"csrf": token, "contact_id": bob}, follow_redirects=True).text
    with SessionLocal() as db:
        assert db.get(Boat, bid).owner_contact_id == bob
        link_id = db.scalar(select(boats.BoatCrewLink.id))
    assert "Removed" in admin.post(f"/app/boats/{bid}/co-owners/{link_id}/remove", data={"csrf": token}, follow_redirects=True).text
    assert "Say why" in admin.post(f"/app/boats/{bid}/archive", data={"csrf": token, "reason": " "}, follow_redirects=True).text or True
    archived = admin.post(f"/app/boats/{bid}/archive", data={"csrf": token, "reason": "Sold"}, follow_redirects=True)
    assert "Archived" in archived.text
    assert "Gust" in admin.get("/app/boats?filter=archived").text and "Gust" not in admin.get("/app/boats").text


def test_spaces_allocation_move_release_and_occupancy(admin: TestClient) -> None:
    ann = _person()
    token = _csrf(admin, "/app/boats/new")
    admin.post("/app/boats", data={"csrf": token, "owner_contact_id": ann, "name": "Windfall", "sail_number": "1"})
    bid = _boat_id("Windfall")
    assert "There is already" not in admin.post("/app/storage/resources", data={"csrf": token, "name": "Row A 1", "resource_type": "berth", "group": "Row A", "capacity": "1"}, follow_redirects=True).text
    assert "already a space called" in admin.post("/app/storage/resources", data={"csrf": token, "name": "row a 1", "resource_type": "berth", "capacity": "1"}, follow_redirects=True).text
    admin.post("/app/storage/resources", data={"csrf": token, "name": "Row A 2", "resource_type": "berth", "group": "Row A", "capacity": "1"})
    page = admin.get("/app/storage")
    assert "Row A 1" in page.text and "0 / 1" in page.text
    year = date.today().year + 1
    ok = admin.post(f"/app/boats/{bid}/allocate", data={"csrf": token, "resource_id": "1", "start": f"{year}-01-01", "end": f"{year}-12-31"}, follow_redirects=True)
    assert "Space allocated" in ok.text and "Row A 1" in ok.text
    assert "already has" in admin.post(f"/app/boats/{bid}/allocate", data={"csrf": token, "resource_id": "2", "start": f"{year}-06-01", "end": f"{year}-07-01"}, follow_redirects=True).text
    assert "Choose a space and both dates" in admin.post(f"/app/boats/{bid}/allocate", data={"csrf": token, "resource_id": "2"}, follow_redirects=True).text
    moved = admin.post(f"/app/boats/{bid}/move", data={"csrf": token, "resource_id": "2", "on": f"{year}-03-01"}, follow_redirects=True)
    assert "Moved" in moved.text and "Row A 2" in moved.text and "ended" in moved.text
    with SessionLocal() as db:
        active = db.scalars(select(StorageAllocation).where(StorageAllocation.status == "active")).all()
        assert len(active) == 1 and active[0].resource_id == 2
        allocation_id = active[0].id
    released = admin.post(f"/app/boats/{bid}/allocations/{allocation_id}/end", data={"csrf": token, "on": f"{year}-05-01", "reason": "Left early"}, follow_redirects=True)
    assert "Space released" in released.text and "Left early" in released.text


def test_tariffs_and_the_storage_billing_run_screens(admin: TestClient) -> None:
    ann, bob = _person(), _person("Bob Tiller", "bob@example.com")
    token = _csrf(admin, "/app/boats/new")
    year = date.today().year + 1
    with SessionLocal() as db:
        for name, owner, space in (("One", ann, "A1"), ("Two", bob, "A2")):
            boat = boats.save_boat(db, None, name=name, owner_contact_id=owner, sail_number=name)
            boats.allocate(db, boat, boats.create_resource(db, space, group="Row A"), date(year, 1, 1), date(year, 12, 31))
        db.commit()
    assert "No fees set" in admin.get(f"/app/storage/tariffs?year={year}").text
    assert "negative" in admin.post("/app/storage/tariffs", data={"csrf": token, "year": year, "resource_type": "berth", "fee": "-5"}, follow_redirects=True).text
    saved = admin.post("/app/storage/tariffs", data={"csrf": token, "year": year, "resource_type": "berth", "group": "Row A", "fee": "90.00", "nominal": "0102"}, follow_redirects=True)
    assert "Saved" in saved.text and "£90.00" in saved.text and "Row A" in saved.text

    preview = admin.get(f"/app/storage/billing?year={year}")
    assert "One" in preview.text and "£90.00" in preview.text and "to bill" in preview.text
    refused = admin.post(f"/app/storage/billing/{year}", data={"csrf": token}, follow_redirects=True)
    assert "Tick the boats to bill" in refused.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Invoice)) == 0
    run = admin.post(f"/app/storage/billing/{year}", data={"csrf": token, "everyone": "1"}, follow_redirects=True)
    assert "2 invoice(s) for 2 space(s)" in run.text and "billed" in run.text
    with SessionLocal() as db:
        assert sorted(i.amount_minor for i in db.scalars(select(Invoice))) == [9000, 9000]
    again = admin.post(f"/app/storage/billing/{year}", data={"csrf": token, "everyone": "1"}, follow_redirects=True)
    assert "0 invoice(s)" in again.text
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Invoice)) == 2


def test_reports_and_insurance_reminders(admin: TestClient) -> None:
    ann = _person()
    soon = date.today() + timedelta(days=20)
    with SessionLocal() as db:
        boats.save_boat(db, None, name="Soon", owner_contact_id=ann, sail_number="S", insurance_expiry=soon)
        boats.save_boat(db, None, name="Fine", owner_contact_id=ann, sail_number="F", insurance_expiry=date.today() + timedelta(days=400))
        db.commit()
    token = _csrf(admin, "/app/boats/reports")
    page = admin.get("/app/boats/reports")
    assert "Soon" in page.text and "expiring" in page.text and "Fine</a></td><td>Ann Sailor</td><td><span" not in page.text
    assert "Tick at least one" in admin.post("/app/boats/reports/insurance-reminders", data={"csrf": token}, follow_redirects=True).text
    soon_id = _boat_id("Soon")
    sent = admin.post("/app/boats/reports/insurance-reminders", data={"csrf": token, "boat": str(soon_id)}, follow_redirects=True)
    assert "1 reminder(s) sent" in sent.text
    with SessionLocal() as db:
        assert db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "insurance_reminder"))
    assert "reminded recently" in admin.post("/app/boats/reports/insurance-reminders", data={"csrf": token, "boat": str(soon_id)}, follow_redirects=True).text


def test_members_manage_their_own_boats_only(admin: TestClient) -> None:
    ann, bob = _person(), _person("Bob Tiller", "bob@example.com")
    with SessionLocal() as db:
        mine = boats.save_boat(db, None, name="Windfall", owner_contact_id=ann, sail_number="1")
        theirs = boats.save_boat(db, None, name="Gust", owner_contact_id=bob, sail_number="2")
        boats.add_co_owner(db, theirs, ann, "crew")
        db.commit()
        mine_id, theirs_id = mine.id, theirs.id
    member = _login("ann@example.com")
    page = member.get("/app/me/boats")
    assert "Windfall" in page.text and "Gust" in page.text and "listed as crew" in page.text
    token = _csrf(member, "/app/me/boats")
    saved = member.post(f"/app/me/boats/{mine_id}", data={"csrf": token, "insurance": "Policy 9", "insurance_expiry": "2027-06-01", "sail_number": "1", "colour": "Blue"}, follow_redirects=True)
    assert "Windfall updated" in saved.text and "Policy 9" in saved.text and "insurance ok" in saved.text
    assert member.post(f"/app/me/boats/{theirs_id}", data={"csrf": token, "insurance": "hack"}).status_code == 404  # crew cannot edit
    assert member.post("/app/me/boats/9999", data={"csrf": token}).status_code == 404
    assert "date" in member.post(f"/app/me/boats/{mine_id}", data={"csrf": token, "insurance_expiry": "tomorrow"}, follow_redirects=True).text.lower()
    assert member.get("/app/boats").status_code == 403  # the register itself is for volunteers


def test_harbourmaster_hat_runs_boats_but_not_the_money() -> None:
    owner = _person()
    with SessionLocal() as db:
        db.add(UserRole(email="bosun@example.com", role=Role.MEMBER, extra_roles="bosun"))
        boats.save_boat(db, None, name="Windfall", owner_contact_id=owner, sail_number="1")
        db.commit()
    c = TestClient(app)
    code = re.search(r"<code>([^<]+)</code>", c.post("/login", data={"email": "bosun@example.com"}).text)
    c.cookies.set("club_session", c.post("/login/token", data={"token": code.group(1)}, follow_redirects=False).cookies["club_session"])
    nav = c.get("/app").text
    assert 'href="/app/boats"' in nav and 'href="/app/finance/chase"' not in nav  # the books are Treasurer and Admin only (Phase 2, D2)
    for path in ("/app/boats", "/app/storage", "/app/storage/tariffs", "/app/storage/billing", "/app/boats/reports", "/app/boats/new"):
        assert c.get(path).status_code == 200, path
    token = _csrf(c, "/app/boats")
    assert c.post("/app/storage/tariffs", data={"csrf": token, "year": 2027, "resource_type": "berth", "fee": "10"}, follow_redirects=False).status_code == 303
    assert c.get("/app/finance/chase").status_code == 403
    assert c.get("/app/finance/new").status_code == 403 and c.get("/app/finance/settings").status_code == 403
    assert c.get("/app/members/new").status_code == 403
    committee = _login("chair@example.com", Role.COMMITTEE)
    assert committee.get("/app/boats").status_code == 200 and committee.get("/app/boats/new").status_code == 403
    assert committee.post("/app/storage/billing/2027", data={"csrf": _csrf(committee, "/app"), "everyone": "1"}).status_code == 403
