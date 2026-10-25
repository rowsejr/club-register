"""The filters on Members and Contacts must narrow the list, and the dashboard must count memberships, not people."""

import re
from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import membership
from app.db import SessionLocal
from app.models import Boat, Contact, ContactList, Invoice, ListMembership, Member, Membership, Role

TODAY = date.today()


def _person(db, name, *, type_name="Single Adult Sailing", status="active", born=None, boat=False, owes=False, on_list=None, email=None):
    member = Member(name=name, email=email or f"{name.split()[0].lower()}@example.org", membership_type=type_name, renewal_status=status, active=status == "active")
    db.add(member)
    db.flush()
    contact = Contact(member_id=member.id, name=name, email=member.email, birth_date=born)
    db.add(contact)
    db.flush()
    if boat:
        db.add(Boat(name=f"{name} boat", owner_contact_id=contact.id))
    if owes:
        db.add(Invoice(reference=f"INV-{name}", amount_minor=1000, status="issued", contact_id=contact.id, kind="invoice"))
    if on_list:
        lst = db.scalar(select(ContactList).where(ContactList.name == on_list)) or ContactList(name=on_list)
        db.add(lst)
        db.flush()
        db.add(ListMembership(contact_id=contact.id, list_id=lst.id))
    return member


@pytest.fixture
def people():
    with SessionLocal() as db:
        _person(db, "Adult Active", born=date(1980, 1, 1), boat=True)
        _person(db, "Junior Active", type_name="Sailing Family 2A+2C", born=TODAY - timedelta(days=365 * 10), on_list="Juniors")
        _person(db, "Lapsed Lee", status="lapsed", born=date(1975, 5, 5), owes=True)
        _person(db, "Resigned Rae", status="resigned", on_list="Committee")
        db.add(Membership(member_id=1, plan_name="Single Adult Sailing", starts_on=date(TODAY.year, 1, 1), ends_on=date(TODAY.year, 12, 31), status="active"))
        db.add(Membership(member_id=2, plan_name="Sailing Family 2A+2C", starts_on=date(TODAY.year, 1, 1), ends_on=date(TODAY.year, 12, 31), status="active"))
        db.add(Membership(member_id=4, plan_name="Single Adult Sailing", starts_on=date(TODAY.year - 1, 1, 1), ends_on=date(TODAY.year - 1, 12, 31), status="active"))
        db.commit()
    return login("sec@example.org", Role.ADMIN)


def _names(client, path: str) -> list[str]:
    html = client.get(path).text
    kind = "members" if "/members" in path else "contacts"
    return sorted(set(re.findall(rf'<td><a href="/app/{kind}/\d+">([^<]+)</a>', html)))


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["Adult Active", "Junior Active", "Lapsed Lee", "Resigned Rae"]),
        ("status=active", ["Adult Active", "Junior Active"]),
        ("status=inactive", ["Lapsed Lee", "Resigned Rae"]),
        ("status=lapsed", ["Lapsed Lee"]),
        ("status=resigned", ["Resigned Rae"]),
        ("type=Sailing+Family+2A%2B2C", ["Junior Active"]),
        ("age=junior", ["Junior Active"]),
        ("age=adult", ["Adult Active", "Lapsed Lee"]),
        ("age=unknown", ["Resigned Rae"]),
        ("list=Juniors", ["Junior Active"]),
        ("list=Committee", ["Resigned Rae"]),
        ("boat=yes", ["Adult Active"]),
        ("unpaid=yes", ["Lapsed Lee"]),
        ("status=active&boat=no", ["Junior Active"]),
        ("q=junior", ["Junior Active"]),
    ],
)
def test_member_filters_narrow_the_list(people, query, expected) -> None:
    assert _names(people, f"/app/members?{query}") == expected


def test_contact_filters_work_the_same_way(people) -> None:
    with SessionLocal() as db:
        db.add(Contact(name="Plain Contact"))
        db.commit()
    assert _names(people, "/app/contacts?member=no") == ["Plain Contact"]
    assert _names(people, "/app/contacts?age=junior") == ["Junior Active"]
    assert _names(people, "/app/contacts?list=Committee") == ["Resigned Rae"]
    assert _names(people, "/app/contacts?status=lapsed") == ["Lapsed Lee"]
    assert _names(people, "/app/contacts?type=Sailing+Family+2A%2B2C&age=junior") == ["Junior Active"]


def test_filtered_export_matches_the_screen(people) -> None:
    csv = people.get("/app/members.csv?status=lapsed").text.splitlines()
    assert len(csv) == 2 and "Lapsed Lee" in csv[1]


def test_dashboard_counts_memberships_in_force_not_people(people) -> None:
    with SessionLocal() as db:
        assert membership.active_memberships(db) == 2  # last year's active membership does not count
    assert "What needs doing" in people.get("/app").text


@pytest.mark.parametrize("type_name", ["Sailing Family 2A+2C", "Sailing Adult + Partner", "Single Adult Sailing", "Non Sailing + 2C", "Mystery"])
def test_household_capacity_is_read_from_the_type_name(type_name) -> None:
    expected = {"Sailing Family 2A+2C": 4, "Sailing Adult + Partner": 2, "Single Adult Sailing": 1, "Non Sailing + 2C": 3, "Mystery": None}
    assert membership.household_capacity(type_name) == expected[type_name]


def test_adding_a_member_by_hand_gives_them_a_membership_and_number(people) -> None:
    token = csrf(people, "/app/members/new")
    people.post(
        "/app/members",
        data={"csrf": token, "name": "Hand Added", "email": "hand@example.org", "membership_type": "Single Adult Sailing", "renewal_status": "active", "payment_status": "paid"},
        follow_redirects=True,
    )
    with SessionLocal() as db:
        added = db.scalar(select(Member).where(Member.email == "hand@example.org"))
        assert added and added.membership_number
        row = db.scalar(select(Membership).where(Membership.member_id == added.id))
        assert row and row.plan_name == "Single Adult Sailing" and row.status == "active"
        assert membership.active_memberships(db) == 3
