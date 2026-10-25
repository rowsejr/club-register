"""Boat register, storage spaces, allocations, storage tariffs and the annual storage billing run.

Storage fees ride on the same invoice machinery as everything else: the billing run builds draft or issued
invoices through ``finance`` (one per payer, one line per allocation), and a line's ``source_id`` makes the run
idempotent, so running it twice bills nothing twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance, mailer
from app.models import (
    Boat,
    BoatCrewLink,
    BoatDesign,
    Contact,
    Invoice,
    InvoiceLine,
    OutboundEmail,
    StorageAllocation,
    StorageResource,
    StorageTariff,
)

INSURANCE_WARN_DAYS = 60
REMINDER_GAP_DAYS = 14
DEFAULT_NOMINAL = "0102"


class BoatError(ValueError):
    """A rule violation, safe to show to the volunteer or member."""


# --------------------------------------------------------------------------- the register


def owner_contact(db: Session, boat: Boat) -> Contact | None:
    if boat.owner_contact_id:
        return db.get(Contact, boat.owner_contact_id)
    if boat.owner_member_id:
        return db.scalar(select(Contact).where(Contact.member_id == boat.owner_member_id))
    return None


def co_owners(db: Session, boat: Boat) -> list[tuple[BoatCrewLink, Contact]]:
    rows = db.execute(select(BoatCrewLink, Contact).join(Contact, Contact.id == BoatCrewLink.contact_id).where(BoatCrewLink.boat_id == boat.id).order_by(BoatCrewLink.id)).all()
    return [(link, contact) for link, contact in rows]


def insurance_state(boat: Boat, today: date | None = None) -> str:
    today = today or date.today()
    if boat.insurance_expiry is None:
        return "unknown" if not boat.insurance else "noted"
    if boat.insurance_expiry < today:
        return "expired"
    if boat.insurance_expiry <= today + timedelta(days=INSURANCE_WARN_DAYS):
        return "expiring"
    return "ok"


def _design(db: Session, name: str | None) -> BoatDesign | None:
    name = " ".join((name or "").split())
    if not name:
        return None
    design = db.scalar(select(BoatDesign).where(func.lower(BoatDesign.name) == name.lower()))
    if design is None:
        design = BoatDesign(name=name)
        db.add(design)
        db.flush()
    return design


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def save_boat(
    db: Session,
    boat: Boat | None,
    *,
    name: str,
    owner_contact_id: int | None,
    design_name: str | None = None,
    sail_number: str = "",
    boat_class: str = "",
    colour: str | None = None,
    portsmouth_number: int | None = None,
    insurance: str | None = None,
    insurance_expiry: date | None = None,
    notes: str | None = None,
    is_club_boat: bool = False,
    actor: str = "system",
) -> Boat:
    """Create or update a boat. The owner is a contact (they need not be a member)."""
    name = " ".join(name.split())
    if not name and not sail_number.strip():
        raise BoatError("A boat needs a name or a sail number")
    owner = db.get(Contact, owner_contact_id) if owner_contact_id else None
    if owner is None and not is_club_boat:
        raise BoatError("Choose the owner")
    created = boat is None
    if boat is None:
        boat = Boat(name=name or sail_number)
        db.add(boat)
    design = _design(db, design_name)
    boat.name = name or sail_number.strip()
    boat.sail_number = sail_number.strip()
    boat.boat_class = boat_class.strip()
    boat.colour = _clean(colour)
    boat.design_id = design.id if design else None
    boat.portsmouth_number = portsmouth_number
    boat.insurance, boat.insurance_expiry = _clean(insurance), insurance_expiry
    boat.notes = _clean(notes)
    boat.is_club_boat = is_club_boat
    boat.owner_contact_id = owner.id if owner else None
    boat.owner_member_id = owner.member_id if owner else None
    if created:
        boat.status, boat.source = "active", "office"
    if design and portsmouth_number and not design.portsmouth_rating:
        design.portsmouth_rating = portsmouth_number
    db.flush()
    return boat


def add_co_owner(db: Session, boat: Boat, contact_id: int, role: str = "co-owner") -> None:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise BoatError("Contact not found")
    if boat.owner_contact_id == contact_id:
        raise BoatError("That person is already the owner")
    if db.scalar(select(BoatCrewLink.id).where(BoatCrewLink.boat_id == boat.id, BoatCrewLink.contact_id == contact_id)):
        raise BoatError("They are already linked to this boat")
    db.add(BoatCrewLink(boat_id=boat.id, contact_id=contact_id, role=role if role in {"co-owner", "crew"} else "crew"))
    db.flush()


def remove_co_owner(db: Session, boat: Boat, link_id: int) -> None:
    link = db.get(BoatCrewLink, link_id)
    if link is None or link.boat_id != boat.id:
        raise BoatError("Not found")
    db.delete(link)
    db.flush()


def archive_boat(db: Session, boat: Boat, reason: str, today: date | None = None) -> None:
    today = today or date.today()
    if not reason.strip():
        raise BoatError("Say why (sold, scrapped, left the club)")
    for allocation in db.scalars(select(StorageAllocation).where(StorageAllocation.boat_id == boat.id, StorageAllocation.status == "active")):
        allocation.status, allocation.ended_reason = "ended", reason.strip()
        if allocation.end_date > today:
            allocation.end_date = max(allocation.start_date, today)
    boat.status = "archived"
    boat.notes = f"{boat.notes + chr(10) if boat.notes else ''}Archived: {reason.strip()}"
    db.flush()


# --------------------------------------------------------------------------- spaces and allocations


def create_resource(db: Session, name: str, resource_type: str = "berth", group: str | None = None, capacity: int = 1) -> StorageResource:
    name = " ".join(name.split())
    if not name:
        raise BoatError("A space needs a name")
    if capacity < 1:
        raise BoatError("Capacity must be at least 1")
    if db.scalar(select(StorageResource.id).where(func.lower(StorageResource.name) == name.lower())):
        raise BoatError(f"There is already a space called {name}")
    resource = StorageResource(name=name, resource_type=resource_type.strip() or "berth", resource_group=_clean(group), capacity=capacity, active=True)
    db.add(resource)
    db.flush()
    return resource


def _overlapping(db: Session, *, resource_id: int | None = None, boat_id: int | None = None, start: date, end: date, exclude: int | None = None) -> list[StorageAllocation]:
    stmt = select(StorageAllocation).where(StorageAllocation.status == "active", StorageAllocation.start_date <= end, StorageAllocation.end_date >= start)
    if resource_id is not None:
        stmt = stmt.where(StorageAllocation.resource_id == resource_id)
    if boat_id is not None:
        stmt = stmt.where(StorageAllocation.boat_id == boat_id)
    if exclude is not None:
        stmt = stmt.where(StorageAllocation.id != exclude)
    return list(db.scalars(stmt))


def allocate(db: Session, boat: Boat, resource: StorageResource, start: date, end: date, *, fee_minor: int | None = None) -> StorageAllocation:
    if boat.status != "active":
        raise BoatError(f"A {boat.status} boat cannot be given a space")
    if resource.active is False:
        raise BoatError("That space is not in use")
    if end < start:
        raise BoatError("The end date is before the start date")
    clash = _overlapping(db, boat_id=boat.id, start=start, end=end)
    if clash:
        other = db.get(StorageResource, clash[0].resource_id)
        raise BoatError(f"{boat.name} already has {other.name if other else 'a space'} for those dates: move it instead")
    if len(_overlapping(db, resource_id=resource.id, start=start, end=end)) >= (resource.capacity or 1):
        raise BoatError(f"{resource.name} is full for those dates")
    if fee_minor is not None and fee_minor < 0:
        raise BoatError("A fee cannot be negative")
    allocation = StorageAllocation(boat_id=boat.id, resource_id=resource.id, start_date=start, end_date=end, seasonal=True, fee_minor=fee_minor, status="active")
    db.add(allocation)
    db.flush()
    return allocation


def end_allocation(db: Session, allocation: StorageAllocation, on: date, reason: str) -> None:
    if allocation.status != "active":
        raise BoatError("That allocation has already ended")
    allocation.end_date = max(allocation.start_date, min(allocation.end_date, on))
    allocation.status, allocation.ended_reason = "ended", reason.strip() or "ended"
    db.flush()


def move_boat(db: Session, boat: Boat, new_resource: StorageResource, on: date, reason: str = "moved") -> StorageAllocation:
    """Close the boat's current space the day before ``on`` and open the new one from ``on`` to the same end date."""
    current = db.scalars(select(StorageAllocation).where(StorageAllocation.boat_id == boat.id, StorageAllocation.status == "active", StorageAllocation.end_date >= on).order_by(StorageAllocation.start_date)).first()
    if current is None:
        raise BoatError(f"{boat.name} has no space to move from")
    if current.resource_id == new_resource.id:
        raise BoatError("It is already in that space")
    end = current.end_date
    fee = current.fee_minor
    end_allocation(db, current, on - timedelta(days=1), reason)
    return allocate(db, boat, new_resource, on, max(end, on), fee_minor=fee)


