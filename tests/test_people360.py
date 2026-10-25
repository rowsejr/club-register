"""Tags, lists, saved views, households, the whole-person page and the data check."""

from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import finance, people
from app.db import SessionLocal
from app.models import (
    Boat,
    Contact,
    ContactList,
    ContactTag,
    ListMembership,
    Member,
    Membership,
    ParticipantProfile,
    Role,
    SavedView,
)

TODAY = date.today()


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _person(db, name, *, email="auto", member=True, born=None, number="1", plan="Single Adult Sailing", expiry=None):
    if email == "auto":
        email = f"{name.split()[0].lower()}@example.org"
    m = None
    if member:
        m = Member(name=name, email=email, membership_type=plan, renewal_status="active", active=True, membership_number=number, expiry_date=expiry)
        db.add(m)
        db.flush()
        db.add(Membership(member_id=m.id, plan_name=plan, starts_on=date(TODAY.year, 1, 1), ends_on=date(TODAY.year, 12, 31), status="active"))
    contact = Contact(name=name, email=email, member_id=m.id if m else None, birth_date=born)
    db.add(contact)
    db.flush()
    return contact


def test_tags_are_tidied_unique_and_removable(db) -> None:
    ann = _person(db, "Ann Sailor")
    assert people.add_tag(db, ann, "  Volunteer, Safety  ") == "volunteer safety"
    people.add_tag(db, ann, "volunteer safety")
    people.add_tag(db, ann, "Junior Coach")
    assert people.tags_for(db, ann.id) == ["junior coach", "volunteer safety"]
    people.remove_tag(db, ann, "Junior Coach")
    assert people.tags_for(db, ann.id) == ["volunteer safety"]
    with pytest.raises(people.PeopleError, match="some text"):
        people.add_tag(db, ann, "  ,  ")


def test_lists_can_be_created_filled_and_emptied(db) -> None:
    ann, bob = _person(db, "Ann Sailor"), _person(db, "Bob Rower")
    committee = people.create_list(db, "Committee")
    with pytest.raises(people.PeopleError, match="already a list"):
        people.create_list(db, "committee")
    assert people.add_to_list(db, ann, committee) and not people.add_to_list(db, ann, committee)
    people.add_to_list(db, bob, committee)
    assert [c.name for c in people.list_members(db, committee)] == ["Ann Sailor", "Bob Rower"]
    assert [(lst.name, n) for lst, n in people.lists_with_counts(db)] == [("Committee", 2)]
    people.remove_from_list(db, bob, committee)
    assert db.scalar(select(ListMembership.id).where(ListMembership.contact_id == bob.id)) is None


def test_saved_views_are_shared_by_scope_and_overwrite_by_name(db) -> None:
    people.save_view(db, "members", "Juniors", "age=junior", "a@example.org")
    people.save_view(db, "members", "juniors", "age=junior&status=active", "b@example.org")
    people.save_view(db, "contacts", "Juniors", "age=junior", "a@example.org")
    assert [(v.name, v.query) for v in people.views_for(db, "members")] == [("Juniors", "age=junior&status=active")]
    assert len(people.views_for(db, "contacts")) == 1
    with pytest.raises(people.PeopleError):
        people.save_view(db, "boats", "x", "y", "a")
    with pytest.raises(people.PeopleError, match="name"):
        people.save_view(db, "members", " ", "age=junior", "a")


