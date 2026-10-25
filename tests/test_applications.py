from datetime import date

import pytest
from sqlalchemy import func, select

from app import applications as apps
from app import banking, finance, membership, ratelimit
from app.db import SessionLocal
from app.models import (
    Contact,
    ContactList,
    ContactRelation,
    Enquiry,
    Invoice,
    ListMembership,
    Member,
    Membership,
    MembershipType,
    OutboundEmail,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _type(db, name="Single Adult Sailing", fee=10800, joinable=True, joining=None) -> MembershipType:
    mtype = membership.ensure_type(db, name)
    mtype.fee_minor, mtype.online_joinable, mtype.joining_fee_minor = fee, joinable, joining
    db.flush()
    return mtype


def _data(db, **overrides) -> apps.ApplicationIn:
    values = {"name": "Ann Sailor", "email": "ann@example.com", "rules_agreed": True, "consent": True}
    values.update(overrides)
    if "membership_type_id" not in values:  # only create the default type when the test did not choose one
        values["membership_type_id"] = _type(db).id
    return apps.ApplicationIn(**values)


# --- enquiries ---------------------------------------------------------------------------------


def test_enquiry_needs_verification_and_never_stores_the_link(db) -> None:
    enquiry, created = apps.submit_enquiry(db, name="  Ann   Sailor ", email="Ann@Example.com", interest="sailing", message="Do you teach adults?", consent=True)
    assert created and enquiry.status == "unverified" and enquiry.email == "ann@example.com" and enquiry.name == "Ann Sailor"
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "enquiry_verify"))
    assert mail and "link not stored" in mail.body and mail.to_address == "ann@example.com"
    assert apps.verify_enquiry(db, "not-the-token") is None
    token = enquiry.verify_token_hash and None  # the raw token only ever existed in the email
    assert token is None and enquiry.verify_token_hash


def test_enquiry_verification_flow_with_token(db, monkeypatch) -> None:
    captured: dict[str, str] = {}
    real = apps.issue_token

    def spy():
        token, digest = real()
        captured["token"] = token
        return token, digest

    monkeypatch.setattr(apps, "issue_token", spy)
    enquiry, _ = apps.submit_enquiry(db, name="Ann", email="ann@example.com")
    verified = apps.verify_enquiry(db, captured["token"])
    assert verified is enquiry and enquiry.status == "new" and enquiry.verified_at is not None and enquiry.verify_token_hash is None
    assert apps.verify_enquiry(db, captured["token"]) is None  # single use


def test_enquiry_validation_and_dedupe(db) -> None:
    with pytest.raises(apps.ApplicationError, match="name"):
        apps.submit_enquiry(db, name=" ", email="a@example.com")
    with pytest.raises(apps.ApplicationError, match="email address is needed"):
        apps.submit_enquiry(db, name="Ann", email="")
    with pytest.raises(apps.ApplicationError, match="does not look right"):
        apps.submit_enquiry(db, name="Ann", email="nope")
    with pytest.raises(apps.ApplicationError, match="email address or phone"):
        apps.submit_enquiry(db, name="Ann", verify=False)
    first, created = apps.submit_enquiry(db, name="Ann", email="ann@example.com", message="One")
    again, created_again = apps.submit_enquiry(db, name="Ann", email="ANN@example.com", message="Two")
    assert created and not created_again and again.id == first.id
    assert db.scalar(select(func.count()).select_from(Enquiry)) == 1
    phone_only, _ = apps.submit_enquiry(db, name="Walk In", phone="01843 000", source="walk_in", verify=False, interest="weird", actor="sec@example.com")
    assert phone_only.status == "new" and phone_only.interest == "other"


