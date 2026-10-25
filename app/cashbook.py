"""The cash book: every bank line ends up as an invoice payment, income, an expense, a transfer or bank charges.

Invoice payments are handled by `banking` (matching). This module covers everything else, so the books can be completed and
the treasurer's reports add up. A line can be split, rules categorise repeat items automatically, and any categorisation can
be undone while the financial year is open.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import finance
from app.models import AuditLog, BankAccount, BankAllocation, BankEntry, BankRule, NominalCode

KINDS = {"income": "Income (no invoice)", "expense": "Expense", "transfer": "Transfer between accounts", "charges": "Bank charges"}
CATEGORISABLE = ("unmatched", "ignored")

# Income codes a small sailing club needs on day one; the treasurer edits them under Finance > Nominal codes.
STARTER_INCOME_CODES = [
    ("0101", "Membership"),
    ("0102", "Boat park and storage"),
    ("0103", "Training"),
    ("0104", "Donations"),
    ("0106", "Club boat hire"),
    ("0107", "Merchandise"),
    ("0108", "Events and open meetings"),
]

# Expense codes a small sailing club needs on day one; the treasurer edits them under Finance > Nominal codes.
STARTER_EXPENSE_CODES = [
    ("0201", "Insurance"),
    ("0202", "Training costs"),
    ("0203", "Maintenance and repairs"),
    ("0204", "Events and social costs"),
    ("0205", "Rent, rates and utilities"),
    ("0206", "Administration and subscriptions"),
    ("0207", "Equipment and safety boats"),
    ("0208", "Fuel"),
]


class CashbookError(ValueError):
    """A rule of the cash book, safe to show to the user."""


@dataclass
class Part:
    kind: str
    amount_minor: int
    nominal_code: str | None = None
    description: str = ""
    payee: str = ""
    transfer_account_id: int | None = None


def seed_codes(db: Session) -> int:
    """Starter income and expense codes, each only when the club has none of that kind yet. Bank Fees is an expense."""
    added = 0
    if db.scalar(select(NominalCode.id).limit(1)) is None:  # a brand-new club
        for code, label in STARTER_INCOME_CODES:
            if db.scalar(select(NominalCode.id).where(NominalCode.code == code)) is None:
                db.add(NominalCode(code=code, label=label, kind="income", active=True))
                added += 1
        db.flush()
    bank_fees = db.scalar(select(NominalCode).where(NominalCode.label == "Bank Fees"))
    if bank_fees is not None and bank_fees.kind != "expense":
        bank_fees.kind = "expense"
    db.flush()
    if db.scalar(select(NominalCode.id).where(NominalCode.kind == "expense", NominalCode.label != "Bank Fees").limit(1)) is not None:
        return added
    for code, label in STARTER_EXPENSE_CODES:
        if db.scalar(select(NominalCode.id).where(NominalCode.code == code)) is None:
            db.add(NominalCode(code=code, label=label, kind="expense", active=True))
            added += 1
    db.flush()
    return added


def nominal_choices(db: Session, kind: str) -> list[NominalCode]:
    wanted = "expense" if kind in {"expense", "charges"} else "income"
    return list(db.scalars(select(NominalCode).where(func.coalesce(NominalCode.kind, "income") == wanted, NominalCode.active.is_not(False)).order_by(NominalCode.code)))


def _audit(db: Session, actor: str, action: str, entry: BankEntry, **details) -> None:
    db.add(AuditLog(actor_email=actor, actor_role="system" if actor == "system" else "user", action=action, target_type="bank_entry", target_id=str(entry.id), details=details))


def _assert_open(db: Session, entry: BankEntry) -> None:
    try:
        finance.assert_unlocked(db, entry.entry_date, "this bank line")
    except finance.FinanceError as exc:
        raise CashbookError(str(exc)) from exc


def categorise(db: Session, entry: BankEntry, parts: list[Part], actor: str, rule_id: int | None = None) -> list[BankAllocation]:
    _assert_open(db, entry)
    if entry.status not in CATEGORISABLE:
        raise CashbookError(f"A {entry.status} line cannot be categorised" + (" (un-match it first)" if entry.status == "matched" else ""))
    if not parts:
        raise CashbookError("Say what this line was")
    sign = 1 if entry.amount_minor >= 0 else -1
    total = 0
    for part in parts:
        if part.kind not in KINDS:
            raise CashbookError(f"Unknown kind '{part.kind}'")
        if part.amount_minor == 0 or (part.amount_minor > 0) != (sign > 0):
            raise CashbookError("Each part must be an amount in the same direction as the line" + (" (money in)" if sign > 0 else " (money out)"))
        if part.kind == "income" and sign < 0:
            raise CashbookError("Money going out cannot be income: use Expense, or a credit against the original invoice")
        if part.kind in {"expense", "charges"} and sign > 0:
            raise CashbookError("Money coming in is not an expense: use Income, or Transfer if it came from another club account")
        if part.kind == "transfer":
            if not part.transfer_account_id or part.transfer_account_id == entry.account_id or db.get(BankAccount, part.transfer_account_id) is None:
                raise CashbookError("Choose the other account for a transfer")
        elif not part.nominal_code:
            raise CashbookError("Choose a nominal code so it is reported in the right place")
        elif db.scalar(select(NominalCode.id).where(NominalCode.code == part.nominal_code)) is None:
            raise CashbookError(f"Unknown nominal code {part.nominal_code}")
        total += part.amount_minor
    if total != entry.amount_minor:
        raise CashbookError(f"The parts add up to {total / 100:,.2f} but the line is {entry.amount_minor / 100:,.2f}")
    made = []
    for part in parts:
        row = BankAllocation(
            entry_id=entry.id, kind=part.kind, nominal_code=None if part.kind == "transfer" else part.nominal_code, amount_minor=part.amount_minor,
            description=part.description.strip() or None, payee=part.payee.strip() or None, transfer_account_id=part.transfer_account_id if part.kind == "transfer" else None, rule_id=rule_id,
        )
        db.add(row)
        made.append(row)
    entry.status = "categorised"
    db.flush()
    _audit(db, actor, "bank.categorise", entry, parts=[(p.kind, p.nominal_code, p.amount_minor) for p in parts], rule=rule_id)
    return made


def uncategorise(db: Session, entry: BankEntry, actor: str) -> None:
    _assert_open(db, entry)
    if entry.status != "categorised":
        raise CashbookError("This line is not categorised")
    for row in db.scalars(select(BankAllocation).where(BankAllocation.entry_id == entry.id)).all():
        db.delete(row)
    entry.status = "unmatched"
    db.flush()
    _audit(db, actor, "bank.uncategorise", entry)


def allocations_for(db: Session, entry_id: int) -> list[BankAllocation]:
    return list(db.scalars(select(BankAllocation).where(BankAllocation.entry_id == entry_id).order_by(BankAllocation.id)))


# --------------------------------------------------------------------------- rules


def _haystack(entry: BankEntry) -> str:
    return " ".join(filter(None, [entry.reference, entry.details, entry.counterparty])).lower()


def save_rule(db: Session, *, pattern: str, kind: str, nominal_code: str | None = None, account_id: int | None = None, direction: str = "any", transfer_account_id: int | None = None, payee: str = "") -> BankRule:
    pattern = " ".join(pattern.split())
    if len(pattern) < 3:
        raise CashbookError("A rule needs at least three characters of text to look for")
    if kind not in KINDS:
        raise CashbookError(f"Unknown kind '{kind}'")
    if kind == "transfer" and not transfer_account_id:
        raise CashbookError("Choose the other account for a transfer rule")
    if kind != "transfer" and not nominal_code:
        raise CashbookError("Choose a nominal code for the rule")
    if direction not in {"in", "out", "any"}:
        direction = "any"
    rule = db.scalar(select(BankRule).where(func.lower(BankRule.pattern) == pattern.lower(), BankRule.account_id == account_id, BankRule.direction == direction))
    if rule is None:
        rule = BankRule(pattern=pattern)
        db.add(rule)
    rule.account_id, rule.direction, rule.kind, rule.nominal_code = account_id, direction, kind, nominal_code
    rule.transfer_account_id, rule.payee, rule.active = transfer_account_id, payee.strip() or None, True
    db.flush()
    return rule


def _applies(rule: BankRule, entry: BankEntry) -> bool:
    if not rule.active or (rule.account_id and rule.account_id != entry.account_id):
        return False
    if rule.direction == "in" and entry.amount_minor <= 0 or rule.direction == "out" and entry.amount_minor >= 0:
        return False
    if (rule.kind == "income" and entry.amount_minor < 0) or (rule.kind in {"expense", "charges"} and entry.amount_minor > 0):
        return False
    return rule.pattern.lower() in _haystack(entry)


def apply_rules(db: Session, actor: str = "system", account_id: int | None = None) -> int:
    """Categorise every waiting line that a rule recognises. Returns how many were done."""
    rules = db.scalars(select(BankRule).where(BankRule.active.is_(True)).order_by(func.length(BankRule.pattern).desc(), BankRule.id)).all()
    if not rules:
        return 0
    stmt = select(BankEntry).where(BankEntry.status == "unmatched")
    if account_id:
        stmt = stmt.where(BankEntry.account_id == account_id)
    done = 0
    lock = finance.locked_until(db)
    for entry in db.scalars(stmt).all():
        if lock is not None and entry.entry_date <= lock:
            continue  # a closed period is not touched by rules
        rule = next((r for r in rules if _applies(r, entry)), None)
        if rule is None:
            continue
        categorise(db, entry, [Part(rule.kind, entry.amount_minor, rule.nominal_code, entry.details[:200] if entry.details else "", rule.payee or "", rule.transfer_account_id)], actor, rule_id=rule.id)
        rule.times_used += 1
        done += 1
    db.flush()
    return done


def suggest_pattern(entry: BankEntry) -> str:
    """The text a rule would most usefully look for: the payee if the bank gave one, else the first words of the details."""
    text = (entry.counterparty or entry.details or entry.reference or "").strip()
    words = [w for w in text.replace("*", " ").split() if not w.isdigit()]
    return " ".join(words[:3])[:60]
