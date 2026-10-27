"""How the club website and the office work together, server to server.

The website keeps its own pages, calendar, results and duties. It talks to the office in three ways, all signed with the
website's key (``website:<secret>`` in ``CLUB_FEED_KEYS``):

1. **Reading lists** (members' directory, boats, coming events): the feeds in app/feeds.py.
2. **Signing members in**: the "My membership" link on the website opens the office already signed in::

       GET /sso?email=<address>&ts=<unix seconds>&nonce=<random>&sig=<hex>
       sig = HMAC-SHA256(secret, "sso\\n<email in lower case>\\n<ts>\\n<nonce>")

   The link works once, for two minutes. Every email address belongs to one person, so there is nobody to choose
   between. It only signs in ordinary members: anyone who can see other people's
   details (administrators, committee members, volunteers with a hat) signs in with the office's own emailed code,
   so a break-in on the website cannot open the club's records.
3. **Forms on the website** (a question about joining, a message for the Bosun) post to the office::

       POST /incoming/<kind>          body: JSON
       X-Club-Client: website
       X-Club-Timestamp: <unix seconds>
       X-Club-Nonce: <random>
       X-Club-Signature: HMAC-SHA256(secret, "<kind>\\nwebsite\\n<ts>\\n<nonce>\\n<SHA-256 hex of the body>")

See docs/WEBSITE.md for worked examples in PHP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import applications, auth, feeds, office
from app.feeds import FeedRefused
from app.models import Contact, Role

SSO_MAX_AGE = 120
INCOMING_MAX_AGE = 300
KINDS = ("enquiry", "bosun")


class StaffMustSignIn(Exception):
    """The address belongs to someone who can see other people's records: they use the office's own sign-in."""


def _secret() -> str:
    secret = feeds.keys().get("website")
    if secret is None:
        raise FeedRefused(403, "The website is not connected to the club office yet.")
    return secret


def sso_signature(secret: str, email: str, ts: str, nonce: str) -> str:
    return feeds.sign_parts(secret, "sso", email.strip().lower(), ts, nonce)


def check_sso(db: Session, email: str, ts: str, nonce: str, sig: str, now: float | None = None) -> Role:
    """Who this sign-in link is for, if it is genuine, fresh and unused. Raises FeedRefused or StaffMustSignIn."""
    secret = _secret()
    email = (email or "").strip().lower()
    if not feeds.fresh(ts, SSO_MAX_AGE, now):
        raise FeedRefused(401, "This sign-in link has run out. Go back to the website and press My membership again.")
    if not email or not hmac.compare_digest(sso_signature(secret, email, ts, nonce), sig or ""):
        raise FeedRefused(401, "This sign-in link is not valid.")
    feeds.remember(db, "sso:website", nonce, "sso")
    role = auth.role_for_email(db, email)
    if role is None:
        raise FeedRefused(404, "The club office does not have that email address. If you are a member, ask the membership secretary to check it.")
    if auth.is_staff(db, email):
        raise StaffMustSignIn(email)
    return role


def incoming_signature(secret: str, kind: str, client: str, ts: str, nonce: str, body: bytes) -> str:
    return feeds.sign_parts(secret, kind, client, ts, nonce, hashlib.sha256(body).hexdigest())


def check_incoming(db: Session, kind: str, headers: dict[str, str], body: bytes, now: float | None = None) -> dict[str, Any]:
    """The form data, if this is a genuine, fresh, unused request from the website."""
    if kind not in KINDS:
        raise FeedRefused(404, "unknown form")
    client = headers.get("x-club-client", "")
    if client != "website":
        raise FeedRefused(403, "only the website may send forms")
    secret = _secret()
    ts, nonce = headers.get("x-club-timestamp", ""), headers.get("x-club-nonce", "")
    if not feeds.fresh(ts, INCOMING_MAX_AGE, now):
        raise FeedRefused(401, "the request is too old (check the clock)")
    if not hmac.compare_digest(incoming_signature(secret, kind, client, ts, nonce, body), headers.get("x-club-signature", "")):
        raise FeedRefused(401, "bad signature")
    feeds.remember(db, "form:website", nonce, kind)
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise FeedRefused(400, "the body must be JSON") from exc
    if not isinstance(data, dict):
        raise FeedRefused(400, "the body must be a JSON object")
    return {k: str(v).strip() if v is not None else "" for k, v in data.items()}


def receive(db: Session, kind: str, data: dict[str, str]) -> str:
    """Act on a form from the website. Returns the thank-you to show the person. Raises ApplicationError for bad input."""
    name, email, phone, message = data.get("name", ""), data.get("email", ""), data.get("phone", ""), data.get("message", "")
    consent = data.get("consent", "").lower() in {"1", "true", "yes", "on"}
    if kind == "bosun":
        contact = db.scalar(select(Contact).where(func.lower(Contact.email) == email.lower(), Contact.member_id.is_not(None))) if email else None
        if contact is not None:
            office.create_request(db, contact, "bosun", message, member_id=contact.member_id)
            return "Thank you. The Bosun has been told and will reply by email."
        applications.submit_enquiry(db, name=name, email=email, phone=phone, interest="boat_storage", message=f"For the Bosun: {message}".strip(), source="website", consent=consent)
        return "Thank you. Please check your email and confirm your address, then the club will be in touch."
    applications.submit_enquiry(db, name=name, email=email, phone=phone, interest=data.get("interest") or "other", message=message, source="website", consent=consent)
    return "Thank you. Please check your email and confirm your address, then a member of the club will be in touch."