def occupancy(db: Session, on: date) -> dict[int, int]:
    rows = db.execute(select(StorageAllocation.resource_id, func.count()).where(StorageAllocation.status == "active", StorageAllocation.start_date <= on, StorageAllocation.end_date >= on).group_by(StorageAllocation.resource_id)).all()
    return {rid: n for rid, n in rows}


def current_allocation(db: Session, boat_id: int, on: date | None = None) -> StorageAllocation | None:
    on = on or date.today()
    return db.scalars(select(StorageAllocation).where(StorageAllocation.boat_id == boat_id, StorageAllocation.status == "active", StorageAllocation.start_date <= on, StorageAllocation.end_date >= on)).first()


# --------------------------------------------------------------------------- tariffs and billing


def set_tariff(db: Session, resource_type: str, group: str, year: int, fee_minor: int, nominal_code: str | None = None, prorata: bool | None = None) -> StorageTariff:
    if fee_minor < 0:
        raise BoatError("A fee cannot be negative")
    group = (group or "").strip()
    tariff = db.scalar(select(StorageTariff).where(StorageTariff.resource_type == resource_type, StorageTariff.resource_group == group, StorageTariff.season_year == year))
    if tariff is None:
        tariff = StorageTariff(resource_type=resource_type, resource_group=group, season_year=year, fee_minor=fee_minor)
        db.add(tariff)
    tariff.fee_minor = fee_minor
    tariff.nominal_code = nominal_code or tariff.nominal_code or DEFAULT_NOMINAL
    if prorata is not None:
        tariff.prorata = prorata
    db.flush()
    return tariff


