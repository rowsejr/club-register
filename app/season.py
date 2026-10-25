"""The annual cycle: renewal notices, one invoice per payer for membership, boat park and extras, chasing and lapsing.

Each season the club bills the same people for several things. Rather than three runs that send three invoices, this
module plans one invoice per payer from the memberships due, the boat park spaces held and the recurring fees, shows it
for checking, and creates it. Every step is safe to repeat: whatever has been billed is recognised and skipped.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import boats, finance, invoice_comms, mailer, membership
from app.models import (
    AuditLog,
    Boat,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    Membership,
    MembershipCharge,
    MembershipPeriod,
    MembershipRenewal,
    OutboundEmail,
    RecurringFee,
    StorageAllocation,
)

NOTICE_GAP_DAYS = 30


# --------------------------------------------------------------------------- the plan


@dataclass
class Item:
    kind: str  # membership | storage | extra
    description: str
    amount_minor: int
    nominal: str
    source_type: str
    source_id: str
    renewal_id: int | None = None  # for memberships
    member_id: int | None = None


@dataclass
class PayerPlan:
    contact: Contact
    items: list[Item] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)  # what has been billed this season

    @property
    def total(self) -> int:
        return sum(i.amount_minor for i in self.items)

    @property
    def can_email(self) -> bool:
        return bool(self.contact.email) and not self.contact.suppressed


def _renewal_billed(db: Session, renewal_id: int) -> Invoice | None:
    charge = db.scalar(select(MembershipCharge).where(MembershipCharge.renewal_id == renewal_id, MembershipCharge.invoice_id.is_not(None)))
    return db.get(Invoice, charge.invoice_id) if charge and charge.invoice_id else None


def _line_billed(db: Session, source_type: str, source_id: str) -> bool:
    return (
        db.scalar(
            select(InvoiceLine.id)
            .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
            .where(InvoiceLine.source_type == source_type, InvoiceLine.source_id == source_id, Invoice.status.not_in(["void", "cancelled"]))
            .limit(1)
        )
        is not None
    )


def recurring_fees_for(db: Session, year: int) -> list[RecurringFee]:
    start, end = date(year, 1, 1), date(year, 12, 31)
    return [
        fee
        for fee in db.scalars(select(RecurringFee).where(RecurringFee.active.is_(True)).order_by(RecurringFee.id))
        if (fee.starts_on is None or fee.starts_on <= end) and (fee.ends_on is None or fee.ends_on >= start)
    ]


def plan(db: Session, year: int) -> list[PayerPlan]:
    """What each payer should be invoiced for the season, and what cannot be billed yet and why."""
    period = membership.create_period(db, year)
    plans: dict[int, PayerPlan] = {}

    def for_contact(contact: Contact) -> PayerPlan:
        return plans.setdefault(contact.id, PayerPlan(contact))

    orphan = PayerPlan(Contact(id=0, name="(no payer)"))
    for row in membership.preview(db, period):
        contact = db.scalar(select(Contact).where(Contact.member_id == row.member.id))
        target = for_contact(contact) if contact else orphan
        if row.renewal is not None and row.renewal.state == "declined":
            continue
        billed = _renewal_billed(db, row.renewal.id) if row.renewal else None
        if billed is not None:
            target.already.append(f"{row.mtype.name if row.mtype else 'Membership'} (invoice {billed.reference})")
            continue
        if row.mtype is None or row.fee_minor is None:
            target.problems.append(f"{row.member.name}: {row.note or 'no fee set'}")
            continue
        if row.fee_minor == 0 and row.renewal is not None and row.renewal.state == "confirmed":
            target.already.append(f"{row.mtype.name} (free)")
            continue
        target.items.append(
            Item(
                "membership", f"{row.mtype.name} membership {period.name}", row.fee_minor, row.mtype.nominal_code or membership.DEFAULT_NOMINAL, "membership",
                str(row.renewal.id) if row.renewal else f"new:{row.member.id}", row.renewal.id if row.renewal else None, row.member.id,
            )
        )
    for brow in boats.billing_preview(db, year):
        if brow.billed:
            if brow.payer:
                for_contact(brow.payer).already.append(f"{brow.resource.name}: {brow.boat.name}")
            continue
        if brow.payer is None:
            orphan.problems.append(f"{brow.boat.name} ({brow.resource.name}): no owner to bill")
            continue
        if brow.fee_minor is None:
            for_contact(brow.payer).problems.append(f"{brow.boat.name} ({brow.resource.name}): {brow.note}")
            continue
        if brow.fee_minor > 0:
            for_contact(brow.payer).items.append(
                Item("storage", f"{brow.resource.name} {year}: {brow.boat.name}" + (f" ({brow.detail})" if brow.detail else ""), brow.fee_minor, brow.nominal, "storage", f"{brow.allocation.id}:{year}")
            )
    for fee in recurring_fees_for(db, year):
        contact = db.get(Contact, fee.contact_id)
        if contact is None:
            continue
        key = f"{fee.id}:{year}"
        if _line_billed(db, "recurring", key):
            for_contact(contact).already.append(fee.name)
            continue
        quantity = fee.quantity or 1.0
        label = fee.name + (f" × {quantity:g}" if quantity != 1 else "")
        for_contact(contact).items.append(Item("extra", f"{label} {year}", round(fee.amount_minor * quantity), fee.nominal_code or "", "recurring", key))
    result = sorted((p for p in plans.values() if p.items or p.problems or p.already), key=lambda p: p.contact.name.lower())
    if orphan.problems:
        result.append(orphan)
    return result


# --------------------------------------------------------------------------- creating the invoices


@dataclass
class RunReport:
    invoices: list[int] = field(default_factory=list)
    emailed: int = 0
    not_emailed: list[str] = field(default_factory=list)  # people with no email: they need the PDF posted or handed over
    skipped: list[str] = field(default_factory=list)
    memberships_confirmed: int = 0

    def as_dict(self) -> dict:
        return {"invoices": self.invoices, "emailed": self.emailed, "not_emailed": self.not_emailed, "skipped": self.skipped, "memberships_confirmed": self.memberships_confirmed}


def _groups(items: list[Item], mode: str) -> list[list[Item]]:
    if mode == "separate":
        by_kind: dict[str, list[Item]] = {}
        for item in items:
            by_kind.setdefault(item.kind, []).append(item)
        return list(by_kind.values())
    return [items]


def _confirm_membership(db: Session, item: Item, period: MembershipPeriod, invoice: Invoice | None, actor: str) -> None:
    renewal = db.get(MembershipRenewal, item.renewal_id) if item.renewal_id else None
    if renewal is None and item.member_id:
        renewal = db.scalar(select(MembershipRenewal).where(MembershipRenewal.member_id == item.member_id, MembershipRenewal.period_id == period.id))
        if renewal is None:
            renewal = MembershipRenewal(member_id=item.member_id, period_id=period.id, state="prepared")
            db.add(renewal)
            db.flush()
    if renewal is None:
        return
    membership.confirm_effects(db, renewal)
    if invoice is not None:
        db.add(MembershipCharge(renewal_id=renewal.id, amount_minor=item.amount_minor, invoice_id=invoice.id, status="invoiced"))


def run(db: Session, year: int, actor: str, *, payer_ids: list[int] | None = None, issue: bool = True, send: bool = True) -> RunReport:
    """Create the season's invoices: one per payer (or one per kind, if the club prefers), then email them. Safe to run twice."""
    report = RunReport()
    period = membership.create_period(db, year)
    mode = finance.get_setting(db, "season_invoice_mode") or "combined"
    for payer in plan(db, year):
        if payer.contact.id == 0 or (payer_ids is not None and payer.contact.id not in payer_ids):
            report.skipped += [f"{p}" for p in payer.problems] if payer.contact.id == 0 else []
            continue
        report.skipped += [f"{payer.contact.name}: {problem}" for problem in payer.problems]
        if not payer.items:
            continue
        for group in _groups(payer.items, mode):
            savepoint = db.begin_nested()
            try:
                billable = [i for i in group if i.amount_minor > 0]
                invoice = None
                if billable:
                    invoice = finance.create_invoice(
                        db,
                        contact_id=payer.contact.id,
                        lines=[{"description": i.description, "amount_minor": i.amount_minor, "nominal_code": i.nominal or None, "source_type": i.source_type, "source_id": i.source_id} for i in billable],
                        actor=actor,
                        notes=f"Season {year}",
                        source="season",
                        tags=f"season {year}",
                    )
                    if issue:
                        finance.issue(db, invoice, actor)
                for item in group:
                    if item.kind == "membership":
                        _confirm_membership(db, item, period, invoice, actor)
                        report.memberships_confirmed += 1
                savepoint.commit()
            except (finance.FinanceError, membership.MembershipError) as exc:
                savepoint.rollback()
                report.skipped.append(f"{payer.contact.name}: {exc}")
                continue
            if invoice is None:
                continue
            report.invoices.append(invoice.id)
            if send and issue:
                if payer.can_email:
                    record = invoice_comms.send_invoice(db, invoice, actor)
                    report.emailed += record.status != "failed"
                    if record.status == "failed":
                        report.not_emailed.append(f"{payer.contact.name}: the email failed")
                else:
                    report.not_emailed.append(f"{payer.contact.name}: no email address" if not payer.contact.email else f"{payer.contact.name}: asked not to be emailed")
    db.flush()
    db.add(AuditLog(actor_email=actor, actor_role="user", action="season.run", target_type="period", target_id=str(period.id), details=report.as_dict()))
    return report


