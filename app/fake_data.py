"""A made-up club for demonstrations and training. Every person, boat and amount here is invented.

``python -m app.cli demo-data`` fills an empty database. Run it on a new database only: it refuses if members already exist.
Phone numbers use the ranges Ofcom keeps for drama (07700 900xxx), and emails are at example.org, so nothing can reach a real person.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import applications, berths, boats, events, finance, mailings, membership, training
from app import seed as reference_data
from app.localtime import LONDON
from app.models import (
    Application,
    AuditLog,
    BankAccount,
    BankEntry,
    Contact,
    ContactRelation,
    Enquiry,
    Member,
    Membership,
    MembershipType,
    OutboundEmail,
    RecurringFee,
    Role,
    UserRole,
)

FIRST = ["Alice", "Ben", "Chloe", "Dan", "Ella", "Finn", "Grace", "Harry", "Isla", "Jack", "Kate", "Liam", "Maya", "Noah", "Olive", "Paul", "Ruby", "Sam",
         "Tara", "Will", "Amy", "Rob", "Jo", "Mark", "Helen", "Steve", "Claire", "Tom", "Lucy", "Pete", "Sarah", "Andy", "Jane", "Chris", "Emma", "Nick", "Lou", "Rich"]
LAST = ["Abbott", "Barker", "Carter", "Dawson", "Ellis", "Fowler", "Gibbs", "Holt", "Irwin", "Jarvis", "Keane", "Lowe", "Marsh", "Nash", "Oakley", "Price",
        "Quinn", "Reeve", "Shaw", "Tate", "Upton", "Vale", "Webb", "Yates", "Ashby", "Burton", "Cole", "Drake", "Field", "Grove", "Hale", "Kemp", "Lane", "Moss",
        "North", "Parr", "Rowe", "Stone", "Tozer", "Wren"]
STREETS = ["Harbour Road", "Sea View Terrace", "Pier Lane", "Lighthouse Close", "Anchor Way", "Beacon Hill", "Tide Mill Road", "Shell Bay Avenue", "Gull Crescent", "Jetty Street"]
CHILDREN = ["Poppy", "Alfie", "Freya", "Oscar", "Daisy", "Leo", "Evie", "Archie", "Ivy", "Teddy", "Rosie", "Max"]
# Dinghy classes with Portsmouth Yardstick numbers (approximate, for show).
CLASSES = [("Laser", 1100), ("Topper", 1365), ("RS Feva", 1240), ("Wayfarer", 1102), ("Streaker", 1128), ("Solo", 1142), ("Mirror", 1385), ("Laser 2000", 1114),
           ("RS200", 1047), ("Hartley 12", 1250), ("Contender", 969), ("420", 1105)]
BOAT_NAMES = ["Sea Spray", "Puffin", "Wild Goose", "Blue Moon", "Kestrel", "Seahorse", "Jolly Roger", "Swift", "Tern", "Breeze", "Spindrift", "Lively Lady",
              "Mischief", "Otter", "Dolphin", "Pippin", "Saltire", "Halcyon", "Curlew", "Flying Fish"]
WEIGHTS = {  # how common each membership is in the made-up club
    "Single Adult Sailing": 30, "Sailing Adult + Partner": 14, "Sailing Family 2A+2C": 9, "Sailing Family 2A+1C": 5, "Sailing Family 1A+2C": 4, "Sailing Family 1A+1C": 3,
    "Non-Sailing Adult": 12, "Non Sailing + 1C": 3, "Sailing Student Member": 4, "Honorary Member": 3, "Sailing Adult (6 Month)": 2,
}
STAFF = [("admin@example.org", Role.ADMIN, None), ("treasurer@example.org", Role.COMMITTEE, "treasurer"), ("secretary@example.org", Role.COMMITTEE, "secretary"),
         ("bosun@example.org", Role.COMMITTEE, "bosun"), ("training@example.org", Role.COMMITTEE, "training"), ("social@example.org", Role.COMMITTEE, "social")]


def _phone(rng: random.Random) -> str:
    return f"07700 900{rng.randint(0, 999):03d}"


def _first_name(rng: random.Random, firsts: list[str], last: str, taken: set[str]) -> str:
    """A first name nobody else in the club has with this surname, so the checks page does not fill with made-up duplicates."""
    first = rng.choice([f for f in firsts if f"{f} {last}" not in taken] or firsts)
    taken.add(f"{first} {last}")
    return first


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=LONDON).astimezone(UTC)


def build(db: Session, seed: int = 7, today: date | None = None) -> dict[str, int]:
    if db.scalar(select(func.count()).select_from(Member)):
        return {"refused": 1, "reason_members_exist": 1}
    rng = random.Random(seed)  # noqa: S311 - made-up data, not security
    today = today or date.today()
    year = today.year
    finance.save_settings(db, {
        "club_name": "Margate Yacht Club", "bank_details": "Account: Demo Sailing Club\nSort code: 00-00-00\nAccount number: 00000000",
        "payment_instructions": "Please pay by card using the button, by Direct Debit, or by bank transfer quoting the invoice number.",
    })
    for email, role, hats in STAFF:
        db.add(UserRole(email=email, role=role, extra_roles=hats))
    for name in WEIGHTS:
        membership.ensure_type(db, name)
    db.flush()
    reference_data.apply_known_fees(db)
    db.flush()
    fees = {t.name: t.fee_minor or 0 for t in db.scalars(select(MembershipType))}
    period = membership.create_period(db, year)
    counts = {"households": 0, "people": 0, "invoices": 0, "payments": 0, "boats": 0}

    bank = BankAccount(name="Club current account", kind="bank", opening_balance_minor=1254300)
    db.add(bank)
    db.flush()

    used_emails: set[str] = set()
    taken: set[str] = set()
    households: list[tuple[Contact, Member]] = []
    names = list(WEIGHTS)
    weights = [WEIGHTS[n] for n in names]
    for index in range(108):
        last = rng.choice(LAST)
        first = _first_name(rng, FIRST, last, taken)
        email = f"{first}.{last}.{index + 1}@example.org".lower()
        used_emails.add(email)
        plan = rng.choices(names, weights)[0]
        joined = date(rng.randint(year - 25, year), rng.randint(1, 12), 1)
        lapsed = index % 23 == 7
        contact = Contact(name=f"{first} {last}", email=email, phone=_phone(rng), address=f"{rng.randint(1, 120)} {rng.choice(STREETS)}\nMargate\nCT9 {rng.randint(1, 9)}ZZ",
                          birth_date=date(rng.randint(1950, 2000), rng.randint(1, 12), rng.randint(1, 28)))
        member = Member(name=contact.name, email=email, membership_type=plan, renewal_status="lapsed" if lapsed else "active", payment_status="paid", join_date=joined,
                        expiry_date=period.ends_on, active=not lapsed)
        db.add_all([contact, member])
        db.flush()
        member.membership_number = applications.next_membership_number(db)
        contact.member_id = member.id
        db.add(Membership(member_id=member.id, plan_name=plan, starts_on=period.starts_on, ends_on=period.ends_on, status="lapsed" if lapsed else "active"))
        partner = "Partner" in plan or "2A" in plan
        found = re.search(r"(\d)\s*C\b", plan)
        kids = int(found.group(1)) if found else 0
        if partner:
            p_first = _first_name(rng, FIRST, last, taken)
            p_email = f"{p_first}.{last}.{index + 1}p@example.org".lower()
            pc = Contact(name=f"{p_first} {last}", email=p_email, phone=_phone(rng), birth_date=date(rng.randint(1955, 2000), rng.randint(1, 12), rng.randint(1, 28)))
            pm = Member(name=pc.name, email=p_email, membership_type=plan, renewal_status=member.renewal_status, payment_status="paid", join_date=joined, expiry_date=period.ends_on, active=not lapsed)
            db.add_all([pc, pm])
            db.flush()
            pm.membership_number = applications.next_membership_number(db)
            pc.member_id = pm.id
            db.add(ContactRelation(from_contact_id=contact.id, to_contact_id=pc.id, relation_type="partner"))
            counts["people"] += 1
        for _ in range(kids):
            kc = Contact(name=f"{_first_name(rng, CHILDREN, last, taken)} {last}", birth_date=date(year - rng.randint(6, 16), rng.randint(1, 12), rng.randint(1, 28)))
            db.add(kc)
            db.flush()
            db.add(ContactRelation(from_contact_id=contact.id, to_contact_id=kc.id, relation_type="child"))
            counts["people"] += 1
        households.append((contact, member))
        counts["households"] += 1
        counts["people"] += 1
    db.flush()

    # This year's membership invoices: most paid, a few still owing.
    for contact, member in households:
        if not member.active or fees.get(member.membership_type, 0) <= 0:
            continue
        issued = date(year, 1, rng.randint(3, 20))
        invoice = finance.create_invoice(db, contact_id=contact.id, member_id=member.id, actor="demo", source="membership",
                                         lines=[{"description": f"{member.membership_type} membership {year}", "amount_minor": fees[member.membership_type], "nominal_code": "0101", "source_type": "membership", "source_id": f"{member.id}:{year}"}])
        finance.issue(db, invoice, "demo", issue_date=issued, terms=30)
        counts["invoices"] += 1
        _pay(db, rng, invoice, bank, issued, counts)

    # Boats and the dinghy park.
    designs = {name: pn for name, pn in CLASSES}
    spaces = [boats.create_resource(db, f"{row}{n}", "berth", "Dinghy park") for row in "ABC" for n in range(1, 21)]
    _racks = [boats.create_resource(db, f"Rack {n}", "rack", "Kayak racks") for n in range(1, 13)]
    boats.set_tariff(db, "berth", "Dinghy park", year, 9500)
    boats.set_tariff(db, "rack", "Kayak racks", year, 3000)
    owners = rng.sample([h for h in households if h[1].active], 62)
    for index, (contact, _member) in enumerate(owners):
        cls = rng.choice(list(designs))
        expiry = today + timedelta(days=rng.randint(-60, 300))
        boat = boats.save_boat(db, None, name=rng.choice(BOAT_NAMES), owner_contact_id=contact.id, design_name=cls, sail_number=str(rng.randint(100, 220000)),
                               boat_class=cls, portsmouth_number=designs[cls], insurance="Demo Marine Insurance", insurance_expiry=expiry, actor="demo")
        counts["boats"] += 1
        if index < len(spaces):
            boats.allocate(db, boat, spaces[index], date(year, 1, 1), date(year, 12, 31))
    for contact, _member in rng.sample(households, 6):
        db.add(RecurringFee(contact_id=contact.id, name="Kayak / SUP rack", amount_minor=3000, nominal_code="0102", note="By the slipway"))
    db.flush()
    report = boats.run_storage_billing(db, year, "demo")
    for invoice_id in report.invoices:
        stored = db.get(finance.Invoice, invoice_id)
        if stored is not None:
            counts["invoices"] += 1
            _pay(db, rng, stored, bank, stored.issue_date or today, counts)

    # People waiting for a space, new enquiries and applications.
    for contact, _member in rng.sample([h for h in households if h not in owners], 3):
        berths.join_waiting_list(db, contact, note="Would like a space in the dinghy park")
    for name, interest, message in (("Hannah Brook", "sailing", "We have just moved to Margate and would love to try sailing."),
                                    ("Owen Pike", "training", "Is there a Level 1 course in the spring?"),
                                    ("Megan Frost", "boat_storage", "Do you have room for a Topper?"),
                                    ("Ravi Patel", "social", "Can non-members come to the laying-up supper?")):
        applications.submit_enquiry(db, name=name, email=f"{name.split()[0].lower()}@example.org", interest=interest, message=message, verify=False, consent=True, actor="demo")
    single = db.scalar(select(MembershipType).where(MembershipType.name == "Single Adult Sailing"))
    for name in ("Gemma Hart", "Lewis Croft"):
        db.add(Application(name=name, email=f"{name.split()[0].lower()}.{name.split()[1].lower()}@example.org", phone=_phone(rng), address="7 Harbour Road\nMargate",
                           membership_type_id=single.id if single else None, status="submitted", experience="Sailed as a teenager, keen to start again.",
                           consent_at=datetime.now(UTC), rules_agreed_at=datetime.now(UTC)))

    # A few unmatched bank lines for the treasurer to sort out.
    for days, amount, details in ((2, 10800, "FASTER PAYMENT REF MEMBERSHIP"), (4, -45000, "DEMO MARINE INSURANCE ANNUAL"), (6, -3200, "CHANDLERY RESCUE BOAT PARTS"), (8, 8500, "CASH DONATIONS BOX")):
        db.add(BankEntry(account_id=bank.id, entry_date=today - timedelta(days=days), details=details, reference="", amount_minor=amount, status="unmatched", fingerprint=f"demo-{days}", source="demo"))

    # Events and courses.
    supper_day = today + timedelta(days=(5 - today.weekday()) % 7 + 28)
    supper = events.save_event(db, None, title="Laying-up Supper", start_at=_at(supper_day, 19), end_at=_at(supper_day, 23), location="Clubhouse", event_type="social",
                               bookable=True, tickets_limit=60, waiting_limit=10, published=True, description="Three courses and a raffle to close the season.")
    member_ticket = events.save_ticket_type(db, supper, None, name="Member", price_minor=1800, audience="members")
    events.save_ticket_type(db, supper, None, name="Guest", price_minor=2200, audience="public")
    events.save_ticket_type(db, supper, None, name="Child (under 12)", price_minor=0, audience="all", capacity=8)
    talk_day = today + timedelta(days=(3 - today.weekday()) % 7 + 14)
    talk = events.save_event(db, None, title="Open Meeting: Winter Maintenance Talk", start_at=_at(talk_day, 19, 30), location="Clubhouse", event_type="meeting",
                             bookable=True, tickets_limit=40, published=True, description="Free and open to everyone. Please book so we know how many chairs to put out.")
    events.save_ticket_type(db, talk, None, name="Place", price_minor=0, audience="all")
    db.flush()
    for contact, _member in [h for h in households if h[1].active][:6]:
        events.book(db, supper, member_ticket, contact, 2 if contact.id % 2 else 1, actor="demo")
    spring = date(year + 1, 4, 18)
    training.save_course(db, None, name="RYA Dinghy Level 1 & 2", starts_at=_at(spring, 9), ends_at=_at(spring + timedelta(days=1), 17), capacity=8, price_minor=19500)
    training.save_course(db, None, name="RYA Powerboat Level 2", starts_at=_at(spring + timedelta(days=14), 9), ends_at=_at(spring + timedelta(days=15), 17), capacity=4, price_minor=22500)
    mailings.seed_templates(db)

    db.flush()
    db.execute(delete(OutboundEmail))  # the demo starts with an empty email log
    db.execute(delete(AuditLog))
    db.flush()
    counts["enquiries"] = db.scalar(select(func.count()).select_from(Enquiry)) or 0
    return counts


def _pay(db: Session, rng: random.Random, invoice, bank: BankAccount, issued: date, counts: dict[str, int]) -> None:  # noqa: ANN001
    roll = rng.random()
    if roll < 0.12:
        return  # still owing
    paid_on = min(issued + timedelta(days=rng.randint(1, 40)), date.today())
    if roll < 0.16:
        amount = invoice.amount_minor // 2
    else:
        amount = invoice.amount_minor
    method = "card" if roll > 0.6 else "bank"
    entry_id = None
    if method == "bank":
        entry = BankEntry(account_id=bank.id, entry_date=paid_on, details=f"INV {invoice.reference}", reference=invoice.reference, amount_minor=amount, status="matched",
                          matched_invoice_id=invoice.id, fingerprint=f"demo-pay-{invoice.id}", source="demo")
        db.add(entry)
        db.flush()
        entry_id = entry.id
    payment = finance.record_payment(db, invoice, amount_minor=amount, method=method, reference=f"DEMO-{invoice.reference}", received_on=paid_on, actor="demo", bank_entry_id=entry_id)
    if entry_id:
        db.get(BankEntry, entry_id).payment_id = payment.id  # type: ignore[union-attr]
    counts["payments"] += 1
