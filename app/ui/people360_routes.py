"""People screens beyond the basic list: tags, lists, households, saved views and the data-issues check."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app import people
from app.db import get_db
from app.models import Contact, ContactList, SavedView, SessionRecord
from app.ui.finance_routes import _back
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)
READ = ui_session(perm="members.read")
WRITE = ui_session(perm="members.write")


def _contact(db: Session, contact_id: int) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    return contact


# --------------------------------------------------------------------------- tags, lists, household on a contact


@router.post("/app/contacts/{contact_id:int}/tags")
def tag_add(contact_id: int, tag: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    contact = _contact(db, contact_id)
    try:
        added = people.add_tag(db, contact, tag)
        _audit(db, session, "contact.tag", "contact", contact_id, tag=added)
        db.commit()
    except people.PeopleError as exc:
        db.rollback()
        return _back(f"/app/contacts/{contact_id}", err=str(exc))
    return _back(f"/app/contacts/{contact_id}", msg=f"Tagged “{added}”")


@router.post("/app/contacts/{contact_id:int}/tags/remove")
def tag_remove(contact_id: int, tag: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    people.remove_tag(db, _contact(db, contact_id), tag)
    _audit(db, session, "contact.untag", "contact", contact_id, tag=tag)
    db.commit()
    return _back(f"/app/contacts/{contact_id}")


@router.post("/app/contacts/{contact_id:int}/lists")
def list_add(contact_id: int, list_id: int = Form(...), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    contact, lst = _contact(db, contact_id), db.get(ContactList, list_id)
    if lst is None:
        raise UiError(404, "List not found")
    people.add_to_list(db, contact, lst)
    _audit(db, session, "contact.list_add", "contact", contact_id, list=lst.name)
    db.commit()
    return _back(f"/app/contacts/{contact_id}", msg=f"Added to {lst.name}")


@router.post("/app/contacts/{contact_id:int}/lists/{list_id:int}/remove")
def list_remove(contact_id: int, list_id: int, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    contact, lst = _contact(db, contact_id), db.get(ContactList, list_id)
    if lst is not None:
        people.remove_from_list(db, contact, lst)
        _audit(db, session, "contact.list_remove", "contact", contact_id, list=lst.name)
        db.commit()
    return _back(f"/app/contacts/{contact_id}")


@router.post("/app/contacts/{contact_id:int}/household")
def household_link(contact_id: int, other_id: int = Form(...), relation: str = Form("partner"), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    contact, other = _contact(db, contact_id), db.get(Contact, other_id)
    if other is None:
        return _back(f"/app/contacts/{contact_id}", err=f"There is no contact number {other_id}")
    try:
        people.link_people(db, contact, other, relation)
        _audit(db, session, "contact.link", "contact", contact_id, other=other_id, relation=relation)
        db.commit()
    except people.PeopleError as exc:
        db.rollback()
        return _back(f"/app/contacts/{contact_id}", err=str(exc))
    return _back(f"/app/contacts/{contact_id}", msg=f"Linked to {other.name} ({relation})")


@router.post("/app/contacts/{contact_id:int}/household/{other_id:int}/remove")
def household_unlink(contact_id: int, other_id: int, session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    contact, other = _contact(db, contact_id), db.get(Contact, other_id)
    if other is not None:
        people.unlink_people(db, contact, other)
        _audit(db, session, "contact.unlink", "contact", contact_id, other=other_id)
        db.commit()
    return _back(f"/app/contacts/{contact_id}")


# --------------------------------------------------------------------------- lists


@router.get("/app/lists", response_class=HTMLResponse)
def lists_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "lists.html", _ctx(session, "contacts", lists=people.lists_with_counts(db), tags=people.all_tags(db), msg=msg, err=err))


@router.post("/app/lists")
def list_create(name: str = Form(""), session: SessionRecord = Depends(WRITE), db: Session = Depends(get_db)) -> Response:
    try:
        lst = people.create_list(db, name)
        _audit(db, session, "list.create", "contact_list", lst.id, name=lst.name)
        db.commit()
    except people.PeopleError as exc:
        db.rollback()
        return _back("/app/lists", err=str(exc))
    return _back("/app/lists", msg=f"Created {lst.name}")


@router.get("/app/lists/{list_id:int}", response_class=HTMLResponse)
def list_page(list_id: int, request: Request, session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    lst = db.get(ContactList, list_id)
    if lst is None:
        raise UiError(404, "List not found")
    return templates.TemplateResponse(request, "list_detail.html", _ctx(session, "contacts", lst=lst, members=people.list_members(db, lst)))


# --------------------------------------------------------------------------- saved views


@router.post("/app/views")
def view_save(scope: str = Form(...), name: str = Form(""), query: str = Form(""), session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    try:
        view = people.save_view(db, scope, name, query, session.email)
        db.commit()
    except people.PeopleError as exc:
        db.rollback()
        return _back(f"/app/{scope if scope in {'members', 'contacts'} else 'members'}?{query}", err=str(exc))
    return RedirectResponse(f"/app/{scope}?{view.query}", status_code=303)


@router.post("/app/views/{view_id:int}/delete")
def view_delete(view_id: int, back: str = Form("/app/members"), session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    view = db.get(SavedView, view_id)
    if view is not None:
        db.delete(view)
        db.commit()
    return RedirectResponse(back if back in {"/app/members", "/app/contacts"} else "/app/members", status_code=303)


# --------------------------------------------------------------------------- data issues


@router.get("/app/people/issues", response_class=HTMLResponse)
def issues_page(request: Request, session: SessionRecord = Depends(READ), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "people_issues.html", _ctx(session, "contacts", issues=people.data_issues(db)))