# --------------------------------------------------------------------------- renewal notices


@dataclass
class NoticeCandidate:
    member: Member
    contact: Contact
    mtype_name: str
    fee_minor: int
    last_notice: date | None

    @property
    def can_email(self) -> bool:
        return bool(self.contact.email) and not self.contact.suppressed


def _notice_type(year: int) -> str:
    return f"renewal_notice_{year}"


def notice_candidates(db: Session, year: int) -> list[NoticeCandidate]:
    """Members due to renew into the season who have not yet been invoiced for it."""
    period = membership.create_period(db, year)
    sent: dict[int, datetime] = {}
    for related_id, created in db.execute(select(OutboundEmail.related_id, OutboundEmail.created_at).where(OutboundEmail.related_type == _notice_type(year), OutboundEmail.status.in_(["sent", "simulated"]))):
        if related_id is not None and (related_id not in sent or created > sent[related_id]):
            sent[related_id] = created
    out = []
    for row in membership.preview(db, period):
        if (row.renewal is not None and (row.renewal.state in {"declined", "confirmed"})) or row.mtype is None or row.fee_minor is None:
            continue
        contact = db.scalar(select(Contact).where(Contact.member_id == row.member.id))
        if contact is None:
            continue
        last = sent.get(contact.id)
        out.append(NoticeCandidate(row.member, contact, row.mtype.name, row.fee_minor, last.date() if last else None))
    return out