def test_triage_notes_assignment_and_closing(db) -> None:
    enquiry, _ = apps.submit_enquiry(db, name="Ann", email="ann@example.com", verify=False)
    apps.assign(db, enquiry, " sec@example.com ", "boss")
    apps.add_note(db, enquiry, "sec@example.com", "Phoned, will visit Sunday")
    with pytest.raises(apps.ApplicationError):
        apps.add_note(db, enquiry, "sec@example.com", "  ")
    apps.set_enquiry_status(db, enquiry, "contacted", "sec@example.com")
    assert enquiry.assigned_to == "sec@example.com" and enquiry.status == "contacted"
    apps.set_enquiry_status(db, enquiry, "closed", "sec@example.com", "Decided not to join")
    assert enquiry.outcome == "Decided not to join"
    spam, _ = apps.submit_enquiry(db, name="Bot", email="bot@example.com", verify=False)
    apps.set_enquiry_status(db, spam, "spam", "sec")
    assert spam.outcome == "spam"
    with pytest.raises(apps.ApplicationError, match="Unknown"):
        apps.set_enquiry_status(db, spam, "banana", "sec")
    unverified, _ = apps.submit_enquiry(db, name="Un", email="un@example.com")
    with pytest.raises(apps.ApplicationError, match="not been confirmed"):
        apps.set_enquiry_status(db, unverified, "contacted", "sec")
    with pytest.raises(apps.ApplicationError, match="not been confirmed"):
        apps.convert_enquiry(db, unverified, None, "sec")


def test_convert_enquiry_to_application_once(db) -> None:
    enquiry, _ = apps.submit_enquiry(db, name="Ann Sailor", email="ann@example.com", message="Interested in sailing", verify=False)
    mtype = _type(db)
    application = apps.convert_enquiry(db, enquiry, mtype.id, "sec")
    assert application.status == "submitted" and application.source == "enquiry" and application.notes == "Interested in sailing"
    assert enquiry.status == "converted" and enquiry.application_id == application.id
    assert apps.convert_enquiry(db, enquiry, mtype.id, "sec").id == application.id
    assert apps.queue_counts(db) == {"enquiries": 0, "applications": 1, "awaiting_payment": 0}


# --- applications ------------------------------------------------------------------------------


def test_public_application_rules(db) -> None:
    with pytest.raises(apps.ApplicationError, match="rules and the privacy"):
        apps.submit_application(db, _data(db, consent=False))
    with pytest.raises(apps.ApplicationError, match="one of the membership types"):
        apps.submit_application(db, _data(db, membership_type_id=_type(db, "Hidden", joinable=False).id))
    with pytest.raises(apps.ApplicationError, match="one of the membership types"):
        apps.submit_application(db, _data(db, membership_type_id=None))
    with pytest.raises(apps.ApplicationError, match="covers 1 person; you have listed 2"):
        apps.submit_application(db, _data(db, people=[apps.PersonIn("Pat Partner")]))  # a single adult membership has room for one
    application, created = apps.submit_application(db, _data(db, people=[apps.PersonIn("  ", "child")]))  # a blank row is not a person
    assert created and application.status == "unverified" and application.rules_agreed_at and application.consent_at
    again, created_again = apps.submit_application(db, _data(db))
    assert not created_again and again.id == application.id
    staff, _ = apps.submit_application(db, _data(db, name="Phone Joiner", email=None, rules_agreed=False, consent=False, membership_type_id=_type(db, "Hidden", joinable=False).id), source="staff", verify=False)
    assert staff.status == "submitted"  # volunteers can enter any type and skip the online-only checks


def test_application_verification_moves_it_to_the_committee_queue(db, monkeypatch) -> None:
    captured: dict[str, str] = {}
    real = apps.issue_token
    monkeypatch.setattr(apps, "issue_token", lambda: captured.setdefault("t", real()[0]) and (captured["t"], apps.hash_token(captured["t"])))
    application, _ = apps.submit_application(db, _data(db))
    assert apps.queue_counts(db)["applications"] == 0
    assert apps.verify_application(db, captured["t"]) is application and application.status == "submitted"
    assert apps.queue_counts(db)["applications"] == 1


