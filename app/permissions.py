"""Who may do what.

A person has one *base* role (Admin, Committee or Member) plus any number of *hats* (Treasurer, Membership
secretary). Hats add permissions, so one volunteer can cover several jobs without a shared login and without the
whole committee being able to void invoices. Self-service means most members never need either hat.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import SessionRecord, UserRole

# Base roles. Committee can see everything and decide applications; it cannot change money or records.
BASE_PERMISSIONS: dict[str, set[str]] = {
    "Admin": {"*"},
    "Committee": {"members.read", "boats.read", "training.read", "events.read", "reports.read", "applications.decide"},
    "Member": set(),
}

# Hats a volunteer can wear on top of their base role.
@dataclass(frozen=True)
class Hat:
    label: str
    perms: frozenset[str]
    help: str


HATS: dict[str, Hat] = {
    "treasurer": Hat(
        "Treasurer",
        frozenset({"finance.read", "finance.write", "finance.admin", "finance.lock", "billing.run", "storage.bill", "boats.read", "training.read", "members.read", "reports.read"}),
        "Invoices, payments, bank matching, credit notes, write-offs, fees and invoice settings.",
    ),
    "secretary": Hat(
        "Membership secretary",
        frozenset({"members.read", "members.write", "applications.decide", "billing.run", "boats.read", "training.read", "reports.read"}),
        "Members, contacts, enquiries, applications and the renewal billing run. Cannot see the bank or change payments.",
    ),
    "bosun": Hat(
        "Harbourmaster (boats and berths)",
        frozenset({"boats.read", "boats.write", "storage.bill", "members.read", "reports.read"}),
        "Boat register, storage spaces, allocations, tariffs, the storage billing run and insurance reminders.",
    ),
    "social": Hat(
        "Social secretary",
        frozenset({"events.read", "events.write", "members.read", "reports.read"}),
        "Events, tickets for members and the public, and attendee lists.",
    ),
    "training": Hat(
        "Training officer",
        frozenset({"training.read", "training.write", "members.read", "reports.read"}),
        "Courses, bookings, waiting lists, attendance, qualifications and junior consent.",
    ),
    "race_box": Hat(
        "Race Box",
        frozenset(),
        "Opens the website's race box (duties and race results). Carries no extra access in the office.",
    ),
}

# The committee posts an administrator can set on a member (Admin ▸ Members): club offices, not office permissions.
COMMITTEE_POSTS = ["Commodore", "Vice-Commodore", "Rear Commodore", "Secretary", "Treasurer", "Bosun", "Social", "Training", "Membership", "Other"]

PERMISSION_LABELS = {
    "members.read": "See members, contacts, enquiries and applications",
    "members.write": "Edit members, contacts, enquiries and applications",
    "applications.decide": "Approve or reject applications",
    "billing.run": "Run the membership renewal billing",
    "boats.read": "See boats, spaces and reports",
    "boats.write": "Edit boats, allocate and move spaces, send insurance reminders",
    "storage.bill": "Set storage tariffs and run storage billing",
    "training.read": "See courses, rosters and qualifications",
    "training.write": "Create courses, book people on, record attendance and outcomes, cancel courses",
    "finance.read": "See invoices, debtors and the bank",
    "finance.write": "Create invoices, record payments, match bank lines, send reminders",
    "finance.admin": "Void, write off, credit, reverse payments, change fees and invoice settings",
    "finance.lock": "Close a financial year (lock the books) and download the year-end pack",
    "reports.read": "See reports and exports",
    "events.read": "See events and ticket sales",
    "events.write": "Create events, sell tickets and manage attendees",
   }


def hats_for(db: Session, email: str) -> list[str]:
    row = db.scalar(select(UserRole).where(func.lower(UserRole.email) == email.lower()))
    if row is None or not row.extra_roles:
        return []
    return [h for h in (part.strip() for part in row.extra_roles.split(",")) if h in HATS]


def effective(db: Session, session: SessionRecord) -> set[str]:
    perms = set(BASE_PERMISSIONS.get(session.role.value, set()))
    for hat in hats_for(db, session.email):
        perms |= HATS[hat].perms
    return perms


def allows(perms: set[str], needed: str) -> bool:
    return "*" in perms or needed in perms


def is_operator(perms: set[str]) -> bool:
    return bool(perms)