def send_notices(db: Session, year: int, actor: str, member_ids: list[int] | None = None, today: date | None = None) -> dict[str, list[str]]:
    today = today or date.today()
    result: dict[str, list[str]] = {"sent": [], "skipped": []}
    settings = finance.all_settings(db)
    club = settings["club_name"]
    base = os.getenv("CLUB_BASE_URL", "").rstrip("/")
    for candidate in notice_candidates(db, year):
        if member_ids is not None and candidate.member.id not in member_ids:
            continue
        name = candidate.contact.name
        if not candidate.can_email:
            result["skipped"].append(f"{name}: no email address")
            continue
        if candidate.last_notice and (today - candidate.last_notice).days < NOTICE_GAP_DAYS:
            result["skipped"].append(f"{name}: told on {candidate.last_notice.strftime('%d %b')}")
            continue
        first = name.split()[0] if name else "member"
        record = mailer.send_email(
            db,
            to=candidate.contact.email or "",
            subject=f"Time to renew your {club} membership for {year}",
            body=(
                f"Hello {first},\n\nYour {candidate.mtype_name} membership of {club} runs to the end of {year - 1}. "
                f"To carry on in {year} the fee is £{candidate.fee_minor / 100:,.2f}.\n\n"
                f"You can renew online: sign in at {base}/login with this email address and choose Renew. "
                f"If you would rather we sent you the invoice, just reply and say so. If you do not wish to renew, please let us know so we do not chase you.\n\n"
                f"{settings['payment_instructions']}\n\n{club}"
            ).replace("\n\n\n\n", "\n\n"),
            related_type=_notice_type(year),
            related_id=candidate.contact.id,
            actor=actor,
            reply_to=settings["reply_to"] or None,
        )
        (result["skipped"] if record.status == "failed" else result["sent"]).append(name if record.status != "failed" else f"{name}: the email failed")
    return result


