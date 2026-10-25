"""Boat park through the year: keep or give up a space, roll the park into next season, the waiting list. Staff and member sides."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import berths, boats
from app.db import get_db
from app.models import (
    Boat,
    Contact,
    SessionRecord,
    SpaceRequest,
    StorageAllocation,
    StorageResource,
)
from app.ui.boat_routes import ANY, READ, WRITE, _go
from app.ui.routes import UiError, _audit, _ctx, templates

router = APIRouter(include_in_schema=False)


def _season_year() -> int:
    """The season being decided in autumn is the one that is ending; otherwise this year's."""
    return date.today().year


@router.get("/app/storage/season", response_class=HTMLResponse)
def season_page(request: Request, year: int | None = None, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    year = year if year and 2000 <= year <= 2100 else _season_year()
    rows = berths.season_rows(db, year)
    counts = {state: len([r for r in rows if r.state == state]) for state in ("keeping", "leaving", "no reply")}
    free_now = sum(g.free for g in berths.occupancy_by_group(db))
    waiting = len(berths.waiting_list(db))
    return templates.TemplateResponse(
        request, "berth_season.html",
        _ctx(session, "boats", year=year, rows=rows, counts=counts, groups=berths.occupancy_by_group(db), free_now=free_now, waiting=waiting, msg=msg, err=err, sub="season"),
    )


@router.post("/app/storage/season/roll")
def season_roll(year: int = Form(...), confirm: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    if not confirm:
        return _go(f"/app/storage/season?year={year}", err="Tick the box to confirm: this carries every space into next season")
    result = berths.roll_over(db, year)
    _audit(db, session, "storage.roll_over", "season", year, **result)
    db.commit()
    return _go(f"/app/storage/season?year={year}", msg=f"{result['extended']} space(s) carried into {year + 1}; {result['leaving']} leaving were left to end")


@router.post("/app/storage/season/{allocation_id:int}/{action}")
def season_mark(allocation_id: int, action: str, year: int = Form(...), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    allocation = db.get(StorageAllocation, allocation_id)
    if allocation is None or action not in {"keep", "leave"}:
        raise UiError(404, "Space not found")
    (berths.keep if action == "keep" else berths.leave)(db, allocation, year + 1)
    _audit(db, session, f"storage.{action}", "allocation", allocation_id, year=year)
    db.commit()
    return _go(f"/app/storage/season?year={year}", msg="Noted")


# --------------------------------------------------------------------------- waiting list (staff)


@router.get("/app/storage/waiting", response_class=HTMLResponse)
def waiting_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = READ, db: Session = Depends(get_db)) -> Response:
    queue = berths.waiting_list(db)
    free = [(g, name) for g in berths.occupancy_by_group(db) for name in g.free_names]
    spaces = db.scalars(select(StorageResource).where(StorageResource.active.is_not(False)).order_by(StorageResource.name)).all()
    return templates.TemplateResponse(request, "berth_waiting.html", _ctx(session, "boats", queue=queue, free=free, spaces=spaces, today=date.today(), msg=msg, err=err, sub="waiting"))


@router.post("/app/storage/waiting")
def waiting_add(contact_id: int = Form(...), resource_type: str = Form("berth"), group: str = Form(""), note: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    try:
        berths.join_waiting_list(db, contact, resource_type=resource_type.strip() or "berth", group=group, note=note)
        _audit(db, session, "storage.waiting_add", "contact", contact_id)
        db.commit()
    except boats.BoatError as exc:
        db.rollback()
        return _go("/app/storage/waiting", err=str(exc))
    return _go("/app/storage/waiting", msg=f"{contact.name} is on the waiting list")


@router.post("/app/storage/waiting/{request_id:int}/withdraw")
def waiting_withdraw(request_id: int, session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    request = db.get(SpaceRequest, request_id)
    if request is None:
        raise UiError(404, "Request not found")
    try:
        berths.withdraw(db, request)
        db.commit()
    except boats.BoatError as exc:
        db.rollback()
        return _go("/app/storage/waiting", err=str(exc))
    return _go("/app/storage/waiting", msg="Taken off the waiting list")


@router.post("/app/storage/waiting/{request_id:int}/offer")
def waiting_offer(request_id: int, resource_id: int = Form(...), boat_id: int = Form(...), start: str = Form(""), end: str = Form(""), session: SessionRecord = WRITE, db: Session = Depends(get_db)) -> Response:
    request, resource, boat = db.get(SpaceRequest, request_id), db.get(StorageResource, resource_id), db.get(Boat, boat_id)
    if request is None or resource is None or boat is None:
        raise UiError(404, "Not found")
    try:
        first = date.fromisoformat(start) if start else date.today()
        last = date.fromisoformat(end) if end else date(first.year, 12, 31)
        berths.offer(db, request, resource, boat, first, last)
        _audit(db, session, "storage.offer", "space_request", request_id, space=resource.name, boat=boat.name)
        db.commit()
    except (boats.BoatError, ValueError) as exc:
        db.rollback()
        return _go("/app/storage/waiting", err=str(exc))
    return _go("/app/storage/waiting", msg=f"{boat.name} now has {resource.name}")


# --------------------------------------------------------------------------- members


def _my_contact(db: Session, session: SessionRecord) -> Contact | None:
    return db.scalar(select(Contact).where(func.lower(Contact.email) == session.email.lower()).order_by(Contact.id))


@router.post("/app/me/boats/{boat_id:int}/space/{action}")
def me_space(boat_id: int, action: str, session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    contact, boat = _my_contact(db, session), db.get(Boat, boat_id)
    if contact is None or boat is None or boat.owner_contact_id != contact.id or action not in {"keep", "leave"}:
        raise UiError(404, "Boat not found")
    allocation = boats.current_allocation(db, boat.id)
    if allocation is None:
        return _go("/app/me/boats", err="This boat has no space to keep")
    next_year = date.today().year + 1
    (berths.keep if action == "keep" else berths.leave)(db, allocation, next_year)
    _audit(db, session, f"storage.self_{action}", "allocation", allocation.id, year=next_year)
    db.commit()
    return _go("/app/me/boats", msg=f"Thank you: we have noted you are {'keeping' if action == 'keep' else 'giving up'} the space for {boat.name}")


@router.post("/app/me/waiting")
def me_waiting(boat_id: str = Form(""), note: str = Form(""), session: SessionRecord = ANY, db: Session = Depends(get_db)) -> Response:
    contact = _my_contact(db, session)
    if contact is None:
        raise UiError(404, "No contact record for this account")
    boat = db.get(Boat, int(boat_id)) if boat_id.isdigit() else None
    if boat is not None and boat.owner_contact_id != contact.id:
        raise UiError(404, "Boat not found")
    try:
        berths.join_waiting_list(db, contact, boat=boat, note=note)
        db.commit()
    except boats.BoatError as exc:
        db.rollback()
        return _go("/app/me/boats", err=str(exc))
    return _go("/app/me/boats", msg="You are on the waiting list. The harbourmaster will be in touch when a space comes up.")
