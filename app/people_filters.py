"""Filtering people: one place that turns the filters on the Members and Contacts pages into queries.

Members and contacts are filtered the same way, so "juniors who have a boat" or "everyone on the Committee list who owes
money" means the same on both pages and in exports and mailings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Boat,
    Contact,
    ContactList,
    ContactTag,
    Invoice,
    ListMembership,
    Member,
    MembershipType,
)

STATUSES = [
    ("all", "Any status"),
    ("active", "Active"),
    ("inactive", "Not active"),
    ("lapsed", "Lapsed"),
    ("resigned", "Resigned"),
    ("suspended", "Suspended"),
    ("applying", "Applying"),
]
AGES = [
    ("", "Any age"),
    ("junior", "Under 18"),
    ("adult", "18 and over"),
    ("unknown", "Age not recorded"),
]
YES_NO = [("", "Any"), ("yes", "Yes"), ("no", "No")]
UNPAID_STATUSES = ("issued", "part_paid")


@dataclass
class PeopleFilter:
    q: str = ""
    status: str = "all"
    type: str = ""
    age: str = ""
    list: str = ""
    boat: str = ""
    unpaid: str = ""
    member: str = ""  # contacts only: is this person a member?
    tag: str = ""

    def as_params(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v and v != "all"}

    @property
    def active_filters(self) -> int:
        return len(self.as_params()) - (1 if self.q else 0)


def _years_ago(today: date, years: int) -> date:
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # 29 February
        return today.replace(year=today.year - years, day=28)


def _contact_conditions(f: PeopleFilter, contact_id_col, member_id_col, birth_col, today: date):
    """Conditions shared by both lists, written against whichever columns identify the person."""
    conditions = []
    if f.age == "junior":
        conditions.append(birth_col > _years_ago(today, 18))
    elif f.age == "adult":
        conditions.append(birth_col <= _years_ago(today, 18))
    elif f.age == "unknown":
        conditions.append(birth_col.is_(None))
    if f.list:
        on_list = exists().where(
            ListMembership.contact_id == contact_id_col,
            ListMembership.list_id == ContactList.id,
            ContactList.name == f.list,
        )
        conditions.append(on_list)
    if f.tag:
        conditions.append(exists().where(ContactTag.contact_id == contact_id_col, ContactTag.tag == f.tag.strip().lower()))
    if f.boat in {"yes", "no"}:
        has_boat = (
            exists()
            .where(Boat.status != "archived")
            .where(
                or_(
                    Boat.owner_contact_id == contact_id_col,
                    and_(member_id_col.is_not(None), Boat.owner_member_id == member_id_col),
                )
            )
        )
        conditions.append(has_boat if f.boat == "yes" else ~has_boat)
    if f.unpaid in {"yes", "no"}:
        owes = exists().where(
            Invoice.status.in_(UNPAID_STATUSES),
            or_(
                Invoice.contact_id == contact_id_col,
                and_(member_id_col.is_not(None), Invoice.member_id == member_id_col),
            ),
        )
        conditions.append(owes if f.unpaid == "yes" else ~owes)
    return conditions


def members_statement(f: PeopleFilter, today: date | None = None) -> Select:
    today = today or date.today()
    stmt = select(Member).outerjoin(Contact, Contact.member_id == Member.id).order_by(Member.name)
    if f.q.strip():
        term = f"%{f.q.strip()}%"
        stmt = stmt.where(
            or_(
                Member.name.ilike(term),
                Member.email.ilike(term),
                Member.membership_number.ilike(term),
            )
        )
    if f.status == "active":
        stmt = stmt.where(Member.active.is_(True))
    elif f.status == "inactive":
        stmt = stmt.where(Member.active.is_(False))
    elif f.status in {"lapsed", "resigned", "suspended", "applying"}:
        stmt = stmt.where(Member.renewal_status == f.status)
    if f.type:
        stmt = stmt.where(Member.membership_type == f.type)
    for condition in _contact_conditions(f, Contact.id, Member.id, Contact.birth_date, today):
        stmt = stmt.where(condition)
    return stmt.distinct()


def contacts_statement(f: PeopleFilter, today: date | None = None) -> Select:
    today = today or date.today()
    stmt = select(Contact).order_by(Contact.name)
    if f.q.strip():
        term = f"%{f.q.strip()}%"
        stmt = stmt.where(
            or_(Contact.name.ilike(term), Contact.email.ilike(term), Contact.phone.ilike(term))
        )
    if f.member == "yes":
        stmt = stmt.where(Contact.member_id.is_not(None))
    elif f.member == "no":
        stmt = stmt.where(Contact.member_id.is_(None))
    if f.status != "all" or f.type:
        stmt = stmt.join(Member, Member.id == Contact.member_id)
        if f.status in {"active", "inactive"}:
            stmt = stmt.where(Member.active.is_(f.status == "active"))
        elif f.status != "all":
            stmt = stmt.where(Member.renewal_status == f.status)
        if f.type:
            stmt = stmt.where(Member.membership_type == f.type)
    for condition in _contact_conditions(
        f, Contact.id, Contact.member_id, Contact.birth_date, today
    ):
        stmt = stmt.where(condition)
    return stmt


def choices(db: Session) -> dict[str, list]:
    """The dropdown contents for the filter bar."""
    types = [
        name
        for (name,) in db.execute(
            select(MembershipType.name)
            .where(MembershipType.active.is_not(False))
            .order_by(MembershipType.name)
        )
    ]
    lists = [name for (name,) in db.execute(select(ContactList.name).order_by(ContactList.name))]
    tags = [t for (t,) in db.execute(select(ContactTag.tag).distinct().order_by(ContactTag.tag))]
    return {"statuses": STATUSES, "types": types, "lists": lists, "tags": tags, "ages": AGES, "yes_no": YES_NO}


def count_by_status(db: Session) -> dict[str, int]:
    return {
        status: n
        for status, n in db.execute(
            select(Member.renewal_status, func.count()).group_by(Member.renewal_status)
        )
    }
