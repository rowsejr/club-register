from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app import boats, finance
from app.db import SessionLocal
from app.models import (
    Boat,
    BoatCrewLink,
    Contact,
    Invoice,
    InvoiceLine,
    Member,
    OutboundEmail,
    StorageAllocation,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def _owner(db, name="Ann Sailor", email="ann@example.com", member=True) -> Contact:
    member_id = None
    if member:
        m = Member(name=name, email=email)
        db.add(m)
        db.flush()
        member_id = m.id
    contact = Contact(name=name, email=email, member_id=member_id)
    db.add(contact)
    db.flush()
    return contact


def _boat(db, owner, name="Windfall", **kw) -> Boat:
    return boats.save_boat(db, None, name=name, owner_contact_id=owner.id, design_name=kw.pop("design", "Mirror"), sail_number=kw.pop("sail", "7270"), **kw)


def test_save_boat_links_owner_design_and_validates(db) -> None:
    ann = _owner(db)
    boat = _boat(db, ann, portsmouth_number=1364, insurance="Policy 1", insurance_expiry=date(2027, 3, 31))
    assert boat.owner_contact_id == ann.id and boat.owner_member_id == ann.member_id and boat.status == "active" and boat.source == "office"
    assert boat.design_id and boat.portsmouth_number == 1364
    again = boats.save_boat(db, boat, name="Windfall II", owner_contact_id=ann.id, design_name="mirror", sail_number="7270")
    assert again.id == boat.id and again.name == "Windfall II" and db.scalar(select(func.count()).select_from(boats.BoatDesign)) == 1  # design reused, case-insensitively
    with pytest.raises(boats.BoatError, match="name or a sail"):
        boats.save_boat(db, None, name=" ", owner_contact_id=ann.id)
    with pytest.raises(boats.BoatError, match="Choose the owner"):
        boats.save_boat(db, None, name="Orphan", owner_contact_id=None)
    club = boats.save_boat(db, None, name="Club Topper", owner_contact_id=None, is_club_boat=True)
    assert club.owner_contact_id is None and club.is_club_boat


def test_contact_only_owners_are_fine(db) -> None:
    visitor = _owner(db, "Non Member", "nm@example.com", member=False)
    boat = _boat(db, visitor)
    assert boat.owner_member_id is None and boats.owner_contact(db, boat).id == visitor.id


def test_co_owners_and_archiving(db) -> None:
    ann, bob = _owner(db), _owner(db, "Bob Tiller", "bob@example.com")
    boat = _boat(db, ann)
    boats.add_co_owner(db, boat, bob.id)
    assert [(c.name, link.role) for link, c in boats.co_owners(db, boat)] == [("Bob Tiller", "co-owner")]
    with pytest.raises(boats.BoatError, match="already linked"):
        boats.add_co_owner(db, boat, bob.id)
    with pytest.raises(boats.BoatError, match="already the owner"):
        boats.add_co_owner(db, boat, ann.id)
    link = db.scalar(select(BoatCrewLink))
    boats.remove_co_owner(db, boat, link.id)
    assert not boats.co_owners(db, boat)
    resource = boats.create_resource(db, "Row A 1")
    allocation = boats.allocate(db, boat, resource, date(2026, 4, 1), date(2027, 3, 31))
    with pytest.raises(boats.BoatError, match="Say why"):
        boats.archive_boat(db, boat, " ")
    boats.archive_boat(db, boat, "Sold", today=date(2026, 10, 1))
    assert boat.status == "archived" and allocation.status == "ended" and allocation.end_date == date(2026, 10, 1)
    with pytest.raises(boats.BoatError, match="archived boat cannot"):
        boats.allocate(db, boat, resource, date(2027, 4, 1), date(2028, 3, 31))


def test_insurance_states(db) -> None:
    ann = _owner(db)
    today = date(2026, 10, 10)
    cases = {None: "unknown", date(2026, 9, 1): "expired", date(2026, 11, 30): "expiring", date(2027, 6, 1): "ok"}
    for expiry, want in cases.items():
        boat = _boat(db, ann, name=f"B{want}", sail=want, insurance_expiry=expiry)
        assert boats.insurance_state(boat, today) == want
    noted = _boat(db, ann, name="Noted", sail="N", insurance="Policy only")
    assert boats.insurance_state(noted, today) == "noted"
    assert {b.name for b, _ in boats.insurance_problems(db, today)} == {"Bunknown", "Bexpired", "Bexpiring"}


def test_allocation_rules_capacity_overlap_and_moves(db) -> None:
    ann, bob = _owner(db), _owner(db, "Bob Tiller", "bob@example.com")
    one, two = _boat(db, ann), _boat(db, bob, name="Gust", sail="918")
    row = boats.create_resource(db, "Row A 1", group="Row A")
    pen = boats.create_resource(db, "Dinghy pen", resource_type="dinghy park", capacity=2)
    with pytest.raises(boats.BoatError, match="already a space called"):
        boats.create_resource(db, "row a 1")
    with pytest.raises(boats.BoatError, match="at least 1"):
        boats.create_resource(db, "Zero", capacity=0)
    boats.allocate(db, one, row, date(2026, 4, 1), date(2027, 3, 31))
    with pytest.raises(boats.BoatError, match="is full"):
        boats.allocate(db, two, row, date(2026, 10, 1), date(2027, 1, 31))
    boats.allocate(db, two, row, date(2027, 4, 1), date(2028, 3, 31))  # the next season is free
    with pytest.raises(boats.BoatError, match="already has Row A 1"):
        boats.allocate(db, one, pen, date(2026, 6, 1), date(2026, 7, 1))
    with pytest.raises(boats.BoatError, match="before the start"):
        boats.allocate(db, one, pen, date(2030, 2, 1), date(2030, 1, 1))
    third = _boat(db, ann, name="Third", sail="3")
    fourth = _boat(db, ann, name="Fourth", sail="4")
    fifth = _boat(db, ann, name="Fifth", sail="5")
    boats.allocate(db, third, pen, date(2026, 4, 1), date(2027, 3, 31))
    boats.allocate(db, fourth, pen, date(2026, 4, 1), date(2027, 3, 31))
    with pytest.raises(boats.BoatError, match="is full"):
        boats.allocate(db, fifth, pen, date(2026, 4, 1), date(2027, 3, 31))
    assert boats.occupancy(db, date(2026, 10, 10)) == {row.id: 1, pen.id: 2}

    moved = boats.move_boat(db, one, pen if False else boats.create_resource(db, "Row B 7"), date(2026, 10, 10))
    old = db.scalars(select(StorageAllocation).where(StorageAllocation.boat_id == one.id).order_by(StorageAllocation.id)).first()
    assert old.status == "ended" and old.end_date == date(2026, 10, 9) and moved.start_date == date(2026, 10, 10) and moved.end_date == date(2027, 3, 31)
    assert boats.occupancy(db, date(2026, 10, 10)).get(row.id, 0) == 0
    with pytest.raises(boats.BoatError, match="already in that space"):
        boats.move_boat(db, one, db.get(boats.StorageResource, moved.resource_id), date(2026, 11, 1))
    with pytest.raises(boats.BoatError, match="no space to move"):
        boats.move_boat(db, _boat(db, ann, name="Homeless", sail="H"), row, date(2026, 11, 1))
    with pytest.raises(boats.BoatError, match="already ended"):
        boats.end_allocation(db, old, date(2026, 12, 1), "x")
    assert [b.name for b in boats.boats_without_space(db, date(2026, 10, 10))] == ["Fifth", "Gust", "Homeless"]  # Gust only has next season's space
    assert [a.id for a in boats.allocations_ending(db, 200, date(2026, 10, 10))]


def test_tariffs_most_specific_wins_and_validates(db) -> None:
    row = boats.create_resource(db, "Row A 1", group="Row A")
    other = boats.create_resource(db, "Row C 2", group="Row C")
    boats.set_tariff(db, "berth", "", 2027, 9000)
    boats.set_tariff(db, "berth", "Row A", 2027, 12000, "0105")
    assert boats.tariff_for(db, row, 2027).fee_minor == 12000 and boats.tariff_for(db, other, 2027).fee_minor == 9000
    assert boats.tariff_for(db, row, 2028) is None
    boats.set_tariff(db, "berth", "Row A", 2027, 13000)  # updates, no duplicate
    assert db.scalar(select(func.count()).select_from(boats.StorageTariff)) == 2 and boats.tariff_for(db, row, 2027).nominal_code == "0105"
    with pytest.raises(boats.BoatError, match="negative"):
        boats.set_tariff(db, "berth", "", 2027, -1)


def test_storage_billing_run_groups_by_owner_and_is_idempotent(db) -> None:
    ann, bob = _owner(db), _owner(db, "Bob Tiller", "bob@example.com")
    a1, a2, b1 = _boat(db, ann, name="One", sail="1"), _boat(db, ann, name="Two", sail="2"), _boat(db, bob, name="Gust", sail="3")
    free = _boat(db, bob, name="Free Fee", sail="4")
    unpriced = _boat(db, bob, name="No Tariff", sail="5")
    for boat, space, kw in ((a1, "Row A 1", {}), (a2, "Row A 2", {}), (b1, "Row A 3", {}), (free, "Row A 4", {"fee_minor": 0})):
        boats.allocate(db, boat, boats.create_resource(db, space, group="Row A"), date(2027, 1, 1), date(2027, 12, 31), **kw)
    boats.allocate(db, unpriced, boats.create_resource(db, "Shed 1", resource_type="shed"), date(2027, 1, 1), date(2027, 12, 31))
    boats.set_tariff(db, "berth", "Row A", 2027, 9000)

    preview = {r.boat.name: r for r in boats.billing_preview(db, 2027)}
    assert preview["One"].fee_minor == 9000 and preview["Free Fee"].fee_minor == 0 and "No 2027 tariff" in preview["No Tariff"].note

    report = boats.run_storage_billing(db, 2027, "treasurer@example.com")
    assert report.lines == 3 and len(report.invoices) == 2 and any("No Tariff" in s for s in report.skipped)
    invoices = {db.get(Contact, i.contact_id).name: i for i in db.scalars(select(Invoice))}
    assert invoices["Ann Sailor"].amount_minor == 18000 and invoices["Bob Tiller"].amount_minor == 9000
    assert all(i.status == "issued" and i.reference.isdigit() for i in invoices.values())
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoices["Ann Sailor"].id).order_by(InvoiceLine.id)).all()
    assert [(ln.description, ln.nominal_code) for ln in lines] == [("Row A 1 2027: One", "0102"), ("Row A 2 2027: Two", "0102")]

    again = boats.run_storage_billing(db, 2027, "t")
    assert again.lines == 0 and again.invoices == [] and db.scalar(select(func.count()).select_from(Invoice)) == 2

    finance.void_invoice(db, invoices["Bob Tiller"], "t", "wrong owner")  # a voided invoice frees the allocation to be billed again
    redo = boats.run_storage_billing(db, 2027, "t", issue=False)
    assert redo.lines == 1 and db.get(Invoice, redo.invoices[0]).status == "draft"
    assert all(r.billed for r in boats.billing_preview(db, 2027) if r.boat.name in {"One", "Two", "Gust"})


