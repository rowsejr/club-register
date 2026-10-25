# ruff: noqa: S608  (table and column names come from the database schema or fixed lists here, never from input)
"""Make a copy of a club database that is safe to show people: every personal detail is replaced.

What is kept: the shape of the club (who owes what, which invoices were paid, bank balances, boats and berths, courses and
bookings), so a demo behaves like the real thing. What is replaced: names, emails, phones, addresses, dates of birth (nudged),
boat names and sail numbers, free text, and anything that names a person. What is dropped: sessions, sign-in codes, sent mail,
audit logs, imported file contents, medical details and attachments.

The copy is then scanned against the original. If a real email, phone number or full name survives anywhere, the copy is deleted
and the run fails, so a leaky file is never left lying around. Nothing in this module prints a personal detail, only counts.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

FIRST_NAMES = (
    "Alex Sam Jamie Morgan Taylor Jordan Casey Robin Charlie Drew Harper Quinn Rowan Skyler Avery Blake Cameron Dana Ellis "
    "Finley Gray Hayden Indigo Jules Kai Lane Marlow Nico Oakley Parker Reese Sage Tatum Uma Vale Wren Yael Zane Ari "
    "Bryn Cleo Dale Eden Fern Glen Hollis Ivy Jude Kit Lark Merritt Noor Orla Piper Remy Sloane Teddy Wynn Xan Yuri Zora"
).split()
LAST_NAMES = (
    "Ashford Bellamy Calloway Dunmore Eastwood Fairbairn Garrick Halloran Ingram Jessop Kingsley Lockwood Merrow Northcott "
    "Oakshott Pemberton Quayle Radcliffe Sandford Thorne Underhill Vickery Whitlock Yardley Abbot Brindle Carver Denholm "
    "Elwood Fenwick Gresham Hartley Ivers Jarrow Kestrel Langley Mortimer Netherby Ormsby Prestwick Ravenhill Stanbury "
    "Tillotson Upton Verity Wetherby Yeomans Zeal Attwood Bexley Corrigan Draycott Everard Fosbury Gatling Hollin"
).split()
BOAT_NAMES = (
    "Sea Sprite;Blue Heron;Salt Spray;Lazy Gull;Tern;Kittiwake;Skylark;Puffin;Dabchick;Wavelet;Harbour Lights;Seaholly;"
    "Little Egret;Samphire;Driftwood;Ebb Tide;Spindrift;Cormorant;Gannet;Razorbill;Sanderling;Turnstone;Curlew;Plover;"
    "Marram;Thrift;Sea Kale;Rock Pool;Mermaid's Purse;Whelk;Cockle;Limpet;Sandpiper;Kestrel;Merlin;Peregrine;Osprey;"
    "Fulmar;Shearwater;Petrel;Skua;Guillemot;Eider;Brent;Teal;Wigeon;Pintail;Mallard;Shoveler;Garganey"
).split(";")
STREETS = "Harbour Road;Sea View;Marine Parade;Quay Lane;Beacon Close;Lighthouse Way;Mill Lane;Church Street;The Esplanade;Sandy Lane".split(
    ";"
)

# Words that appear in names but are not personal (so they are not scrubbed out of free text).
COMMON_WORDS = {
    "the", "and", "for", "mrs", "miss", "ltd", "club", "sailing", "yacht", "margate", "invoice", "payment", "bank", "transfer", "membership",
    "family", "junior", "youth", "senior", "adult", "annual", "storage", "dinghy", "training", "course", "refund", "credit", "note",
    "paypal", "cash", "cheque", "cheques", "standing", "order", "direct", "debit", "from", "with", "year", "season", "fee", "fees",
    "mr", "ms", "dr", "sir", "van", "von", "del", "mac", "mc", "ben", "ann", "joe", "sue", "tim", "tom", "jim", "dan", "ian", "ray",
}  # fmt: skip

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_NUMBER = re.compile(r"(?<!\d)(?:\+?\d[\d ()-]{7,}\d)(?!\d)")
_INVOICE_REF = re.compile(r"Invoice\s+(\d+)")


@dataclass
class Report:
    tables: dict[str, int] = field(default_factory=dict)
    leaks: dict[str, int] = field(
        default_factory=dict
    )  # table.column -> rows still holding a real email/phone/full name
    warnings: dict[str, int] = field(
        default_factory=dict
    )  # table.column -> rows mentioning a real surname or first name
    accounts: list[tuple[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.leaks


class Anonymiser:
    def __init__(self, conn: sqlite3.Connection, seed: int | None = None) -> None:
        self.db = conn
        self.rng = random.Random(seed)  # noqa: S311 - pseudonyms, not secrets
        self.first = FIRST_NAMES[:]
        self.last = LAST_NAMES[:]
        self.boats = BOAT_NAMES[:]
        self.tokens: set[str] = set()
        self.emails: set[str] = set()
        self.phones: set[str] = set()
        self.full_names: set[str] = set()
        self.names: dict[
            str, str
        ] = {}  # real name (lower) -> fake name, so one person is one pseudonym everywhere
        self._used: set[str] = set()
        self.report = Report()

    # ---------------------------------------------------------------- what is real (read before anything changes)

    def learn(self) -> None:
        queries = (
            "select name from contacts", "select name from members", "select name from applications", "select name from enquiries",
            "select name from application_people", "select emergency_name from participant_profiles", "select counterparty from bank_entries",
        )  # fmt: skip
        for q in queries:
            for (value,) in self._rows(q):
                self._remember_name(value)
        for q in ("select email from contacts", "select email from members", "select email from applications", "select email from enquiries",
                  "select email from application_people", "select email from user_roles"):  # fmt: skip
            for (value,) in self._rows(q):
                if value:
                    self.emails.add(value.strip().lower())
        for q in (
            "select phone from contacts",
            "select phone from applications",
            "select phone from enquiries",
            "select emergency_phone from participant_profiles",
        ):
            for (value,) in self._rows(q):
                digits = re.sub(r"\D", "", value or "")
                if len(digits) >= 9:
                    self.phones.add(digits[-9:])
        vocabulary: set[str] = set()
        for q in (
            "select name from membership_types",
            "select label from nominal_codes",
            "select name from training_courses",
            "select name from boat_designs",
            "select boat_class from boats",
            "select colour from boats",
            "select keel from boats",
            "select vessel_type from boats",
            "select event_type from events",
        ):
            for (value,) in self._rows(q):
                vocabulary |= set(re.findall(r"[a-z]{3,}", (value or "").lower()))
        self.tokens -= vocabulary | COMMON_WORDS
        # Fake names must never collide with a real token, or the leak scan could not tell them apart.
        self.first = [n for n in self.first if n.lower() not in self.tokens]
        self.last = [n for n in self.last if n.lower() not in self.tokens]
        self.boats = [
            n for n in self.boats if not (set(re.findall(r"[a-z]{3,}", n.lower())) & self.tokens)
        ]

    def _remember_name(self, value: str | None) -> None:
        if not value or "@" in value:
            return
        words = re.findall(r"[A-Za-z][A-Za-z'’-]+", value)
        if not words:
            return
        for word in words:
            if len(word) >= 3:
                self.tokens.add(word.lower())
        if len(words) >= 2:
            self.full_names.add(" ".join(words).lower())

    def _rows(self, sql: str) -> list[tuple]:
        try:
            return self.db.execute(sql).fetchall()
        except sqlite3.OperationalError:  # a table or column this database predates
            return []

    # ---------------------------------------------------------------- fake values

    def fake_name(self, real: str | None) -> str:
        key = " ".join((real or "").lower().split())
        if key in self.names:
            return self.names[key]
        for _ in range(500):
            name = f"{self.rng.choice(self.first)} {self.rng.choice(self.last)}"
            if name not in self._used:
                break
        else:  # pool exhausted: keep names unique with a numeral
            name = f"{name} {len(self._used)}"
        self._used.add(name)
        self.names[key] = name
        return name

    def fake_email(self, name: str, uid: int) -> str:
        return re.sub(r"[^a-z0-9.]", "", name.lower().replace(" ", ".")) + f".{uid}@example.org"

    def fake_phone(self) -> str:
        return f"07700 900{self.rng.randint(0, 999):03d}"  # Ofcom's reserved drama range: it can never ring a real person

    def fake_address(self) -> str:
        return f"{self.rng.randint(1, 98)} {self.rng.choice(STREETS)}, Margate, Kent"

    def fake_boat(self) -> str:
        return self.rng.choice(self.boats)

    def nudge(self, value: str | None) -> str | None:
        if not value:
            return value
        try:
            return (
                date.fromisoformat(value[:10]) + timedelta(days=self.rng.randint(-170, 170))
            ).isoformat()
        except ValueError:
            return None

    def scrub(self, text: str | None, real_name: str | None = None) -> str | None:
        """Free text with the named person swapped for their pseudonym and any other name, email or number removed."""
        if not text:
            return text
        if real_name:
            text = re.sub(re.escape(real_name), self.fake_name(real_name), text, flags=re.I)
        text = _EMAIL.sub("(email removed)", text)
        text = _LONG_NUMBER.sub("(number removed)", text)
        if self.tokens:
            pattern = re.compile(
                r"\b(?:"
                + "|".join(sorted(map(re.escape, self.tokens), key=len, reverse=True))
                + r")\b",
                re.I,
            )
            text = pattern.sub("…", text)
        return text

    # ---------------------------------------------------------------- the passes

    def run(self) -> None:
        self.learn()
        self._contacts_and_members()
        self._applications_and_enquiries()
        self._profiles()
        self._boats()
        self._finance()
        self._free_text()
        self._newer_tables()
        self._drop_traces()
        self._accounts()

    def _count(self, table: str, n: int) -> None:
        self.report.tables[table] = self.report.tables.get(table, 0) + n

    def _contacts_and_members(self) -> None:
        member_contact: dict[int, tuple[str, str]] = {}
        for cid, mid, name, email, phone, address, birth in self._rows(
            "select id, member_id, name, email, phone, address, birth_date from contacts"
        ):
            fake = self.fake_name(name)
            new_email = self.fake_email(fake, cid) if email else None
            self.db.execute(
                "update contacts set name=?, email=?, phone=?, address=?, birth_date=?, notes='' where id=?",
                (
                    fake,
                    new_email,
                    self.fake_phone() if phone else None,
                    self.fake_address() if address else None,
                    self.nudge(birth),
                    cid,
                ),
            )
            if mid:
                member_contact.setdefault(mid, (fake, new_email or ""))
            self._count("contacts", 1)
        if self._column("contacts", "phones_extra"):
            for cid, phones, addresses in self._rows(
                "select id, phones_extra, addresses_extra from contacts where phones_extra is not null or addresses_extra is not null"
            ):
                self.db.execute(
                    "update contacts set phones_extra=?, addresses_extra=? where id=?",
                    (
                        "\n".join(self.fake_phone() for _ in (phones or "").splitlines()) or None,
                        "\n".join(self.fake_address() for _ in (addresses or "").splitlines())
                        or None,
                        cid,
                    ),
                )
        for mid, name in self._rows("select id, name from members"):
            fake, email = member_contact.get(mid) or (self.fake_name(name), "")
            self.db.execute(
                "update members set name=?, email=? where id=?",
                (
                    fake,
                    (email or self.fake_email(fake, 100000 + mid))
                    if self._had_email(mid)
                    else None,
                    mid,
                ),
            )
            self._count("members", 1)

    def _had_email(self, member_id: int) -> bool:
        row = self.db.execute("select email from members where id=?", (member_id,)).fetchone()
        return bool(row and row[0])

    def _applications_and_enquiries(self) -> None:
        for aid, name, email, phone, address, birth in self._rows(
            "select id, name, email, phone, address, birth_date from applications"
        ):
            fake = self.fake_name(name)
            self.db.execute(
                "update applications set name=?, email=?, phone=?, address=?, birth_date=?, experience=null, notes=null, decision_note=null, decided_by=null, ip_hash=null, verify_token_hash=null where id=?",
                (
                    fake,
                    self.fake_email(fake, 200000 + aid) if email else None,
                    self.fake_phone() if phone else None,
                    self.fake_address() if address else None,
                    self.nudge(birth),
                    aid,
                ),
            )
            self._count("applications", 1)
        for pid, name, email, birth in self._rows(
            "select id, name, email, birth_date from application_people"
        ):
            fake = self.fake_name(name)
            self.db.execute(
                "update application_people set name=?, email=?, birth_date=? where id=?",
                (
                    fake,
                    self.fake_email(fake, 300000 + pid) if email else None,
                    self.nudge(birth),
                    pid,
                ),
            )
        for eid, name, email, phone in self._rows("select id, name, email, phone from enquiries"):
            fake = self.fake_name(name)
            self.db.execute(
                "update enquiries set name=?, email=?, phone=?, message=null, outcome=null, assigned_to=null, ip_hash=null, verify_token_hash=null where id=?",
                (
                    fake,
                    self.fake_email(fake, 400000 + eid) if email else None,
                    self.fake_phone() if phone else None,
                    eid,
                ),
            )
            self._count("enquiries", 1)
        if self._has("enquiry_notes"):
            self.db.execute("update enquiry_notes set body='(note removed)', author='demo'")

    def _profiles(self) -> None:
        if self._has("participant_profiles"):
            for cid, name in self._rows(
                "select contact_id, emergency_name from participant_profiles"
            ):
                self.db.execute(
                    "update participant_profiles set emergency_name=?, emergency_phone=?, medical=null, allergies=null where contact_id=?",
                    (
                        self.fake_name(name) if name else None,
                        self.fake_phone() if name else None,
                        cid,
                    ),
                )
            self._count("participant_profiles", 1)
        if self._has("qualifications"):
            self.db.execute("update qualifications set certificate_no=null")

    def _boats(self) -> None:
        for bid, sail, disc, reg, insurance in self._rows(
            "select id, sail_number, disc_number, nat_registration, insurance from boats"
        ):
            digits = "".join(
                str(self.rng.randint(0, 9)) if ch.isdigit() else ch for ch in (sail or "")
            )
            self.db.execute(
                "update boats set name=?, sail_number=?, disc_number=?, nat_registration=?, insurance=?, notes=null where id=?",
                (
                    f"{self.fake_boat()} {bid}",
                    digits,
                    f"D{bid:04d}" if disc else None,
                    f"REG{bid:05d}" if reg else None,
                    "Demo Mutual Insurance" if insurance else None,
                    bid,
                ),
            )
            self._count("boats", 1)

    def _finance(self) -> None:
        names = {
            cid: name for cid, name in self._rows("select id, name from contacts")
        }  # already pseudonymised
        for iid, notes in self._rows("select id, notes from invoices"):
            self.db.execute("update invoices set notes=? where id=?", (self.scrub(notes), iid))
        for lid, description in self._rows("select id, description from invoice_lines"):
            self.db.execute(
                "update invoice_lines set description=? where id=?", (self.scrub(description), lid)
            )
        payer_of_invoice = {
            iid: cid for iid, cid in self._rows("select id, contact_id from invoices")
        }
        invoice_by_ref = {ref: iid for iid, ref in self._rows("select id, reference from invoices")}
        for eid, details, counterparty, matched in self._rows(
            "select id, details, counterparty, matched_invoice_id from bank_entries"
        ):
            refs = _INVOICE_REF.findall(details or "")
            if refs:  # keep "Invoice N" so ledger cross-checks still work
                new_details = " | ".join(f"Invoice {n}" for n in refs)
                payer = payer_of_invoice.get(invoice_by_ref.get(refs[0], -1) or -1)
            else:
                new_details, payer = "Payment received", payer_of_invoice.get(matched or -1)
            pay_name = names.get(payer) if payer else None
            self.db.execute(
                "update bank_entries set reference=?, details=?, counterparty=?, fingerprint=? where id=?",
                (
                    f"REF{eid:06d}",
                    new_details,
                    pay_name or (self.fake_name(counterparty) if counterparty else None),
                    f"demo-{eid}",
                    eid,
                ),
            )
            self._count("bank_entries", 1)
        self.db.execute(
            "update payment_transactions set bank_reference='REF'||id, recorded_by='demo'"
        )

    def _free_text(self) -> None:
        for table, key, column in (
            ("events", "id", "notes"), ("events", "id", "title"), ("events", "id", "group_name"), ("event_notices", "id", "message"), ("event_wrap_ups", "id", "tally_notes"), ("event_wrap_ups", "id", "result_summary"),
            ("event_wrap_ups", "id", "actions"), ("orders", "id", "description"), ("training_bookings", "id", "note"), ("participation_records", "id", "notes"),
            ("training_courses", "id", "description"),
        ):  # fmt: skip
            if not self._has(table):
                continue
            for row_id, text in self._rows(f"select {key}, {column} from {table}"):
                self.db.execute(
                    f"update {table} set {column}=? where {key}=?", (self.scrub(text), row_id)
                )
        if self._has("events"):
            self.db.execute("update events set slug = 'event-' || id where slug is not null")
        for table in ("event_documents",):
            if self._has(table):
                self.db.execute("update event_documents set uri='https://example.org/document'")

    def _newer_tables(self) -> None:
        """Free text and names in the tables added since the first version."""
        if self._has("bank_allocations"):
            self.db.execute("update bank_allocations set payee=null, description=null")
        if self._has("bank_rules"):
            self.db.execute("update bank_rules set payee=null, pattern='(rule text removed)'")
        for table, column in (
            ("recurring_fees", "name"),
            ("recurring_fees", "note"),
            ("space_requests", "note"),
            ("invoices", "message"),
        ):
            if self._column(table, column):
                for row_id, text in self._rows(f"select id, {column} from {table}"):
                    self.db.execute(
                        f"update {table} set {column}=? where id=?", (self.scrub(text), row_id)
                    )

    def _drop_traces(self) -> None:
        for table in (
            "sessions", "auth_magic_links", "rate_hits", "outbound_emails", "communication_deliveries", "communications", "audit_logs", "attachments",
            "job_runs", "mailings", "saved_views", "online_payments", "payment_mandates", "webhook_events",
        ):  # fmt: skip
            if self._has(table):
                self.db.execute(f"delete from {table}")
        if self._has("import_batches"):
            for bid, report, totals in self._rows(
                "select id, report, file_totals from import_batches"
            ):
                try:
                    kept = {
                        k: v
                        for k, v in json.loads(report or "{}").items()
                        if k in {"rows", "counts", "error_total", "warning_total"}
                    }
                except ValueError:
                    kept = {}
                self.db.execute(
                    "update import_batches set content='', report=?, file_totals=?, created_by='demo' where id=?",
                    (json.dumps(kept), "{}", bid),
                )
        if self._has("app_settings"):
            self.db.execute(
                "delete from app_settings where key like '%secret%' or key like '%token%' or key like '%password%' or key like '%key%'"
            )  # the app makes a fresh form_secret on first use
            for key, value in self._rows("select key, value from app_settings"):
                if _EMAIL.search(value or "") or _LONG_NUMBER.search(value or ""):
                    self.db.execute("update app_settings set value='' where key=?", (key,))

    def _accounts(self) -> None:
        """Replace every sign-in with fictional ones, one per role, so the demo can show each person's view."""
        self.db.execute("delete from user_roles")
        member = self.db.execute(
            "select email from members where active=1 and email is not null order by id limit 1"
        ).fetchone()
        accounts = [
            ("admin@example.org", "Admin", None), ("treasurer@example.org", "Committee", "treasurer"), ("secretary@example.org", "Committee", "secretary"),
            ("bosun@example.org", "Committee", "bosun"), ("training@example.org", "Committee", "training"),
        ]  # fmt: skip
        if member:
            accounts.append((member[0], "Member", None))
        for email, role, hat in accounts:
            self.db.execute(
                "insert into user_roles (email, role, extra_roles, created_at) values (?, ?, ?, datetime('now'))",
                (email, role.upper(), hat),  # the column stores the enum's name
            )
            self.report.accounts.append((email, f"{role}{' + ' + hat if hat else ''}"))

    def _column(self, table: str, column: str) -> bool:
        return self._has(table) and any(
            row[1] == column for row in self.db.execute(f'pragma table_info("{table}")')
        )

    def _has(self, table: str) -> bool:
        return bool(
            self.db.execute(
                "select 1 from sqlite_master where type='table' and name=?", (table,)
            ).fetchone()
        )


