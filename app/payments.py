"""Paying invoices online: Direct Debit first (the cheapest for the club), card at once, bank transfer always.

Stripe handles both cards and Bacs Direct Debit, so one account does everything. GoCardless can be chosen for Direct
Debit instead; it is off unless ``CLUB_DIRECT_DEBIT=gocardless``.

How Direct Debit works here
---------------------------
* A member sets up a Direct Debit once (Stripe's own page, in "setup" mode). Their bank details never touch this server.
* After that, each invoice is collected on its due date by the nightly job, or at once when they press Pay. Stripe
  emails them before every collection, as the Direct Debit rules require.
* The money is only counted when Stripe says the collection succeeded, a few working days later.
* Members can stop their Direct Debit from their own page at any time (or through their bank).

How it stays safe
-----------------
* An invoice is marked paid **only when the provider tells our server so** (a signed webhook), never because the
  customer's browser says it paid. The sandbox provider, which simulates this for demos, is switched off unless the
  deployment is a development or demo one.
* Webhooks are checked against the provider's signature before anything is read, and each event is recorded so a
  replay cannot record the money twice.
* If an invoice was already settled some other way (a bank transfer arrived first) the online payment is flagged for a
  refund rather than being applied twice.

Configuration (environment)
---------------------------
``CLUB_ONLINE_PAYMENTS``    ``off`` (default) | ``live`` | ``sandbox`` (demos only)
``CLUB_DIRECT_DEBIT``       ``stripe`` (default) | ``gocardless`` | ``off``
``CLUB_GOCARDLESS_TOKEN``   access token; ``CLUB_GOCARDLESS_ENV`` = ``sandbox`` | ``live`` (default live)
``CLUB_GOCARDLESS_WEBHOOK_SECRET``, ``CLUB_STRIPE_SECRET_KEY``, ``CLUB_STRIPE_WEBHOOK_SECRET``

The Stripe and GoCardless request shapes follow their public API documentation and are covered by tests that fake the
network; both must be run once against the providers' own sandboxes before going live.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, finance, ratelimit
from app.models import (
    AuditLog,
    Contact,
    Invoice,
    OnlinePayment,
    PaymentMandate,
    PaymentTransaction,
    WebhookEvent,
)
from app.security import issue_token

STRIPE_TOLERANCE_SECONDS = 300


class PaymentError(ValueError):
    """A problem a person can act on: shown to them as it is."""


@dataclass
class Method:
    key: str  # direct_debit | card
    label: str
    provider: str
    note: str


@dataclass
class Event:
    """What a provider's notification means, in our terms."""

    event_id: str
    kind: str  # paid | failed | mandate_active | mandate_failed | refunded
    external_id: str | None = None
    token: str | None = None  # our reference, when the provider echoes it back
    mandate_id: str | None = None
    customer_id: str | None = None
    reason: str = ""
    setup_intent: str | None = None  # Stripe: the saved Direct Debit to look up
    intent: str | None = None  # Stripe: the payment itself, remembered so a later refund can be matched
    amount_minor: int = 0  # refunds: how much has been given back in all


# --------------------------------------------------------------------------- configuration


def mode() -> str:
    """off | live | sandbox. Sandbox only counts in a development or demo deployment, so it can never fake payments in production."""
    wanted = os.getenv("CLUB_ONLINE_PAYMENTS", "off").lower()
    if wanted == "sandbox":
        return "sandbox" if (auth.is_dev() or os.getenv("CLUB_DEMO", "") == "1") else "off"
    return wanted if wanted == "live" else "off"


def gocardless_ready() -> bool:
    return mode() == "live" and bool(os.getenv("CLUB_GOCARDLESS_TOKEN"))


def stripe_ready() -> bool:
    return mode() == "live" and bool(os.getenv("CLUB_STRIPE_SECRET_KEY"))