def test_approval_creates_member_pending_membership_and_issued_invoice(db) -> None:
    finance.save_settings(db, {"payment_instructions": "Pay by bank transfer.", "bank_details": "Sort 00-00-00", "club_name": "MYC"})
    application, _ = apps.submit_application(db, _data(db), verify=False)
    invoice = apps.approve(db, application, "sec@example.com", "Welcome")
    assert invoice and invoice.status == "issued" and invoice.amount_minor == 10800 and invoice.reference.isdigit()
    member = db.scalar(select(Member).where(Member.email == "ann@example.com"))
    assert member and member.active is False and member.renewal_status == "pending"
    pending = db.scalar(select(Membership).where(Membership.member_id == member.id))
    assert pending and pending.status == "pending_payment" and pending.plan_name == "Single Adult Sailing"
    contact = db.scalar(select(Contact).where(Contact.email == "ann@example.com"))
    assert contact.member_id == member.id and application.contact_id == contact.id and application.status == "approved" and application.decided_by == "sec@example.com"
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "application", OutboundEmail.subject.like("Welcome%")))
    assert mail and mail.attachment_name == f"Invoice-{invoice.reference}.pdf" and f"invoice number {invoice.reference}" in mail.body and "Sort 00-00-00" in mail.body
    assert apps.approve(db, application, "sec", "again").id == invoice.id  # idempotent
    assert db.scalar(select(func.count()).select_from(Invoice)) == 1


def test_approval_guards(db) -> None:
    mtype = _type(db, fee=None)
    application, _ = apps.submit_application(db, _data(db, membership_type_id=mtype.id), verify=False)
    with pytest.raises(apps.ApplicationError, match="No fee is set"):
        apps.approve(db, application, "sec")
    nothing, _ = apps.submit_application(db, _data(db, name="No Type", email="n@example.com", membership_type_id=None), source="staff", verify=False)
    with pytest.raises(apps.ApplicationError, match="Choose a membership type"):
        apps.approve(db, nothing, "sec")
    apps.reject(db, nothing, "sec", "Not eligible")
    with pytest.raises(apps.ApplicationError, match="cannot be approved"):
        apps.approve(db, nothing, "sec")
    with pytest.raises(apps.ApplicationError, match="cannot be rejected"):
        apps.reject(db, nothing, "sec", "again")
    other, _ = apps.submit_application(db, _data(db, name="Q", email="q@example.com"), verify=False)
    with pytest.raises(apps.ApplicationError, match="Give a reason"):
        apps.reject(db, other, "sec", " ")
    apps.request_info(db, other, "sec", "Please confirm your age")
    assert other.status == "info_requested" and db.scalar(select(OutboundEmail).where(OutboundEmail.subject == "About your membership application"))
    with pytest.raises(apps.ApplicationError, match="Say what you need"):
        apps.request_info(db, other, "sec", "")


def test_active_members_must_renew_not_apply(db) -> None:
    member = Member(name="Ann Sailor", email="ann@example.com", active=True)
    db.add(member)
    db.flush()
    db.add(Contact(name="Ann Sailor", email="ann@example.com", member_id=member.id))
    db.add(Membership(member_id=member.id, plan_name="Single Adult Sailing", starts_on=date.today().replace(month=1, day=1), ends_on=date.today().replace(month=12, day=31), status="active"))
    application, _ = apps.submit_application(db, _data(db), verify=False)
    with pytest.raises(apps.ApplicationError, match="already an active member"):
        apps.approve(db, application, "sec")


def test_family_application_creates_everyone_and_links_the_household(db) -> None:
    mtype = _type(db, "Sailing Family 2A+1C", fee=20000, joining=2500)
    people = [apps.PersonIn("Pat Partner", "partner", "pat@example.com"), apps.PersonIn("Charlie Child", "child", None, date(2015, 5, 1))]
    application, _ = apps.submit_application(db, _data(db, membership_type_id=mtype.id, people=people), verify=False)
    invoice = apps.approve(db, application, "sec")
    assert invoice and invoice.amount_minor == 22500
    lines = [(line.description, line.amount_minor) for line in invoice.__class__ and db.scalars(select(finance.InvoiceLine).where(finance.InvoiceLine.invoice_id == invoice.id).order_by(finance.InvoiceLine.id))]
    assert lines == [("Sailing Family 2A+1C membership " + str(date.today().year), 20000), ("Joining fee", 2500)]
    assert db.scalar(select(func.count()).select_from(Member)) == 3
    assert db.scalar(select(func.count()).select_from(ContactRelation)) == 2
    finance.record_payment(db, invoice, amount_minor=22500, method="bank", reference="R1")
    members = db.scalars(select(Member)).all()
    assert all(m.active and m.renewal_status == "active" for m in members)
    assert len({m.membership_number for m in members}) == 1 and members[0].membership_number


