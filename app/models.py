from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Role(StrEnum):
    ADMIN = "Admin"
    COMMITTEE = "Committee"
    MEMBER = "Member"


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    membership_type: Mapped[str] = mapped_column(String(50), default="standard")
    renewal_status: Mapped[str] = mapped_column(String(50), default="active")
    payment_status: Mapped[str] = mapped_column(String(50), default="paid")
    join_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    membership_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    phones_extra: Mapped[str | None] = mapped_column(Text, nullable=True)  # further numbers, one per line
    addresses_extra: Mapped[str | None] = mapped_column(Text, nullable=True)  # further addresses, one per line
    gender: Mapped[str | None] = mapped_column(String(30), nullable=True)  # female | male | other | not_given (for the RYA return)
    emergency_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    emergency_phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    photo_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = not asked yet
    directory_consent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # may other members see their phone and email
    consents_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consents_by: Mapped[str | None] = mapped_column(String(255), nullable=True)  # who answered (a parent, for a junior)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class ContactList(Base):
    __tablename__ = "contact_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    list_type: Mapped[str] = mapped_column(String(40), default="static")


class ListMembership(Base):
    __tablename__ = "list_memberships"
    __table_args__ = (UniqueConstraint("contact_id", "list_id", name="uq_list_membership"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    list_id: Mapped[int] = mapped_column(ForeignKey("contact_lists.id", ondelete="CASCADE"), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class ContactRelation(Base):
    __tablename__ = "contact_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    to_contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class ParticipationRecord(Base):
    __tablename__ = "participation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"), nullable=True)
    participation_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active")
    notes: Mapped[str] = mapped_column(Text, default="")


class Membership(Base):
    __tablename__ = "memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    plan_name: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    member: Mapped[Member] = relationship()


class MembershipPeriod(Base):
    __tablename__ = "membership_periods"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MembershipRenewal(Base):
    __tablename__ = "membership_renewals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    period_id: Mapped[int] = mapped_column(ForeignKey("membership_periods.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), default="draft")
    lock_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    suggestion_notes: Mapped[str] = mapped_column(Text, default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MembershipCharge(Base):
    __tablename__ = "membership_charges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    renewal_id: Mapped[int] = mapped_column(ForeignKey("membership_renewals.id", ondelete="CASCADE"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tickets_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    waiting_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ticket_access: Mapped[str | None] = mapped_column(String(40), nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    bookable: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)  # tickets can be bought
    sales_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)  # where ticket income is reported




class EventDocument(Base):
    __tablename__ = "event_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)


class EventNotice(Base):
    __tablename__ = "event_noticeboard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class EventTicket(Base):
    __tablename__ = "event_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="booked")  # held | booked | waiting | cancelled
    ticket_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    holder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # the person this place is for, when not the buyer
    booked_by_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=True)
    hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_in: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)


class TicketType(Base):
    """A kind of ticket for an event: member price, public price, children's price."""

    __tablename__ = "ticket_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    price_minor: Mapped[int] = mapped_column(Integer, default=0)
    audience: Mapped[str] = mapped_column(String(10), default="all")  # members | public | all
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # places of this kind; None = only the event's limit applies
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class EventWrapUp(Base):
    __tablename__ = "event_wrapups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=False, unique=True)
    tally_notes: Mapped[str] = mapped_column(Text, default="")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[str] = mapped_column(Text, default="")


class BoatDesign(Base):
    __tablename__ = "boat_designs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(255), default="")
    rig_type: Mapped[str] = mapped_column(String(120), default="")
    portsmouth_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Boat(Base):
    __tablename__ = "boats"
    __table_args__ = (UniqueConstraint("owner_member_id", "name", "sail_number", name="uq_boat_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), nullable=True)
    owner_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    design_id: Mapped[int | None] = mapped_column(ForeignKey("boat_designs.id", ondelete="SET NULL"), nullable=True)
    variant: Mapped[str] = mapped_column(String(120), default="")
    boat_class: Mapped[str] = mapped_column(String(120), default="")
    sail_number: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="active")
    vessel_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    keel: Mapped[str | None] = mapped_column(String(80), nullable=True)
    construction: Mapped[str | None] = mapped_column(String(80), nullable=True)
    colour: Mapped[str | None] = mapped_column(String(80), nullable=True)
    loa_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    beam_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    draught_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    portsmouth_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    insurance: Mapped[str | None] = mapped_column(String(255), nullable=True)
    insurance_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    disc_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    nat_registration: Mapped[str | None] = mapped_column(String(60), nullable=True)
    mast_height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_club_boat: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)


