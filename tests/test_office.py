"""The everyday office: the home to-do list, new members, checks, members' requests, the chase list, the RYA census and members' own choices."""

from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import applications as apps
from app import finance, office
from app.db import SessionLocal
from app.models import (
    ChaseNote,
    Contact,
    ContactRelation,
    Member,
    MemberRequest,
    NominalCode,
    OutboundEmail,
    Role,
)


def _member(db, name="Ann Sailor", email="ann@example.org", plan="Single Adult Sailing", born=date(1980, 5, 1), gender=None):
    member = Member(name=name, email=email, membership_type=plan, renewal_status="active", active=True)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id, birth_date=born, gender=gender)
    db.add(contact)
    db.flush()
    return member, contact


@pytest.fixture
def club():
    with SessionLocal() as db:
        db.add(NominalCode(code="0101", label="Membership"))
        member, contact = _member(db)
        db.commit()
        yield {"member_id": member.id, "contact_id": contact.id}


def test_the_home_page_shows_only_what_needs_doing_for_my_roles(club) -> None:
    with SessionLocal() as db:
        apps.submit_enquiry(db, name="Hannah Brook", email="hannah@example.org", verify=False)
        db.commit()
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    page = secretary.get("/app").text
    assert "What needs doing" in page and "Membership secretary" in page and "Enquiries not yet answered" in page
    assert "Treasurer" not in page  # not their job
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    assert "Enquiries not yet answered" not in treasurer.get("/app").text
    committee = login("c@example.org", Role.COMMITTEE)
    assert "Nothing needs doing right now" in committee.get("/app").text
    assert "Membership secretary" in login("admin@example.org", Role.ADMIN).get("/app").text  # an administrator sees every role


def test_members_ask_for_things_and_the_right_volunteer_sorts_them_out(club) -> None:
    login("bosun@example.org", Role.COMMITTEE, hats="bosun")
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    ann = login("ann@example.org")
    page = ann.get("/app")
    assert "Ask the club" in page.text and "Ask for a boat park space" in page.text
    token = csrf(ann, "/app")
    sent = ann.post("/app/me/requests", data={"csrf": token, "kind": "boat_space", "details": "A space for my Laser please"}, follow_redirects=True)
    assert "The Bosun has been told" in sent.text
    assert "Please tell us a little more" in ann.post("/app/me/requests", data={"csrf": token, "kind": "boat_space", "details": ""}, follow_redirects=True).text
    assert "Choose what you would like" in ann.post("/app/me/requests", data={"csrf": token, "kind": "nonsense", "details": "hello there"}, follow_redirects=True).text
    with SessionLocal() as db:
        request = db.scalar(select(MemberRequest))
        told = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "member_request"))
        assert request.kind == "boat_space" and told.to_address == "bosun@example.org" and "A space for my Laser" in told.body
        request_id = request.id
    bosun = login("bosun@example.org")
    assert "requests from members" in bosun.get("/app").text.lower()
    board = bosun.get("/app/requests")
    assert "A space for my Laser please" in board.text
    assert secretary.post(f"/app/requests/{request_id}/close", data={"csrf": csrf(secretary, "/app/members"), "status": "done"}).status_code == 403  # not theirs
    done = bosun.post(f"/app/requests/{request_id}/close", data={"csrf": csrf(bosun, "/app/requests"), "status": "done", "outcome": "Space B4 from April"}, follow_redirects=True)
    assert "they have been told" in done.text
    with SessionLocal() as db:
        reply = db.scalars(select(OutboundEmail).where(OutboundEmail.to_address == "ann@example.org")).all()[-1]
        assert "Space B4 from April" in reply.body and db.get(MemberRequest, request_id).status == "done"
    again = bosun.post(f"/app/requests/{request_id}/close", data={"csrf": csrf(bosun, "/app/requests"), "status": "done"}, follow_redirects=True)
    assert "already been dealt with" in again.text
    assert login("m2@example.org", Role.MEMBER).get("/app/requests").status_code == 403


def test_the_new_members_page_shows_every_stage(club) -> None:
    with SessionLocal() as db:
        apps.submit_enquiry(db, name="Hannah Brook", email="hannah@example.org", verify=False)
        db.commit()
    page = login("sec@example.org", Role.COMMITTEE, hats="secretary").get("/app/new-members")
    assert page.status_code == 200 and "Asked about joining" in page.text and "Hannah Brook" in page.text and "Nobody is waiting to pay" in page.text


def test_checks_point_straight_at_the_fix(club) -> None:
    with SessionLocal() as db:
        _member(db, name="No Email", email=None, born=None)
        db.commit()
    page = login("sec@example.org", Role.COMMITTEE, hats="secretary").get("/app/checks")
    assert "Active members with no email address" in page.text and "No Email" in page.text and "#details" in page.text