def scan_for_leaks(conn: sqlite3.Connection, anonymiser: Anonymiser) -> Report:
    """Search every text column of the finished copy for anything that was real."""
    report = anonymiser.report
    surnames = {t for t in anonymiser.tokens if len(t) >= 5}
    for (table,) in conn.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
    ).fetchall():
        columns = [
            row[1]
            for row in conn.execute(f'pragma table_info("{table}")')
            if "CHAR" in (row[2] or "").upper()
            or "TEXT" in (row[2] or "").upper()
            or row[2] in {"JSON", ""}
        ]
        for column in columns:
            for (value,) in conn.execute(
                f'select "{column}" from "{table}" where "{column}" is not null and typeof("{column}") = \'text\''
            ):
                low = value.lower()
                hit = any(email in low for email in anonymiser.emails)
                if not hit and anonymiser.phones:
                    digits = re.sub(r"\D", "", value)
                    hit = any(p in digits for p in anonymiser.phones)
                if not hit:
                    hit = any(name in low for name in anonymiser.full_names)
                if hit:
                    report.leaks[f"{table}.{column}"] = report.leaks.get(f"{table}.{column}", 0) + 1
                elif surnames and any(re.search(rf"\b{re.escape(t)}\b", low) for t in surnames):
                    report.warnings[f"{table}.{column}"] = (
                        report.warnings.get(f"{table}.{column}", 0) + 1
                    )
    return report


def anonymise(source: Path, destination: Path, seed: int | None = None) -> Report:
    """Write an anonymised copy of ``source`` to ``destination`` (which must not exist). The copy is deleted if the scan finds a leak."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("the anonymised copy must be a different file from the original")
    if destination.exists():
        raise FileExistsError(
            f"{destination.name} already exists; delete it first so an old copy is never mistaken for this one"
        )
    origin = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
    copy = sqlite3.connect(destination)
    try:
        origin.backup(copy)
    finally:
        origin.close()
    try:
        copy.execute("pragma journal_mode=delete")
        worker = Anonymiser(copy, seed)
        worker.run()
        copy.commit()
        report = scan_for_leaks(copy, worker)
        copy.execute("vacuum")  # rewrite the file so deleted rows are not left behind in free pages
    finally:
        copy.close()
    if not report.clean:
        destination.unlink(missing_ok=True)
    return report