def test_the_whole_person_view_gathers_money_boats_and_household(db) -> None:
    ann = _person(db, "Ann Sailor", born=date(1980, 5, 5))
    kid = _person(db, "Kit Sailor", member=False, born=TODAY - timedelta(days=365 * 9), email=None)
    people.link_people(db, ann, kid, "child")
    db.add(Boat(name="Wavelet", owner_contact_id=ann.id))
    paid = finance.create_invoice(db, contact_id=ann.id, lines=[{"description": "Membership", "amount_minor": 10800, "nominal_code": None}])
    finance.issue(db, paid, "t")
    finance.record_payment(db, paid, amount_minor=10800, method="bank")
    owed = finance.create_invoice(db, contact_id=ann.id, lines=[{"description": "Boat park", "amount_minor": 11500, "nominal_code": None}])
    finance.issue(db, owed, "t")
    db.flush()
    person = people.person360(db, ann)
    assert person.balance == 11500 and person.paid_total == 10800 and len(person.invoices) == 2 and person.age == TODAY.year - 1980 - ((TODAY.month, TODAY.day) < (5, 5))
    assert [b.name for b, _ in person.boats] == ["Wavelet"] and [m.plan_name for m in person.memberships] == ["Single Adult Sailing"]
    assert [(rel, c.name) for rel, c in person.household.others] == [("child", "Kit Sailor")] and person.household.payer.id == ann.id
    child = people.person360(db, kid)
    assert [(rel, c.name) for rel, c in child.household.others] == [("parent", "Ann Sailor")] and child.household.payer.id == ann.id  # the member pays for the child


def test_linking_people_rejects_nonsense(db) -> None:
    ann, bob = _person(db, "Ann Sailor"), _person(db, "Bob Rower")
    with pytest.raises(people.PeopleError, match="themselves"):
        people.link_people(db, ann, ann, "partner")
    with pytest.raises(people.PeopleError, match="Unknown relationship"):
        people.link_people(db, ann, bob, "nemesis")
    people.link_people(db, ann, bob, "partner")
    with pytest.raises(people.PeopleError, match="already linked"):
        people.link_people(db, bob, ann, "partner")
    people.unlink_people(db, bob, ann)
    assert people.household_of(db, ann).others == []


def test_the_data_check_finds_the_usual_problems(db) -> None:
    _person(db, "No Email", email=None, number="2")
    _person(db, "Shared One", email="family@example.org", number="3")
    _person(db, "Shared Two", email="family@example.org", member=False)  # a family member with the same address, not a member in their own right
    _person(db, "Twin Name", number="5")
    _person(db, "Twin Name", email="other@example.org", number="6")
    _person(db, "No Number", number=None)
    _person(db, "Old Member", number="7", expiry=TODAY - timedelta(days=30))
    _person(db, "Standard Type", number="8", plan="standard")
    junior = _person(db, "Young Sailor", number="9", born=TODAY - timedelta(days=365 * 10))
    db.add(Boat(name="Orphan", status="active"))
    db.flush()
    found = {i.key: i for i in people.data_issues(db)}
    names = lambda key: sorted(c.name for c in found[key].contacts)  # noqa: E731
    assert names("no_email") == ["No Email"] and names("shared_email") == ["Shared One", "Shared Two"]
    assert names("duplicate_name") == ["Twin Name", "Twin Name"] and names("no_number") == ["No Number"] and names("expired") == ["Old Member"] and names("no_type") == ["Standard Type"]
    assert names("junior_consent") == ["Young Sailor"] and found["boat_no_owner"].extra == ["Orphan"]
    db.add(ParticipantProfile(contact_id=junior.id, consent_valid_to=TODAY + timedelta(days=100)))
    db.flush()
    assert [c.name for c in {i.key: i for i in people.data_issues(db)}["junior_consent"].contacts] == []  # consent on file clears it


@pytest.fixture
def secretary():
    return login("sec@example.org", Role.COMMITTEE, hats="secretary")


@pytest.fixture
def folk(db):
    ann = _person(db, "Ann Sailor", born=date(1980, 5, 5))
    bob = _person(db, "Bob Rower", number="2")
    db.commit()
    return {"ann": ann.id, "bob": bob.id}


def test_tagging_and_filtering_on_the_pages(secretary, folk) -> None:
    token = csrf(secretary, "/app/contacts")
    done = secretary.post(f"/app/contacts/{folk['ann']}/tags", data={"csrf": token, "tag": "Volunteer"}, follow_redirects=True)
    assert "Tagged “volunteer”" in done.text and "volunteer" in done.text
    assert "Ann Sailor" in secretary.get("/app/contacts?tag=volunteer").text and "Bob Rower" not in secretary.get("/app/contacts?tag=volunteer").text
    assert "Ann Sailor" in secretary.get("/app/members?tag=volunteer").text and "Bob Rower" not in secretary.get("/app/members?tag=volunteer").text
    assert "No tags" not in secretary.get(f"/app/contacts/{folk['ann']}").text
    secretary.post(f"/app/contacts/{folk['ann']}/tags/remove", data={"csrf": token, "tag": "volunteer"}, follow_redirects=True)
    with SessionLocal() as db2:
        assert db2.scalar(select(ContactTag.id)) is None


