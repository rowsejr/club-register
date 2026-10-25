"""Online payments: provider choice, request shapes, signed webhooks, replays, Direct Debit mandates, and the demo sandbox."""

import hashlib
import hmac
import json
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient
from helpers import csrf, login
from sqlalchemy import select

from app import finance, payments
from app.db import SessionLocal
from app.main import app
from app.models import (
    Contact,
    Invoice,
    Member,
    Membership,
    NominalCode,
    OnlinePayment,
    OutboundEmail,
    PaymentMandate,
    PaymentTransaction,
    Role,
    WebhookEvent,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        session.add(NominalCode(code="0101", label="Membership"))
        session.commit()
        yield session


def _invoice(db, email="ann@example.org", amount=10800, name="Ann Sailor"):
    member = db.scalar(select(Member).where(Member.email == email))
    if member is None:
        member = Member(name=name, email=email, membership_type="Single Adult Sailing", renewal_status="pending", active=False)
        db.add(member)
        db.flush()
        db.add(Contact(name=name, email=email, member_id=member.id))
        db.flush()
    contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
    invoice = finance.create_invoice(db, contact_id=contact.id, lines=[{"description": "Membership 2027", "amount_minor": amount, "nominal_code": "0101"}])
    finance.issue(db, invoice, "t")
    db.commit()
    return invoice


@pytest.fixture
def live(monkeypatch):
    """Both providers configured, as in production."""
    monkeypatch.setenv("CLUB_ENV", "production")
    for key, value in {
        "CLUB_ONLINE_PAYMENTS": "live", "CLUB_GOCARDLESS_TOKEN": "gc-token", "CLUB_GOCARDLESS_ENV": "sandbox", "CLUB_GOCARDLESS_WEBHOOK_SECRET": "gc-secret",
        "CLUB_STRIPE_SECRET_KEY": "sk_test_x", "CLUB_STRIPE_WEBHOOK_SECRET": "whsec_x", "CLUB_BASE_URL": "https://myc.example.org",
    }.items():
        monkeypatch.setenv(key, value)


class FakeNetwork:
    def __init__(self, monkeypatch, replies):
        self.calls = []
        self.replies = list(replies)
        monkeypatch.setattr(payments, "_request", self.request)

    def request(self, method, url, *, headers, body=None, timeout=20):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.replies.pop(0)


# --------------------------------------------------------------------------- configuration


def test_online_payment_is_off_unless_configured_and_sandbox_only_in_dev_or_demo(monkeypatch) -> None:
    monkeypatch.delenv("CLUB_ONLINE_PAYMENTS", raising=False)
    assert payments.mode() == "off"
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "sandbox")
    assert payments.mode() == "sandbox"  # the test suite runs in dev mode
    monkeypatch.setenv("CLUB_ENV", "production")
    assert payments.mode() == "off"  # production never fakes payments...
    monkeypatch.setenv("CLUB_DEMO", "1")
    assert payments.mode() == "sandbox"  # ...unless this is a labelled demo
    monkeypatch.delenv("CLUB_DEMO")
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "live")
    assert payments.mode() == "live" and not payments.gocardless_ready() and not payments.stripe_ready()  # no keys yet


def test_direct_debit_is_offered_first_and_only_for_payable_invoices(db, live) -> None:
    invoice = _invoice(db)
    assert [(m.key, m.provider) for m in payments.methods_for(db, invoice)] == [("direct_debit", "gocardless"), ("card", "stripe")]
    credit = finance.credit_note(db, invoice, amount_minor=500, reason="x", actor="t")
    assert payments.methods_for(db, credit) == []
    finance.record_payment(db, invoice, amount_minor=invoice.amount_minor - 500, method="bank")
    assert payments.methods_for(db, invoice) == []  # nothing left to pay
    draft = finance.create_invoice(db, contact_id=invoice.contact_id, lines=[{"description": "x", "amount_minor": 100}])
    assert payments.methods_for(db, draft) == []


def test_only_the_configured_provider_is_offered(db, live, monkeypatch) -> None:
    monkeypatch.delenv("CLUB_STRIPE_SECRET_KEY")
    assert [m.key for m in payments.methods_for(db, _invoice(db))] == ["direct_debit"]


# --------------------------------------------------------------------------- Stripe