def round_fee(minor: int, step: int = 50) -> int:
    """Round a price to the nearest 50p (or another step) so a percentage rise gives a sensible figure."""
    return int(round(minor / step)) * step


def copy_tariffs(db: Session, from_year: int, to_year: int, rise_percent: float = 0.0) -> int:
    """Carry last season's storage fees into a new season, optionally raised by a percentage. Fees already set for the new year are kept."""
    if to_year <= from_year:
        raise BoatError("Copy forward into a later year")
    copied = 0
    for old in db.scalars(select(StorageTariff).where(StorageTariff.season_year == from_year)).all():
        exists = db.scalar(select(StorageTariff.id).where(StorageTariff.resource_type == old.resource_type, StorageTariff.resource_group == old.resource_group, StorageTariff.season_year == to_year))
        if exists is not None:
            continue
        db.add(
            StorageTariff(
                resource_type=old.resource_type, resource_group=old.resource_group, season_year=to_year, fee_minor=round_fee(round(old.fee_minor * (1 + rise_percent / 100))) if rise_percent else old.fee_minor,
                nominal_code=old.nominal_code, prorata=old.prorata,
            )
        )
        copied += 1
    db.flush()
    return copied


def tariff_for(db: Session, resource: StorageResource, year: int) -> StorageTariff | None:
    for group in ((resource.resource_group or ""), ""):
        found = db.scalar(select(StorageTariff).where(StorageTariff.resource_type == resource.resource_type, StorageTariff.resource_group == group, StorageTariff.season_year == year))
        if found is not None:
            return found
    return None


