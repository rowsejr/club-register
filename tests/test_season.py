"""The annual cycle: one invoice per payer, notices, resigning after billing, lapsing, and the fee roll-forward."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app import boats, finance, membership, season
from app.db import SessionLocal
from app.models import (
    Boat,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    Membership,
    MembershipCharge,
    MembershipRenewal,
    MembershipType,
    NominalCode,
    OutboundEmail,
    RecurringFee,
    StorageAllocation,
    StorageTariff,
)

YEAR = 2027


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add_all([NominalCode(code="0101", label="Membership"), NominalCode(code="0102", label="Boat park")])
        session.add_all(
            [
                MembershipType(name="Single Adult Sailing", fee_minor=10800, nominal_code="0101"),
                MembershipType(name="Sailing Family 2A+2C", fee_minor=16500, nominal_code="0101"),
                MembershipType(name="Honorary Member", fee_minor=0, nominal_code="0101"),
                MembershipType(name="Mystery Type", fee_minor=None, nominal_code="0101"),
            ]
        )
        session.commit()
        yield session


def _member(db, name, plan, email="auto", year=YEAR - 1):
    member = Member(name=name, email=(f"{name.split()[0].lower()}@example.org" if email == "auto" else email), membership_type=plan, renewal_status="active", active=True)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=member.email, member_id=member.id)
    db.add(contact)
    db.flush()
    db.add(Membership(member_id=member.id, plan_name=plan, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status="active"))
    db.flush()
    return member, contact


def _boat_space(db, owner, boat_name="Wavelet", space="Row A 1"):
    boat = Boat(name=boat_name, owner_contact_id=owner.id)
    db.add(boat)
    resource = boats.create_resource(db, space, resource_type="berth", group="Hard standing")
    db.flush()
    allocation = boats.allocate(db, boat, resource, date(YEAR - 1, 4, 1), date(YEAR, 12, 31))
    return boat, resource, allocation


@pytest.fixture
def club(db):
    ann, ann_c = _member(db, "Ann Sailor", "Single Adult Sailing")
    bob, bob_c = _member(db, "Bob Rower", "Sailing Family 2A+2C")
    cara, cara_c = _member(db, "Cara Mystery", "Mystery Type")
    dan, dan_c = _member(db, "Dan Honorary", "Honorary Member")
    eve_c = Contact(name="Eve Boatowner", email="eve@example.org")
    fred, fred_c = _member(db, "Fred Postal", "Single Adult Sailing", email=None)
    db.add(eve_c)
    db.flush()
    boat, _, _ = _boat_space(db, ann_c)
    _boat_space(db, eve_c, "Gust", "Row A 2")
    boats.set_tariff(db, "berth", "Hard standing", YEAR, 11500)
    db.add(RecurringFee(contact_id=ann_c.id, boat_id=boat.id, name="Kayak storage", amount_minor=3000, nominal_code="0102"))
    db.commit()
    return {"ann": ann, "ann_c": ann_c, "bob": bob, "bob_c": bob_c, "cara": cara, "dan": dan, "eve_c": eve_c, "fred": fred, "fred_c": fred_c, "boat": boat}


def _plans(db):
    return {p.contact.name: p for p in season.plan(db, YEAR)}


def test_the_plan_combines_membership_boat_park_and_extras_per_payer(db, club) -> None:
    plans = _plans(db)
    ann = plans["Ann Sailor"]
    assert [(i.kind, i.amount_minor) for i in ann.items] == [("membership", 10800), ("storage", 11500), ("extra", 3000)] and ann.total == 25300
    assert plans["Bob Rower"].total == 16500
    assert plans["Eve Boatowner"].total == 11500  # not a member, still a payer for the boat park
    assert [p for p in plans["Cara Mystery"].problems] == ["Cara Mystery: No fee set for this type"]
    dan = plans["Dan Honorary"]
    assert [(i.kind, i.amount_minor) for i in dan.items] == [("membership", 0)] and dan.total == 0 and dan.problems == []  # free: confirmed without an invoice


def test_running_the_season_makes_one_invoice_each_emails_it_and_confirms_memberships(db, club) -> None:
    report = season.run(db, YEAR, "treasurer@example.org")
    db.commit()
    invoices = db.scalars(select(Invoice).where(Invoice.source == "season").order_by(Invoice.id)).all()
    assert len(invoices) == 4 and sorted(i.amount_minor for i in invoices) == [10800, 11500, 16500, 25300]
    ann_invoice = next(i for i in invoices if i.amount_minor == 25300)
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == ann_invoice.id).order_by(InvoiceLine.id)).all()
    assert [(ln.source_type, ln.nominal_code, ln.amount_minor) for ln in lines] == [("membership", "0101", 10800), ("storage", "0102", 11500), ("recurring", "0102", 3000)]
    assert ann_invoice.status == "issued" and ann_invoice.tags == "season 2027"
    assert report.emailed == 3 and report.not_emailed == ["Fred Postal: no email address"]  # Fred has no email: his invoice is for posting
    assert any("Cara Mystery" in s and "No fee" in s for s in report.skipped)
    mails = db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "invoice")).all()
    assert len(mails) == 3 and all(m.attachment_name for m in mails)
    # memberships for the season now exist, and each renewal is tied to its invoice
    for who, active in ((club["ann"], True), (club["bob"], True), (club["dan"], True), (club["fred"], True), (club["cara"], False)):
        row = db.scalar(select(Membership).where(Membership.member_id == who.id, Membership.starts_on == date(YEAR, 1, 1)))
        assert (row is not None) == active, who.name
    assert db.scalar(select(MembershipCharge.invoice_id).join(MembershipRenewal, MembershipRenewal.id == MembershipCharge.renewal_id).where(MembershipRenewal.member_id == club["ann"].id)) == ann_invoice.id


def test_running_again_bills_nothing_twice(db, club) -> None:
    season.run(db, YEAR, "t")
    db.commit()
    again = season.run(db, YEAR, "t")
    db.commit()
    assert again.invoices == [] and again.emailed == 0
    assert db.scalar(select(Invoice.id).where(Invoice.source == "season").limit(1)) is not None
    assert len(db.scalars(select(Invoice).where(Invoice.source == "season")).all()) == 4
    ann = _plans(db)["Ann Sailor"]
    assert ann.items == [] and len(ann.already) == 3  # what has been billed is shown as billed


def test_separate_mode_makes_an_invoice_for_each_kind(db, club) -> None:
    finance.save_settings(db, {"season_invoice_mode": "separate"})
    db.flush()
    season.run(db, YEAR, "t", payer_ids=[club["ann_c"].id])
    db.commit()
    amounts = sorted(i.amount_minor for i in db.scalars(select(Invoice).where(Invoice.source == "season")))
    assert amounts == [3000, 10800, 11500]


def test_only_the_chosen_payers_are_billed_and_drafts_are_not_emailed(db, club) -> None:
    report = season.run(db, YEAR, "t", payer_ids=[club["bob_c"].id], issue=False, send=True)
    db.commit()
    invoice = db.scalar(select(Invoice).where(Invoice.source == "season"))
    assert report.invoices == [invoice.id] and invoice.status == "draft" and report.emailed == 0


def test_resigning_after_billing_voids_the_unpaid_invoice_and_cancels_the_year(db, club) -> None:
    season.run(db, YEAR, "t", payer_ids=[club["bob_c"].id])
    db.commit()
    membership.resign_at_expiry(db, club["bob"], "Moving away", "bob@example.org")
    db.commit()
    invoice = db.scalar(select(Invoice).where(Invoice.source == "season"))
    row = db.scalar(select(Membership).where(Membership.member_id == club["bob"].id, Membership.starts_on == date(YEAR, 1, 1)))
    assert invoice.status == "void" and row.status == "resigned"
    assert _plans(db).get("Bob Rower") is None or _plans(db)["Bob Rower"].items == []  # and the run will not bill him again


def test_resigning_after_paying_reports_that_a_refund_is_needed(db, club) -> None:
    season.run(db, YEAR, "t", payer_ids=[club["bob_c"].id])
    invoice = db.scalar(select(Invoice).where(Invoice.source == "season"))
    finance.record_payment(db, invoice, amount_minor=16500, method="bank")
    renewal = db.scalar(select(MembershipRenewal).where(MembershipRenewal.member_id == club["bob"].id))
    notes = membership.unwind_renewal(db, renewal, "t", "left")
    assert invoice.status == "paid" and any("already paid" in n for n in notes)


def test_renewal_notices_go_once_and_skip_people_with_no_email(db, club) -> None:
    today = date.today()  # notices are stamped with the real clock, so the test measures from it
    first = season.send_notices(db, YEAR, "sec@example.org", today=today)
    db.commit()
    assert "Ann Sailor" in first["sent"] and "Fred Postal: no email address" in first["skipped"]
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "renewal_notice_2027", OutboundEmail.to_address == "ann@example.org"))
    assert "£108.00" in mail.body and "carry on in 2027" in mail.body
    second = season.send_notices(db, YEAR, "sec@example.org", today=today + timedelta(days=10))
    assert "Ann Sailor" not in second["sent"] and any("Ann Sailor: told on" in s for s in second["skipped"])
    later = season.send_notices(db, YEAR, "sec@example.org", today=today + timedelta(days=40))
    assert "Ann Sailor" in later["sent"]  # a month on, a second nudge is allowed


def test_notices_are_not_sent_to_people_already_invoiced_or_not_renewing(db, club) -> None:
    season.run(db, YEAR, "t", payer_ids=[club["ann_c"].id], send=False)
    renewal = MembershipRenewal(member_id=club["bob"].id, period_id=membership.create_period(db, YEAR).id, state="declined")
    db.add(renewal)
    db.commit()
    names = {c.contact.name for c in season.notice_candidates(db, YEAR)}
    assert "Ann Sailor" not in names and "Bob Rower" not in names and "Fred Postal" in names


def test_unpaid_renewals_become_lapse_candidates_after_the_grace_period(db, club) -> None:
    season.run(db, YEAR, "t", payer_ids=[club["ann_c"].id, club["bob_c"].id], send=False)
    db.commit()
    ann_invoice = db.scalar(select(Invoice).where(Invoice.amount_minor == 25300))
    due = ann_invoice.due_date
    assert season.lapse_candidates(db, YEAR, today=due + timedelta(days=30)) == []
    late = season.lapse_candidates(db, YEAR, today=due + timedelta(days=61))
    assert {c.member.name for c in late} == {"Ann Sailor", "Bob Rower"} and late[0].days_overdue == 61
    ann = next(c for c in late if c.member.name == "Ann Sailor")
    assert [a.boat_id for a in ann.spaces] == [club["boat"].id]
    finance.record_payment(db, ann_invoice, amount_minor=25300, method="bank")
    assert {c.member.name for c in season.lapse_candidates(db, YEAR, today=due + timedelta(days=61))} == {"Bob Rower"}


def test_lapsing_marks_the_member_and_can_release_their_space(db, club) -> None:
    season.run(db, YEAR, "t", payer_ids=[club["ann_c"].id], send=False)
    db.commit()
    due = db.scalar(select(Invoice)).due_date
    result = season.lapse(db, YEAR, [club["ann"].id], "sec@example.org", release_spaces=True, today=due + timedelta(days=70))
    db.commit()
    assert result == {"lapsed": 1, "released": 1}
    db.refresh(club["ann"])
    assert club["ann"].renewal_status == "lapsed" and club["ann"].active is False
    allocation = db.scalar(select(StorageAllocation).where(StorageAllocation.boat_id == club["boat"].id))
    assert allocation.status == "ended" and "lapsed" in allocation.ended_reason
    assert db.scalar(select(Invoice)).status == "issued"  # the debt stays on the books


def test_progress_counts(db, club) -> None:
    before = season.progress(db, YEAR)
    assert before["to_invoice_payers"] == 5 and before["invoiced"] == 0 and before["problems"] >= 1 and before["notice_waiting"] == 4
    season.run(db, YEAR, "t", send=False)
    db.commit()
    after = season.progress(db, YEAR)
    assert after["invoiced"] == 4 and after["to_invoice_payers"] == 0


def test_the_renewal_window_opens_four_months_before_the_season(db) -> None:
    assert membership._window_opens(2027, 4) == date(2026, 9, 1)
    assert membership._window_opens(2027, 0) == date(2027, 1, 1)
    assert membership._window_opens(2027, 12) == date(2026, 1, 1)


def test_fees_copy_forward_with_a_rise_rounded_to_50p(db) -> None:
    boats.set_tariff(db, "berth", "Hard standing", 2026, 11500, prorata=True)
    db.flush()
    assert boats.copy_tariffs(db, 2026, 2027, 3.0) == 1
    new = db.scalar(select(StorageTariff).where(StorageTariff.season_year == 2027))
    assert new.fee_minor == 11850 and new.prorata is True  # 115.00 + 3% = 118.45 -> 118.50
    assert boats.copy_tariffs(db, 2026, 2027, 3.0) == 0  # already there
    with pytest.raises(boats.BoatError):
        boats.copy_tariffs(db, 2027, 2026)


def test_membership_fees_can_be_raised_by_a_percentage(db) -> None:
    assert membership.raise_fees(db, 5.0) == 2  # single 108 -> 113.40 -> 113.50; family 165 -> 173.25 -> 173.00; honorary stays free; mystery has no fee
    prices = {t.name: t.fee_minor for t in db.scalars(select(MembershipType))}
    assert prices["Single Adult Sailing"] == 11350 and prices["Sailing Family 2A+2C"] == 17300 and prices["Honorary Member"] == 0 and prices["Mystery Type"] is None
    with pytest.raises(membership.MembershipError):
        membership.raise_fees(db, 200)


def test_pro_rata_charges_by_the_month_from_the_month_a_space_starts() -> None:
    assert boats.prorata_fee(12000, date(2027, 1, 1), 2027) == (12000, "")
    assert boats.prorata_fee(12000, date(2027, 7, 15), 2027) == (6000, "pro rata 6/12")  # July counts in full
    assert boats.prorata_fee(12000, date(2026, 7, 15), 2027) == (12000, "")  # started last year: full fee
    assert boats.prorata_fee(12000, date(2027, 12, 20), 2027) == (1000, "pro rata 1/12")