def _stripe_header(body: bytes, key="whsec_x", stamp=None):
    stamp = int(time.time()) if stamp is None else stamp
    return {"Stripe-Signature": f"t={stamp},v1={hmac.new(key.encode(), f'{stamp}.'.encode() + body, hashlib.sha256).hexdigest()}"}


def test_a_stripe_checkout_is_created_for_exactly_what_is_owed(db, live, monkeypatch) -> None:
    invoice = _invoice(db, amount=15300)
    net = FakeNetwork(monkeypatch, [{"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}])
    payment, url = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    assert url == "https://checkout.stripe.com/c/pay/cs_test_1" and payment.external_id == "cs_test_1" and payment.status == "pending" and payment.provider == "stripe"
    call = net.calls[0]
    assert call["url"] == "https://api.stripe.com/v1/checkout/sessions" and call["headers"]["Authorization"] == "Bearer sk_test_x"
    form = dict(item.split("=", 1) for item in call["body"].decode().split("&"))
    assert form["line_items%5B0%5D%5Bprice_data%5D%5Bunit_amount%5D"] == "15300" and form["line_items%5B0%5D%5Bprice_data%5D%5Bcurrency%5D"] == "gbp" and form["client_reference_id"] == payment.token


def test_a_signed_stripe_webhook_marks_the_invoice_paid_and_a_replay_does_nothing(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    FakeNetwork(monkeypatch, [{"id": "cs_test_1", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    db.commit()
    event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {"id": "cs_test_1", "payment_status": "paid", "client_reference_id": payment.token}}}
    body = json.dumps(event).encode()
    client = TestClient(app)
    bad = client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert bad.status_code == 400
    stale = client.post("/webhooks/stripe", content=body, headers=_stripe_header(body, stamp=int(time.time()) - 4000))
    assert stale.status_code == 400  # a captured old request cannot be replayed later
    forged = client.post("/webhooks/stripe", content=body, headers=_stripe_header(body, key="not-the-secret"))
    assert forged.status_code == 400
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "issued"  # nothing believed so far
    ok = client.post("/webhooks/stripe", content=body, headers=_stripe_header(body))
    assert ok.status_code == 200 and ok.json()["outcomes"] == ["paid"]
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "paid"
    money = db.scalar(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id))
    assert (money.amount_minor, money.payment_method, money.bank_reference) == (10800, "stripe", "cs_test_1")
    replay = client.post("/webhooks/stripe", content=body, headers=_stripe_header(body))
    assert replay.json()["outcomes"] == ["duplicate"]
    assert len(db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id)).all()) == 1
    assert db.scalar(select(WebhookEvent.id).where(WebhookEvent.event_id == "evt_1")) is not None


def test_other_stripe_events_are_ignored_and_expiry_marks_the_attempt_failed(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    FakeNetwork(monkeypatch, [{"id": "cs_test_2", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    db.commit()
    client = TestClient(app)
    noise = json.dumps({"id": "evt_n", "type": "customer.created", "data": {"object": {}}}).encode()
    assert client.post("/webhooks/stripe", content=noise, headers=_stripe_header(noise)).json() == {"ok": True, "handled": 0, "outcomes": []}
    expired = json.dumps({"id": "evt_x", "type": "checkout.session.expired", "data": {"object": {"id": "cs_test_2", "client_reference_id": payment.token}}}).encode()
    client.post("/webhooks/stripe", content=expired, headers=_stripe_header(expired))
    db.expire_all()
    assert db.get(OnlinePayment, payment.id).status == "failed" and db.get(Invoice, invoice.id).status == "issued"
    unreadable = b"not json"
    assert client.post("/webhooks/stripe", content=unreadable, headers=_stripe_header(unreadable)).status_code == 400


# --------------------------------------------------------------------------- GoCardless


def _gc_signature(body: bytes, key="gc-secret"):
    return {"Webhook-Signature": hmac.new(key.encode(), body, hashlib.sha256).hexdigest()}


def test_first_direct_debit_sets_up_a_mandate_then_collects_the_invoice(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    net = FakeNetwork(monkeypatch, [{"billing_requests": {"id": "BRQ1"}}, {"billing_request_flows": {"authorisation_url": "https://pay.gocardless.com/flow/BRF1"}}])
    payment, url = payments.start(db, invoice, "direct_debit", base_url="https://myc.example.org")
    db.commit()
    assert url == "https://pay.gocardless.com/flow/BRF1" and payment.external_id == "BRQ1"
    assert net.calls[0]["url"] == "https://api-sandbox.gocardless.com/billing_requests" and net.calls[0]["headers"]["Authorization"] == "Bearer gc-token"
    request = json.loads(net.calls[0]["body"])["billing_requests"]
    assert request["mandate_request"] == {"scheme": "bacs"} and request["metadata"]["token"] == payment.token
    flow = json.loads(net.calls[1]["body"])["billing_request_flows"]
    assert flow["links"] == {"billing_request": "BRQ1"} and flow["redirect_uri"].startswith("https://myc.example.org/app/me/invoices/")
    # the customer finishes at the bank; GoCardless tells us the mandate is ready, and we collect the money
    collect = FakeNetwork(monkeypatch, [{"payments": {"id": "PM1"}}])
    body = json.dumps({"events": [{"id": "EV1", "resource_type": "billing_requests", "action": "fulfilled", "links": {"billing_request": "BRQ1", "mandate_request_mandate": "MD1", "customer": "CU1"}}]}).encode()
    done = TestClient(app).post("/webhooks/gocardless", content=body, headers=_gc_signature(body))
    assert done.status_code == 200 and done.json()["outcomes"] == ["collecting"]
    sent = json.loads(collect.calls[0]["body"])["payments"]
    assert sent["amount"] == 10800 and sent["currency"] == "GBP" and sent["links"] == {"mandate": "MD1"} and collect.calls[0]["headers"]["Idempotency-Key"] == payment.token
    db.expire_all()
    assert db.scalar(select(PaymentMandate.status).where(PaymentMandate.mandate_id == "MD1")) == "active"
    assert db.get(OnlinePayment, payment.id).external_id == "PM1" and db.get(Invoice, invoice.id).status == "issued"  # not paid until the bank confirms
    confirmed = json.dumps({"events": [{"id": "EV2", "resource_type": "payments", "action": "confirmed", "links": {"payment": "PM1"}}]}).encode()
    TestClient(app).post("/webhooks/gocardless", content=confirmed, headers=_gc_signature(confirmed))
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "paid"
    assert db.scalar(select(PaymentTransaction.payment_method).where(PaymentTransaction.invoice_id == invoice.id)) == "gocardless"
    paid_out = json.dumps({"events": [{"id": "EV3", "resource_type": "payments", "action": "paid_out", "links": {"payment": "PM1"}}]}).encode()
    assert TestClient(app).post("/webhooks/gocardless", content=paid_out, headers=_gc_signature(paid_out)).json()["outcomes"] == ["already confirmed"]
    assert len(db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id)).all()) == 1


def test_with_a_live_mandate_the_next_invoice_is_collected_straight_away(db, live, monkeypatch) -> None:
    first = _invoice(db)
    db.add(PaymentMandate(contact_id=first.contact_id, mandate_id="MD9", status="active"))
    db.commit()
    second = _invoice(db, amount=5000)
    net = FakeNetwork(monkeypatch, [{"payments": {"id": "PM2"}}])
    payment, url = payments.start(db, second, "direct_debit", base_url="https://myc.example.org")
    assert len(net.calls) == 1 and net.calls[0]["url"].endswith("/payments") and "collecting+your+Direct+Debit" in url and payment.external_id == "PM2"
    assert json.loads(net.calls[0]["body"])["payments"]["amount"] == 5000


def test_failed_direct_debits_and_cancelled_mandates_are_recorded(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    db.add(PaymentMandate(contact_id=invoice.contact_id, mandate_id="MD5", status="active"))
    db.commit()
    FakeNetwork(monkeypatch, [{"payments": {"id": "PM5"}}])
    payment, _ = payments.start(db, invoice, "direct_debit", base_url="https://myc.example.org")
    db.commit()
    body = json.dumps({"events": [{"id": "E1", "resource_type": "payments", "action": "failed", "links": {"payment": "PM5"}}, {"id": "E2", "resource_type": "mandates", "action": "cancelled", "links": {"mandate": "MD5"}}]}).encode()
    result = TestClient(app).post("/webhooks/gocardless", content=body, headers=_gc_signature(body)).json()
    assert result["outcomes"] == ["failed", "mandate cancelled"]
    db.expire_all()
    assert db.get(OnlinePayment, payment.id).status == "failed" and "Direct Debit failed" in db.get(OnlinePayment, payment.id).failure
    assert db.scalar(select(PaymentMandate.status)) == "cancelled" and db.get(Invoice, invoice.id).status == "issued"


def test_a_webhook_with_a_bad_gocardless_signature_is_refused(db, live) -> None:
    body = json.dumps({"events": []}).encode()
    assert TestClient(app).post("/webhooks/gocardless", content=body, headers={"Webhook-Signature": "nope"}).status_code == 400
    assert TestClient(app).post("/webhooks/gocardless", content=body).status_code == 400


# --------------------------------------------------------------------------- when the money arrives some other way too


def test_an_online_payment_for_an_invoice_already_paid_is_flagged_for_a_refund_not_applied_twice(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    FakeNetwork(monkeypatch, [{"id": "cs_late", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    finance.record_payment(db, invoice, amount_minor=10800, method="bank", reference="FP1")  # the bank transfer arrived first
    db.commit()
    event = payments.Event(event_id="evt_late", kind="paid", external_id="cs_late", token=payment.token)
    assert payments.apply(db, "stripe", event) == "needs refund"
    db.commit()
    assert db.get(OnlinePayment, payment.id).status == "confirmed" and "refund" in db.get(OnlinePayment, payment.id).failure
    assert len(db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id)).all()) == 1


def test_a_part_paid_invoice_only_takes_what_is_still_owed(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    FakeNetwork(monkeypatch, [{"id": "cs_part", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    finance.record_payment(db, invoice, amount_minor=3000, method="bank")
    db.commit()
    payments.apply(db, "stripe", payments.Event(event_id="evt_p", kind="paid", token=payment.token))
    db.commit()
    assert db.get(Invoice, invoice.id).status == "paid" and finance.paid_total(db, invoice.id) == 10800
    assert "refund the difference" in db.get(OnlinePayment, payment.id).failure


def test_paying_a_new_members_first_invoice_online_activates_their_membership(db, live, monkeypatch) -> None:
    from app import membership

    invoice = _invoice(db)
    member = db.scalar(select(Member))
    db.add(Membership(member_id=member.id, plan_name="Single Adult Sailing", starts_on=date(2027, 1, 1), ends_on=date(2027, 12, 31), status="pending_payment"))
    period = membership.create_period(db, 2027)
    renewal = membership.MembershipRenewal(member_id=member.id, period_id=period.id, state="confirmed")
    db.add(renewal)
    db.flush()
    db.add(membership.MembershipCharge(renewal_id=renewal.id, amount_minor=10800, invoice_id=invoice.id, status="invoiced"))
    FakeNetwork(monkeypatch, [{"id": "cs_m", "url": "https://x"}])
    payment, _ = payments.start(db, invoice, "card", base_url="https://myc.example.org")
    db.commit()
    payments.apply(db, "stripe", payments.Event(event_id="evt_m", kind="paid", token=payment.token))
    db.commit()
    db.expire_all()
    assert db.get(Member, member.id).active is True and db.scalar(select(Membership.status)) == "active"


def test_starting_a_payment_checks_the_method_and_reports_provider_errors(db, live, monkeypatch) -> None:
    invoice = _invoice(db)
    with pytest.raises(payments.PaymentError, match="not available"):
        payments.start(db, invoice, "bitcoin", base_url="https://x")

    def refuse(method, url, *, headers, body=None, timeout=20):
        raise payments.PaymentError("The payment provider could not be reached. Please try again in a few minutes.")

    monkeypatch.setattr(payments, "_request", refuse)
    with pytest.raises(payments.PaymentError, match="could not be reached"):
        payments.start(db, invoice, "card", base_url="https://x")


# --------------------------------------------------------------------------- the demo sandbox and the member's page


@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("CLUB_ONLINE_PAYMENTS", "sandbox")
    monkeypatch.setenv("CLUB_BASE_URL", "https://demo.example.org")


def test_a_member_pays_in_the_demo_and_the_books_show_it(db, demo) -> None:
    invoice = _invoice(db)
    ann = login("ann@example.org")
    page = ann.get(f"/app/me/invoices/{invoice.id}")
    assert "Or pay online" in page.text and "by Direct Debit" in page.text and "by Debit or credit card" in page.text and "pretend" in page.text
    assert page.text.index("by Direct Debit") < page.text.index("by Debit or credit card")  # the cheaper way first
    started = ann.post(f"/app/me/invoices/{invoice.id}/pay", data={"csrf": csrf(ann, f"/app/me/invoices/{invoice.id}"), "method": "direct_debit"}, follow_redirects=False)
    assert started.status_code == 303 and "/pay/" in started.headers["location"]
    token = started.headers["location"].rsplit("/", 1)[1]
    anonymous = TestClient(app)
    assert "Demo payment page" in anonymous.get(f"/pay/{token}").text and "£108.00" in anonymous.get(f"/pay/{token}").text
    paid = anonymous.post(f"/pay/{token}", follow_redirects=False)
    assert paid.status_code == 303
    db.expire_all()
    assert db.get(Invoice, invoice.id).status == "paid"
    assert db.scalar(select(PaymentTransaction.payment_method).where(PaymentTransaction.invoice_id == invoice.id)) == "gocardless"
    assert "Paid. Thank you." in ann.get(f"/app/me/invoices/{invoice.id}").text
    again = anonymous.post(f"/pay/{token}", follow_redirects=False)  # pressing it twice does not pay twice
    assert again.status_code == 303 and len(db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id)).all()) == 1


def test_the_sandbox_pages_do_not_exist_in_production(db, demo, monkeypatch) -> None:
    invoice = _invoice(db)
    ann = login("ann@example.org")
    started = ann.post(f"/app/me/invoices/{invoice.id}/pay", data={"csrf": csrf(ann, "/app"), "method": "card"}, follow_redirects=False)
    token = started.headers["location"].rsplit("/", 1)[1]
    monkeypatch.setenv("CLUB_ENV", "production")
    monkeypatch.delenv("CLUB_DEMO", raising=False)
    anonymous = TestClient(app)
    assert anonymous.get(f"/pay/{token}").status_code == 404 and anonymous.post(f"/pay/{token}").status_code == 404
    with pytest.raises(payments.PaymentError, match="switched off"):
        payments.sandbox_pay(db, token)
    assert anonymous.get("/pay/not-a-token").status_code == 404


def test_a_member_cannot_start_a_payment_on_someone_elses_invoice(db, demo) -> None:
    mine, theirs = _invoice(db), _invoice(db, email="bob@example.org", name="Bob Rower")
    ann = login("ann@example.org")
    assert ann.post(f"/app/me/invoices/{theirs.id}/pay", data={"csrf": csrf(ann, "/app"), "method": "card"}).status_code == 404
    assert ann.post(f"/app/me/invoices/{mine.id}/pay", data={"csrf": csrf(ann, "/app"), "method": "bitcoin"}, follow_redirects=True).status_code == 200
    assert "not available" in ann.post(f"/app/me/invoices/{mine.id}/pay", data={"csrf": csrf(ann, "/app"), "method": "bitcoin"}, follow_redirects=True).text


def test_emails_and_the_staff_panel_mention_online_payment_when_it_is_on(db, demo) -> None:
    invoice = _invoice(db)
    from app import invoice_comms

    invoice_comms.send_invoice(db, invoice, "t")
    db.commit()
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "invoice"))
    assert "pay online by Direct Debit or Debit or credit card" in mail.body and "/login?next=/app/me/invoices/" in mail.body
    treasurer = login("tre@example.org", Role.COMMITTEE, hats="treasurer")
    panel = treasurer.get(f"/app/finance/invoices/{invoice.id}/online")
    assert panel.status_code == 200 and "Demo mode" in panel.text and "Direct Debit" in panel.text
    assert f'hx-get="/app/finance/invoices/{invoice.id}/online"' in treasurer.get(f"/app/finance/invoices/{invoice.id}").text


def test_with_online_payment_off_nothing_is_offered_and_emails_stay_plain(db, monkeypatch) -> None:
    monkeypatch.delenv("CLUB_ONLINE_PAYMENTS", raising=False)
    invoice = _invoice(db)
    ann = login("ann@example.org")
    assert "Or pay online" not in ann.get(f"/app/me/invoices/{invoice.id}").text
    from app import invoice_comms

    invoice_comms.send_invoice(db, invoice, "t")
    assert "pay online" not in db.scalar(select(OutboundEmail.body).where(OutboundEmail.related_type == "invoice"))