@dataclass
class BillingRow:
    allocation: StorageAllocation
    boat: Boat
    resource: StorageResource
    payer: Contact | None
    fee_minor: int | None
    nominal: str
    note: str = ""
    billed: bool = False
    detail: str = ""  # how the fee was worked out when it is not the full tariff, e.g. "pro rata 7/12"


@dataclass
class BillingReport:
    invoices: list[int] = field(default_factory=list)
    lines: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"invoices": self.invoices, "lines": self.lines, "skipped": self.skipped}


def _line_key(allocation: StorageAllocation, year: int) -> str:
    return f"{allocation.id}:{year}"


def _already_billed(db: Session, allocation: StorageAllocation, year: int) -> bool:
    return (
        db.scalar(
            select(InvoiceLine.id).join(Invoice, Invoice.id == InvoiceLine.invoice_id).where(
                InvoiceLine.source_type == "storage", InvoiceLine.source_id == _line_key(allocation, year), Invoice.status.not_in(["void", "cancelled"])
            )
        )
        is not None
    )


def prorata_fee(full_minor: int, start: date, year: int) -> tuple[int, str]:
    """A space that starts part way through the season is charged by the month; the month it starts in counts in full."""
    if start <= date(year, 1, 1) or start.year != year:
        return full_minor, ""
    months = 12 - (start.month - 1)
    return round(full_minor * months / 12), f"pro rata {months}/12"


def billing_preview(db: Session, year: int) -> list[BillingRow]:
    start, end = date(year, 1, 1), date(year, 12, 31)
    rows = []
    stmt = select(StorageAllocation).where(StorageAllocation.status == "active", StorageAllocation.start_date <= end, StorageAllocation.end_date >= start).order_by(StorageAllocation.id)
    for allocation in db.scalars(stmt):
        boat, resource = db.get(Boat, allocation.boat_id), db.get(StorageResource, allocation.resource_id)
        if boat is None or resource is None:
            continue
        tariff = tariff_for(db, resource, year)
        fee = allocation.fee_minor if allocation.fee_minor is not None else (tariff.fee_minor if tariff else None)
        payer = owner_contact(db, boat)
        note, detail = "", ""
        if fee is not None and allocation.fee_minor is None and tariff is not None and tariff.prorata:
            fee, detail = prorata_fee(fee, allocation.start_date, year)
        if payer is None:
            note = "No owner to bill"
        elif fee is None:
            note = f"No {year} tariff for {resource.resource_type}" + (f" in {resource.resource_group}" if resource.resource_group else "")
        rows.append(BillingRow(allocation, boat, resource, payer, fee, (tariff.nominal_code if tariff else None) or DEFAULT_NOMINAL, note, _already_billed(db, allocation, year), detail))
    return rows


