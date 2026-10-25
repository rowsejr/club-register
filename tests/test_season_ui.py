"""The season screens: the routine page, the confirmations it insists on, and the extras panel on a contact."""

from datetime import date, timedelta

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import boats
from app.db import SessionLocal
from app.models import (
    Boat,
    Contact,
    Invoice,
    Member,
    Membership,
    MembershipType,
    NominalCode,
    OutboundEmail,
    RecurringFee,
    Role,
    StorageAllocation,
)

YEAR = 2027


@pytest.fixture
def club():
    with SessionLocal() as db:
        db.add_all([NominalCode(code="0101", label="Membership"), NominalCode(code="0102", label="Boat park"), MembershipType(name="Single Adult Sailing", fee_minor=10800, nominal_code="0101")])
        out = {}
        for name in ("Ann Sailor", "Bob Rower"):
            member = Member(name=name, email=f"{name.split()[0].lower()}@example.org", membership_type="Single Adult Sailing", renewal_status="active", active=True)
            db.add(member)
            db.flush()
            contact = Contact(name=name, email=member.email, member_id=member.id)
            db.add(contact)
            db.flush()
            db.add(Membership(member_id=member.id, plan_name="Single Adult Sailing", starts_on=date(YEAR - 1, 1, 1), ends_on=date(YEAR - 1, 12, 31), status="active"))
            out[name.split()[0].lower()] = (member.id, contact.id)
        boat = Boat(name="Wavelet", owner_contact_id=out["ann"][1])
        db.add(boat)
        resource = boats.create_resource(db, "Row A 1", group="Hard standing")
        db.flush()
        boats.allocate(db, boat, resource, date(YEAR - 1, 4, 1), date(YEAR, 12, 31))
        boats.set_tariff(db, "berth", "Hard standing", YEAR, 11500)
        db.commit()
    return out


@pytest.fixture
def secretary():
    return login("sec@example.org", Role.COMMITTEE, hats="secretary")


def test_the_season_page_lists_the_routine_and_the_plan(secretary, club) -> None:
    page = secretary.get(f"/app/season?year={YEAR}")
    assert page.status_code == 200
    for text in (f"Season {YEAR}", "1. Get the prices right", "2. Tell members it is time to renew", "3. Invoice the season", "4. Chase and tidy up", "Ann Sailor", "£223.00", "Boat park fees for 2027"):
        assert text in page.text, text
    assert 'href="/app/season"' in secretary.get("/app").text  # in the main menu


def test_invoicing_insists_on_a_ticked_confirmation_and_a_selection(secretary, club) -> None:
    token = csrf(secretary, f"/app/season?year={YEAR}")
    none = secretary.post("/app/season/invoices", data={"csrf": token, "year": YEAR, "confirm": "1"}, follow_redirects=True)
    assert "Tick at least one payer" in none.text
    unconfirmed = secretary.post("/app/season/invoices", data={"csrf": token, "year": YEAR, "payer": [club["ann"][1]]}, follow_redirects=True)
    assert "Tick the box to confirm" in unconfirmed.text
    with SessionLocal() as db:
        assert db.scalar(select(Invoice.id)) is None


def test_issue_and_email_the_ticked_payers(secretary, club) -> None:
    token = csrf(secretary, f"/app/season?year={YEAR}")
    done = secretary.post("/app/season/invoices", data={"csrf": token, "year": YEAR, "payer": [club["ann"][1]], "confirm": "1", "action": "issue_email"}, follow_redirects=True)
    assert "1 invoice(s) created" in done.text and "1 emailed" in done.text
    with SessionLocal() as db:
        invoice = db.scalar(select(Invoice))
        assert invoice.amount_minor == 22300 and invoice.status == "issued"
        assert db.scalar(select(OutboundEmail.id).where(OutboundEmail.related_type == "invoice")) is not None
    page = secretary.get(f"/app/season?year={YEAR}").text
    assert "Already:" in page and "Bob Rower" in page