def direct_debit_provider() -> str | None:
    """Who collects Direct Debits: stripe, gocardless, sandbox (demos), or None when Direct Debit is not offered."""
    wanted = os.getenv("CLUB_DIRECT_DEBIT", "stripe").lower()
    if wanted not in {"stripe", "gocardless"}:
        return None
    if mode() == "sandbox":
        return "sandbox"
    ready = stripe_ready() if wanted == "stripe" else gocardless_ready()
    return wanted if ready else None


def card_provider() -> str | None:
    if mode() == "sandbox":
        return "sandbox"
    return "stripe" if stripe_ready() else None


def methods_for(db: Session, invoice: Invoice) -> list[Method]:
    """How this invoice can be paid online, cheapest first. Empty when online payment is off or the invoice is not payable."""
    if invoice.status not in finance.OPEN or (invoice.kind or "invoice") != "invoice" or finance.outstanding(db, invoice) <= 0:
        return []
    out: list[Method] = []
    debit, card = direct_debit_provider(), card_provider()
    if debit:
        note = "Cheapest for the club: please choose this if you can. Taken from your bank account in a few days."
        if invoice.contact_id and active_mandate(db, invoice.contact_id, debit):
            note = "Uses the Direct Debit you have already set up. Taken from your bank account in a few days."
        out.append(Method("direct_debit", "Direct Debit", debit, note))
    if card:
        out.append(Method("card", "Debit or credit card", card, "Paid at once."))
    return out


def guest_token(db: Session, invoice_id: int) -> str:
    """A link token for people with no account (a non-member who bought a ticket): only this server could have made it."""
    sig = hmac.new(ratelimit._secret(db), f"pay-invoice:{invoice_id}".encode(), hashlib.sha256).hexdigest()[:24]
    return f"{invoice_id}.{sig}"


def invoice_for_guest_token(db: Session, token: str) -> Invoice | None:
    head, _, sig = (token or "").partition(".")
    if not head.isdigit():
        return None
    expected = guest_token(db, int(head)).partition(".")[2]
    return db.get(Invoice, int(head)) if hmac.compare_digest(expected, sig) else None


def pay_link(invoice: Invoice, db: Session | None = None) -> str:
    """The address to put in emails. Members are signed in first; people with no account get a private link to their invoice."""
    base = os.getenv("CLUB_BASE_URL", "").rstrip("/")
    if db is not None and invoice.contact_id:
        contact = db.get(Contact, invoice.contact_id)
        if contact is not None and contact.member_id is None:
            return f"{base}/i/{guest_token(db, invoice.id)}"
    return f"{base}/login?next=/app/me/invoices/{invoice.id}"


# --------------------------------------------------------------------------- talking to the providers (faked in tests)