def run_storage_billing(db: Session, year: int, actor: str, allocation_ids: list[int] | None = None, issue: bool = True) -> BillingReport:
    """One invoice per payer with a line per allocation. Safe to run twice: already-billed allocations are skipped."""
    report = BillingReport()
    grouped: dict[int, list[BillingRow]] = {}
    for row in billing_preview(db, year):
        if allocation_ids is not None and row.allocation.id not in allocation_ids:
            continue
        if row.billed:
            continue
        if row.note or row.payer is None or row.fee_minor is None:
            report.skipped.append(f"{row.boat.name} ({row.resource.name}): {row.note or 'cannot be billed'}")
            continue
        if row.fee_minor <= 0:
            continue
        grouped.setdefault(row.payer.id, []).append(row)
    for payer_id, rows in grouped.items():
        savepoint = db.begin_nested()
        try:
            invoice = finance.create_invoice(
                db,
                contact_id=payer_id,
                lines=[
                    {
                        "description": f"{r.resource.name} {year}: {r.boat.name}" + (f" ({r.detail})" if r.detail else ""),
                        "amount_minor": r.fee_minor or 0,
                        "nominal_code": r.nominal,
                        "source_type": "storage",
                        "source_id": _line_key(r.allocation, year),
                    }
                    for r in rows
                ],
                actor=actor,
                notes=f"Storage {year}",
            )
            if issue:
                finance.issue(db, invoice, actor)
            savepoint.commit()
        except finance.FinanceError as exc:
            savepoint.rollback()
            report.skipped.append(f"{rows[0].payer.name if rows[0].payer else payer_id}: {exc}")
            continue
        report.invoices.append(invoice.id)
        report.lines += len(rows)
    return report


# --------------------------------------------------------------------------- reports and reminders


def boats_without_space(db: Session, on: date | None = None) -> list[Boat]:
    on = on or date.today()
    housed = select(StorageAllocation.boat_id).where(StorageAllocation.status == "active", StorageAllocation.start_date <= on, StorageAllocation.end_date >= on)
    return list(db.scalars(select(Boat).where(Boat.status == "active", Boat.id.not_in(housed), func.coalesce(Boat.is_club_boat, False).is_(False)).order_by(Boat.name)))


def allocations_ending(db: Session, within_days: int = 60, today: date | None = None) -> list[StorageAllocation]:
    today = today or date.today()
    return list(db.scalars(select(StorageAllocation).where(StorageAllocation.status == "active", StorageAllocation.end_date >= today, StorageAllocation.end_date <= today + timedelta(days=within_days)).order_by(StorageAllocation.end_date)))


def insurance_problems(db: Session, today: date | None = None) -> list[tuple[Boat, str]]:
    today = today or date.today()
    out = []
    for boat in db.scalars(select(Boat).where(Boat.status == "active").order_by(Boat.name)):
        state = insurance_state(boat, today)
        if state in {"expired", "expiring", "unknown"}:
            out.append((boat, state))
    return out


def send_insurance_reminders(db: Session, boat_ids: list[int], actor: str, today: date | None = None) -> dict[str, list[str]]:
    today = today or date.today()
    result: dict[str, list[str]] = {"sent": [], "skipped": []}
    cutoff = today - timedelta(days=REMINDER_GAP_DAYS)
    for boat_id in boat_ids:
        boat = db.get(Boat, boat_id)
        owner = owner_contact(db, boat) if boat else None
        if boat is None or owner is None or not owner.email or owner.suppressed:
            result["skipped"].append(f"#{boat_id}: no one to email")
            continue
        recent = db.scalar(select(func.max(OutboundEmail.created_at)).where(OutboundEmail.related_type == "insurance_reminder", OutboundEmail.related_id == boat.id))
        if recent is not None and (recent.date() if hasattr(recent, "date") else cutoff) > cutoff:
            result["skipped"].append(f"{boat.name}: reminded recently")
            continue
        state = insurance_state(boat, today)
        when = boat.insurance_expiry.strftime("%d %B %Y") if boat.insurance_expiry else "not recorded"
        club = finance.get_setting(db, "club_name")
        record = mailer.send_email(
            db,
            to=owner.email,
            subject=f"Insurance for {boat.name}",
            body=f"Hello {owner.name.split()[0]},\n\nOur records show the insurance for {boat.name} {'has expired' if state == 'expired' else 'runs out soon' if state == 'expiring' else 'is not recorded'} (expiry: {when}).\n\nPlease sign in at the club website and update it under My boats, or reply to this email.\n\n{club}",
            related_type="insurance_reminder",
            related_id=boat.id,
            actor=actor,
            reply_to=finance.reply_to(db, "bosun"),
        )
        (result["skipped"] if record.status == "failed" else result["sent"]).append(boat.name)
    return result