def test_first_payment_activates_the_membership_and_welcomes_them(db) -> None:
    db.add(Member(name="Old Hand", email="old@example.com", membership_number="98/136"))
    application, _ = apps.submit_application(db, _data(db), verify=False)
    invoice = apps.approve(db, application, "sec")
    member = db.scalar(select(Member).where(Member.email == "ann@example.com"))
    finance.record_payment(db, invoice, amount_minor=5000, method="bank", reference="P1")
    assert member.active is False and application.status == "approved"  # a part payment does not activate
    finance.record_payment(db, invoice, amount_minor=5800, method="bank", reference="P2")
    assert application.status == "completed" and member.active and member.renewal_status == "active" and member.payment_status == "paid"
    assert member.membership_number == "137"  # continues after the highest existing number, including family numbers like 98/136
    assert member.expiry_date == date(date.today().year, 12, 31)
    assert db.scalar(select(Membership.status).where(Membership.member_id == member.id)) == "active"
    assert db.scalar(select(func.count()).select_from(ListMembership).join(ContactList, ContactList.id == ListMembership.list_id).where(ContactList.name == "New Members")) == 1
    welcome = db.scalar(select(OutboundEmail).where(OutboundEmail.subject.like("You are now a member%")))
    assert welcome and "membership number is 137" in welcome.body


def test_bank_matching_a_payment_completes_the_application_too(db) -> None:
    application, _ = apps.submit_application(db, _data(db), verify=False)
    invoice = apps.approve(db, application, "sec")
    account = banking.get_or_create_account(db, "Club current account")
    banking.import_statement(db, account, f"Date,Description,Reference,Amount\n01/10/2026,BACS CREDIT ANN SAILOR,INV {invoice.reference},108.00\n")
    assert banking.auto_match(db, "t").matched == 1
    assert application.status == "completed"


def test_free_membership_starts_immediately_without_an_invoice(db) -> None:
    mtype = _type(db, "RYA Course Membership", fee=0)
    application, _ = apps.submit_application(db, _data(db, membership_type_id=mtype.id), verify=False)
    assert apps.approve(db, application, "sec") is None
    member = db.scalar(select(Member).where(Member.email == "ann@example.com"))
    assert application.status == "completed" and member.active and member.membership_number
    assert db.scalar(select(func.count()).select_from(Invoice)) == 0


def test_withdrawing_voids_an_unpaid_first_invoice(db) -> None:
    application, _ = apps.submit_application(db, _data(db), verify=False)
    invoice = apps.approve(db, application, "sec")
    apps.withdraw(db, application, "sec")
    assert application.status == "withdrawn" and invoice.status == "void"
    with pytest.raises(apps.ApplicationError, match="cannot be withdrawn"):
        apps.withdraw(db, application, "sec")


# --- self-service renewal ------------------------------------------------------------------------


def _member_with_history(db, year: int, plan="Single Adult Sailing", fee=10800, status="active", email="ann@example.com") -> Member:
    mtype = _type(db, plan, fee=fee)
    member = Member(name="Ann Sailor", email=email, membership_type=plan, active=status == "active")
    db.add(member)
    db.flush()
    db.add(Contact(name="Ann Sailor", email=email, member_id=member.id))
    db.add(Membership(member_id=member.id, plan_name=mtype.name, starts_on=date(year, 1, 1), ends_on=date(year, 12, 31), status=status))
    db.flush()
    return member