class BoatCrewLink(Base):
    __tablename__ = "boat_crew_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(80), default="crew")
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class StorageResource(Base):
    __tablename__ = "storage_resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_group: Mapped[str | None] = mapped_column(String(120), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)
    active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)


class StorageAllocation(Base):
    __tablename__ = "storage_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id", ondelete="RESTRICT"), nullable=False)
    resource_id: Mapped[int] = mapped_column(ForeignKey("storage_resources.id", ondelete="RESTRICT"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    seasonal: Mapped[bool] = mapped_column(Boolean, default=True)
    billing_flag: Mapped[str] = mapped_column(String(80), default="standard")
    fee_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)  # overrides the tariff for this boat
    status: Mapped[str | None] = mapped_column(String(20), default="active", nullable=True)  # requested | active | ended
    ended_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confirmed_year: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the season the owner said they are keeping the space for




class TrainingCourse(Base):
    __tablename__ = "training_courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_minor: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    nominal_code: Mapped[str | None] = mapped_column(String(10), default="0103", nullable=True)
    min_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    venue: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), default="open", nullable=True)  # draft | open | closed | cancelled | complete
    waitlist_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cancel_cutoff_days: Mapped[int | None] = mapped_column(Integer, default=7, nullable=True)
    qualification_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instructor_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrainingBooking(Base):
    __tablename__ = "training_bookings"
    __table_args__ = (UniqueConstraint("course_id", "member_id", name="uq_booking_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("training_courses.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the attendee (may not be a member, e.g. a child)
    booked_by_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # who books and pays (a parent, for a child)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    offered_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id", ondelete="SET NULL"), nullable=True)
    reference: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GBP")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gateway: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(20), default="invoice", nullable=True)  # invoice | credit_note
    credit_for_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_rate_bp: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)  # basis points; 0 while the club is not VAT registered
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # shown to the payer on the invoice and PDF (notes are internal)
    tags: Mapped[str | None] = mapped_column(String(255), nullable=True)  # comma-separated
    terms_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # payment terms chosen for this invoice
    send_reminders: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    send_receipt: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)


class FeeItem(Base):
    """Something the club charges for: pick it on an invoice and the nominal code and price fill in."""

    __tablename__ = "fee_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(40), default="club")  # membership | storage | training | club | other
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unit_price_minor: Mapped[int] = mapped_column(Integer, default=0)
    unit: Mapped[str | None] = mapped_column(String(40), default="each", nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[str] = mapped_column(String(80), default="bank")
    bank_reference: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    recorded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)








class AuthMagicLink(Base):
    __tablename__ = "auth_magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.MEMBER)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_email: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(80), nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class UserRole(Base):
    """Persistent sign-in role for an email address (operators and members)."""

    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    extra_roles: Mapped[str | None] = mapped_column(String(255), nullable=True)  # comma-separated hats: treasurer, secretary
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Qualification(Base):
    __tablename__ = "qualifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str | None] = mapped_column(String(80), nullable=True)
    issued_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_booking_id: Mapped[int | None] = mapped_column(Integer, nullable=True)




class NominalCode(Base):
    __tablename__ = "nominal_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(10), default="income", nullable=True)  # income | expense
    active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)


class MembershipType(Base):
    __tablename__ = "membership_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    fee_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nominal_code: Mapped[str | None] = mapped_column(String(10), default="0101", nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    online_joinable: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    active: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    joining_fee_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    household_size: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)  # people covered: 1 single, 2 couple, 3+ family