def test_drafts_option_and_the_notices_form(secretary, club) -> None:
    token = csrf(secretary, f"/app/season?year={YEAR}")
    secretary.post("/app/season/invoices", data={"csrf": token, "year": YEAR, "payer": [club["bob"][1]], "confirm": "1", "action": "draft"}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.scalar(select(Invoice.status)) == "draft"
    assert "Tick at least one member" in secretary.post("/app/season/notices", data={"csrf": token, "year": YEAR}, follow_redirects=True).text
    sent = secretary.post("/app/season/notices", data={"csrf": token, "year": YEAR, "member": [club["ann"][0], club["bob"][0]]}, follow_redirects=True)
    assert "notice(s) sent" in sent.text


def test_lapsing_needs_the_members_write_permission_and_a_confirmation(secretary, club) -> None:
    token = csrf(secretary, f"/app/season?year={YEAR}")
    secretary.post("/app/season/invoices", data={"csrf": token, "year": YEAR, "payer": [club["ann"][1]], "confirm": "1", "action": "issue"}, follow_redirects=True)
    with SessionLocal() as db:
        invoice = db.scalar(select(Invoice))
        invoice.due_date = date.today() - timedelta(days=90)
        db.commit()
    page = secretary.get(f"/app/season?year={YEAR}").text
    assert "Unpaid long enough to lapse" in page
    assert "Tick the box to confirm" in secretary.post("/app/season/lapse", data={"csrf": token, "year": YEAR, "member": [club["ann"][0]]}, follow_redirects=True).text
    done = secretary.post("/app/season/lapse", data={"csrf": token, "year": YEAR, "member": [club["ann"][0]], "confirm": "1", "release": "1"}, follow_redirects=True)
    assert "1 lapsed; 1 space(s) released" in done.text
    with SessionLocal() as db:
        assert db.get(Member, club["ann"][0]).renewal_status == "lapsed"
        assert db.scalar(select(StorageAllocation.status)) == "ended"


def test_only_billing_hats_see_the_season_pages(club) -> None:
    for hat in ("bosun", "training"):
        assert login(f"{hat}@example.org", Role.COMMITTEE, hats=hat).get("/app/season").status_code == 403
    assert login("tre@example.org", Role.COMMITTEE, hats="treasurer").get("/app/season").status_code == 200


def test_extras_on_a_contact_are_added_stopped_and_billed_with_the_season(secretary, club) -> None:
    treasurer = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    contact_id = club["ann"][1]
    panel = treasurer.get(f"/app/contacts/{contact_id}/extras")
    assert panel.status_code == 200 and "Extras charged every season" in panel.text
    assert f'hx-get="/app/contacts/{contact_id}/extras"' in treasurer.get(f"/app/contacts/{contact_id}").text
    token = csrf(treasurer, "/app/finance")
    added = treasurer.post(f"/app/contacts/{contact_id}/extras", data={"csrf": token, "name": "Kayak storage", "amount": "30", "quantity": "2", "nominal": "0102"}, follow_redirects=True)
    assert "will be added to Ann Sailor" in added.text
    bad = treasurer.post(f"/app/contacts/{contact_id}/extras", data={"csrf": token, "name": "", "amount": "30"}, follow_redirects=True)
    assert "Say what the extra charge is for" in bad.text
    assert "£60.00" in treasurer.get(f"/app/contacts/{contact_id}/extras").text
    page = secretary.get(f"/app/season?year={YEAR}").text
    assert "Kayak storage × 2 2027" in page and "£283.00" in page  # 108 membership + 115 boat park + 2 x 30
    with SessionLocal() as db:
        fee = db.scalar(select(RecurringFee))
    treasurer.post(f"/app/extras/{fee.id}/stop", data={"csrf": token}, follow_redirects=True)
    assert "Kayak storage" not in secretary.get(f"/app/season?year={YEAR}").text
    treasurer.post(f"/app/extras/{fee.id}/delete", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db:
        assert db.scalar(select(RecurringFee.id)) is None
    assert secretary.post(f"/app/contacts/{contact_id}/extras", data={"csrf": csrf(secretary), "name": "x", "amount": "1"}).status_code == 403
