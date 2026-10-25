"""Direct Debit through Stripe: setting it up once, collecting each invoice, the nightly collections, failures,
cancellations, refunds, and the member setting up or stopping their own Direct Debit."""

import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from helpers import csrf, login
from sqlalchemy import select

from app import finance, invoice_comms, jobs, office, payments
from app.db import SessionLocal
from app.main import app
from app.models import (
    Contact,
    Invoice,
    Member,
    NominalCode,
    OnlinePayment,
    OutboundEmail,
    PaymentMandate,
    PaymentTransaction,
    Role,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0101", label="Membership"))
        session.commit()
        yield session


@pytest.fixture
def live(monkeypatch):
    for key, value in {"CLUB_ONLINE_PAYMENTS": "live", "CLUB_STRIPE_SECRET_KEY": "sk_test_x", "CLUB_STRIPE_WEBHOOK_SECRET": "whsec_x", "CLUB_BASE_URL": "https://myc.example.org"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CLUB_DIRECT_DEBIT", raising=False)


class FakeStripe:
    """Stands in for Stripe's API: records each call and gives back the next prepared reply."""

    def __init__(self, monkeypatch, replies):
        self.calls, self.replies = [], list(replies)
        monkeypatch.setattr(payments, "_request", self.request)

    def request(self, method, url, *, headers, body=None, timeout=20):
        form = dict(urllib.parse.parse_qsl(body.decode())) if body else {}
        self.calls.append({"method": method, "path": url.removeprefix("https://api.stripe.com"), "form": form, "headers": headers})
        return self.replies.pop(0)


def _person(db, email="ann@example.org", name="Ann Sailor"):
    member = Member(name=name, email=email, membership_type="Single Adult Sailing", renewal_status="active", active=True)
    db.add(member)
    db.flush()
    contact = Contact(name=name, email=email, member_id=member.id)
    db.add(contact)
    db.flush()
    return contact


def _invoice(db, contact, amount=10800, issued=None, terms=30):
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Membership", "amount_minor": amount, "nominal_code": "0101"}])
    finance.issue(db, invoice, "t", issue_date=issued or date.today(), terms=terms)
    db.commit()
    return invoice


def _mandate(db, contact, pm="pm_9", customer="cus_9"):
    db.add(PaymentMandate(contact_id=contact.id, provider="stripe", mandate_id=pm, customer_id=customer, status="active"))
    db.commit()


def _post(event: dict):
    body = json.dumps(event).encode()
    stamp = int(time.time())
    signature = hmac.new(b"whsec_x", f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return TestClient(app).post("/webhooks/stripe", content=body, headers={"Stripe-Signature": f"t={stamp},v1={signature}"})


def test_the_first_direct_debit_is_set_up_on_stripes_page_then_the_invoice_is_collected(db, live, monkeypatch) -> None:
    contact = _person(db)
    invoice = _invoice(db, contact)
    stripe = FakeStripe(monkeypatch, [{"id": "cus_1"}, {"id": "cs_setup_1", "url": "https://checkout.stripe.com/c/setup/cs_setup_1"}])
    payment, url = payments.start(db, invoice, "direct_debit", base_url="https://myc.example.org")
    db.commit()
    assert url == "https://checkout.stripe.com/c/setup/cs_setup_1" and payment.provider == "stripe"
    customer, session = stripe.calls
    assert customer["path"] == "/v1/customers" and customer["form"]["email"] == "ann@example.org" and customer["headers"]["Idempotency-Key"] == f"customer-{contact.id}"
    assert session["form"]["mode"] == "setup" and session["form"]["payment_method_types[0]"] == "bacs_debit" and session["form"]["customer"] == "cus_1" and session["form"]["currency"] == "gbp"
    assert session["form"]["client_reference_id"] == payment.token and "amount" not in session["form"]  # nothing is collected on this page
    # the member finishes at Stripe; Stripe tells us, we look up the bank account they gave and collect the invoice
    collect = FakeStripe(monkeypatch, [{"id": "seti_1", "payment_method": "pm_1"}, {"id": "pi_1", "status": "processing"}])
    done = _post({"id": "evt_s1", "type": "checkout.session.completed", "data": {"object": {"id": "cs_setup_1", "mode": "setup", "setup_intent": "seti_1", "customer": "cus_1", "client_reference_id": payment.token}}})
    assert done.status_code == 200 and done.json()["outcomes"] == ["collecting"]
    lookup, intent = collect.calls
    assert lookup["method"] == "GET" and lookup["path"] == "/v1/setup_intents/seti_1"
    assert intent["path"] == "/v1/payment_intents" and intent["form"]["amount"] == "10800" and intent["form"]["payment_method"] == "pm_1" and intent["form"]["customer"] == "cus_1"
    assert intent["form"]["confirm"] == "true" and intent["form"]["payment_method_types[0]"] == "bacs_debit" and intent["headers"]["Idempotency-Key"] == payment.token
    db.expire_all()
    assert db.scalar(select(PaymentMandate).where(PaymentMandate.status == "active")).mandate_id == "pm_1"
    assert db.get(Invoice, invoice.id).status == "issued"  # not paid until the bank has paid
    assert payments.being_collected(db, invoice.id) and not invoice_comms.reminder_candidates(db, today=date.today() + timedelta(days=40))  # nobody chases it meanwhile
    paid = _post({"id": "evt_p1", "type": "payment_intent.succeeded", "data": {"object": {"id": "pi_1", "metadata": {"token": payment.token, "invoice": invoice.reference}}}})
    assert paid.json()["outcomes"] == ["paid"]
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "paid"
    money = db.scalar(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id))
    assert (money.amount_minor, money.payment_method, money.bank_reference) == (10800, "stripe", "pi_1")
    assert _post({"id": "evt_p1", "type": "payment_intent.succeeded", "data": {"object": {"id": "pi_1", "metadata": {"token": payment.token}}}}).json()["outcomes"] == ["duplicate"]


def test_with_a_direct_debit_set_up_pressing_pay_collects_straight_away(db, live, monkeypatch) -> None:
    contact = _person(db)
    _mandate(db, contact)
    invoice = _invoice(db, contact, amount=5000)
    assert "already set up" in payments.methods_for(db, invoice)[0].note
    stripe = FakeStripe(monkeypatch, [{"id": "pi_2"}])
    payment, url = payments.start(db, invoice, "direct_debit", base_url="https://myc.example.org")
    assert len(stripe.calls) == 1 and stripe.calls[0]["form"]["amount"] == "5000" and stripe.calls[0]["form"]["payment_method"] == "pm_9"
    assert "collecting+your+Direct+Debit" in url and payment.external_id == "pi_2"


def test_card_payments_are_not_counted_twice_by_their_payment_intent_events(db, live, monkeypatch) -> None:
    invoice = _invoice(db, _person(db))
    FakeStripe(monkeypatch, [{"id": "cs_card", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://x")
    db.commit()
    assert _post({"id": "evt_c1", "type": "checkout.session.completed", "data": {"object": {"id": "cs_card", "payment_status": "paid", "client_reference_id": payment.token, "payment_intent": "pi_card"}}}).json()["outcomes"] == ["paid"]
    assert _post({"id": "evt_c2", "type": "payment_intent.succeeded", "data": {"object": {"id": "pi_card", "metadata": {"invoice": invoice.reference}}}}).json()["outcomes"] == []
    db.expire_all()
    assert len(db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id)).all()) == 1


def test_a_failed_collection_and_a_cancelled_direct_debit_are_recorded(db, live, monkeypatch) -> None:
    contact = _person(db)
    _mandate(db, contact)
    invoice = _invoice(db, contact)
    FakeStripe(monkeypatch, [{"id": "pi_3"}])
    payment, _ = payments.start(db, invoice, "direct_debit", base_url="https://x")
    db.commit()
    failed = _post({"id": "evt_f", "type": "payment_intent.payment_failed", "data": {"object": {"id": "pi_3", "metadata": {"token": payment.token}, "last_payment_error": {"message": "Insufficient funds"}}}})
    assert failed.json()["outcomes"] == ["failed"]
    cancelled = _post({"id": "evt_m", "type": "mandate.updated", "data": {"object": {"id": "mandate_1", "status": "inactive", "payment_method": "pm_9"}}})
    assert cancelled.json()["outcomes"] == ["mandate cancelled"]
    db.expire_all()
    attempt = db.get(OnlinePayment, payment.id)
    assert attempt.status == "failed" and "Insufficient funds" in attempt.failure and db.get(Invoice, invoice.id).status == "issued"
    assert payments.active_mandate(db, contact.id) is None
    assert [m.key for m in payments.methods_for(db, invoice)][0] == "direct_debit" and "Cheapest" in payments.methods_for(db, invoice)[0].note  # they can set it up again


def test_the_nightly_job_collects_each_due_invoice_once(db, live, monkeypatch) -> None:
    contact = _person(db)
    _mandate(db, contact)
    due = _invoice(db, contact, issued=date.today() - timedelta(days=30), terms=30)  # due today
    later = _invoice(db, contact, amount=2000, terms=30)  # due next month
    other = _invoice(db, _person(db, "bob@example.org", "Bob Rower"), issued=date.today() - timedelta(days=60))  # no Direct Debit
    rehearsal = jobs.direct_debit_collections(db, date.today(), dry_run=True)
    assert rehearsal == {"would_collect": [due.reference]}
    stripe = FakeStripe(monkeypatch, [{"id": "pi_n1"}])
    assert jobs.direct_debit_collections(db, date.today(), dry_run=False) == {"collecting": 1, "failed": []}
    db.commit()
    assert len(stripe.calls) == 1 and stripe.calls[0]["form"]["metadata[invoice]"] == due.reference
    assert jobs.direct_debit_collections(db, date.today(), dry_run=False) == {"collecting": 0, "failed": []}  # asked for once only
    chase = {d.contact.name: d for d in office.chase_list(db)}
    assert chase["Ann Sailor"].direct_debit == "collecting" and chase["Bob Rower"].direct_debit == ""
    assert "being collected by Direct Debit" in login("t@example.org", Role.COMMITTEE, hats="treasurer").get("/app/finance/chase").text
    assert [c.invoice.reference for c in invoice_comms.reminder_candidates(db)] == [other.reference]
    finance.save_settings(db, {"direct_debit_auto_collect": "0"})
    db.flush()
    assert payments.collectable(db, date.today() + timedelta(days=40)) == []  # the treasurer can switch it off
    finance.save_settings(db, {"direct_debit_auto_collect": "1"})
    assert [c.invoice.reference for c in payments.collectable(db, date.today() + timedelta(days=40))] == [later.reference]


def test_a_collection_the_provider_refuses_is_marked_failed_and_not_retried(db, live, monkeypatch) -> None:
    contact = _person(db)
    _mandate(db, contact)
    invoice = _invoice(db, contact, issued=date.today() - timedelta(days=31))

    def refuse(method, url, *, headers, body=None, timeout=20):
        raise payments.PaymentError("The payment provider refused the request (402): no such payment method")

    monkeypatch.setattr(payments, "_request", refuse)
    assert payments.collect_due(db, date.today()) == {"collecting": [], "failed": [invoice.reference]}
    assert db.scalar(select(OnlinePayment.status).where(OnlinePayment.invoice_id == invoice.id)) == "failed"
    assert payments.collectable(db, date.today()) == []
    assert [c.invoice.id for c in invoice_comms.reminder_candidates(db)] == [invoice.id]  # back to normal chasing


def test_refunds_made_in_stripe_come_off_the_invoice_once(db, live, monkeypatch) -> None:
    invoice = _invoice(db, _person(db))
    FakeStripe(monkeypatch, [{"id": "cs_r", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://x")
    db.commit()
    _post({"id": "evt_r0", "type": "checkout.session.completed", "data": {"object": {"id": "cs_r", "payment_status": "paid", "client_reference_id": payment.token, "payment_intent": "pi_r"}}})
    part = _post({"id": "evt_r1", "type": "charge.refunded", "data": {"object": {"id": "ch_1", "payment_intent": "pi_r", "amount_refunded": 3000}}})
    assert part.json()["outcomes"] == ["refunded"]
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "part_paid" and finance.outstanding(db, db.get(Invoice, invoice.id)) == 3000
    rest = _post({"id": "evt_r2", "type": "charge.refunded", "data": {"object": {"id": "ch_1", "payment_intent": "pi_r", "amount_refunded": 10800}}})  # Stripe sends the running total
    assert rest.json()["outcomes"] == ["refunded"]
    again = _post({"id": "evt_r3", "type": "charge.refunded", "data": {"object": {"id": "ch_1", "payment_intent": "pi_r", "amount_refunded": 10800}}})
    assert again.json()["outcomes"] == ["already refunded"]
    db.expire_all()
    rows = [p.amount_minor for p in db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id).order_by(PaymentTransaction.id))]
    assert rows == [10800, -3000, -7800] and db.get(Invoice, invoice.id).status == "issued"
    assert "credit note" in db.get(OnlinePayment, payment.id).failure
    assert _post({"id": "evt_r4", "type": "charge.refunded", "data": {"object": {"payment_intent": "pi_unknown", "amount_refunded": 100}}}).json()["outcomes"] == ["unknown payment"]


def test_a_direct_debit_can_be_set_up_before_any_invoice_and_reuses_the_stripe_customer(db, live, monkeypatch) -> None:
    contact = _person(db)
    db.add(PaymentMandate(contact_id=contact.id, provider="stripe", mandate_id="pm_old", customer_id="cus_old", status="cancelled"))
    db.commit()
    stripe = FakeStripe(monkeypatch, [{"id": "cs_m", "url": "https://checkout.stripe.com/c/setup/cs_m"}])
    url = payments.start_direct_debit(db, contact, base_url="https://myc.example.org")
    db.commit()
    assert url.endswith("cs_m") and len(stripe.calls) == 1 and stripe.calls[0]["form"]["customer"] == "cus_old"  # no second customer
    reference = stripe.calls[0]["form"]["client_reference_id"]
    FakeStripe(monkeypatch, [{"id": "seti_m", "payment_method": "pm_new"}])
    assert _post({"id": "evt_ms", "type": "checkout.session.completed", "data": {"object": {"id": "cs_m", "mode": "setup", "setup_intent": "seti_m", "customer": "cus_old", "client_reference_id": reference}}}).json()["outcomes"] == ["mandate saved"]
    db.expire_all()
    assert payments.active_mandate(db, contact.id).mandate_id == "pm_new"
    with pytest.raises(payments.PaymentError, match="already have"):
        payments.start_direct_debit(db, contact, base_url="https://x")
    stop = FakeStripe(monkeypatch, [{"id": "pm_new"}])
    payments.stop_direct_debit(db, contact, "ann@example.org")
    assert stop.calls[0]["path"] == "/v1/payment_methods/pm_new/detach" and payments.active_mandate(db, contact.id) is None


def test_a_setup_nobody_started_is_ignored(db, live, monkeypatch) -> None:
    assert _post({"id": "evt_x", "type": "checkout.session.completed", "data": {"object": {"id": "cs_nobody", "mode": "setup", "setup_intent": "seti_x", "client_reference_id": "mandate-x"}}}).json()["outcomes"] == ["unknown mandate"]


def test_members_set_up_and_stop_their_direct_debit_from_their_page(db, monkeypatch) -> None:
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "sandbox")
    contact = _person(db)
    db.commit()
    ann = login("ann@example.org")
    page = ann.get("/app").text
    assert "Pay by Direct Debit" in page and "Set up Direct Debit" in page
    done = ann.post("/app/me/direct-debit", data={"csrf": csrf(ann, "/app")}, follow_redirects=True)
    assert "Direct Debit set up" in done.text and "You pay by Direct Debit" in done.text
    invoice = _invoice(db, contact)
    invoice_comms.send_invoice(db, invoice, "t")
    db.commit()
    assert "collected from your bank account" in db.scalar(select(OutboundEmail.body).where(OutboundEmail.related_type == "invoice"))
    stopped = ann.post("/app/me/direct-debit/stop", data={"csrf": csrf(ann, "/app")}, follow_redirects=True)
    assert "has been stopped" in stopped.text and "Set up Direct Debit" in stopped.text
    assert "no Direct Debit to stop" in ann.post("/app/me/direct-debit/stop", data={"csrf": csrf(ann, "/app")}, follow_redirects=True).text
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "off")
    assert "Set up Direct Debit" not in ann.get("/app").text


def test_replies_go_to_the_right_volunteer(db) -> None:
    contact = _person(db)
    invoice = _invoice(db, contact)
    invoice_comms.send_invoice(db, invoice, "t")
    assert db.scalar(select(OutboundEmail.reply_to).where(OutboundEmail.related_type == "invoice")) is None  # nothing set: replies go to the sending address
    finance.save_settings(db, {"reply_to": "office@example.org", "reply_to_treasurer": "treasurer@example.org"})
    db.flush()
    assert finance.reply_to(db, "treasurer") == "treasurer@example.org" and finance.reply_to(db, "bosun") == "office@example.org"
    invoice_comms.send_invoice(db, invoice, "t")
    assert db.scalars(select(OutboundEmail.reply_to).where(OutboundEmail.related_type == "invoice").order_by(OutboundEmail.id)).all()[-1] == "treasurer@example.org"
    office.create_request(db, contact, "bosun", "Where do I put my trailer?")
    assert db.scalars(select(OutboundEmail.reply_to).where(OutboundEmail.related_type == "member_request")).all() == []  # no Bosun yet
