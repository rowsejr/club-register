"""What each volunteer role needs to know this week, in a short email to whoever holds that hat.

Each role-holder hears about their own area, so the treasurer hears about money, the membership
secretary about applications and renewals, the harbourmaster about boats and spaces, and the training officer about courses.
A digest with nothing in it is not sent: silence means nothing needs doing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import applications, berths, boats, finance, mailer, people, season, training
from app.models import BankEntry, Invoice, Member, PaymentTransaction, UserRole


@dataclass
class Line:
    text: str
    count: int
    link: str = ""
    urgent: bool = False


@dataclass
class Digest:
    hat: str
    title: str
    lines: list[Line] = field(default_factory=list)

    @property
    def todo(self) -> list[Line]:
        return [line for line in self.lines if line.count]

    @property
    def empty(self) -> bool:
        return not self.todo


def _base() -> str:
    return os.getenv("CLUB_BASE_URL", "").rstrip("/")


def treasurer(db: Session, today: date) -> Digest:
    unpaid = Invoice.status.in_(finance.OPEN)
    overdue = db.scalars(select(Invoice).where(unpaid, Invoice.due_date < today, func.coalesce(Invoice.kind, "invoice") == "invoice")).all()
    owed = sum(finance.outstanding(db, i) for i in overdue)
    late30 = [i for i in overdue if i.due_date and i.due_date < today - timedelta(days=30)]
    received = db.scalar(select(func.coalesce(func.sum(PaymentTransaction.amount_minor), 0)).where(PaymentTransaction.status == "received", PaymentTransaction.payment_method != "credit_note", PaymentTransaction.received_at >= datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=UTC))) or 0
    digest = Digest("treasurer", "Treasurer")
    digest.lines = [
        Line("bank lines waiting to be matched or categorised", db.scalar(select(func.count()).select_from(BankEntry).where(BankEntry.status == "unmatched")) or 0, "/app/finance/bank", True),
        Line(f"overdue invoices, £{owed / 100:,.2f} in all", len(overdue), "/app/finance?filter=overdue", True),
        Line("invoices more than 30 days late", len(late30), "/app/finance?filter=late30", True),
        Line("draft invoices waiting to be issued", db.scalar(select(func.count()).select_from(Invoice).where(Invoice.status == "draft")) or 0, "/app/finance?filter=draft"),
    ]
    digest.lines.append(Line(f"money received in the last 7 days: £{received / 100:,.2f}", 0))  # informational: never makes a digest worth sending by itself
    return digest


def secretary(db: Session, today: date) -> Digest:
    queues = applications.queue_counts(db)
    soon = db.scalars(select(Member).where(Member.active.is_(True), Member.expiry_date.is_not(None), Member.expiry_date >= today, Member.expiry_date <= today + timedelta(days=60))).all()
    year = today.year + 1 if today.month >= 7 else today.year
    digest = Digest("secretary", "Membership secretary")
    digest.lines = [
        Line("applications waiting for a decision", queues["applications"], "/app/applications", True),
        Line("enquiries not yet answered", queues["enquiries"], "/app/enquiries", True),
        Line("approved applicants who have not paid yet", queues["awaiting_payment"], "/app/applications?tab=approved"),
        Line("memberships ending in the next 60 days", len(soon), "/app/members?status=active"),
        Line(f"renewals unpaid long enough to lapse ({year})", len(season.lapse_candidates(db, year, today)), f"/app/season?year={year}"),
        Line("juniors without valid consent", len(next(i for i in people.data_issues(db, today) if i.key == "junior_consent").contacts), "/app/people/issues#junior_consent"),
    ]
    return digest


def bosun(db: Session, today: date) -> Digest:
    free = sum(g.free for g in berths.occupancy_by_group(db, today))
    digest = Digest("bosun", "Harbourmaster")
    digest.lines = [
        Line("boats with expired, expiring or unknown insurance", len(boats.insurance_problems(db, today)), "/app/boats/reports", True),
        Line("boats with no space", len(boats.boats_without_space(db, today)), "/app/boats?filter=no_space"),
        Line("spaces ending in the next 60 days", len(boats.allocations_ending(db, 60, today)), "/app/boats/reports"),
        Line("people on the waiting list", len(berths.waiting_list(db)), "/app/storage/waiting"),
    ]
    if free and berths.waiting_list(db):
        digest.lines.append(Line(f"free spaces while people are waiting ({free} free)", free, "/app/storage/waiting", True))
    return digest


def training_officer(db: Session, today: date) -> Digest:
    from app.models import TrainingCourse

    upcoming = db.scalars(select(TrainingCourse).where(TrainingCourse.status == "open", TrainingCourse.starts_at >= datetime.combine(today, datetime.min.time(), tzinfo=UTC), TrainingCourse.starts_at <= datetime.combine(today + timedelta(days=30), datetime.min.time(), tzinfo=UTC))).all()
    digest = Digest("training", "Training officer")
    digest.lines = [
        Line("courses starting in the next 30 days", len(upcoming), "/app/training"),
        Line("qualifications expiring or expired (next 60 days)", len(training.expiring_qualifications(db, 60, today)), "/app/training/qualifications", True),
        Line("juniors without valid consent", len(next(i for i in people.data_issues(db, today) if i.key == "junior_consent").contacts), "/app/people/issues#junior_consent"),
    ]
    return digest


def social(db: Session, today: date) -> Digest:
    from app.models import Event, EventTicket

    soon = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    coming = db.scalars(select(Event).where(Event.start_at >= soon, Event.start_at <= soon + timedelta(days=30)).order_by(Event.start_at)).all()
    unpublished = [e for e in coming if not e.published]
    holds = db.scalar(select(func.count()).select_from(EventTicket).where(EventTicket.status == "held")) or 0
    digest = Digest("social", "Social secretary")
    digest.lines = [
        Line("events in the next 30 days", len(coming), "/app/events"),
        Line("of them not published yet", len(unpublished), "/app/events", True),
        Line("ticket places held but not yet paid for", holds, "/app/events"),
    ]
    return digest


BUILDERS = {"treasurer": treasurer, "social": social, "secretary": secretary, "bosun": bosun, "training": training_officer}


def render(digest: Digest, club: str, first: str = "there") -> tuple[str, str]:
    subject = f"{club}: your {digest.title.lower()} jobs this week"
    parts = [f"Hello {first},", "", f"Here is what needs your attention as {digest.title.lower()}:", ""]
    base = _base()
    for line in digest.lines:
        if line.count or not line.link:
            mark = "!" if line.urgent and line.count else "-"
            parts.append(f"{mark} {line.count} {line.text}" if line.count else f"- {line.text}")
            if line.count and line.link:
                parts.append(f"    {base}{line.link}")
    parts += ["", club]
    return subject, "\n".join(parts)


def recipients(db: Session, hat: str) -> list[str]:
    """Everyone who holds the hat, by email address."""
    out = []
    for row in db.scalars(select(UserRole).order_by(UserRole.email)):
        if hat in [h.strip() for h in (row.extra_roles or "").split(",")]:
            out.append(row.email)
    return out


def send_all(db: Session, today: date, dry_run: bool = False, actor: str = "system") -> dict[str, object]:
    """Email each hat's digest to its holders. Returns who was told, and which digests were empty."""
    club = finance.get_setting(db, "club_name")
    told: dict[str, list[str]] = {}
    empty: list[str] = []
    for hat, builder in BUILDERS.items():
        holders = recipients(db, hat)
        if not holders:
            continue
        digest = builder(db, today)
        if digest.empty:
            empty.append(hat)
            continue
        for email in holders:
            told.setdefault(hat, []).append(email)
            if not dry_run:
                member = db.scalar(select(Member).where(func.lower(Member.email) == email.lower()))
                subject, body = render(digest, club, (member.name.split()[0] if member and member.name else "there"))
                mailer.send_email(db, to=email, subject=subject, body=body, related_type="digest", actor=actor)
    return {"told": told, "empty": empty}

