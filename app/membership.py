"""Membership types, fees and renewals.

A *renewal* is the decision to continue a member into a period. Confirming it creates the
membership record for that period, updates the member, and (optionally) bills the fee through
the finance service, so renewals can no longer be confirmed without any effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance
from app.models import (
    AuditLog,
    Contact,
    Invoice,
    Member,
    Membership,
    MembershipCharge,
    MembershipPeriod,
    MembershipRenewal,
    MembershipType,
)
from app.security import now_utc

# The club's membership price list, in pence. Applied only where the
# type exists and has no fee yet, so a treasurer's edits are never overwritten.
KNOWN_FEES = {
    "Honorary Member": 0,
    "Non Sailing + 1C": 6450,
    "Non Sailing + 2C": 7050,
    "Non Sailing + 3C": 7650,
    "Non-Sailing Adult": 5850,
    "Sailing Adul (6 Month)": 5400,
    "Sailing Adult (6 Month)": 5400,
    "Sailing Adult + Partner": 15300,
    "Sailing Family 1A+1C": 11400,
    "Sailing Family 1A+2C": 12000,
    "Sailing Family 1A+3C": 12600,
    "Sailing Family 2A+1C": 15900,
    "Sailing Family 2A+2C": 16500,
    "Sailing Family 2A+3C": 17100,
    "Sailing Student Member": 7650,
    "Single Adult Sailing": 10800,
    "Temp Sailing Adult (3mth)": 3000,
    "RYA Course Membership": 0,
}
DEFAULT_NOMINAL = "0101"


def household_capacity(type_name: str | None) -> int | None:
    """How many people a membership type covers, read from its name ("2A+2C", "Adult + Partner"), or None if the name does not say."""
    name = (type_name or "").strip()
    if match := re.search(r"(\d)\s*A\s*\+\s*(\d)\s*C", name, re.I):
        return int(match.group(1)) + int(match.group(2))
    if match := re.search(r"\+\s*(\d)\s*C\b", name, re.I):
        return 1 + int(match.group(1))
    if re.search(r"adult\s*\+\s*partner", name, re.I):
        return 2
    if re.search(r"single|student|non-sailing adult|honorary|temp|6 month|course", name, re.I):
        return 1
    return None


def active_memberships(db: Session, on: date | None = None) -> int:
    """Memberships in force on a date: the number of active memberships, not the people they cover."""
    day = on or date.today()
    return (
        db.scalar(
            select(func.count()).select_from(Membership).where(Membership.status == "active", Membership.starts_on <= day, Membership.ends_on >= day)
        )
        or 0
    )


class MembershipError(ValueError):
    pass


def ensure_type(db: Session, name: str) -> MembershipType:
    mtype = db.scalar(select(MembershipType).where(func.lower(MembershipType.name) == name.strip().lower()))
    if mtype is None:
        mtype = MembershipType(name=name.strip(), nominal_code=DEFAULT_NOMINAL, active=True, online_joinable=False)
        db.add(mtype)
        db.flush()
    return mtype


def sync_types(db: Session) -> int:
    """Create a type for every plan name used by a membership, and apply the known fees. Returns types created."""
    before = db.scalar(select(func.count()).select_from(MembershipType)) or 0
    for (name,) in db.execute(select(Membership.plan_name).distinct()):
        if name and name != "unknown":
            ensure_type(db, name)
    for mtype in db.scalars(select(MembershipType)):
        if mtype.fee_minor is None and mtype.name in KNOWN_FEES:
            mtype.fee_minor = KNOWN_FEES[mtype.name]
    db.flush()
    return (db.scalar(select(func.count()).select_from(MembershipType)) or 0) - before


def raise_fees(db: Session, percent: float, type_ids: list[int] | None = None) -> int:
    """Put every (or the chosen) membership fee up by a percentage, rounded to the nearest 50p. Free types stay free."""
    if percent <= -50 or percent > 100:
        raise MembershipError("A rise must be between -50% and 100%")
    changed = 0
    for mtype in db.scalars(select(MembershipType).where(MembershipType.fee_minor.is_not(None))):
        if (type_ids is not None and mtype.id not in type_ids) or not mtype.fee_minor:
            continue
        new_fee = int(round(round(mtype.fee_minor * (1 + percent / 100)) / 50)) * 50
        if new_fee != mtype.fee_minor:
            mtype.fee_minor = new_fee
            changed += 1
    db.flush()
    return changed


def latest_membership(db: Session, member_id: int, before: date | None = None) -> Membership | None:
    stmt = select(Membership).where(Membership.member_id == member_id)
    if before:
        stmt = stmt.where(Membership.starts_on < before)
    return db.scalars(stmt.order_by(Membership.starts_on.desc(), Membership.id.desc())).first()


def type_for(db: Session, member: Member, period: MembershipPeriod | None = None) -> MembershipType | None:
    previous = latest_membership(db, member.id, period.starts_on if period else None)
    name = previous.plan_name if previous else member.membership_type
    if not name or name in {"standard", "unknown"}:
        return None
    return db.scalar(select(MembershipType).where(func.lower(MembershipType.name) == name.lower()))


@dataclass
class RenewalRow:
    renewal: MembershipRenewal | None
    member: Member
    mtype: MembershipType | None
    fee_minor: int | None
    state: str
    note: str = ""


@dataclass
class RunReport:
    prepared: int = 0
    confirmed: int = 0
    invoiced: int = 0
    skipped: list[str] = field(default_factory=list)
    invoices: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"prepared": self.prepared, "confirmed": self.confirmed, "invoiced": self.invoiced, "skipped": self.skipped, "invoices": self.invoices}


def _eligible_members(db: Session, period: MembershipPeriod) -> list[Member]:
    """Members whose most recent membership before the period is active (they are due to renew)."""
    out = []
    for member in db.scalars(select(Member).order_by(Member.name)):
        previous = latest_membership(db, member.id, period.starts_on)
        if previous is not None and previous.status == "active":
            out.append(member)
    return out


def preview(db: Session, period: MembershipPeriod) -> list[RenewalRow]:
    existing = {r.member_id: r for r in db.scalars(select(MembershipRenewal).where(MembershipRenewal.period_id == period.id))}
    rows: list[RenewalRow] = []
    seen: set[int] = set()
    for member in _eligible_members(db, period):
        renewal = existing.get(member.id)
        mtype = type_for(db, member, period)
        note = ""
        if mtype is None:
            note = "No membership type"
        elif mtype.fee_minor is None:
            note = "No fee set for this type"
        rows.append(RenewalRow(renewal, member, mtype, mtype.fee_minor if mtype else None, renewal.state if renewal else "not started", note))
        seen.add(member.id)
    for member_id, renewal in existing.items():
        if member_id not in seen:
            extra = db.get(Member, member_id)
            if extra:
                mtype = type_for(db, extra, period)
                rows.append(RenewalRow(renewal, extra, mtype, mtype.fee_minor if mtype else None, renewal.state, "Added manually"))
    return rows


def prepare(db: Session, period: MembershipPeriod) -> RunReport:
    report = RunReport()
    for row in preview(db, period):
        if row.renewal is not None:
            continue
        db.add(MembershipRenewal(member_id=row.member.id, period_id=period.id, state="prepared", suggestion_notes=row.mtype.name if row.mtype else ""))
        report.prepared += 1
    db.flush()
    return report


def confirm_effects(db: Session, renewal: MembershipRenewal, status: str = "active") -> Membership:
    """Create the membership for the renewal's period and bring the member up to date. Idempotent.

    ``status="pending_payment"`` (used by self-service) leaves the member as they are until the invoice is paid."""
    member = db.get(Member, renewal.member_id)
    period = db.get(MembershipPeriod, renewal.period_id)
    if member is None or period is None:
        raise MembershipError("Renewal refers to a missing member or period")
    existing = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == period.starts_on))
    previous = latest_membership(db, member.id, period.starts_on)
    plan = (previous.plan_name if previous else None) or member.membership_type
    if existing is None:
        existing = Membership(member_id=member.id, plan_name=plan, starts_on=period.starts_on, ends_on=period.ends_on, status="active")
        db.add(existing)
    existing.status, existing.ends_on = status, period.ends_on
    if status == "active":
        member.active, member.renewal_status, member.expiry_date, member.membership_type = True, "active", period.ends_on, existing.plan_name
    renewal.state, renewal.confirmed_at = "confirmed", renewal.confirmed_at or now_utc()
    db.flush()
    return existing


def invoice_renewal(db: Session, renewal: MembershipRenewal, actor: str, issue: bool = True) -> Invoice | None:
    """Bill the renewal once. Returns the existing invoice if it was already billed."""
    existing_charge = db.scalar(select(MembershipCharge).where(MembershipCharge.renewal_id == renewal.id, MembershipCharge.invoice_id.is_not(None)))
    if existing_charge is not None:
        return db.get(Invoice, existing_charge.invoice_id)
    member = db.get(Member, renewal.member_id)
    period = db.get(MembershipPeriod, renewal.period_id)
    if member is None or period is None:
        raise MembershipError("Renewal refers to a missing member or period")
    membership = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == period.starts_on))
    mtype = db.scalar(select(MembershipType).where(func.lower(MembershipType.name) == (membership.plan_name if membership else member.membership_type).lower()))
    if mtype is None or mtype.fee_minor is None:
        raise MembershipError(f"No fee is set for {membership.plan_name if membership else member.membership_type}")
    if mtype.fee_minor <= 0:
        return None  # free memberships (honorary, course) need no invoice
    contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
    invoice = finance.create_invoice(
        db,
        member_id=member.id,
        contact_id=contact.id if contact else None,
        lines=[
            {
                "description": f"{mtype.name} membership {period.name}",
                "amount_minor": mtype.fee_minor,
                "nominal_code": mtype.nominal_code or DEFAULT_NOMINAL,
                "source_type": "membership",
                "source_id": str(renewal.id),
            }
        ],
        actor=actor,
    )
    if issue:
        finance.issue(db, invoice, actor)
    db.add(MembershipCharge(renewal_id=renewal.id, amount_minor=mtype.fee_minor, invoice_id=invoice.id, status="invoiced"))
    db.flush()
    return invoice


def confirm_and_invoice(db: Session, renewal: MembershipRenewal, actor: str, issue: bool = True) -> Invoice | None:
    confirm_effects(db, renewal)
    return invoice_renewal(db, renewal, actor, issue)


def run_billing(db: Session, period: MembershipPeriod, actor: str, member_ids: list[int] | None = None, issue: bool = True) -> RunReport:
    """Confirm and invoice every prepared renewal (or just the chosen members). Safe to run twice."""
    report = RunReport()
    rows = db.scalars(select(MembershipRenewal).where(MembershipRenewal.period_id == period.id, MembershipRenewal.state != "declined"))
    for renewal in rows:
        if member_ids is not None and renewal.member_id not in member_ids:
            continue
        member = db.get(Member, renewal.member_id)
        name = member.name if member else str(renewal.member_id)
        was_confirmed = renewal.state == "confirmed"
        already_billed = db.scalar(select(MembershipCharge.id).where(MembershipCharge.renewal_id == renewal.id, MembershipCharge.invoice_id.is_not(None))) is not None
        savepoint = db.begin_nested()
        try:
            invoice = confirm_and_invoice(db, renewal, actor, issue)
            savepoint.commit()
        except (MembershipError, finance.FinanceError) as exc:
            savepoint.rollback()
            report.skipped.append(f"{name}: {exc}")
            continue
        if not was_confirmed:
            report.confirmed += 1
        if invoice is not None and not already_billed:
            report.invoices.append(invoice.id)
            report.invoiced += 1
    db.flush()
    return report


def unwind_renewal(db: Session, renewal: MembershipRenewal, actor: str, reason: str) -> list[str]:
    """Undo what billing a renewal did when the member then decides not to continue.

    The membership for that period is marked resigned (so it is not counted), an unpaid renewal invoice is voided, and an invoice
    that was already paid is left alone and reported, because refunding money is the treasurer's decision. Returns what was done.
    """
    done: list[str] = []
    period = db.get(MembershipPeriod, renewal.period_id)
    if period is None:
        return done
    row = db.scalar(select(Membership).where(Membership.member_id == renewal.member_id, Membership.starts_on == period.starts_on))
    if row is not None and row.status in {"active", "pending_payment"}:
        row.status = "resigned"
        done.append(f"{period.name} membership cancelled")
    charge = db.scalar(select(MembershipCharge).where(MembershipCharge.renewal_id == renewal.id, MembershipCharge.invoice_id.is_not(None)))
    invoice = db.get(Invoice, charge.invoice_id) if charge and charge.invoice_id else None
    if invoice is not None and invoice.status in finance.OPEN:
        if finance.paid_total(db, invoice.id) == 0:
            finance.void_invoice(db, invoice, actor, f"Member resigned: {reason}".strip(": "))
            if charge is not None:
                charge.status = "void"
            done.append(f"invoice {invoice.reference} voided")
        else:
            done.append(f"invoice {invoice.reference} was part paid: credit or refund it from the invoice page")
    elif invoice is not None and invoice.status == "paid":
        done.append(f"invoice {invoice.reference} was already paid: credit or refund it from the invoice page")
    return done


def decline(db: Session, renewal: MembershipRenewal, actor: str = "system", reason: str = "") -> None:
    """The member is not continuing: record it and mark their membership resigned for that period."""
    member = db.get(Member, renewal.member_id)
    period = db.get(MembershipPeriod, renewal.period_id)
    unwind_renewal(db, renewal, actor, reason)
    renewal.state = "declined"
    if member and period:
        previous = latest_membership(db, member.id, period.starts_on)
        if previous and previous.status == "active":
            previous.status = "resigned"
        member.active, member.renewal_status = False, "resigned"
    db.flush()


def create_period(db: Session, year: int, name: str | None = None) -> MembershipPeriod:
    start, end = date(year, 1, 1), date(year, 12, 31)
    existing = db.scalar(select(MembershipPeriod).where(MembershipPeriod.starts_on == start))
    if existing:
        return existing
    period = MembershipPeriod(name=name or str(year), starts_on=start, ends_on=end, active=True)
    db.add(period)
    db.flush()
    return period


# --------------------------------------------------------------------------- self-service


@dataclass
class RenewalOffer:
    period: MembershipPeriod
    mtype: MembershipType | None
    fee_minor: int | None
    reason: str = ""  # why it cannot be self-served, if it cannot


def renewal_window_months(db: Session) -> int:
    try:
        return max(0, min(12, int(finance.get_setting(db, "renewal_window_months"))))
    except ValueError:
        return 4


def _window_opens(year: int, months: int) -> date:
    """The first day members can renew into a season that starts on 1 January: ``months`` months earlier."""
    month_index = 12 * year + 0 - months  # months since year 0, counting January as 0
    return date(month_index // 12, month_index % 12 + 1, 1)


def renewal_offer(db: Session, member: Member, today: date | None = None, window_months: int | None = None) -> RenewalOffer | None:
    """The period this member could renew into right now, or None if there is nothing to renew.

    The current year if they hold no membership for it; next year once the renewal window (months before it starts) has opened.
    """
    today = today or date.today()
    months = renewal_window_months(db) if window_months is None else window_months
    for year in (today.year, today.year + 1):
        if year == today.year + 1 and today < _window_opens(year, months):
            continue
        have = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == date(year, 1, 1), Membership.status.in_(["active", "pending_payment"])))
        if have is not None:
            continue
        previous = latest_membership(db, member.id, date(year, 1, 1))
        if previous is None:
            return None  # never been a member: they apply, not renew
        period = create_period(db, year)
        mtype = type_for(db, member, period)
        reason = ""
        if mtype is None:
            reason = "We need to confirm your membership type: please contact the membership secretary."
        elif mtype.fee_minor is None:
            reason = "The fee for your membership type has not been set yet: please contact the membership secretary."
        return RenewalOffer(period, mtype, mtype.fee_minor if mtype else None, reason)
    return None


def awaiting_payment_invoice(db: Session, member: Member) -> Invoice | None:
    """An open renewal invoice for this member, if they have already renewed but not yet paid."""
    stmt = (
        select(Invoice)
        .join(MembershipCharge, MembershipCharge.invoice_id == Invoice.id)
        .join(MembershipRenewal, MembershipRenewal.id == MembershipCharge.renewal_id)
        .where(MembershipRenewal.member_id == member.id, Invoice.status.in_(finance.OPEN))
        .order_by(Invoice.id.desc())
    )
    return db.scalars(stmt).first()


def self_renew(db: Session, member: Member, actor: str, today: date | None = None) -> Invoice | None:
    """A member renews themselves. The membership is 'pending_payment' until the invoice is paid (free types start at once)."""
    waiting = awaiting_payment_invoice(db, member)
    if waiting is not None:
        return waiting  # they already renewed: a second click must not create a second invoice
    offer = renewal_offer(db, member, today=today)
    if offer is None:
        raise MembershipError("There is nothing to renew right now")
    if offer.reason or offer.mtype is None or offer.fee_minor is None:
        raise MembershipError(offer.reason or "This membership cannot be renewed online")
    renewal = db.scalar(select(MembershipRenewal).where(MembershipRenewal.member_id == member.id, MembershipRenewal.period_id == offer.period.id))
    if renewal is None:
        renewal = MembershipRenewal(member_id=member.id, period_id=offer.period.id, state="prepared", suggestion_notes="self-service")
        db.add(renewal)
        db.flush()
    elif renewal.state == "declined":
        renewal.state = "prepared"
    free = offer.fee_minor <= 0
    confirm_effects(db, renewal, status="active" if free else "pending_payment")
    if free:
        return None
    return invoice_renewal(db, renewal, actor)


def resign_at_expiry(db: Session, member: Member, reason: str, actor: str) -> None:
    """The member will not renew. They stay a member until their current membership ends."""
    today = date.today()
    previous = latest_membership(db, member.id)
    if previous is not None and previous.starts_on > today:
        year = previous.starts_on.year  # already renewed into a season that has not started: that is the one they are cancelling
    elif previous is not None and previous.ends_on >= today:
        year = previous.ends_on.year + 1  # they stay a member until it ends, and do not renew after
    else:
        year = today.year
    period = create_period(db, year)
    renewal = db.scalar(select(MembershipRenewal).where(MembershipRenewal.member_id == member.id, MembershipRenewal.period_id == period.id))
    if renewal is None:
        renewal = MembershipRenewal(member_id=member.id, period_id=period.id, state="declined", suggestion_notes=f"Resigned: {reason.strip()[:200]}")
        db.add(renewal)
    else:
        renewal.state, renewal.suggestion_notes = "declined", f"Resigned: {reason.strip()[:200]}"
    db.flush()
    undone = unwind_renewal(db, renewal, actor, reason)
    db.add(AuditLog(actor_email=actor, actor_role="user", action="membership.resign", target_type="member", target_id=str(member.id), details={"reason": reason, "period": period.name, "unwound": undone}))
    db.flush()


def activate_on_payment(db: Session, invoice: Invoice) -> None:
    """Hook: a paid renewal invoice turns the pending membership active and brings the member up to date."""
    charge = db.scalar(select(MembershipCharge).where(MembershipCharge.invoice_id == invoice.id))
    if charge is None:
        return
    renewal = db.get(MembershipRenewal, charge.renewal_id)
    period = db.get(MembershipPeriod, renewal.period_id) if renewal else None
    member = db.get(Member, renewal.member_id) if renewal else None
    if renewal is None or period is None or member is None:
        return
    pending = db.scalar(select(Membership).where(Membership.member_id == member.id, Membership.starts_on == period.starts_on, Membership.status == "pending_payment"))
    if pending is None:
        return
    pending.status = "active"
    member.active, member.renewal_status, member.payment_status = True, "active", "paid"
    member.expiry_date, member.membership_type = period.ends_on, pending.plan_name
    charge.status = "paid"
    db.flush()


finance.PAID_HOOKS.append(activate_on_payment)