# --------------------------------------------------------------------------- lapsing


@dataclass
class LapseCandidate:
    member: Member
    contact: Contact | None
    renewal: MembershipRenewal
    invoice: Invoice
    days_overdue: int
    owed: int
    spaces: list[StorageAllocation]


def lapse_candidates(db: Session, year: int, today: date | None = None, after_days: int | None = None) -> list[LapseCandidate]:
    """Renewals that were invoiced and are still unpaid well past their due date."""
    today = today or date.today()
    after = after_days if after_days is not None else _int_setting(db, "lapse_after_days", 60)
    period = membership.create_period(db, year)
    out = []
    for renewal in db.scalars(select(MembershipRenewal).where(MembershipRenewal.period_id == period.id, MembershipRenewal.state == "confirmed")):
        invoice = _renewal_billed(db, renewal.id)
        if invoice is None or invoice.status not in finance.OPEN or invoice.due_date is None:
            continue
        days = (today - invoice.due_date).days
        if days <= after:
            continue
        member = db.get(Member, renewal.member_id)
        if member is None:
            continue
        contact = db.scalar(select(Contact).where(Contact.member_id == member.id))
        spaces = []
        if contact:
            spaces = [a for a in db.scalars(select(StorageAllocation).where(StorageAllocation.status == "active")) if (b := db.get(Boat, a.boat_id)) is not None and (boats.owner_contact(db, b) or contact).id == contact.id]
        out.append(LapseCandidate(member, contact, renewal, invoice, days, finance.outstanding(db, invoice), spaces))
    return sorted(out, key=lambda c: -c.days_overdue)


def lapse(db: Session, year: int, member_ids: list[int], actor: str, release_spaces: bool = False, today: date | None = None) -> dict[str, int]:
    """Lapse the chosen members' memberships for the season. Their unpaid invoices stay as debts; spaces are released only if asked."""
    today = today or date.today()
    period = membership.create_period(db, year)
    lapsed = released = 0
    for candidate in lapse_candidates(db, year, today=today, after_days=-10_000):
        if candidate.member.id not in member_ids:
            continue
        row = db.scalar(select(Membership).where(Membership.member_id == candidate.member.id, Membership.starts_on == period.starts_on))
        if row is not None:
            row.status = "lapsed"
        candidate.member.active, candidate.member.renewal_status = False, "lapsed"
        candidate.renewal.state = "lapsed"
        lapsed += 1
        if release_spaces:
            for allocation in candidate.spaces:
                allocation.status, allocation.end_date = "ended", min(allocation.end_date, today)
                allocation.ended_reason = f"Membership lapsed {today.strftime('%d %b %Y')}"
                released += 1
        db.add(AuditLog(actor_email=actor, actor_role="user", action="membership.lapse", target_type="member", target_id=str(candidate.member.id), details={"period": period.name, "released_spaces": release_spaces}))
    db.flush()
    return {"lapsed": lapsed, "released": released}


def _int_setting(db: Session, key: str, default: int) -> int:
    try:
        return int(finance.get_setting(db, key))
    except ValueError:
        return default


# --------------------------------------------------------------------------- how far along the season is


def progress(db: Session, year: int) -> dict[str, int]:
    """The numbers the season page opens with."""
    period = membership.create_period(db, year)
    rows = membership.preview(db, period)
    plans = plan(db, year)
    invoiced = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.source == "season", Invoice.tags.like(f"%season {year}%"), Invoice.status.not_in(["void", "cancelled"]))) or 0
    paid = db.scalar(select(func.count()).select_from(Invoice).where(Invoice.source == "season", Invoice.tags.like(f"%season {year}%"), Invoice.status == "paid")) or 0
    return {
        "due": len([r for r in rows if r.renewal is None or r.renewal.state != "declined"]),
        "declined": len([r for r in rows if r.renewal is not None and r.renewal.state == "declined"]),
        "to_invoice_payers": len([p for p in plans if p.items]),
        "to_invoice_total": sum(p.total for p in plans),
        "problems": sum(len(p.problems) for p in plans),
        "invoiced": invoiced,
        "paid": paid,
        "notice_waiting": len([c for c in notice_candidates(db, year) if c.last_notice is None]),
        "lapse_candidates": len(lapse_candidates(db, year)),
    }