def test_billing_selected_allocations_only_and_ownerless_boats(db) -> None:
    ann = _owner(db)
    one, two = _boat(db, ann, name="One", sail="1"), _boat(db, ann, name="Two", sail="2")
    club = boats.save_boat(db, None, name="Club Topper", owner_contact_id=None, is_club_boat=True)
    spaces = [boats.create_resource(db, n, group="Row A") for n in ("A1", "A2", "A3")]
    allocs = [boats.allocate(db, b, s, date(2027, 1, 1), date(2027, 12, 31)) for b, s in zip((one, two, club), spaces, strict=True)]
    boats.set_tariff(db, "berth", "", 2027, 5000)
    report = boats.run_storage_billing(db, 2027, "t", allocation_ids=[allocs[0].id, allocs[2].id])
    assert report.lines == 1 and any("No owner to bill" in s for s in report.skipped)
    assert db.scalar(select(Invoice.amount_minor)) == 5000


def test_insurance_reminders_respect_gaps_and_opt_outs(db) -> None:
    today = date(2026, 10, 10)
    ann = _owner(db)
    quiet = _owner(db, "Quiet", "quiet@example.com")
    quiet.suppressed = True
    ghost = _owner(db, "Ghost", "ghost@example.com")
    ghost.email = None
    expiring = _boat(db, ann, name="Expiring", sail="E", insurance_expiry=date(2026, 11, 15))
    muted, nobody = _boat(db, quiet, name="Muted", sail="M"), _boat(db, ghost, name="Nobody", sail="N")
    result = boats.send_insurance_reminders(db, [expiring.id, muted.id, nobody.id, 9999], "t", today)
    assert result["sent"] == ["Expiring"] and len(result["skipped"]) == 3
    mail = db.scalar(select(OutboundEmail).where(OutboundEmail.related_type == "insurance_reminder"))
    assert mail and "runs out soon" in mail.body and "15 November 2026" in mail.body
    repeat = boats.send_insurance_reminders(db, [expiring.id], "t", today + timedelta(days=1))
    assert repeat["sent"] == [] and "reminded recently" in repeat["skipped"][0]