def test_the_census_counts_everyone_a_membership_covers(club) -> None:
    with SessionLocal() as db:
        member, parent = _member(db, name="Pat Family", email="pat@example.org", plan="Sailing Family 1A+1C", born=date(1975, 1, 1), gender="female")
        child = Contact(name="Kit Family", birth_date=date.today() - timedelta(days=365 * 10 + 5), gender="male")
        db.add(child)
        db.flush()
        db.add(ContactRelation(from_contact_id=parent.id, to_contact_id=child.id, relation_type="child"))
        _member(db, name="Old Hand", email="old@example.org", plan="Honorary Member", born=None)
        db.commit()
        c = office.census(db, date.today())
        assert c.total == 4
        assert c.by_band["8 to 11"]["male"] == 1 and c.by_band["Age not known"]["not_given"] == 1
        assert c.by_category["Family"]["8 to 11"] == 1 and c.by_category["Honorary"]["Age not known"] == 1
        assert [p.name for p in c.missing_dob] == ["Old Hand"]
    page = login("sec@example.org", Role.COMMITTEE, hats="secretary").get("/app/census")
    assert page.status_code == 200 and "RYA census" in page.text and "Old Hand" in page.text
    sheet = login("sec2@example.org", Role.COMMITTEE, hats="secretary").get("/app/census.csv").text
    assert sheet.splitlines()[0] == "age band,gender or category,people" and "category: Family" in sheet
    assert office.parse_bands("nonsense") == [(0, 17), (18, 120)] and office.category("Non Sailing + 1C") == "Non-sailing"


def test_the_chase_list_keeps_people_until_they_pay(club) -> None:
    with SessionLocal() as db:
        invoice = finance.create_invoice(db, contact_id=club["contact_id"], lines=[{"description": "Subs", "amount_minor": 10800, "nominal_code": "0101"}])
        finance.issue(db, invoice, "t", issue_date=date.today() - timedelta(days=60), terms=30)
        _, quiet = _member(db, name="Quiet Person", email="quiet@example.org")
        quiet.suppressed = True
        other = finance.create_invoice(db, contact_id=quiet.id, lines=[{"description": "Subs", "amount_minor": 5000, "nominal_code": "0101"}])
        finance.issue(db, other, "t")
        db.commit()
        invoice_id = invoice.id
    treasurer = login("t@example.org", Role.COMMITTEE, hats="treasurer")
    page = treasurer.get("/app/finance/chase")
    assert "Ann Sailor" in page.text and "30 days late" in page.text and "£158.00" in page.text and "asked not to be emailed" in page.text
    assert page.text.index("Ann Sailor") < page.text.index("Quiet Person")  # longest late first
    token = csrf(treasurer, "/app/finance/chase")
    sent = treasurer.post(f"/app/finance/chase/{club['contact_id']}/send", data={"csrf": token}, follow_redirects=True)
    assert "Reminder emailed to Ann Sailor" in sent.text and "Last chased: " + date.today().strftime("%d %b %Y") in sent.text
    noted = treasurer.post(f"/app/finance/chase/{club['contact_id']}/note", data={"csrf": token, "body": "Rang, paying Friday"}, follow_redirects=True)
    assert "Note saved" in noted.text and "Rang, paying Friday" in noted.text
    assert "Write the note first" in treasurer.post(f"/app/finance/chase/{club['contact_id']}/note", data={"csrf": token, "body": " "}, follow_redirects=True).text
    with SessionLocal() as db:
        finance.record_payment(db, db.get(finance.Invoice, invoice_id), amount_minor=10800, method="bank")
        db.commit()
        assert db.scalar(select(ChaseNote.body)) == "Rang, paying Friday"
    after = treasurer.get("/app/finance/chase").text
    assert "Ann Sailor" not in after and "Quiet Person" in after
    assert "They do not owe anything now" in treasurer.post(f"/app/finance/chase/{club['contact_id']}/send", data={"csrf": token}, follow_redirects=True).text
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    assert secretary.post(f"/app/finance/chase/{club['contact_id']}/note", data={"csrf": csrf(secretary, "/app/members"), "body": "x"}).status_code == 403


def test_members_record_their_choices_and_emergency_contact(club) -> None:
    ann = login("ann@example.org")
    token = csrf(ann, "/app/me/profile")
    ann.post("/app/me/profile", data={"csrf": token, "phone": "07700 900123", "gender": "female", "emergency_name": "Bob Sailor", "emergency_phone": "07700 900456",
                                      "photo_consent": "1", "newsletters": "1"}, follow_redirects=True)
    with SessionLocal() as db:
        contact = db.get(Contact, club["contact_id"])
        assert contact.gender == "female" and contact.emergency_name == "Bob Sailor" and contact.photo_consent and not contact.directory_consent and not contact.suppressed
        assert contact.consents_by == "ann@example.org" and contact.consents_updated_at is not None
    page = ann.get("/app/me/profile").text
    assert "Last changed" in page and "Bob Sailor" in page


def test_staff_can_record_choices_on_someones_behalf(club) -> None:
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    token = csrf(secretary, f"/app/contacts/{club['contact_id']}")
    secretary.post(f"/app/contacts/{club['contact_id']}", data={"csrf": token, "name": "Ann Sailor", "email": "ann@example.org", "directory_consent": "1", "gender": "female"}, follow_redirects=True)
    with SessionLocal() as db:
        contact = db.get(Contact, club["contact_id"])
        assert contact.directory_consent and contact.gender == "female" and "on their behalf" in (contact.consents_by or "")


def test_staff_also_reach_their_own_membership(club) -> None:
    with SessionLocal() as db:
        _member(db, name="Sue Secretary", email="sec@example.org")
        db.commit()
    secretary = login("sec@example.org", Role.COMMITTEE, hats="secretary")
    page = secretary.get("/app/me")
    assert page.status_code == 200 and "Sue Secretary" in page.text and "Ask the club" in page.text
