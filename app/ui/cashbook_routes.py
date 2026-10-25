"""Treasurer screens for the cash book: categorise bank lines, bank rules, nominal codes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cashbook, finance
from app.db import get_db
from app.models import BankAccount, BankEntry, BankRule, NominalCode, SessionRecord
from app.ui.finance_routes import _back, _entry, _money
from app.ui.routes import UiError, _audit, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)


def _nominal_groups(db: Session, entry: BankEntry) -> list[tuple[str, list[NominalCode]]]:
    incoming = entry.amount_minor >= 0
    groups = [("Income", cashbook.nominal_choices(db, "income")), ("Expenses", cashbook.nominal_choices(db, "expense"))]
    return groups if incoming else groups[::-1]


@router.get("/app/finance/bank/entries/{entry_id}/categorise", response_class=HTMLResponse)
def categorise_form(entry_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    others = db.scalars(select(BankAccount).where(BankAccount.id != entry.account_id).order_by(BankAccount.name)).all()
    return templates.TemplateResponse(
        request,
        "bank_categorise.html",
        _ctx(session, "finance", entry=entry, account=db.get(BankAccount, entry.account_id), groups=_nominal_groups(db, entry), others=others, kinds=cashbook.KINDS, allocations=cashbook.allocations_for(db, entry.id), pattern=cashbook.suggest_pattern(entry), msg=msg, err=err, sub="bank"),
    )


@router.post("/app/finance/bank/entries/{entry_id}/categorise")
async def categorise_save(entry_id: int, request: Request, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    form = await request.form()
    path = f"/app/finance/bank/entries/{entry_id}/categorise"
    try:
        parts: list[cashbook.Part] = []
        for n in ("1", "2", "3"):
            kind = str(form.get(f"kind{n}", "")).strip()
            if not kind:
                continue
            amount_text = str(form.get(f"amount{n}", "")).strip()
            minor = _money(amount_text) if amount_text else entry.amount_minor
            if entry.amount_minor < 0 and minor > 0:
                minor = -minor  # people type spending as a positive number
            transfer = str(form.get(f"transfer{n}", ""))
            parts.append(cashbook.Part(kind, minor, str(form.get(f"nominal{n}", "")) or None, str(form.get(f"description{n}", "")), str(form.get(f"payee{n}", "")), int(transfer) if transfer.isdigit() else None))
        if len(parts) == 1 and not str(form.get("amount1", "")).strip():
            parts[0].amount_minor = entry.amount_minor
        cashbook.categorise(db, entry, parts, session.email)
        if form.get("make_rule") and len(parts) == 1:
            part = parts[0]
            cashbook.save_rule(
                db, pattern=str(form.get("pattern", "")), kind=part.kind, nominal_code=part.nominal_code, account_id=entry.account_id if form.get("rule_this_account") else None,
                direction="in" if entry.amount_minor > 0 else "out", transfer_account_id=part.transfer_account_id, payee=part.payee,
            )
            applied = cashbook.apply_rules(db, session.email)
            note = f"Saved as a rule: {applied} other waiting line(s) categorised too"
        else:
            note = "Categorised"
        _audit(db, session, "bank.categorise", "bank_entry", entry.id)
        db.commit()
    except (cashbook.CashbookError, finance.FinanceError) as exc:
        db.rollback()
        return _back(path, err=str(exc))
    return _back(f"/app/finance/bank/entries/{entry_id}", msg=note)


@router.post("/app/finance/bank/entries/{entry_id}/uncategorise")
def uncategorise(entry_id: int, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    try:
        cashbook.uncategorise(db, entry, session.email)
        db.commit()
    except cashbook.CashbookError as exc:
        db.rollback()
        return _back(f"/app/finance/bank/entries/{entry_id}", err=str(exc))
    return _back(f"/app/finance/bank/entries/{entry_id}", msg="Back in the queue")


# --------------------------------------------------------------------------- rules


@router.get("/app/finance/bank/rules", response_class=HTMLResponse)
def rules_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    rules = db.scalars(select(BankRule).order_by(BankRule.active.desc(), BankRule.pattern)).all()
    accounts = db.scalars(select(BankAccount).order_by(BankAccount.name)).all()
    names = {a.id: a.name for a in accounts}
    return templates.TemplateResponse(
        request,
        "bank_rules.html",
        _ctx(session, "finance", rules=rules, accounts=accounts, names=names, income=cashbook.nominal_choices(db, "income"), expense=cashbook.nominal_choices(db, "expense"), kinds=cashbook.KINDS, msg=msg, err=err, sub="bank"),
    )


@router.post("/app/finance/bank/rules")
def rules_save(
    pattern: str = Form(""),
    kind: str = Form("expense"),
    nominal: str = Form(""),
    account: str = Form(""),
    direction: str = Form("any"),
    transfer: str = Form(""),
    payee: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="finance.write")),
    db: Session = Depends(get_db),
) -> Response:
    try:
        rule = cashbook.save_rule(db, pattern=pattern, kind=kind, nominal_code=nominal or None, account_id=int(account) if account.isdigit() else None, direction=direction, transfer_account_id=int(transfer) if transfer.isdigit() else None, payee=payee)
        applied = cashbook.apply_rules(db, session.email)
        _audit(db, session, "bank.rule", "bank_rule", rule.id, pattern=rule.pattern)
        db.commit()
    except cashbook.CashbookError as exc:
        db.rollback()
        return _back("/app/finance/bank/rules", err=str(exc))
    return _back("/app/finance/bank/rules", msg=f"Rule saved; {applied} waiting line(s) categorised")


@router.post("/app/finance/bank/rules/{rule_id}/{action}")
def rule_action(rule_id: int, action: str, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    rule = db.get(BankRule, rule_id)
    if rule is None or action not in {"toggle", "delete"}:
        raise UiError(404, "Rule not found")
    if action == "delete":
        db.delete(rule)
    else:
        rule.active = not rule.active
    _audit(db, session, f"bank.rule_{action}", "bank_rule", rule_id)
    db.commit()
    return _back("/app/finance/bank/rules", msg="Done")


# --------------------------------------------------------------------------- nominal codes


@router.get("/app/finance/nominals", response_class=HTMLResponse)
def nominals_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    codes = db.scalars(select(NominalCode).order_by(NominalCode.kind, NominalCode.code)).all()
    return templates.TemplateResponse(request, "finance_nominals.html", _ctx(session, "finance", codes=codes, msg=msg, err=err, sub="nominals"))


@router.post("/app/finance/nominals")
def nominals_save(code: str = Form(""), label: str = Form(""), kind: str = Form("income"), active: str = Form("1"), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    code, label = code.strip(), " ".join(label.split())
    if not code.isdigit() or len(code) > 6 or not label or kind not in {"income", "expense"}:
        return _back("/app/finance/nominals", err="A code is up to six digits, with a label and a kind")
    row = db.scalar(select(NominalCode).where(NominalCode.code == code))
    if row is None:
        row = NominalCode(code=code, label=label)
        db.add(row)
    row.label, row.kind, row.active = label, kind, active == "1"
    _audit(db, session, "finance.nominal", "nominal_code", code, label=label, kind=kind)
    db.commit()
    return _back("/app/finance/nominals", msg=f"Saved {code} {label}")