def _request(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)  # noqa: S310 - fixed https endpoints below
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise PaymentError(f"The payment provider refused the request ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise PaymentError("The payment provider could not be reached. Please try again in a few minutes.") from exc


def _stripe(path: str, form: dict[str, str] | None = None, idempotency_key: str | None = None) -> dict:
    """Call Stripe. With no form it is a GET (looking something up); with a form it is a POST."""
    headers = {"Authorization": f"Bearer {os.getenv('CLUB_STRIPE_SECRET_KEY', '')}"}
    if form is None:
        return _request("GET", f"https://api.stripe.com{path}", headers=headers)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return _request("POST", f"https://api.stripe.com{path}", headers=headers, body=urllib.parse.urlencode(form).encode())


def _gocardless(path: str, payload: dict, idempotency_key: str | None = None) -> dict:
    host = "api-sandbox.gocardless.com" if os.getenv("CLUB_GOCARDLESS_ENV", "live") == "sandbox" else "api.gocardless.com"
    headers = {"Authorization": f"Bearer {os.getenv('CLUB_GOCARDLESS_TOKEN', '')}", "GoCardless-Version": "2015-07-06", "Content-Type": "application/json", "Accept": "application/json"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return _request("POST", f"https://{host}{path}", headers=headers, body=json.dumps(payload).encode())


# --------------------------------------------------------------------------- starting a payment


def active_mandate(db: Session, contact_id: int, provider: str | None = None) -> PaymentMandate | None:
    """The person's working Direct Debit, if they have one (for this provider, when given)."""
    query = select(PaymentMandate).where(PaymentMandate.contact_id == contact_id, PaymentMandate.status == "active")
    if provider:
        query = query.where(PaymentMandate.provider == provider)
    return db.scalar(query.order_by(PaymentMandate.id.desc()))


def _new_payment(db: Session, invoice: Invoice, provider: str, method: str, amount: int) -> OnlinePayment:
    token, _ = issue_token()
    payment = OnlinePayment(invoice_id=invoice.id, provider=provider, method=method, amount_minor=amount, token=token[:64], status="pending")
    db.add(payment)
    db.flush()
    return payment


def start(db: Session, invoice: Invoice, method: str, *, base_url: str, back: str | None = None) -> tuple[OnlinePayment, str]:
    """Begin paying an invoice online. Returns the attempt and where to send the customer."""
    allowed = {m.key: m for m in methods_for(db, invoice)}
    if method not in allowed:
        raise PaymentError("That way of paying is not available for this invoice.")
    owed = finance.outstanding(db, invoice)
    provider = allowed[method].provider
    payment = _new_payment(db, invoice, provider, method, owed)
    base = base_url.rstrip("/")
    back = back or f"{base}/app/me/invoices/{invoice.id}"
    if provider == "sandbox":
        return payment, f"{base}/pay/{payment.token}"
    if provider == "stripe" and method == "direct_debit":
        contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
        if contact is None:
            raise PaymentError("This invoice has no person to collect from.")
        mandate = active_mandate(db, contact.id, "stripe")
        if mandate is not None:
            _stripe_collect(db, payment, invoice, mandate)
            return payment, f"{back}?msg=Thank+you.+We+are+collecting+your+Direct+Debit"
        session = _stripe_setup(db, contact, payment.token, success=f"{back}?msg=Direct+Debit+set+up.+We+are+collecting+your+payment", cancel=f"{back}?err=Direct+Debit+not+set+up:+nothing+was+taken")
        payment.external_id = session["id"]
        db.flush()
        return payment, session["url"]
    if provider == "stripe":
        session = _stripe(
            "/v1/checkout/sessions",
            {
                "mode": "payment", "client_reference_id": payment.token, "success_url": f"{back}?msg=Thank+you.+Your+payment+is+being+confirmed", "cancel_url": f"{back}?err=Payment+cancelled:+nothing+was+taken",
                "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "gbp", "line_items[0][price_data][unit_amount]": str(owed),
                "line_items[0][price_data][product_data][name]": f"Invoice {invoice.reference}",
                "payment_intent_data[description]": f"Invoice {invoice.reference}", "payment_intent_data[metadata][invoice]": invoice.reference,
            },
        )
        payment.external_id = session["id"]
        db.flush()
        return payment, session["url"]
    # GoCardless Direct Debit: a member with a live mandate pays at once; otherwise they set one up first.
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    if contact is None:
        raise PaymentError("This invoice has no person to collect from.")
    mandate = active_mandate(db, contact.id)
    if mandate is not None:
        created = _gocardless("/payments", {"payments": {"amount": owed, "currency": "GBP", "description": f"Invoice {invoice.reference}", "metadata": {"invoice": invoice.reference, "token": payment.token}, "links": {"mandate": mandate.mandate_id}}}, idempotency_key=payment.token)
        payment.external_id = created["payments"]["id"]
        db.flush()
        return payment, f"{back}?msg=Thank+you.+We+are+collecting+your+Direct+Debit"
    request = _gocardless("/billing_requests", {"billing_requests": {"mandate_request": {"scheme": "bacs"}, "metadata": {"token": payment.token, "contact": str(contact.id)}}})
    request_id = request["billing_requests"]["id"]
    flow = _gocardless("/billing_request_flows", {"billing_request_flows": {"redirect_uri": f"{back}?msg=Direct+Debit+set+up.+We+are+collecting+your+payment", "exit_uri": f"{back}?err=Direct+Debit+not+set+up:+nothing+was+taken", "links": {"billing_request": request_id}}})
    payment.external_id = request_id
    db.flush()
    return payment, flow["billing_request_flows"]["authorisation_url"]


# --------------------------------------------------------------------------- Stripe Direct Debit


def _stripe_customer(db: Session, contact: Contact) -> str:
    """The person's Stripe customer, made the first time they set up a Direct Debit and reused after that."""
    known = db.scalar(select(PaymentMandate.customer_id).where(PaymentMandate.contact_id == contact.id, PaymentMandate.provider == "stripe", PaymentMandate.customer_id.is_not(None)).order_by(PaymentMandate.id.desc()))
    if known:
        return known
    form = {"name": contact.name, "metadata[contact]": str(contact.id)}
    if contact.email:
        form["email"] = contact.email
    return str(_stripe("/v1/customers", form, idempotency_key=f"customer-{contact.id}")["id"])


def _stripe_setup(db: Session, contact: Contact, token: str, *, success: str, cancel: str) -> dict:
    """Open Stripe's page where the member gives their bank details for Direct Debit. Nothing is collected here."""
    customer = _stripe_customer(db, contact)
    session = _stripe("/v1/checkout/sessions", {
        "mode": "setup", "currency": "gbp", "customer": customer, "payment_method_types[0]": "bacs_debit",
        "client_reference_id": token, "metadata[contact]": str(contact.id), "success_url": success, "cancel_url": cancel,
    })
    db.add(PaymentMandate(contact_id=contact.id, provider="stripe", mandate_id=f"setup:{session['id']}", customer_id=customer, status="pending"))
    db.flush()
    return session


def _stripe_collect(db: Session, payment: OnlinePayment, invoice: Invoice, mandate: PaymentMandate) -> None:
    """Ask Stripe to collect what is owed on an invoice from a Direct Debit already set up. Safe to repeat: Stripe sees the same key."""
    owed = finance.outstanding(db, invoice)
    intent = _stripe("/v1/payment_intents", {
        "amount": str(owed), "currency": "gbp", "customer": mandate.customer_id or "", "payment_method": mandate.mandate_id,
        "payment_method_types[0]": "bacs_debit", "confirm": "true", "description": f"Invoice {invoice.reference}",
        "metadata[token]": payment.token, "metadata[invoice]": invoice.reference,
    }, idempotency_key=payment.token)
    payment.external_id, payment.provider_payment_id, payment.amount_minor = intent["id"], intent["id"], owed
    db.flush()


def start_direct_debit(db: Session, contact: Contact, *, base_url: str) -> str:
    """Set up a Direct Debit without paying anything yet, so later invoices (the renewal) are collected on their due date.
    Returns where to send the member."""
    provider = direct_debit_provider()
    back = f"{base_url.rstrip('/')}/app"
    if provider is None:
        raise PaymentError("Direct Debit is not available at the moment.")
    if active_mandate(db, contact.id, provider):
        raise PaymentError("You already have a Direct Debit set up.")
    if provider == "sandbox":
        db.add(PaymentMandate(contact_id=contact.id, provider="sandbox", mandate_id=f"sandbox-{contact.id}-{int(time.time())}", status="active"))
        db.flush()
        return f"{back}?msg=Direct+Debit+set+up+(demo).+Your+invoices+will+be+collected+on+their+due+date"
    if provider != "stripe":
        raise PaymentError("To set up a Direct Debit, press Pay on your next invoice.")
    token, _ = issue_token()
    session = _stripe_setup(db, contact, f"mandate-{token[:40]}", success=f"{back}?msg=Thank+you.+Your+Direct+Debit+is+being+set+up", cancel=f"{back}?err=Direct+Debit+not+set+up")
    return str(session["url"])


def stop_direct_debit(db: Session, contact: Contact, actor: str) -> None:
    """The member (or the treasurer) cancels a Direct Debit. Nothing more is collected from it."""
    mandate = active_mandate(db, contact.id)
    if mandate is None:
        raise PaymentError("There is no Direct Debit to stop.")
    if mandate.provider == "stripe":
        _stripe(f"/v1/payment_methods/{mandate.mandate_id}/detach", {})
    elif mandate.provider == "gocardless":
        _gocardless(f"/mandates/{mandate.mandate_id}/actions/cancel", {})
    mandate.status = "cancelled"
    db.add(AuditLog(actor_email=actor, actor_role="member", action="direct_debit.stopped", target_type="contact", target_id=str(contact.id), details={"provider": mandate.provider}))
    db.flush()


def being_collected(db: Session, invoice_id: int, days: int = 14) -> bool:
    """A Direct Debit collection for this invoice is under way (they take a few working days), so nobody should chase it."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.scalars(select(OnlinePayment.created_at).where(OnlinePayment.invoice_id == invoice_id, OnlinePayment.method == "direct_debit", OnlinePayment.status == "pending"))
    return any((stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)) >= since for stamp in rows)


@dataclass
class Collection:
    invoice: Invoice
    contact: Contact
    mandate: PaymentMandate


def collectable(db: Session, today: date) -> list[Collection]:
    """Invoices due by today from people with a Direct Debit, that nobody has tried to collect yet.
    A failed collection is not retried by itself: the person stays on the chase list for the treasurer."""
    provider = direct_debit_provider()
    if provider in {None, "sandbox"} or finance.get_setting(db, "direct_debit_auto_collect") != "1":
        return []
    out: list[Collection] = []
    for mandate in db.scalars(select(PaymentMandate).where(PaymentMandate.status == "active", PaymentMandate.provider == provider).order_by(PaymentMandate.id)):
        contact = db.get(Contact, mandate.contact_id)
        if contact is None:
            continue
        for invoice in db.scalars(select(Invoice).where(Invoice.contact_id == contact.id, Invoice.status.in_(finance.OPEN)).order_by(Invoice.id)):
            due = invoice.due_date or invoice.issue_date
            if (invoice.kind or "invoice") != "invoice" or due is None or due > today or finance.outstanding(db, invoice) <= 0:
                continue
            if db.scalar(select(OnlinePayment.id).where(OnlinePayment.invoice_id == invoice.id, OnlinePayment.method == "direct_debit")):
                continue
            out.append(Collection(invoice, contact, mandate))
    return out


def collect_due(db: Session, today: date) -> dict[str, list[str]]:
    """Collect every invoice that is due from people with a Direct Debit. Run by the nightly job."""
    done: dict[str, list[str]] = {"collecting": [], "failed": []}
    for item in collectable(db, today):
        payment = _new_payment(db, item.invoice, item.mandate.provider, "direct_debit", finance.outstanding(db, item.invoice))
        try:
            if item.mandate.provider == "stripe":
                _stripe_collect(db, payment, item.invoice, item.mandate)
            else:
                created = _gocardless("/payments", {"payments": {"amount": payment.amount_minor, "currency": "GBP", "description": f"Invoice {item.invoice.reference}", "metadata": {"invoice": item.invoice.reference, "token": payment.token}, "links": {"mandate": item.mandate.mandate_id}}}, idempotency_key=payment.token)
                payment.external_id = created["payments"]["id"]
            done["collecting"].append(item.invoice.reference)
        except PaymentError as exc:
            payment.status, payment.failure = "failed", str(exc)[:255]
            done["failed"].append(item.invoice.reference)
        db.flush()
    return done


# --------------------------------------------------------------------------- webhooks


def verify_stripe(headers: dict[str, str], body: bytes, now: float | None = None) -> bool:
    secret = os.getenv("CLUB_STRIPE_WEBHOOK_SECRET", "")
    header = headers.get("stripe-signature", "")
    if not secret or not header:
        return False
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    stamp, signature = parts.get("t", ""), parts.get("v1", "")
    if not stamp.isdigit() or abs((now if now is not None else time.time()) - int(stamp)) > STRIPE_TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_gocardless(headers: dict[str, str], body: bytes) -> bool:
    secret = os.getenv("CLUB_GOCARDLESS_WEBHOOK_SECRET", "")
    signature = headers.get("webhook-signature", "")
    if not secret or not signature:
        return False
    return hmac.compare_digest(hmac.new(secret.encode(), body, hashlib.sha256).hexdigest(), signature)


def parse_stripe(body: bytes) -> list[Event]:
    event = json.loads(body.decode("utf-8"))
    kind, obj = event.get("type", ""), (event.get("data") or {}).get("object") or {}
    event_id, metadata = event.get("id", ""), obj.get("metadata") or {}
    base = {"event_id": event_id, "external_id": obj.get("id"), "token": obj.get("client_reference_id")}
    if kind == "checkout.session.completed" and obj.get("mode") == "setup":
        return [Event(kind="mandate_active", setup_intent=obj.get("setup_intent"), customer_id=obj.get("customer"), **base)]
    if kind == "checkout.session.completed" and obj.get("payment_status") == "paid":
        return [Event(kind="paid", intent=obj.get("payment_intent"), **base)]
    if kind in {"checkout.session.expired", "checkout.session.async_payment_failed"}:
        return [Event(kind="failed", reason=kind.rsplit(".", 1)[-1].replace("_", " "), **base)]
    # Direct Debit collections carry our reference in their metadata; card payments are confirmed by the checkout event above.
    if kind == "payment_intent.succeeded" and metadata.get("token"):
        return [Event(event_id=event_id, kind="paid", external_id=obj.get("id"), token=metadata["token"], intent=obj.get("id"))]
    if kind == "payment_intent.payment_failed" and metadata.get("token"):
        message = (obj.get("last_payment_error") or {}).get("message") or "the bank did not pay"
        return [Event(event_id=event_id, kind="failed", external_id=obj.get("id"), token=metadata["token"], reason=f"Direct Debit failed: {message}")]
    if kind == "mandate.updated" and obj.get("status") == "inactive":
        return [Event(event_id=event_id, kind="mandate_failed", mandate_id=obj.get("payment_method"), reason="Direct Debit cancelled")]
    if kind == "charge.refunded":
        return [Event(event_id=event_id, kind="refunded", external_id=obj.get("payment_intent"), amount_minor=int(obj.get("amount_refunded") or 0), reason="refunded in Stripe")]
    return []


def parse_gocardless(body: bytes) -> list[Event]:
    out: list[Event] = []
    for item in json.loads(body.decode("utf-8")).get("events", []):
        resource, action, links = item.get("resource_type"), item.get("action"), item.get("links") or {}
        common = {"event_id": item.get("id", "")}
        if resource == "payments" and action in {"confirmed", "paid_out"}:
            out.append(Event(kind="paid", external_id=links.get("payment"), **common))
        elif resource == "payments" and action in {"failed", "cancelled", "charged_back"}:
            out.append(Event(kind="failed", external_id=links.get("payment"), reason=f"Direct Debit {action.replace('_', ' ')}", **common))
        elif resource == "billing_requests" and action == "fulfilled":
            out.append(Event(kind="mandate_active", external_id=links.get("billing_request"), mandate_id=links.get("mandate_request_mandate"), customer_id=links.get("customer"), **common))
        elif resource == "mandates" and action in {"cancelled", "failed", "expired"}:
            out.append(Event(kind="mandate_failed", mandate_id=links.get("mandate"), reason=f"mandate {action}", **common))
    return out


def _audit(db: Session, action: str, payment: OnlinePayment | None, **details) -> None:
    db.add(AuditLog(actor_email="webhook", actor_role="system", action=action, target_type="online_payment", target_id=str(payment.id) if payment else "-", details=details))


def apply(db: Session, provider: str, event: Event, base_url: str = "") -> str:
    """Act on one verified provider notification. Idempotent: a replayed event does nothing. Returns what happened."""
    if event.event_id:
        if db.scalar(select(WebhookEvent.id).where(WebhookEvent.provider == provider, WebhookEvent.event_id == event.event_id)):
            return "duplicate"
        db.add(WebhookEvent(provider=provider, event_id=event.event_id))
        db.flush()
    lookup = OnlinePayment.token == event.token if event.token else OnlinePayment.external_id == event.external_id
    payment = db.scalar(select(OnlinePayment).where(lookup, OnlinePayment.provider == provider)) if (event.token or event.external_id) else None
    if event.kind == "mandate_active":
        if provider == "stripe":
            return _stripe_mandate_active(db, payment, event)
        return _mandate_active(db, payment, event, base_url)
    if event.kind == "refunded":
        return _refunded(db, provider, event)
    if event.kind == "mandate_failed":
        for mandate in db.scalars(select(PaymentMandate).where(PaymentMandate.mandate_id == event.mandate_id)):
            mandate.status = "cancelled"
        return "mandate cancelled"
    if payment is None:
        return "unknown payment"
    if event.kind == "failed":
        if payment.status == "pending":
            payment.status, payment.failure = "failed", event.reason[:255]
            _audit(db, "payment.failed", payment, reason=event.reason)
        return "failed"
    return _confirm(db, payment, event)


def _confirm(db: Session, payment: OnlinePayment, event: Event) -> str:
    if payment.status == "confirmed":
        return "already confirmed"
    invoice = db.get(Invoice, payment.invoice_id)
    payment.status, payment.confirmed_at = "confirmed", datetime.now(UTC)
    payment.provider_payment_id = event.intent or payment.provider_payment_id
    if invoice is None or invoice.status not in finance.OPEN:
        payment.failure = "The invoice was already settled when this arrived: refund it from the provider's dashboard."
        _audit(db, "payment.needs_refund", payment, invoice=invoice.reference if invoice else None)
        return "needs refund"
    owed = finance.outstanding(db, invoice)
    amount = min(payment.amount_minor, owed)
    reference = payment.external_id or event.external_id or payment.token
    if amount < payment.amount_minor:
        payment.failure = f"Only £{amount / 100:,.2f} of £{payment.amount_minor / 100:,.2f} was still owed: refund the difference."
    finance.record_payment(db, invoice, amount_minor=amount, method=_ledger_method(payment), reference=reference, actor=f"{payment.provider} webhook")
    _audit(db, "payment.confirmed", payment, invoice=invoice.reference, amount=amount)
    return "paid"


def _ledger_method(payment: OnlinePayment) -> str:
    """The payment method as the books record it (Stripe card and Direct Debit money both arrive in Stripe payouts).
    Demo payments are labelled as what they pretend to be."""
    if payment.provider in {"gocardless", "stripe"}:
        return payment.provider
    if payment.method == "direct_debit":
        return "gocardless" if os.getenv("CLUB_DIRECT_DEBIT", "stripe").lower() == "gocardless" else "stripe"
    return "card"


def _stripe_mandate_active(db: Session, payment: OnlinePayment | None, event: Event) -> str:
    """The member finished Stripe's Direct Debit page: remember the bank account and, if they were paying an invoice, collect it."""
    mandate = db.scalar(select(PaymentMandate).where(PaymentMandate.provider == "stripe", PaymentMandate.mandate_id == f"setup:{event.external_id}"))
    if mandate is None or not event.setup_intent:
        return "unknown mandate"
    setup = _stripe(f"/v1/setup_intents/{event.setup_intent}")
    if not setup.get("payment_method"):
        return "unknown mandate"
    for older in db.scalars(select(PaymentMandate).where(PaymentMandate.contact_id == mandate.contact_id, PaymentMandate.status == "active")):
        older.status = "replaced"
    mandate.mandate_id, mandate.status = str(setup["payment_method"]), "active"
    mandate.customer_id = event.customer_id or mandate.customer_id
    _audit(db, "direct_debit.set_up", payment, contact=mandate.contact_id)
    db.flush()
    invoice = db.get(Invoice, payment.invoice_id) if payment is not None else None
    if payment is None or invoice is None or invoice.status not in finance.OPEN or finance.outstanding(db, invoice) <= 0:
        return "mandate saved"
    _stripe_collect(db, payment, invoice, mandate)
    return "collecting"


def _refunded(db: Session, provider: str, event: Event) -> str:
    """Money given back from the provider's dashboard: take it off the invoice so the books match the bank."""
    payment = db.scalar(select(OnlinePayment).where(OnlinePayment.provider == provider, OnlinePayment.provider_payment_id == event.external_id)) if event.external_id else None
    if payment is None or payment.status != "confirmed":
        return "unknown payment"
    invoice = db.get(Invoice, payment.invoice_id)
    if invoice is None:
        return "unknown payment"
    reference = payment.external_id or ""
    rows = db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id, PaymentTransaction.bank_reference == reference, PaymentTransaction.status == "received")).all()
    applied = sum(p.amount_minor for p in rows if p.payment_method != "refund")  # what this payment put on the invoice
    already = -sum(p.amount_minor for p in rows if p.payment_method == "refund")
    give_back = min(event.amount_minor, applied) - already
    if applied == 0:
        _audit(db, "payment.refunded", payment, invoice=invoice.reference, amount=event.amount_minor, note="was never applied to the invoice")
        return "refund noted"
    if give_back <= 0:
        return "already refunded"
    finance.record_refund(db, invoice, amount_minor=give_back, reference=reference, actor=f"{provider} webhook", reason=event.reason)
    payment.failure = f"£{give_back / 100:,.2f} refunded in {provider.title()}. If the club meant to give it back, add a credit note so they are not chased."
    _audit(db, "payment.refunded", payment, invoice=invoice.reference, amount=give_back)
    return "refunded"