def test_lists_page_and_adding_a_person_to_a_list(secretary, folk) -> None:
    token = csrf(secretary, "/app/lists")
    assert "Created Committee" in secretary.post("/app/lists", data={"csrf": token, "name": "Committee"}, follow_redirects=True).text
    assert "already a list" in secretary.post("/app/lists", data={"csrf": token, "name": "Committee"}, follow_redirects=True).text
    with SessionLocal() as db2:
        list_id = db2.scalar(select(ContactList.id))
    assert "Added to Committee" in secretary.post(f"/app/contacts/{folk['ann']}/lists", data={"csrf": token, "list_id": list_id}, follow_redirects=True).text
    page = secretary.get(f"/app/lists/{list_id}")
    assert "Ann Sailor" in page.text and "Bob Rower" not in page.text and "Email this list" in page.text
    assert "Committee" in secretary.get("/app/lists").text
    assert secretary.get("/app/lists/999").status_code == 404


def test_saved_views_on_the_members_page(secretary, folk) -> None:
    token = csrf(secretary, "/app/members?age=adult")
    saved = secretary.post("/app/views", data={"csrf": token, "scope": "members", "name": "Adults", "query": "age=adult"}, follow_redirects=True)
    assert "Adults" in saved.text and "Ann Sailor" in saved.text
    with SessionLocal() as db2:
        view = db2.scalar(select(SavedView))
    assert view.query == "age=adult" and f"/app/members?{view.query}" in secretary.get("/app/members").text
    secretary.post(f"/app/views/{view.id}/delete", data={"csrf": token, "back": "/app/members"}, follow_redirects=True)
    with SessionLocal() as db2:
        assert db2.scalar(select(SavedView.id)) is None
    assert "Save this filter as" in secretary.get("/app/members?age=adult").text and "Save this filter as" not in secretary.get("/app/members").text


def test_the_contact_page_shows_the_whole_person_and_links_households(secretary, folk) -> None:
    token = csrf(secretary, "/app/contacts")
    linked = secretary.post(f"/app/contacts/{folk['ann']}/household", data={"csrf": token, "other_id": folk["bob"], "relation": "partner"}, follow_redirects=True)
    assert "Linked to Bob Rower (partner)" in linked.text
    page = secretary.get(f"/app/contacts/{folk['ann']}")
    for text in ("Tags and lists", "Household", "Bob Rower", "Boats and spaces", "Training", "Invoices", "Emails sent to them", "Owes the club", "Details"):
        assert text in page.text, text
    assert "Ann Sailor" in secretary.get(f"/app/contacts/{folk['bob']}").text  # the link shows from both sides
    assert "no contact number 9999" in secretary.post(f"/app/contacts/{folk['ann']}/household", data={"csrf": token, "other_id": 9999, "relation": "partner"}, follow_redirects=True).text
    secretary.post(f"/app/contacts/{folk['ann']}/household/{folk['bob']}/remove", data={"csrf": token}, follow_redirects=True)
    assert "Not linked to anyone" in secretary.get(f"/app/contacts/{folk['ann']}").text


def test_the_data_check_page_and_who_may_edit(secretary, folk) -> None:
    page = secretary.get("/app/people/issues")
    assert page.status_code == 200 and "People data check" in page.text and "Active members with no email address" in page.text
    bosun = login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    assert bosun.get(f"/app/contacts/{folk['ann']}").status_code == 200  # may look
    assert bosun.post(f"/app/contacts/{folk['ann']}/tags", data={"csrf": csrf(bosun), "tag": "x"}).status_code == 403  # may not change
    assert bosun.post("/app/lists", data={"csrf": csrf(bosun), "name": "x"}).status_code == 403
