"""Boat register, storage spaces, tariffs, the storage billing run and "my boats" for members."""

from __future__ import annotations

import math
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import boats
from app.csvutil import RowError, parse_minor
from app.db import get_db
from app.models import (
    Boat,
    BoatCrewLink,
    BoatDesign,
    Contact,
    SessionRecord,
    StorageAllocation,
    StorageResource,
    StorageTariff,
)
from app.ui.routes import (
    PAGE_SIZE,
    UiError,
    _audit,
    _csv_response,
    _ctx,
    _opt_date,
    templates,
    ui_session,
)

router = APIRouter(include_in_schema=False)
READ = Depends(ui_session(perm="boats.read"))
WRITE = Depends(ui_session(perm="boats.write"))
BILL = Depends(ui_session(perm="storage.bill"))
ANY = Depends(ui_session())


def _go(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{joiner}{urlencode(params)}" if params else path, status_code=303)


def _money(value: str) -> int:
    try:
        minor = parse_minor(value)
    except RowError as exc:
        raise boats.BoatError(str(exc)) from exc
    if minor is None:
        raise boats.BoatError("Enter an amount")
    return minor


def _boat(db: Session, boat_id: int) -> Boat:
    boat = db.get(Boat, boat_id)
    if boat is None:
        raise UiError(404, "Boat not found")
    return boat


# --------------------------------------------------------------------------- register

FILTERS = [("all", "All active"), ("no_space", "No space"), ("insurance", "Insurance to chase"), ("archived", "Archived")]


def _boat_stmt(db: Session, q: str, filter: str):  # noqa: ANN202
    stmt = select(Boat).order_by(Boat.name)
    if filter == "archived":
        stmt = stmt.where(Boat.status == "archived")
    else:
        stmt = stmt.where(Boat.status == "active")
    if filter == "no_space":
        ids = [b.id for b in boats.boats_without_space(db)]
        stmt = stmt.where(Boat.id.in_(ids or [0]))
    elif filter == "insurance":
        ids = [b.id for b, _ in boats.insurance_problems(db)]
        stmt = stmt.where(Boat.id.in_(ids or [0]))
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(or_(Boat.name.ilike(term), Boat.sail_number.ilike(term), Boat.boat_class.ilike(term)))
    return stmt


@router.get("/app/boats", response_class=HTMLResponse)
def boats_list(request: Request, q: str = "", filter: str = "all", page: int = 1, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    stmt = _boat_stmt(db, q, filter)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = db.scalars(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    today = date.today()
    items = []
    for boat in rows:
        allocation = boats.current_allocation(db, boat.id, today)
        owner = boats.owner_contact(db, boat)
        items.append({"boat": boat, "owner": owner, "space": db.get(StorageResource, allocation.resource_id) if allocation else None, "insurance": boats.insurance_state(boat, today)})
    return templates.TemplateResponse(request, "boats_list.html", _ctx(session, "boats", items=items, pager={"page": page, "pages": pages, "total": total}, q=q, filter=filter, filters=FILTERS, msg=msg, err=err, sub="boats"))


@router.get("/app/boats.csv")
def boats_csv(q: str = "", filter: str = "all", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    today = date.today()
    rows = []
    for boat in db.scalars(_boat_stmt(db, q, filter)):
        allocation = boats.current_allocation(db, boat.id, today)
        owner = boats.owner_contact(db, boat)
        space = db.get(StorageResource, allocation.resource_id) if allocation else None
        design = db.get(BoatDesign, boat.design_id) if boat.design_id else None
        rows.append(
            [boat.name, boat.sail_number, design.name if design else "", boat.boat_class, owner.name if owner else "", owner.email if owner and owner.email else "", space.name if space else "", boat.insurance_expiry.isoformat() if boat.insurance_expiry else "", boats.insurance_state(boat, today), boat.portsmouth_number or (design.portsmouth_rating if design and design.portsmouth_rating else ""), boat.status]
        )
    return _csv_response("boats.csv", ["boat", "sail_number", "design", "class", "owner", "owner_email", "space", "insurance_expiry", "insurance", "portsmouth_number", "status"], rows)


@router.get("/app/boats/new", response_class=HTMLResponse)
def boat_new(request: Request, contact_id: int | None = None, club: int = 0, q: str = "", err: str = "", session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    owner = db.get(Contact, contact_id) if contact_id else None
    matches: list[Contact] = []
    if owner is None and q.strip():
        term = f"%{q.strip()}%"
        matches = list(db.scalars(select(Contact).where(or_(Contact.name.ilike(term), Contact.email.ilike(term))).order_by(Contact.name).limit(20)).all())
    designs = db.scalars(select(BoatDesign).order_by(BoatDesign.name)).all()
    return templates.TemplateResponse(request, "boat_new.html", _ctx(session, "boats", owner=owner, club=bool(club), matches=matches, q=q, designs=designs, err=err, sub="new"))


def _boat_fields(name, owner_contact_id, design, sail_number, boat_class, colour, portsmouth, insurance, insurance_expiry, notes, club):  # noqa: ANN001, ANN202
    pn = int(portsmouth) if portsmouth.strip().isdigit() else None
    return {
        "name": name,
        "owner_contact_id": owner_contact_id,
        "design_name": design,
        "sail_number": sail_number,
        "boat_class": boat_class,
        "colour": colour,
        "portsmouth_number": pn,
        "insurance": insurance,
        "insurance_expiry": _opt_date(insurance_expiry),
        "notes": notes,
        "is_club_boat": club == "1",
    }


@router.post("/app/boats")
def boat_create(
    owner_contact_id: int | None = Form(None),
    name: str = Form(""),
    design: str = Form(""),
    sail_number: str = Form(""),
    boat_class: str = Form(""),
    colour: str = Form(""),
    portsmouth: str = Form(""),
    insurance: str = Form(""),
    insurance_expiry: str = Form(""),
    notes: str = Form(""),
    club: str = Form(""),
    session: SessionRecord = WRITE,
    db: Session = Depends(get_db),
) -> Response:
    try:
        boat = boats.save_boat(db, None, actor=session.email, **_boat_fields(name, owner_contact_id, design, sail_number, boat_class, colour, portsmouth, insurance, insurance_expiry, notes, club))
        _audit(db, session, "boat.create", "boat", boat.id)
        db.commit()
    except (boats.BoatError, ValueError) as exc:
        db.rollback()
        target = f"/app/boats/new?contact_id={owner_contact_id}" if owner_contact_id else ("/app/boats/new?club=1" if club == "1" else "/app/boats/new")
        return _go(target, err=str(exc))
    return _go(f"/app/boats/{boat.id}", msg="Boat added")


@router.get("/app/boats/{boat_id:int}", response_class=HTMLResponse)
def boat_detail(boat_id: int, request: Request, q: str = "", msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    boat = _boat(db, boat_id)
    owner = boats.owner_contact(db, boat)
    design = db.get(BoatDesign, boat.design_id) if boat.design_id else None
    allocations = db.execute(
        select(StorageAllocation, StorageResource).join(StorageResource, StorageResource.id == StorageAllocation.resource_id).where(StorageAllocation.boat_id == boat.id).order_by(StorageAllocation.start_date.desc())
    ).all()
    matches: list[Contact] = []
    if q.strip():
        term = f"%{q.strip()}%"
        matches = list(db.scalars(select(Contact).where(or_(Contact.name.ilike(term), Contact.email.ilike(term))).order_by(Contact.name).limit(15)).all())
    resources = db.scalars(select(StorageResource).where(StorageResource.active.is_not(False)).order_by(StorageResource.resource_group, StorageResource.name)).all()
    return templates.TemplateResponse(
        request,
        "boat_detail.html",
        _ctx(
            session, "boats", boat=boat, owner=owner, design=design, co_owners=boats.co_owners(db, boat), allocations=allocations, resources=resources, matches=matches, q=q,
            insurance=boats.insurance_state(boat), today=date.today().isoformat(), msg=msg, err=err, sub="boats",
            designs=db.scalars(select(BoatDesign).order_by(BoatDesign.name)).all(),
        ),
    )


def _boat_action(db: Session, boat_id: int, fn, ok: str) -> RedirectResponse:  # noqa: ANN001
    boat = _boat(db, boat_id)
    path = f"/app/boats/{boat_id}"
    try:
        fn(boat)
        db.commit()
    except (boats.BoatError, ValueError) as exc:
        db.rollback()
        return _go(path, err=str(exc))
    return _go(path, msg=ok)


@router.post("/app/boats/{boat_id:int}")
def boat_save(
    boat_id: int,
    name: str = Form(""),
    design: str = Form(""),
    sail_number: str = Form(""),
    boat_class: str = Form(""),
    colour: str = Form(""),
    portsmouth: str = Form(""),
    insurance: str = Form(""),
    insurance_expiry: str = Form(""),
    notes: str = Form(""),
    club: str = Form(""),
    session: SessionRecord = WRITE,
    db: Session = Depends(get_db),
) -> Response:
    def save(boat: Boat) -> None:
        fields = _boat_fields(name, boat.owner_contact_id, design, sail_number, boat_class, colour, portsmouth, insurance, insurance_expiry, notes, club)
        boats.save_boat(db, boat, actor=session.email, **fields)
        _audit(db, session, "boat.update", "boat", boat.id)

    return _boat_action(db, boat_id, save, "Saved")


@router.post("/app/boats/{boat_id:int}/owner")
def boat_owner(boat_id: int, contact_id: int = Form(...), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def change(boat: Boat) -> None:
        contact = db.get(Contact, contact_id)
        if contact is None:
            raise boats.BoatError("Contact not found")
        boat.owner_contact_id, boat.owner_member_id = contact.id, contact.member_id
        _audit(db, session, "boat.owner", "boat", boat.id, to=contact.id)

    return _boat_action(db, boat_id, change, "Owner changed")


@router.post("/app/boats/{boat_id:int}/co-owners")
def boat_add_co_owner(boat_id: int, contact_id: int = Form(...), role: str = Form("co-owner"), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    return _boat_action(db, boat_id, lambda b: boats.add_co_owner(db, b, contact_id, role), "Added")


@router.post("/app/boats/{boat_id:int}/co-owners/{link_id:int}/remove")
def boat_remove_co_owner(boat_id: int, link_id: int, session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    return _boat_action(db, boat_id, lambda b: boats.remove_co_owner(db, b, link_id), "Removed")


@router.post("/app/boats/{boat_id:int}/archive")
def boat_archive(boat_id: int, reason: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def archive(boat: Boat) -> None:
        boats.archive_boat(db, boat, reason)
        _audit(db, session, "boat.archive", "boat", boat.id, reason=reason)

    return _boat_action(db, boat_id, archive, "Archived")


@router.post("/app/boats/{boat_id:int}/allocate")
def boat_allocate(boat_id: int, resource_id: int = Form(...), start: str = Form(""), end: str = Form(""), fee: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def do(boat: Boat) -> None:
        resource = db.get(StorageResource, resource_id)
        first, last = _opt_date(start), _opt_date(end)
        if resource is None or first is None or last is None:
            raise boats.BoatError("Choose a space and both dates")
        allocation = boats.allocate(db, boat, resource, first, last, fee_minor=_money(fee) if fee.strip() else None)
        _audit(db, session, "storage.allocate", "boat", boat.id, resource=resource.id, allocation=allocation.id)

    return _boat_action(db, boat_id, do, "Space allocated")


@router.post("/app/boats/{boat_id:int}/move")
def boat_move(boat_id: int, resource_id: int = Form(...), on: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def do(boat: Boat) -> None:
        resource = db.get(StorageResource, resource_id)
        when = _opt_date(on)
        if resource is None or when is None:
            raise boats.BoatError("Choose a space and the date of the move")
        boats.move_boat(db, boat, resource, when)
        _audit(db, session, "storage.move", "boat", boat.id, to=resource.id)

    return _boat_action(db, boat_id, do, "Moved")


@router.post("/app/boats/{boat_id:int}/allocations/{allocation_id:int}/end")
def allocation_end(boat_id: int, allocation_id: int, on: str = Form(""), reason: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    def do(boat: Boat) -> None:
        allocation = db.get(StorageAllocation, allocation_id)
        if allocation is None or allocation.boat_id != boat.id:
            raise boats.BoatError("Allocation not found")
        boats.end_allocation(db, allocation, _opt_date(on) or date.today(), reason)

    return _boat_action(db, boat_id, do, "Space released")


# --------------------------------------------------------------------------- spaces, tariffs, billing


@router.get("/app/storage", response_class=HTMLResponse)
def storage_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    today = date.today()
    used = boats.occupancy(db, today)
    resources = db.scalars(select(StorageResource).order_by(StorageResource.resource_type, StorageResource.resource_group, StorageResource.name)).all()
    groups: dict[str, list] = {}
    for resource in resources:
        key = f"{resource.resource_type} · {resource.resource_group or 'no group'}"
        occupant = None
        if used.get(resource.id):
            allocation = db.scalars(select(StorageAllocation).where(StorageAllocation.resource_id == resource.id, StorageAllocation.status == "active", StorageAllocation.start_date <= today, StorageAllocation.end_date >= today)).first()
            occupant = db.get(Boat, allocation.boat_id) if allocation else None
        groups.setdefault(key, []).append({"resource": resource, "used": used.get(resource.id, 0), "boat": occupant})
    return templates.TemplateResponse(request, "storage.html", _ctx(session, "boats", groups=groups, msg=msg, err=err, sub="storage"))


@router.post("/app/storage/resources")
def resource_create(name: str = Form(""), resource_type: str = Form("berth"), group: str = Form(""), capacity: int = Form(1), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    try:
        boats.create_resource(db, name, resource_type, group, capacity)
        db.commit()
    except boats.BoatError as exc:
        db.rollback()
        return _go("/app/storage", err=str(exc))
    return _go("/app/storage", msg="Space added")


@router.get("/app/storage/tariffs", response_class=HTMLResponse)
def tariffs_page(request: Request, year: int | None = None, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    year = year or date.today().year + (1 if date.today().month >= 10 else 0)
    tariffs = db.scalars(select(StorageTariff).where(StorageTariff.season_year == year).order_by(StorageTariff.resource_type, StorageTariff.resource_group)).all()
    combos = db.execute(select(StorageResource.resource_type, StorageResource.resource_group, func.count()).group_by(StorageResource.resource_type, StorageResource.resource_group)).all()
    return templates.TemplateResponse(request, "tariffs.html", _ctx(session, "boats", year=year, tariffs=tariffs, combos=combos, msg=msg, err=err, sub="tariffs"))


@router.post("/app/storage/tariffs/copy")
def tariffs_copy(from_year: int = Form(...), to_year: int = Form(...), rise: str = Form("0"), session: SessionRecord = BILL, db: Session = Depends(get_db)) -> Response:
    try:
        percent = float(rise.strip() or 0)
        done = boats.copy_tariffs(db, from_year, to_year, percent)
        _audit(db, session, "storage.tariffs_copied", "tariff", f"{from_year}->{to_year}", rise=percent, copied=done)
        db.commit()
    except (boats.BoatError, ValueError) as exc:
        db.rollback()
        return _go(f"/app/storage/tariffs?year={to_year}", err=str(exc))
    return _go(f"/app/storage/tariffs?year={to_year}", msg=f"{done} fee(s) copied from {from_year}" + (f" with a {percent:g}% rise" if percent else ""))


@router.post("/app/storage/tariffs")
def tariff_save(year: int = Form(...), resource_type: str = Form(...), group: str = Form(""), fee: str = Form(""), nominal: str = Form("0102"), prorata: str = Form(""), session: SessionRecord = BILL, db: Session = Depends(get_db)) -> Response:
    try:
        boats.set_tariff(db, resource_type.strip(), group, year, _money(fee), nominal.strip() or None, prorata=bool(prorata))
        _audit(db, session, "storage.tariff", "tariff", f"{resource_type}/{group}/{year}")
        db.commit()
    except boats.BoatError as exc:
        db.rollback()
        return _go(f"/app/storage/tariffs?year={year}", err=str(exc))
    return _go(f"/app/storage/tariffs?year={year}", msg="Saved")


@router.get("/app/storage/billing", response_class=HTMLResponse)
def storage_billing(request: Request, year: int | None = None, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    year = year or date.today().year + (1 if date.today().month >= 10 else 0)
    rows = boats.billing_preview(db, year)
    summary = {
        "due": sum(1 for r in rows if not r.billed and not r.note and (r.fee_minor or 0) > 0),
        "total": sum(r.fee_minor or 0 for r in rows if not r.billed and not r.note),
        "billed": sum(1 for r in rows if r.billed),
        "attention": sum(1 for r in rows if r.note and not r.billed),
    }
    return templates.TemplateResponse(request, "storage_billing.html", _ctx(session, "boats", year=year, rows=rows, summary=summary, msg=msg, err=err, sub="billing"))


@router.post("/app/storage/billing/{year:int}")
async def storage_billing_run(year: int, request: Request, session: SessionRecord = BILL, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    selected = [int(str(v)) for v in form.getlist("allocation") if str(v).isdigit()]
    if not selected and form.get("everyone") != "1":
        return _go(f"/app/storage/billing?year={year}", err='Tick the boats to bill, or use "Bill everyone listed"')
    report = boats.run_storage_billing(db, year, session.email, allocation_ids=selected or None, issue=form.get("draft") != "1")
    _audit(db, session, "storage.bill", "storage", year, **report.as_dict())
    db.commit()
    text = f"{len(report.invoices)} invoice(s) for {report.lines} space(s)" + (f"; skipped: {'; '.join(report.skipped[:4])}" if report.skipped else "")
    return _go(f"/app/storage/billing?year={year}", msg=text)


# --------------------------------------------------------------------------- reports


@router.get("/app/boats/reports", response_class=HTMLResponse)
def boat_reports(request: Request, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    ending = [(a, db.get(Boat, a.boat_id), db.get(StorageResource, a.resource_id)) for a in boats.allocations_ending(db, 90)]
    return templates.TemplateResponse(
        request, "boat_reports.html", _ctx(session, "boats", no_space=boats.boats_without_space(db), ending=ending, insurance=boats.insurance_problems(db), owners=lambda b: boats.owner_contact(db, b), msg=msg, err=err, sub="reports")
    )


@router.post("/app/boats/reports/insurance-reminders")
async def insurance_reminders(request: Request, session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    ids = [int(str(v)) for v in form.getlist("boat") if str(v).isdigit()]
    if not ids:
        return _go("/app/boats/reports", err="Tick at least one boat")
    result = boats.send_insurance_reminders(db, ids, session.email)
    _audit(db, session, "boat.insurance_reminders", "boat", "bulk", sent=len(result["sent"]), skipped=len(result["skipped"]))
    db.commit()
    return _go("/app/boats/reports", msg=f"{len(result['sent'])} reminder(s) sent" + (f"; skipped: {'; '.join(result['skipped'][:4])}" if result["skipped"] else ""))


# --------------------------------------------------------------------------- my boats (members)


def _my_contact(db: Session, session: SessionRecord) -> Contact | None:
    return db.scalar(select(Contact).where(func.lower(Contact.email) == session.email.lower()).order_by(Contact.id))


def _my_boats(db: Session, contact: Contact) -> list[Boat]:
    crew = select(BoatCrewLink.boat_id).where(BoatCrewLink.contact_id == contact.id)
    return list(db.scalars(select(Boat).where(Boat.status == "active", or_(Boat.owner_contact_id == contact.id, Boat.id.in_(crew))).order_by(Boat.name)))


@router.get("/app/me/boats", response_class=HTMLResponse)
def me_boats(request: Request, msg: str = "", err: str = "", session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    contact = _my_contact(db, session)
    items = []
    if contact is not None:
        for boat in _my_boats(db, contact):
            allocation = boats.current_allocation(db, boat.id)
            items.append({"boat": boat, "space": db.get(StorageResource, allocation.resource_id) if allocation else None, "insurance": boats.insurance_state(boat), "is_owner": boat.owner_contact_id == contact.id})
    return templates.TemplateResponse(request, "me_boats.html", _ctx(session, "dashboard", items=items, msg=msg, err=err))


@router.post("/app/me/boats/{boat_id:int}")
def me_boat_save(boat_id: int, insurance: str = Form(""), insurance_expiry: str = Form(""), colour: str = Form(""), sail_number: str = Form(""), notes: str = Form(""), session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    contact = _my_contact(db, session)
    boat = db.get(Boat, boat_id)
    if contact is None or boat is None or boat.owner_contact_id != contact.id or boat.status != "active":
        raise UiError(404, "Boat not found")
    try:
        expiry = _opt_date(insurance_expiry)
    except ValueError as exc:
        return _go("/app/me/boats", err=str(exc))
    before = {"insurance": boat.insurance, "insurance_expiry": str(boat.insurance_expiry or ""), "colour": boat.colour, "sail_number": boat.sail_number}
    boat.insurance, boat.insurance_expiry = insurance.strip() or None, expiry
    boat.colour, boat.sail_number, boat.notes = colour.strip() or None, sail_number.strip(), notes.strip() or None
    _audit(db, session, "boat.self_edit", "boat", boat.id, before=before)
    db.commit()
    return _go("/app/me/boats", msg=f"{boat.name} updated")