def _mandate_active(db: Session, payment: OnlinePayment | None, event: Event, base_url: str) -> str:
    """The member finished setting up their Direct Debit: remember it and collect the invoice they were paying."""
    if payment is None or not event.mandate_id:
        return "unknown mandate"
    invoice = db.get(Invoice, payment.invoice_id)
    contact = db.get(Contact, invoice.contact_id) if invoice and invoice.contact_id else None
    if contact is None:
        return "no contact"
    if not db.scalar(select(PaymentMandate.id).where(PaymentMandate.mandate_id == event.mandate_id)):
        db.add(PaymentMandate(contact_id=contact.id, provider="gocardless", mandate_id=event.mandate_id, customer_id=event.customer_id, status="active"))
        db.flush()
    if invoice is None or invoice.status not in finance.OPEN:
        return "mandate saved"
    owed = finance.outstanding(db, invoice)
    created = _gocardless("/payments", {"payments": {"amount": owed, "currency": "GBP", "description": f"Invoice {invoice.reference}", "metadata": {"invoice": invoice.reference, "token": payment.token}, "links": {"mandate": event.mandate_id}}}, idempotency_key=payment.token)
    payment.external_id, payment.amount_minor = created["payments"]["id"], owed
    db.flush()
    return "collecting"


# --------------------------------------------------------------------------- the sandbox (demos only)


def sandbox_pay(db: Session, token: str) -> str:
    """Pretend the provider confirmed this payment. Refuses outside a development or demo deployment."""
    if mode() != "sandbox":
        raise PaymentError("Demo payments are switched off.")
    payment = db.scalar(select(OnlinePayment).where(OnlinePayment.token == token, OnlinePayment.provider == "sandbox"))
    if payment is None:
        raise PaymentError("That payment link is not valid.")
    return apply(db, "sandbox", Event(event_id=f"sandbox-{payment.token}", kind="paid", token=payment.token))