class NumberSequence(Base):
    __tablename__ = "number_sequences"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="bank")  # bank | paypal | gocardless | stripe | sumup | cash
    opening_balance_minor: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    closing_balance_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the closing figure last recorded


class BankEntry(Base):
    """One line on a bank/PayPal/GoCardless statement. ``status`` is unmatched | matched | ignored | historical."""

    __tablename__ = "bank_entries"
    __table_args__ = (UniqueConstraint("account_id", "fingerprint", name="uq_bank_entry"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    reference: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    counterparty: Mapped[str | None] = mapped_column(String(255), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="unmatched")
    matched_invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(40), nullable=True)  # the entry's id in the system it came from


class OutboundEmail(Base):
    """Every email the office sends (or, in sandbox mode, would send)."""

    __tablename__ = "outbound_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    attachment_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued | sent | simulated | failed
    provider: Mapped[str] = mapped_column(String(40), default="sandbox")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    related_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    attempts: Mapped[int | None] = mapped_column(Integer, default=1, nullable=True)  # delivery attempts so far
    reply_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Enquiry(Base):
    """Someone asking about the club. Kept (never deleted) so the history of who asked what survives."""

    __tablename__ = "enquiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    interest: Mapped[str | None] = mapped_column(String(80), nullable=True)  # sailing | training | boat_storage | social | other
    source: Mapped[str | None] = mapped_column(String(20), default="web", nullable=True)  # web | email | phone | walk_in | imported
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="new")  # unverified | new | contacted | converted | closed | spam
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    assigned_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verify_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_contact: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnquiryNote(Base):
    __tablename__ = "enquiry_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enquiry_id: Mapped[int] = mapped_column(ForeignKey("enquiries.id", ondelete="CASCADE"), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Application(Base):
    """A request to join. Becomes a Member, a pending membership and a first invoice when approved."""

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enquiry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    membership_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="submitted")  # unverified | submitted | info_requested | approved | completed | rejected | withdrawn
    source: Mapped[str | None] = mapped_column(String(20), default="web", nullable=True)  # web | staff | enquiry
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rules_agreed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_marketing: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    verify_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ApplicationPerson(Base):
    """Partner or child covered by the same membership."""

    __tablename__ = "application_people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="partner")  # partner | child
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class RateHit(Base):
    """Counters behind the public-form rate limits (kept in the database so they survive restarts and multiple workers)."""

    __tablename__ = "rate_hits"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)


class StorageTariff(Base):
    """What a space costs for a season. The most specific match wins: type + group, then type alone."""

    __tablename__ = "storage_tariffs"
    __table_args__ = (UniqueConstraint("resource_type", "resource_group", "season_year", name="uq_tariff"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_group: Mapped[str] = mapped_column(String(120), default="")  # "" = every group of that type
    season_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    nominal_code: Mapped[str | None] = mapped_column(String(10), default="0102", nullable=True)
    prorata: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)  # charge by the month for spaces that start mid-season


class QualificationType(Base):
    __tablename__ = "qualification_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    authority: Mapped[str | None] = mapped_column(String(120), nullable=True)
    validity_months: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = never expires
    is_instructor: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)


class ParticipantProfile(Base):
    """Safety and consent details for someone who takes part in training (essential for juniors)."""

    __tablename__ = "participant_profiles"

    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True)
    emergency_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    emergency_phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    medical: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    swim_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    photo_ok: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    consent_signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    signed_by_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BankAllocation(Base):
    """What a bank line that is not an invoice payment was: income, an expense, a transfer between accounts or bank charges.

    A line can be split across several allocations; they must add up to the line's amount (same sign as the line).
    """

    __tablename__ = "bank_allocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("bank_entries.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)  # income | expense | transfer | charges
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payee: Mapped[str | None] = mapped_column(String(160), nullable=True)
    transfer_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="SET NULL"), nullable=True)
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BankRule(Base):
    """"A line containing this text is always that": applied to new bank lines so repeat items categorise themselves."""

    __tablename__ = "bank_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern: Mapped[str] = mapped_column(String(160), nullable=False)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="CASCADE"), nullable=True)  # None = any account
    direction: Mapped[str] = mapped_column(String(6), default="any")  # in | out | any
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    transfer_account_id: Mapped[int | None] = mapped_column(ForeignKey("bank_accounts.id", ondelete="SET NULL"), nullable=True)
    payee: Mapped[str | None] = mapped_column(String(160), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0)


