"""The boat park through the year: who is keeping their space, rolling spaces into the next season, and the waiting list."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import boats
from app.models import Boat, Contact, SpaceRequest, StorageAllocation, StorageResource

LEAVING = "Leaving after"


# --------------------------------------------------------------------------- keeping a space


@dataclass
class SeasonRow:
    allocation: StorageAllocation
    boat: Boat
    resource: StorageResource
    owner: Contact | None
    state: str  # keeping | leaving | no reply
    billed: bool


def _leaving(allocation: StorageAllocation) -> bool:
    return (allocation.ended_reason or "").startswith(LEAVING)


def season_rows(db: Session, year: int) -> list[SeasonRow]:
    """Every space held through the end of ``year``, and whether its owner has said they are keeping it."""
    rows = []
    stmt = select(StorageAllocation).where(StorageAllocation.status == "active", StorageAllocation.end_date >= date(year, 12, 31)).order_by(StorageAllocation.id)
    for allocation in db.scalars(stmt):
        boat, resource = db.get(Boat, allocation.boat_id), db.get(StorageResource, allocation.resource_id)
        if boat is None or resource is None:
            continue
        state = "leaving" if _leaving(allocation) else "keeping" if allocation.confirmed_year == year + 1 else "no reply"
        rows.append(SeasonRow(allocation, boat, resource, boats.owner_contact(db, boat), state, False))
    return sorted(rows, key=lambda r: (r.resource.resource_group or "", r.resource.name))


def keep(db: Session, allocation: StorageAllocation, next_year: int) -> None:
    allocation.confirmed_year = next_year
    if _leaving(allocation):
        allocation.ended_reason = None
    db.flush()


def leave(db: Session, allocation: StorageAllocation, next_year: int) -> None:
    """The owner is giving up the space after the current season: it stays theirs until it ends, then returns to the pool."""
    allocation.ended_reason = f"{LEAVING} {next_year - 1}"
    allocation.confirmed_year = None
    db.flush()


def roll_over(db: Session, from_year: int) -> dict[str, int]:
    """Carry every space that ends with ``from_year`` into the next season. Spaces whose owner is leaving are left to end.

    Safe to run twice: a space already extended no longer ends in ``from_year``.
    """
    end = date(from_year, 12, 31)
    extended = leaving = 0
    for allocation in db.scalars(select(StorageAllocation).where(StorageAllocation.status == "active", StorageAllocation.end_date == end)):
        if _leaving(allocation):
            leaving += 1
            continue
        allocation.end_date = date(from_year + 1, 12, 31)
        allocation.confirmed_year = None  # next autumn they are asked again
        extended += 1
    db.flush()
    return {"extended": extended, "leaving": leaving}


# --------------------------------------------------------------------------- how full the park is


@dataclass
class GroupOccupancy:
    resource_type: str
    group: str
    capacity: int
    used: int
    free_names: list[str] = field(default_factory=list)

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.used)


def occupancy_by_group(db: Session, on: date | None = None) -> list[GroupOccupancy]:
    on = on or date.today()
    used = boats.occupancy(db, on)
    groups: dict[tuple[str, str], GroupOccupancy] = {}
    for resource in db.scalars(select(StorageResource).where(StorageResource.active.is_not(False)).order_by(StorageResource.resource_group, StorageResource.name)):
        key = (resource.resource_type, resource.resource_group or "")
        entry = groups.setdefault(key, GroupOccupancy(resource.resource_type, resource.resource_group or "", 0, 0))
        capacity = resource.capacity or 1
        taken = used.get(resource.id, 0)
        entry.capacity += capacity
        entry.used += min(taken, capacity)
        if taken < capacity:
            entry.free_names.append(resource.name)
    return list(groups.values())


# --------------------------------------------------------------------------- the waiting list


def join_waiting_list(db: Session, contact: Contact, *, boat: Boat | None = None, resource_type: str = "berth", group: str = "", note: str = "") -> SpaceRequest:
    existing = db.scalar(select(SpaceRequest).where(SpaceRequest.contact_id == contact.id, SpaceRequest.status.in_(["waiting", "offered"]), SpaceRequest.resource_type == resource_type))
    if existing is not None:
        raise boats.BoatError(f"{contact.name} is already on the waiting list")
    request = SpaceRequest(contact_id=contact.id, boat_id=boat.id if boat else None, resource_type=resource_type, resource_group=group.strip() or None, note=note.strip()[:255] or None)
    db.add(request)
    db.flush()
    return request


def waiting_list(db: Session) -> list[tuple[SpaceRequest, Contact, Boat | None, int]]:
    """Waiting people in the order they asked, with their place in the queue."""
    rows = db.scalars(select(SpaceRequest).where(SpaceRequest.status.in_(["waiting", "offered"])).order_by(SpaceRequest.created_at, SpaceRequest.id)).all()
    out = []
    for place, request in enumerate(rows, start=1):
        contact = db.get(Contact, request.contact_id)
        if contact is not None:
            out.append((request, contact, db.get(Boat, request.boat_id) if request.boat_id else None, place))
    return out


def withdraw(db: Session, request: SpaceRequest) -> None:
    if request.status not in {"waiting", "offered"}:
        raise boats.BoatError("This request is already closed")
    request.status, request.decided_at = "withdrawn", datetime.now(UTC)
    db.flush()


def offer(db: Session, request: SpaceRequest, resource: StorageResource, boat: Boat, start: date, end: date) -> StorageAllocation:
    """Give the waiting person a space. The request is closed and the boat is allocated."""
    if request.status not in {"waiting", "offered"}:
        raise boats.BoatError("This request is already closed")
    if boat.owner_contact_id != request.contact_id and not (boat.owner_member_id and db.scalar(select(Contact.id).where(Contact.id == request.contact_id, Contact.member_id == boat.owner_member_id))):
        raise boats.BoatError("That boat does not belong to the person waiting")
    allocation = boats.allocate(db, boat, resource, start, end)
    request.status, request.decided_at, request.boat_id = "allocated", datetime.now(UTC), boat.id
    db.flush()
    return allocation


def next_in_queue(db: Session, resource_type: str = "berth", group: str = "") -> SpaceRequest | None:
    stmt = select(SpaceRequest).where(SpaceRequest.status == "waiting", SpaceRequest.resource_type == resource_type)
    if group:
        stmt = stmt.where(func.coalesce(SpaceRequest.resource_group, "") .in_(["", group]))
    return db.scalars(stmt.order_by(SpaceRequest.created_at, SpaceRequest.id)).first()