def test_renewal_offer_timing_and_blockers(db) -> None:
    member = _member_with_history(db, 2026)
    assert membership.renewal_offer(db, member, today=date(2026, 6, 1)) is None  # already covered this year; next year's window (four months) has not opened
    offer = membership.renewal_offer(db, member, today=date(2026, 12, 2))
    assert offer and offer.period.name == "2027" and offer.fee_minor == 10800 and offer.reason == ""
    lapsed = _member_with_history(db, 2025, status="lapsed", email="lapsed@example.com")
    now = membership.renewal_offer(db, lapsed, today=date(2026, 3, 1))
    assert now and now.period.name == "2026"  # a lapsed member can renew into the current year
    brand_new = Member(name="Never Joined", email="new@example.com")
    db.add(brand_new)
    db.flush()
    assert membership.renewal_offer(db, brand_new, today=date(2026, 3, 1)) is None
    db.scalar(select(MembershipType)).fee_minor = None
    blocked = membership.renewal_offer(db, member, today=date(2026, 12, 2))
    assert blocked and "has not been set" in blocked.reason
    with pytest.raises(membership.MembershipError, match="has not been set"):
        membership.self_renew(db, member, "ann@example.com", today=date(2026, 12, 2))
    with pytest.raises(membership.MembershipError, match="nothing to renew"):
        membership.self_renew(db, brand_new, "new@example.com", today=date(2026, 3, 1))


def test_self_renewal_creates_a_pending_membership_activated_by_payment(db) -> None:
    member = _member_with_history(db, 2025, status="lapsed")
    today = date(2026, 3, 1)
    invoice = membership.self_renew(db, member, "ann@example.com", today=today)
    assert invoice and invoice.status == "issued" and invoice.amount_minor == 10800
    this_year = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == date(2026, 1, 1)))
    assert this_year.status == "pending_payment" and member.active is False  # not a member again until they pay
    assert membership.self_renew(db, member, "ann@example.com", today=today).id == invoice.id  # repeat click: same invoice, nothing doubled
    assert db.scalar(select(func.count()).select_from(Invoice)) == 1
    finance.record_payment(db, invoice, amount_minor=10800, method="bank", reference="Z1")
    assert this_year.status == "active" and member.active and member.expiry_date == date(2026, 12, 31) and member.renewal_status == "active"
    assert db.scalar(select(membership.MembershipCharge.status)) == "paid"


def test_free_self_renewal_needs_no_invoice(db) -> None:
    member = _member_with_history(db, 2025, plan="Honorary Member", fee=0)
    assert membership.self_renew(db, member, "ann@example.com", today=date(2026, 2, 1)) is None
    assert db.scalar(select(Membership.status).where(Membership.starts_on == date(2026, 1, 1))) == "active" and member.active


def test_resigning_keeps_membership_until_it_ends_and_skips_the_billing_run(db) -> None:
    member = _member_with_history(db, date.today().year)
    membership.resign_at_expiry(db, member, "Moving away", "ann@example.com")
    assert member.active is True
    period = membership.create_period(db, date.today().year + 1)
    membership.prepare(db, period)
    assert membership.run_billing(db, period, "t").confirmed == 0
    assert any(r.state == "declined" for r in db.scalars(select(membership.MembershipRenewal)))


# --- public-form protections -----------------------------------------------------------------------


def test_database_backed_rate_limit_windows(db) -> None:
    assert [ratelimit.allow(db, "k", 3, 60) for _ in range(5)] == [True, True, True, False, False]
    assert ratelimit.allow(db, "other", 3, 60)
    from app.models import RateHit

    row = db.get(RateHit, "k")
    row.window_start = row.window_start.replace(year=2000)
    assert ratelimit.allow(db, "k", 3, 60) and db.get(RateHit, "k").count == 1  # a new window starts counting again
    ratelimit.purge_old_hits(db)
    assert db.get(RateHit, "k") is not None and db.get(RateHit, "other") is not None


def test_form_tokens_reject_instant_forged_and_stale_submissions(db) -> None:
    token = ratelimit.issue_form_token(db)
    assert not ratelimit.check_form_token(db, token, min_seconds=3)  # submitted instantly: a script
    assert ratelimit.check_form_token(db, token, min_seconds=0)
    stamp, signature = token.split(".")
    assert not ratelimit.check_form_token(db, f"{int(stamp) - 5}.{signature}", min_seconds=0)  # tampered timestamp
    assert not ratelimit.check_form_token(db, token, min_seconds=0, max_seconds=-1)  # stale
    assert not ratelimit.check_form_token(db, "garbage") and not ratelimit.check_form_token(db, "")
    assert ratelimit.hash_ip(db, "1.2.3.4") == ratelimit.hash_ip(db, "1.2.3.4") != ratelimit.hash_ip(db, "1.2.3.5")