class RecurringFee(Base):
    """A charge that repeats every season for a person or a boat (an extra): a locker, a kayak rack, a key."""

    __tablename__ = "recurring_fees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[float | None] = mapped_column(Float, default=1.0, nullable=True)
    nominal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legacy_id: Mapped[str | None] = mapped_column(String(60), nullable=True)


class SpaceRequest(Base):
    """Someone waiting for a boat park space (or other storage), served first come first served."""

    __tablename__ = "space_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id", ondelete="SET NULL"), nullable=True)
    resource_type: Mapped[str] = mapped_column(String(80), default="berth")
    resource_group: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="waiting")  # waiting | offered | allocated | withdrawn
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContactTag(Base):
    """A short label on a contact ("junior", "committee 2026", "volunteer"), for finding groups of people."""

    __tablename__ = "contact_tags"
    __table_args__ = (UniqueConstraint("contact_id", "tag", name="uq_contact_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    tag: Mapped[str] = mapped_column(String(60), nullable=False)


class SavedView(Base):
    """A filter on Members or Contacts worth keeping ("families not renewed", "juniors"), shared by everyone who can see the page."""

    __tablename__ = "saved_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(12), nullable=False)  # members | contacts
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    query: Mapped[str] = mapped_column(String(500), nullable=False)  # the page's query string
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class MailTemplate(Base):
    __tablename__ = "mail_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class Mailing(Base):
    """An email to a group of people. The audience is stored as the filter used, so a draft can be checked and sent later."""

    __tablename__ = "mailings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | sent
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[list] = mapped_column(JSON, default=list)  # who was left out, and why


class JobRun(Base):
    """One run of a scheduled job: when, whether it was a rehearsal, and what it did."""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job: Mapped[str] = mapped_column(String(60), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | ok | failed
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    run_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # the day the run was for (a test can pass a fake one)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    triggered_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class PaymentMandate(Base):
    """A Direct Debit set up by a member (GoCardless): later invoices can be collected from it."""

    __tablename__ = "payment_mandates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), default="gocardless")
    mandate_id: Mapped[str] = mapped_column(String(80), nullable=False)
    customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | active | cancelled | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class OnlinePayment(Base):
    """An attempt to collect an invoice online. The invoice is only marked paid when the provider confirms, never on the customer's say-so."""

    __tablename__ = "online_payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # gocardless | stripe | sandbox
    method: Mapped[str] = mapped_column(String(20), nullable=False)  # direct_debit | card
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # the provider's payment / session id
    provider_payment_id: Mapped[str | None] = mapped_column(String(120), nullable=True)  # Stripe's id for the money itself, to match refunds
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | confirmed | failed | cancelled
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure: Mapped[str | None] = mapped_column(String(255), nullable=True)


class WebhookEvent(Base):
    """Provider notifications already handled, so a replayed webhook never records money twice."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    outcome: Mapped[str | None] = mapped_column(String(120), nullable=True)


class MemberRequest(Base):
    """Something a member asks for from their own page: change of membership, a boat park space, a new boat, a word with the Bosun."""

    __tablename__ = "member_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    member_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # change_membership | boat_space | change_boat | bosun | other
    details: Mapped[str] = mapped_column(Text, default="")
    boat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | done | declined
    handled_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(500), nullable=True)


class ChaseNote(Base):
    """A note on the chase list about someone who owes money: "rang 3 March, paying on Friday"."""

    __tablename__ = "chase_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
