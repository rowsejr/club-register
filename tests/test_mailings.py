"""Mailings: choosing the audience, merge fields, leaving people out, sending once, and the screens that guard it."""

from datetime import date

import pytest
from helpers import csrf, login
from sqlalchemy import select

from app import finance, mailings
from app.db import SessionLocal
from app.models import (
    Contact,
    ContactList,
    ListMembership,
    Mailing,
    MailTemplate,
    Member,
    OutboundEmail,
    Role,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _person(db, name, email, *, member=True, suppressed=False, listed=None, number="1", born=None):
    m = None
    if member:
        m = Member(name=name, email=email if email and "shared" not in email else None, membership_type="Single Adult Sailing", renewal_status="active", active=True, membership_number=number, expiry_date=date(2026, 12, 31))
        db.add(m)
        db.flush()
    contact = Contact(name=name, email=email, member_id=m.id if m else None, suppressed=suppressed, birth_date=born)
    db.add(contact)
    db.flush()
    if listed:
        lst = db.scalar(select(ContactList).where(ContactList.name == listed)) or ContactList(name=listed)
        db.add(lst)
        db.flush()
        db.add(ListMembership(contact_id=contact.id, list_id=lst.id))
    return contact


@pytest.fixture
def folk(db):
    finance.save_settings(db, {"club_name": "Margate Yacht Club"})
    people = [
        _person(db, "Ann Sailor", "ann@example.org", listed="Committee", number="12"),
        _person(db, "Bob Rower", "bob@example.org", number="13"),
        _person(db, "Cara Quiet", "cara@example.org", suppressed=True, number="14"),
        _person(db, "Dan Nomail", None, number="15"),
        _person(db, "Eve Shared", "shared@example.org", number="16"),
        _person(db, "Fay Shared", "shared@example.org", member=False),
    ]
    db.commit()
    return people


def test_merge_fields_are_filled_and_unknown_braces_are_left_alone(db, folk) -> None:
    ann = folk[0]
    values = mailings._values(db, ann, "Margate Yacht Club")
    text = "Hello {first_name} ({membership_number}), your {membership_type} membership ends {expiry}. [date] {nonsense} {club}"
    assert mailings.render(text, values) == "Hello Ann (12), your Single Adult Sailing membership ends 31 December 2026. [date] {nonsense} Margate Yacht Club"
    assert mailings.unknown_fields(text) == ["nonsense"]


def test_the_balance_field_adds_up_what_is_owed(db, folk) -> None:
    ann = folk[0]
    invoice = finance.create_invoice(db, contact_id=ann.id, lines=[{"description": "Fee", "amount_minor": 2500}])
    finance.issue(db, invoice, "t")
    assert mailings._values(db, ann, "MYC")["balance"] == "£25.00" and mailings._values(db, folk[1], "MYC")["balance"] == "£0.00"


def test_the_audience_uses_the_same_filters_as_the_people_pages(db, folk) -> None:
    assert {c.name for c in mailings.audience(db, {"list": "Committee"})} == {"Ann Sailor"}
    assert {c.name for c in mailings.audience(db, {"member": "yes"})} == {"Ann Sailor", "Bob Rower", "Cara Quiet", "Dan Nomail", "Eve Shared"}
    assert len(mailings.audience(db, {})) == 6


def test_people_who_cannot_or_must_not_be_emailed_are_left_out_and_reported(db, folk) -> None:
    mailing = mailings.save_draft(db, None, subject="Hello {first_name}", body="News for {club}", audience_params={}, actor="sec@example.org")
    ready = mailings.preview(db, mailing)
    assert sorted(r.email for r in ready.recipients) == ["ann@example.org", "bob@example.org", "shared@example.org"]
    assert ready.skipped == ["Cara Quiet: asked not to be emailed", "Dan Nomail: no email address", "Fay Shared: shares an address already included"]
    first = next(r for r in ready.recipients if r.email == "ann@example.org")
    assert first.subject == "Hello Ann" and first.body.startswith("News for Margate Yacht Club") and "Reply to this email if you would rather not hear from us" in first.body


def test_sending_logs_every_message_and_can_only_happen_once(db, folk) -> None:
    mailing = mailings.save_draft(db, None, subject="AGM", body="Dear {first_name}", audience_params={"member": "yes"}, actor="sec@example.org")
    result = mailings.send(db, mailing, "sec@example.org")
    db.commit()
    assert result == {"sent": 3, "skipped": 2, "failed": 0}
    logged = db.scalars(select(OutboundEmail).where(OutboundEmail.related_type == "mailing")).all()
    assert len(logged) == 3 and {m.related_id for m in logged} == {mailing.id}
    assert mailing.status == "sent" and mailing.sent_count == 3 and len(mailing.skipped) == 2
    with pytest.raises(mailings.MailingError, match="already been sent"):
        mailings.send(db, mailing, "sec@example.org")
    with pytest.raises(mailings.MailingError, match="already been sent"):
        mailings.save_draft(db, mailing.id, subject="x", body="y", audience_params={}, actor="t")


def test_a_mailing_with_a_typo_or_nobody_to_send_to_is_refused(db, folk) -> None:
    typo = mailings.save_draft(db, None, subject="Hi {firstname}", body="x", audience_params={}, actor="t")
    with pytest.raises(mailings.MailingError, match=r"Unknown merge field\(s\): \{firstname\}"):
        mailings.send(db, typo, "t")
    nobody = mailings.save_draft(db, None, subject="Hi", body="x", audience_params={"q": "zzzz"}, actor="t")
    with pytest.raises(mailings.MailingError, match="Nobody would receive"):
        mailings.send(db, nobody, "t")
    with pytest.raises(mailings.MailingError, match="subject and some text"):
        mailings.save_draft(db, None, subject=" ", body="x", audience_params={}, actor="t")
    assert db.scalar(select(OutboundEmail.id).where(OutboundEmail.related_type == "mailing")) is None


def test_starter_templates_are_seeded_once_and_templates_can_be_saved(db) -> None:
    assert mailings.seed_templates(db) == 4 and mailings.seed_templates(db) == 0
    mailings.save_template(db, "Laying-up supper", "Supper {first_name}", "Join us")
    mailings.save_template(db, "Laying-up supper", "Supper!", "Join us please")
    assert db.scalar(select(MailTemplate.subject).where(MailTemplate.name == "Laying-up supper")) == "Supper!"
    with pytest.raises(mailings.MailingError):
        mailings.save_template(db, "", "x", "y")


@pytest.fixture
def secretary():
    return login("sec@example.org", Role.COMMITTEE, hats="secretary")


def test_the_compose_check_and_send_screens(secretary, folk) -> None:
    page = secretary.get("/app/mailings/new?list=Committee")
    assert page.status_code == 200 and "1 contact match" in page.text and "Renewal reminder" in page.text and "{first_name}" in page.text
    token = csrf(secretary, "/app/mailings/new")
    drafted = secretary.post("/app/mailings", data={"csrf": token, "subject": "Supper {first_name}", "body": "Please come", "member": "yes", "save_template": "Supper"}, follow_redirects=False)
    assert drafted.status_code == 303
    view = secretary.get(drafted.headers["location"])
    assert "What the first person will get" in view.text and "Supper Ann" in view.text and "Left out" in view.text and "member: yes" in view.text
    with SessionLocal() as db2:
        mailing = db2.scalar(select(Mailing))
        assert db2.scalar(select(MailTemplate.id).where(MailTemplate.name == "Supper")) is not None
    path = f"/app/mailings/{mailing.id}/send"
    assert "Tick the box to confirm" in secretary.post(path, data={"csrf": token}, follow_redirects=True).text
    sent = secretary.post(path, data={"csrf": token, "confirm": "1"}, follow_redirects=True)
    assert "Sent to 3 people" in sent.text and "See each message in the email log" in sent.text
    assert "already been sent" in secretary.post(path, data={"csrf": token, "confirm": "1"}, follow_redirects=True).text
    assert "Supper {first_name}" in secretary.get("/app/mailings").text  # the list shows the subject as written


def test_a_draft_can_be_deleted_but_a_sent_mailing_cannot(secretary, folk) -> None:
    token = csrf(secretary, "/app/mailings/new")
    secretary.post("/app/mailings", data={"csrf": token, "subject": "Draft", "body": "x"}, follow_redirects=False)
    with SessionLocal() as db2:
        mailing = db2.scalar(select(Mailing))
    secretary.post(f"/app/mailings/{mailing.id}/delete", data={"csrf": token}, follow_redirects=True)
    with SessionLocal() as db2:
        assert db2.scalar(select(Mailing.id)) is None


def test_only_people_who_may_edit_members_can_compose_or_send(folk) -> None:
    treasurer = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    assert treasurer.get("/app/mailings").status_code == 200  # may see what went out
    assert treasurer.get("/app/mailings/new").status_code == 403
    assert treasurer.post("/app/mailings", data={"csrf": csrf(treasurer), "subject": "x", "body": "y"}).status_code == 403
    member = login("ann@example.org")
    assert member.get("/app/mailings").status_code in (403, 404)
