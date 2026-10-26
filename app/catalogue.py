"""The things the club charges for, as one pick-list for the invoice builder.

Club items (a key deposit, a kayak rack, a donation) are kept here. Membership fees, storage tariffs and course prices
already live with their own screens, so the pick-list reads them from there and a price changed in one place is changed
everywhere: there is no second copy to forget.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeeItem, MembershipType, NominalCode, StorageTariff, TrainingCourse

# Charged by the club as extras; the treasurer sets the nominal code if it is blank.
STARTER_ITEMS = [
    ("Gate key deposit", "club", None, 1500, "each", "Refundable on return of the key"),
    ("Kayak / SUP storage", "storage", "0102", 3000, "per season", "Rack space for a kayak or paddle board"),
    ("Boat park fee", "storage", "0102", 0, "per boat per season", "Keeping a boat in the club's boat park"),
    ("Donation", "club", "0104", 0, "each", "A gift to the club"),
    ("Open meeting entry", "club", "0108", 0, "each", ""),
    ("Club boat fee", "club", "0106", 0, "each", ""),
    ("MYC merchandise", "club", "0107", 0, "each", ""),
]


@dataclass(frozen=True)
class Option:
    key: str
    group: str
    label: str
    nominal: str
    price_minor: int
    unit: str = "each"


class CatalogueError(ValueError):
    pass


def seed_starter_items(db: Session) -> int:
    if db.scalar(select(FeeItem.id).limit(1)) is not None:
        return 0
    codes = {c for (c,) in db.execute(select(NominalCode.code))}
    for name, category, nominal, price, unit, description in STARTER_ITEMS:
        db.add(FeeItem(name=name, category=category, nominal_code=nominal if nominal in codes or not codes else None, unit_price_minor=price, unit=unit, description=description or None))
    db.flush()
    return len(STARTER_ITEMS)


def save_item(db: Session, item_id: int | None, *, name: str, nominal_code: str, price_minor: int, unit: str = "each", category: str = "club", description: str = "", active: bool = True) -> FeeItem:
    name = " ".join(name.split())
    if not name:
        raise CatalogueError("An item needs a name")
    if price_minor < 0:
        raise CatalogueError("A price cannot be negative")
    if nominal_code and db.scalar(select(NominalCode.id).limit(1)) is not None and not db.scalar(select(NominalCode.id).where(NominalCode.code == nominal_code)):
        raise CatalogueError(f"Unknown nominal code {nominal_code}")
    clash = db.scalar(select(FeeItem).where(FeeItem.name == name))
    item = db.get(FeeItem, item_id) if item_id else None
    if item_id and item is None:
        raise CatalogueError("Item not found")
    if clash is not None and (item is None or clash.id != item.id):
        raise CatalogueError(f"There is already an item called {name}")
    if item is None:
        item = FeeItem(name=name)
        db.add(item)
    item.name, item.nominal_code, item.unit_price_minor = name, nominal_code or None, price_minor
    item.unit, item.category, item.description, item.active = (unit or "each").strip(), category, description.strip() or None, active
    db.flush()
    return item


def options(db: Session, year: int | None = None) -> list[Option]:
    """Everything that can be picked on an invoice, grouped."""
    year = year or datetime.now(UTC).year
    out: list[Option] = []
    for item in db.scalars(select(FeeItem).where(FeeItem.active.is_(True)).order_by(FeeItem.category, FeeItem.name)):
        out.append(Option(f"item:{item.id}", "Club items", item.name, item.nominal_code or "", item.unit_price_minor, item.unit or "each"))
    for mtype in db.scalars(select(MembershipType).where(MembershipType.active.is_not(False), MembershipType.fee_minor.is_not(None)).order_by(MembershipType.name)):
        out.append(Option(f"type:{mtype.id}", "Membership", f"{mtype.name} membership {year}", mtype.nominal_code or "0101", mtype.fee_minor or 0, "per year"))
    for tariff in db.scalars(select(StorageTariff).where(StorageTariff.season_year == year).order_by(StorageTariff.resource_type, StorageTariff.resource_group)):
        label = f"{tariff.resource_type.replace('_', ' ').title()} {tariff.resource_group}".strip()
        out.append(Option(f"tariff:{tariff.id}", "Storage", f"{label} fee {year}", tariff.nominal_code or "0102", tariff.fee_minor, "per season"))
    for course in db.scalars(select(TrainingCourse).where(TrainingCourse.status.in_(["open", "draft"]), TrainingCourse.price_minor > 0).order_by(TrainingCourse.starts_at)):
        out.append(Option(f"course:{course.id}", "Training", course.name, course.nominal_code or "0103", course.price_minor or 0, "per place"))
    return out


def as_json(items: list[Option]) -> list[dict]:
    return [{"key": o.key, "group": o.group, "label": o.label, "nominal": o.nominal, "price": f"{o.price_minor / 100:.2f}", "unit": o.unit} for o in items]
