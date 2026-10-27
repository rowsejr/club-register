"""Treasurer screens: invoices, payments, debtors, bank matching, membership billing run, exports, settings."""

from __future__ import annotations

import math
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import (
    audit,
    banking,
    cashbook,
    catalogue,
    finance,
    invoice_comms,
    invoice_query,
    membership,
)
from app.csvutil import RowError, parse_minor
from app.db import get_db
from app.invoice_query import STATUS_FILTERS, InvoiceFilter
from app.models import (
    BankAccount,
    BankEntry,
    Contact,
    FeeItem,
    Invoice,
    InvoiceLine,
    Member,
    MembershipPeriod,
    MembershipRenewal,
    MembershipType,
    NominalCode,
    OutboundEmail,
    PaymentTransaction,
    SessionRecord,
)
from app.ui.routes import (
    PAGE_SIZE,
    UiError,
    _audit,
    _csv_response,
    _ctx,
    _page,
    templates,
    ui_session,
)

router = APIRouter(include_in_schema=False)


def _money(value: str) -> int:
    try:
        minor = parse_minor(value)
    except RowError as exc:
        raise finance.FinanceError(str(exc)) from exc
    if minor is None:
        raise finance.FinanceError("Enter an amount")
    return minor


def _date_or_none(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise finance.FinanceError(f"Invalid date '{value}' (use YYYY-MM-DD)") from exc


def _back(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{joiner}{urlencode(params)}" if params else path, status_code=303)


def _invoice(db: Session, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise UiError(404, "Invoice not found")
    return invoice


def _contact_name(db: Session, invoice: Invoice) -> str:
    if invoice.contact_id and (c := db.get(Contact, invoice.contact_id)):
        return c.name
    if invoice.member_id and (m := db.get(Member, invoice.member_id)):
        return m.name
    return ""


# --------------------------------------------------------------------------- invoices list


def _invoice_filter(filter: str = "all", q: str = "", date_from: str = "", date_to: str = "", nominal: str = "", source: str = "", tag: str = "", view: str = "invoices") -> InvoiceFilter:  # noqa: A002
    try:
        first, last = _date_or_none(date_from), _date_or_none(date_to)
    except finance.FinanceError:
        first = last = None  # a mistyped date just means no date limit
    return InvoiceFilter(filter=filter, q=q, date_from=first, date_to=last, nominal=nominal, source=source, tag=tag, view="lines" if view == "lines" else "invoices")


@router.get("/app/finance", response_class=HTMLResponse)
def finance_list(
    request: Request,
    page: int = 1,
    msg: str = "",
    err: str = "",
    f: InvoiceFilter = Depends(_invoice_filter),
    session: SessionRecord = Depends(ui_session(perm="finance.read")),
    db: Session = Depends(get_db),
) -> Response:
    ctx = _ctx(session, "finance", f=f, filters=STATUS_FILTERS, params=f.as_params(), nominals=db.scalars(select(NominalCode).order_by(NominalCode.code)).all(), sources=invoice_query.sources(db), today=date.today(), msg=msg, err=err, sub="invoices")
    if f.view == "lines":
        total = db.scalar(select(func.count()).select_from(invoice_query.lines_statement(f).order_by(None).subquery())) or 0
        pages = max(1, math.ceil(total / PAGE_SIZE))
        page = min(max(1, page), pages)
        rows = db.execute(invoice_query.lines_statement(f).limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
        ctx.update(line_rows=[{"line": line, "invoice": inv, "name": name or ""} for line, inv, name in rows], pager={"page": page, "pages": pages, "total": total}, totals=None)
        return templates.TemplateResponse(request, "finance_list.html", ctx)
    stmt = invoice_query.invoices_statement(f)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = db.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    reminded = invoice_query.last_reminders(db, [inv.id for inv, _ in rows])
    table = [{"invoice": inv, "name": name or "", "outstanding": finance.outstanding(db, inv), "reminded": reminded.get(inv.id)} for inv, name in rows]
    ctx.update(table=table, pager={"page": page, "pages": pages, "total": total}, totals=invoice_query.totals(db, f))
    return templates.TemplateResponse(request, "finance_list.html", ctx)


@router.post("/app/finance/bulk")
async def invoices_bulk(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    ids = [int(str(v)) for v in form.getlist("invoice") if str(v).isdigit()]
    action = str(form.get("action", ""))
    back = str(form.get("back", "/app/finance"))
    back = back if back.startswith("/app/finance") else "/app/finance"
    if not ids:
        return _back(back, err="Tick at least one invoice first")
    invoices = db.scalars(select(Invoice).where(Invoice.id.in_(ids)).order_by(Invoice.id)).all()
    if action == "remind":
        result = invoice_comms.send_reminders(db, [i.id for i in invoices], session.email)
        _audit(db, session, "finance.bulk_remind", "invoice", "bulk", sent=len(result["sent"]), skipped=len(result["skipped"]))
        db.commit()
        detail = f"{len(result['sent'])} reminder(s) sent" + (f"; skipped: {'; '.join(result['skipped'][:4])}" if result["skipped"] else "")
        return _back(back, msg=detail)
    if action == "email":
        sent, skipped = 0, []
        for inv in invoices:
            try:
                record = invoice_comms.send_invoice(db, inv, session.email)
                sent += record.status != "failed"
            except finance.FinanceError as exc:
                skipped.append(f"{inv.reference}: {exc}")
        _audit(db, session, "finance.bulk_email", "invoice", "bulk", sent=sent, skipped=len(skipped))
        db.commit()
        return _back(back, msg=f"{sent} invoice(s) emailed" + (f"; skipped: {'; '.join(skipped[:4])}" if skipped else ""))
    if action == "pdf":
        import io
        import zipfile

        from app.invoice_pdf import render_invoice_pdf

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for inv in invoices:
                archive.writestr(f"invoice-{inv.reference}.pdf", render_invoice_pdf(db, inv))
        return Response(buffer.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="invoices.zip"'})
    if action == "csv":
        names = {inv.id: _contact_name(db, inv) for inv in invoices}
        return _csv_response(
            "invoices.csv",
            ["number", "date", "due", "contact", "status", "total", "outstanding", "tags"],
            [[i.reference, i.issue_date, i.due_date, names[i.id], invoice_query.status_label(i.status), f"{i.amount_minor / 100:.2f}", f"{finance.outstanding(db, i) / 100:.2f}", i.tags or ""] for i in invoices],
        )
    return _back(back, err="Choose what to do with the ticked invoices")


# --------------------------------------------------------------------------- the invoice builder


def _parse_qty(text: str) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = float(text.replace(",", ""))
    except ValueError as exc:
        raise finance.FinanceError(f"'{text}' is not a quantity") from exc
    if value <= 0:
        raise finance.FinanceError("A quantity must be more than zero")
    return value


def _builder_lines(item: list[str], desc: list[str], qty: list[str], unit: list[str], nominal: list[str], require_nominal: bool = True) -> list[dict]:
    """Turn the form's parallel columns into invoice lines; blank rows are skipped, half-filled rows are errors."""
    lines = []
    for index in range(max(len(desc), len(unit), len(qty))):
        text = (desc[index] if index < len(desc) else "").strip()
        price = (unit[index] if index < len(unit) else "").strip()
        quantity = (qty[index] if index < len(qty) else "").strip()
        code = (nominal[index] if index < len(nominal) else "").strip()
        if not (text or price):
            continue
        row = index + 1
        if not text:
            raise finance.FinanceError(f"Line {row} needs a description")
        if not price:
            raise finance.FinanceError(f"Line {row} needs a price")
        if require_nominal and not code:
            raise finance.FinanceError(f"Line {row} needs a nominal code so the income is reported in the right place")
        minor = _money(price)
        lines.append({"description": text, "unit_price_minor": minor, "quantity": _parse_qty(quantity) or 1.0, "nominal_code": code or None})
    if not lines:
        raise finance.FinanceError("Add at least one line")
    return lines


def _builder_values(invoice: Invoice | None, db: Session) -> dict:
    default_terms = finance.terms_days(db)
    if invoice is None:
        return {"issue_date": date.today().isoformat(), "terms": default_terms, "message": finance.get_setting(db, "invoice_message"), "notes": "", "tags": "", "send_reminders": True, "send_receipt": True}
    return {
        "issue_date": (invoice.issue_date or date.today()).isoformat(), "terms": invoice.terms_days if invoice.terms_days is not None else default_terms, "message": invoice.message or "",
        "notes": invoice.notes or "", "tags": invoice.tags or "", "send_reminders": bool(invoice.send_reminders), "send_receipt": bool(invoice.send_receipt),
    }


def _builder_page(request: Request, session: SessionRecord, db: Session, contact: Contact, invoice: Invoice | None, rows: list[dict], values: dict, err: str = "", status: int = 200) -> Response:
    options = catalogue.options(db)
    groups: dict[str, list] = {}
    for option in options:
        groups.setdefault(option.group, []).append(option)
    nominals = db.scalars(select(NominalCode).order_by(NominalCode.code)).all()
    action_url = f"/app/finance/invoices/{invoice.id}/edit" if invoice else "/app/finance/new"
    return templates.TemplateResponse(
        request,
        "invoice_builder.html",
        _ctx(session, "finance", contact=contact, invoice=invoice, rows=rows or [{"desc": "", "qty": "", "unit": "", "nominal": ""}], v=values, err=err, groups=list(groups.items()),
             catalogue=catalogue.as_json(options), nominals=nominals, action_url=action_url, sub="new"),
        status_code=status,
    )


def _rows_from_form(item: list[str], desc: list[str], qty: list[str], unit: list[str], nominal: list[str]) -> list[dict]:
    count = max(len(desc), len(unit), 1)
    get = lambda seq, i: seq[i] if i < len(seq) else ""  # noqa: E731
    return [{"desc": get(desc, i), "qty": get(qty, i), "unit": get(unit, i), "nominal": get(nominal, i)} for i in range(count)]


@router.get("/app/finance/new", response_class=HTMLResponse)
def invoice_new(request: Request, contact_id: int | None = None, q: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    contact = db.get(Contact, contact_id) if contact_id else None
    if contact is not None:
        return _builder_page(request, session, db, contact, None, [], _builder_values(None, db), err)
    recent_ids = [cid for (cid,) in db.execute(select(Invoice.contact_id).where(Invoice.contact_id.is_not(None)).order_by(Invoice.id.desc()).limit(60))]
    recent: list[Contact] = []
    for cid in dict.fromkeys(recent_ids):
        found = db.get(Contact, cid)
        if found is not None:
            recent.append(found)
        if len(recent) == 8:
            break
    return templates.TemplateResponse(request, "invoice_new.html", _ctx(session, "finance", q=q, recent=recent, matches=_contact_matches(db, q), err=err, sub="new"))


def _contact_matches(db: Session, q: str) -> list[Contact]:
    if not q.strip():
        return []
    term = f"%{q.strip()}%"
    return list(db.scalars(select(Contact).where(or_(Contact.name.ilike(term), Contact.email.ilike(term), Contact.phone.ilike(term))).order_by(Contact.name).limit(12)).all())


@router.get("/app/finance/new/search", response_class=HTMLResponse)
def invoice_contact_search(request: Request, q: str = "", session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "_invoice_contact_results.html", _ctx(session, "finance", q=q, matches=_contact_matches(db, q)))


def _finish_invoice(db: Session, session: SessionRecord, invoice: Invoice, action: str) -> tuple[str, str]:
    """Apply the button that was pressed; returns (where to go, message)."""
    if action in {"issue", "issue_email"}:
        finance.issue(db, invoice, session.email)
        message = f"Invoice {invoice.reference} issued"
        if action == "issue_email":
            record = invoice_comms.send_invoice(db, invoice, session.email)
            message += " and emailed" if record.status != "failed" else f" (the email failed: {record.error})"
        return f"/app/finance/invoices/{invoice.id}", message
    if action == "preview":
        return f"/app/finance/invoices/{invoice.id}.pdf", ""
    return f"/app/finance/invoices/{invoice.id}", "Draft saved"


def _form_options(message: str, notes: str, tags: str, issue_date: str, terms_days: str, send_reminders: str, send_receipt: str) -> dict:
    try:
        terms = int(terms_days) if terms_days.strip() else None
    except ValueError as exc:
        raise finance.FinanceError("Payment terms must be a number of days") from exc
    return {"message": message, "notes": notes, "tags": tags, "issue_date": _date_or_none(issue_date), "terms_days": terms, "send_reminders": bool(send_reminders), "send_receipt": bool(send_receipt)}


@router.post("/app/finance/new")
def invoice_create(
    request: Request,
    contact_id: int = Form(...),
    action: str = Form("draft"),
    notes: str = Form(""),
    message: str = Form(""),
    tags: str = Form(""),
    issue_date: str = Form(""),
    terms_days: str = Form(""),
    send_reminders: str = Form(""),
    send_receipt: str = Form(""),
    item: list[str] = Form([]),
    desc: list[str] = Form([]),
    qty: list[str] = Form([]),
    unit: list[str] = Form([]),
    nominal: list[str] = Form([]),
    session: SessionRecord = Depends(ui_session(perm="finance.write")),
    db: Session = Depends(get_db),
) -> Response:
    contact = db.get(Contact, contact_id)
    if contact is None:
        raise UiError(404, "Contact not found")
    values = {"issue_date": issue_date, "terms": int(terms_days or 0), "message": message, "notes": notes, "tags": tags, "send_reminders": bool(send_reminders), "send_receipt": bool(send_receipt)}
    try:
        lines = _builder_lines(item, desc, qty, unit, nominal)
        invoice = finance.create_invoice(db, contact_id=contact_id, lines=lines, actor=session.email, **_form_options(message, notes, tags, issue_date, terms_days, send_reminders, send_receipt))
        where, msg = _finish_invoice(db, session, invoice, action)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _builder_page(request, session, db, contact, None, _rows_from_form(item, desc, qty, unit, nominal), values, str(exc), 422)
    return _back(where, msg=msg)


@router.get("/app/finance/invoices/{invoice_id:int}/edit", response_class=HTMLResponse)
def invoice_edit(invoice_id: int, request: Request, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    invoice = _invoice(db, invoice_id)
    if invoice.status != "draft":
        return _back(f"/app/finance/invoices/{invoice.id}", err="Only a draft can be edited. Issued invoices are locked: use a credit note and a new invoice.")
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    if contact is None:
        raise UiError(400, "This draft has no contact")
    rows = [
        {"desc": line.description, "qty": f"{line.quantity:g}" if line.quantity is not None else "1", "unit": f"{(line.unit_price_minor if line.unit_price_minor is not None else line.amount_minor) / 100:.2f}", "nominal": line.nominal_code or ""}
        for line in db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id))
    ]
    return _builder_page(request, session, db, contact, invoice, rows, _builder_values(invoice, db))


@router.post("/app/finance/invoices/{invoice_id:int}/edit")
def invoice_edit_save(
    invoice_id: int,
    request: Request,
    action: str = Form("draft"),
    notes: str = Form(""),
    message: str = Form(""),
    tags: str = Form(""),
    issue_date: str = Form(""),
    terms_days: str = Form(""),
    send_reminders: str = Form(""),
    send_receipt: str = Form(""),
    item: list[str] = Form([]),
    desc: list[str] = Form([]),
    qty: list[str] = Form([]),
    unit: list[str] = Form([]),
    nominal: list[str] = Form([]),
    session: SessionRecord = Depends(ui_session(perm="finance.write")),
    db: Session = Depends(get_db),
) -> Response:
    invoice = _invoice(db, invoice_id)
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    if contact is None:
        raise UiError(400, "This draft has no contact")
    values = {"issue_date": issue_date, "terms": int(terms_days or 0), "message": message, "notes": notes, "tags": tags, "send_reminders": bool(send_reminders), "send_receipt": bool(send_receipt)}
    try:
        lines = _builder_lines(item, desc, qty, unit, nominal)
        finance.update_draft(db, invoice, **_form_options(message, notes, tags, issue_date, terms_days, send_reminders, send_receipt))
        finance.replace_lines(db, invoice, lines)
        where, msg = _finish_invoice(db, session, invoice, action)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _builder_page(request, session, db, contact, invoice, _rows_from_form(item, desc, qty, unit, nominal), values, str(exc), 422)
    return _back(where, msg=msg)


@router.post("/app/finance/invoices/{invoice_id:int}/duplicate")
def invoice_duplicate(invoice_id: int, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    invoice = _invoice(db, invoice_id)
    try:
        copy = finance.duplicate(db, invoice, session.email)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back(f"/app/finance/invoices/{invoice_id}", err=str(exc))
    return _back(f"/app/finance/invoices/{copy.id}/edit", msg="Copied as a new draft: change what you need, then issue it")


# --------------------------------------------------------------------------- invoice detail and actions


@router.get("/app/finance/invoices/{invoice_id:int}", response_class=HTMLResponse)
def invoice_detail(invoice_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    invoice = _invoice(db, invoice_id)
    lines = db.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)).all()
    payments = db.scalars(select(PaymentTransaction).where(PaymentTransaction.invoice_id == invoice.id).order_by(PaymentTransaction.received_at, PaymentTransaction.id)).all()
    contact = db.get(Contact, invoice.contact_id) if invoice.contact_id else None
    original = db.get(Invoice, invoice.credit_for_id) if invoice.credit_for_id else None
    credits = db.scalars(select(Invoice).where(Invoice.credit_for_id == invoice.id)).all()
    emails = db.scalars(select(OutboundEmail).where(OutboundEmail.related_id == invoice.id, OutboundEmail.related_type.in_(["invoice", "invoice_reminder"])).order_by(OutboundEmail.id.desc())).all()
    nominals = db.scalars(select(NominalCode).order_by(NominalCode.code)).all()
    return templates.TemplateResponse(
        request,
        "invoice_detail.html",
        _ctx(
            session, "finance", invoice=invoice, lines=lines, payments=payments, contact=contact, original=original, credits=credits, emails=emails, nominals=nominals,
            outstanding=finance.outstanding(db, invoice), methods=finance.PAYMENT_METHODS, today=date.today().isoformat(), msg=msg, err=err, sub="invoices",
        ),
    )


def _act(db: Session, session: SessionRecord, invoice_id: int, fn, ok: str) -> RedirectResponse:
    invoice = _invoice(db, invoice_id)
    path = f"/app/finance/invoices/{invoice_id}"
    try:
        fn(invoice)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back(path, err=str(exc))
    return _back(path, msg=ok)


@router.post("/app/finance/invoices/{invoice_id}/lines")
def invoice_add_line(invoice_id: int, description: str = Form(""), amount: str = Form(""), nominal: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.add_line(db, inv, description=description, amount_minor=_money(amount), nominal_code=nominal or None), "Line added")


@router.post("/app/finance/invoices/{invoice_id}/lines/{line_id}/delete")
def invoice_remove_line(invoice_id: int, line_id: int, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.remove_line(db, inv, line_id), "Line removed")


@router.post("/app/finance/invoices/{invoice_id}/issue")
def invoice_issue(invoice_id: int, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.issue(db, inv, session.email), "Invoice issued")


@router.post("/app/finance/invoices/{invoice_id}/payment")
def invoice_payment(
    invoice_id: int, amount: str = Form(""), method: str = Form("bank"), reference: str = Form(""), received_on: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)
) -> Response:
    return _act(
        db, session, invoice_id,
        lambda inv: finance.record_payment(db, inv, amount_minor=_money(amount), method=method, reference=reference, received_on=_date_or_none(received_on), actor=session.email),
        "Payment recorded",
    )


@router.post("/app/finance/payments/{payment_id}/reverse")
def payment_reverse(payment_id: int, reason: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    payment = db.get(PaymentTransaction, payment_id)
    if payment is None:
        raise UiError(404, "Payment not found")
    if payment.bank_entry_id:  # reversing here would leave the bank line wrongly marked matched
        return _back(f"/app/finance/bank/entries/{payment.bank_entry_id}", err="This payment came from a bank line: un-match the line to reverse it")
    return _act(db, session, payment.invoice_id, lambda inv: finance.reverse_payment(db, payment, session.email, reason or "reversed"), "Payment reversed")


@router.post("/app/finance/invoices/{invoice_id}/void")
def invoice_void(invoice_id: int, reason: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.void_invoice(db, inv, session.email, reason), "Invoice voided")


@router.post("/app/finance/invoices/{invoice_id}/write-off")
def invoice_write_off(invoice_id: int, reason: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.write_off(db, inv, session.email, reason), "Written off")


@router.post("/app/finance/invoices/{invoice_id}/credit")
def invoice_credit(invoice_id: int, amount: str = Form(""), reason: str = Form(""), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    return _act(db, session, invoice_id, lambda inv: finance.credit_note(db, inv, amount_minor=_money(amount), reason=reason, actor=session.email), "Credit note issued")


@router.post("/app/finance/invoices/{invoice_id}/email")
def invoice_email(invoice_id: int, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    def send(inv: Invoice) -> None:
        record = invoice_comms.send_invoice(db, inv, session.email)
        if record.status == "failed":
            raise finance.FinanceError(f"Email failed: {record.error}")

    return _act(db, session, invoice_id, send, "Invoice emailed (or logged, in sandbox mode)")


@router.get("/app/finance/invoices/{invoice_id}.pdf")
def invoice_pdf(invoice_id: int, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    from app.invoice_pdf import render_invoice_pdf

    invoice = _invoice(db, invoice_id)
    return Response(render_invoice_pdf(db, invoice), media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="invoice-{invoice.reference}.pdf"'})


# --------------------------------------------------------------------------- the price list


@router.get("/app/finance/items", response_class=HTMLResponse)
def items_page(request: Request, msg: str = "", err: str = "", edit: int | None = None, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    items = db.scalars(select(FeeItem).order_by(FeeItem.active.desc(), FeeItem.category, FeeItem.name)).all()
    nominals = db.scalars(select(NominalCode).order_by(NominalCode.code)).all()
    return templates.TemplateResponse(request, "finance_items.html", _ctx(session, "finance", items=items, nominals=nominals, editing=db.get(FeeItem, edit) if edit else None, linked=catalogue.options(db), msg=msg, err=err, sub="items"))


@router.post("/app/finance/items")
def items_save(
    item_id: str = Form(""),
    name: str = Form(""),
    nominal: str = Form(""),
    price: str = Form("0"),
    unit: str = Form("each"),
    category: str = Form("club"),
    description: str = Form(""),
    active: str = Form(""),
    session: SessionRecord = Depends(ui_session(perm="finance.admin")),
    db: Session = Depends(get_db),
) -> Response:
    try:
        saved = catalogue.save_item(db, int(item_id) if item_id.isdigit() else None, name=name, nominal_code=nominal, price_minor=_money(price or "0"), unit=unit, category=category, description=description, active=bool(active) or not item_id)
        _audit(db, session, "finance.item", "fee_item", saved.id, name=saved.name, price=saved.unit_price_minor)
        db.commit()
    except (catalogue.CatalogueError, finance.FinanceError) as exc:
        db.rollback()
        return _back("/app/finance/items", err=str(exc))
    return _back("/app/finance/items", msg=f"Saved {saved.name}")


# --------------------------------------------------------------------------- debtors, reminders, exports


@router.get("/app/finance/debtors", response_class=HTMLResponse)
def debtors_page(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    rows = finance.aged_debtors(db)
    return templates.TemplateResponse(request, "debtors.html", _ctx(session, "finance", rows=rows, totals=finance.debtor_totals(rows), sub="debtors"))


@router.get("/app/finance/debtors.csv")
def debtors_csv(session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    rows = finance.aged_debtors(db)
    return _csv_response(
        "aged-debtors.csv",
        ["contact", "not_due", "1-30", "31-60", "61-90", "90+", "total"],
        [[r.name, *(f"{r.buckets[k] / 100:.2f}" for k in ("not_due", "d1_30", "d31_60", "d61_90", "d90_plus")), f"{r.total / 100:.2f}"] for r in rows],
    )


@router.get("/app/finance/reminders", response_class=HTMLResponse)
def reminders_page(request: Request, msg: str = "", err: str = "", min_days: int = 1, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    candidates = invoice_comms.reminder_candidates(db, min_days_overdue=max(1, min_days))
    return templates.TemplateResponse(request, "reminders.html", _ctx(session, "finance", candidates=candidates, min_days=max(1, min_days), msg=msg, err=err, sub="reminders"))


@router.post("/app/finance/reminders")
async def reminders_send(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    ids = [int(str(v)) for v in form.getlist("invoice") if str(v).isdigit()]
    if not ids:
        return _back("/app/finance/reminders", err="Tick at least one invoice")
    result = invoice_comms.send_reminders(db, ids, session.email)
    _audit(db, session, "finance.reminders", "invoice", "bulk", sent=len(result["sent"]), skipped=len(result["skipped"]))
    db.commit()
    detail = f"{len(result['sent'])} reminder(s) sent" + (f"; skipped: {'; '.join(result['skipped'][:5])}" if result["skipped"] else "")
    return _back("/app/finance/reminders", msg=detail)


@router.get("/app/finance/export", response_class=HTMLResponse)
def export_page(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.read"))) -> Response:
    year = date.today().year
    return templates.TemplateResponse(request, "finance_export.html", _ctx(session, "finance", start=f"{year}-01-01", end=f"{year}-12-31", sub="export"))


@router.get("/app/finance/export/{kind}.csv")
def export_csv(kind: str, start: str = "", end: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    try:
        first, last = _date_or_none(start), _date_or_none(end)
    except finance.FinanceError as exc:
        raise UiError(400, str(exc)) from exc
    if kind == "lines":
        data = finance.accounting_lines(db, first, last)
        header = ["date", "document", "type", "contact", "description", "nominal_code", "nominal_label", "net", "vat", "gross", "status"]
        return _csv_response("invoice-lines.csv", header, [[r["date"], r["document"], r["type"], r["contact"], r["description"], r["nominal_code"], r["nominal_label"], f"{r['net'] / 100:.2f}", f"{r['vat'] / 100:.2f}", f"{r['gross'] / 100:.2f}", r["status"]] for r in data])
    if kind == "payments":
        data = finance.payment_lines(db, first, last)
        return _csv_response("payments.csv", ["date", "invoice", "contact", "method", "reference", "amount", "recorded_by"], [[r["date"], r["invoice"], r["contact"], r["method"], r["reference"], f"{r['amount'] / 100:.2f}", r["recorded_by"]] for r in data])
    raise UiError(404, "Unknown export")


# --------------------------------------------------------------------------- bank


@router.get("/app/finance/bank", response_class=HTMLResponse)
def bank_home(request: Request, status: str = "unmatched", account: int | None = None, page: int = 1, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    stmt = select(BankEntry, BankAccount.name).join(BankAccount, BankAccount.id == BankEntry.account_id).order_by(BankEntry.entry_date.desc(), BankEntry.id.desc())
    if status != "all":
        stmt = stmt.where(BankEntry.status == status)
    if account:
        stmt = stmt.where(BankEntry.account_id == account)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, page), pages)
    rows = db.execute(stmt.limit(PAGE_SIZE).offset((page - 1) * PAGE_SIZE)).all()
    return templates.TemplateResponse(
        request, "bank.html",
        _ctx(session, "finance", summary=banking.account_summary(db), rows=rows, status=status, account=account, pager={"page": page, "pages": pages, "total": total}, msg=msg, err=err, sub="bank",
             accounts=db.scalars(select(BankAccount).order_by(BankAccount.name)).all(), statuses=["unmatched", "matched", "categorised", "ignored", "historical", "all"]),
    )


@router.post("/app/finance/bank/upload")
async def bank_upload(
    request: Request, account_id: str = Form(""), new_account: str = Form(""), new_kind: str = Form("bank"), file: UploadFile = File(...), session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)
) -> Response:
    raw = await file.read(5_000_001)
    if len(raw) > 5_000_000:
        return _back("/app/finance/bank", err="File is larger than 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if account_id.isdigit():
        account = db.get(BankAccount, int(account_id))
    elif new_account.strip():
        account = banking.get_or_create_account(db, new_account, kind=new_kind)
    else:
        account = None
    if account is None:
        return _back("/app/finance/bank", err="Choose an account or name a new one")
    try:
        report = banking.import_statement(db, account, text, source="upload")
    except ValueError as exc:
        db.rollback()
        return _back("/app/finance/bank", err=str(exc))
    auto = banking.auto_match(db, session.email, account.id)
    ruled = cashbook.apply_rules(db, session.email, account.id)
    _audit(db, session, "bank.import", "bank_account", account.id, **report.as_dict(), auto_matched=auto.matched, by_rule=ruled)
    db.commit()
    return _back("/app/finance/bank", msg=f"{report.created} new lines ({report.credits} money in), {report.duplicates} already loaded, {len(report.errors)} errors; {auto.matched} matched automatically, {ruled} categorised by your rules")


@router.post("/app/finance/bank/auto-match")
def bank_auto_match(session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    report = banking.auto_match(db, session.email)
    ruled = cashbook.apply_rules(db, session.email)
    _audit(db, session, "bank.auto_match", "bank_entry", "all", matched=report.matched, left=report.left, by_rule=ruled)
    db.commit()
    return _back("/app/finance/bank", msg=f"{report.matched} matched to invoices, {ruled} categorised by rules, {report.left} left for you")


def _entry(db: Session, entry_id: int) -> BankEntry:
    entry = db.get(BankEntry, entry_id)
    if entry is None:
        raise UiError(404, "Bank line not found")
    return entry


@router.get("/app/finance/bank/entries/{entry_id}", response_class=HTMLResponse)
def bank_entry(entry_id: int, request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    account = db.get(BankAccount, entry.account_id)
    payments = db.scalars(select(PaymentTransaction).where(PaymentTransaction.bank_entry_id == entry.id)).all()
    invoices = {p.invoice_id: db.get(Invoice, p.invoice_id) for p in payments}
    suggestions = banking.suggestions(db, entry) if entry.status == "unmatched" else []
    return templates.TemplateResponse(request, "bank_entry.html", _ctx(session, "finance", entry=entry, account=account, payments=payments, invoices=invoices, suggestions=suggestions, allocations=cashbook.allocations_for(db, entry.id), msg=msg, err=err, sub="bank"))


@router.post("/app/finance/bank/entries/{entry_id}/match")
async def bank_entry_match(entry_id: int, request: Request, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    form = await request.form()
    path = f"/app/finance/bank/entries/{entry_id}"
    try:
        allocations: list[tuple[int, int]] = []
        for key in ("1", "2", "3"):
            ref = str(form.get(f"invoice{key}", "")).strip()
            if not ref:
                continue
            invoice = db.scalar(select(Invoice).where(Invoice.reference == ref))
            if invoice is None:
                raise finance.FinanceError(f"No invoice numbered {ref}")
            amount_text = str(form.get(f"amount{key}", "")).strip()
            allocations.append((invoice.id, _money(amount_text) if amount_text else entry.amount_minor))
        if not allocations:
            raise finance.FinanceError("Enter an invoice number")
        banking.match_split(db, entry, allocations, session.email)
        _audit(db, session, "bank.match", "bank_entry", entry.id, invoices=[a[0] for a in allocations])
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back(path, err=str(exc))
    return _back(path, msg="Matched")


@router.post("/app/finance/bank/entries/{entry_id}/{action}")
def bank_entry_action(entry_id: int, action: str, session: SessionRecord = Depends(ui_session(perm="finance.write")), db: Session = Depends(get_db)) -> Response:
    entry = _entry(db, entry_id)
    path = f"/app/finance/bank/entries/{entry_id}"
    try:
        if action == "unmatch":
            banking.unmatch(db, entry, session.email)
        elif action == "ignore":
            banking.ignore(db, entry)
        elif action == "restore":
            banking.restore(db, entry)
        else:
            raise UiError(404, "Unknown action")
        _audit(db, session, f"bank.{action}", "bank_entry", entry.id)
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back(path, err=str(exc))
    return _back(path, msg="Done")


# --------------------------------------------------------------------------- membership billing run and fees


@router.get("/app/finance/billing", response_class=HTMLResponse)
def billing_page(request: Request, period: int | None = None, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="billing.run")), db: Session = Depends(get_db)) -> Response:
    periods = db.scalars(select(MembershipPeriod).order_by(MembershipPeriod.starts_on.desc())).all()
    chosen = db.get(MembershipPeriod, period) if period else (periods[0] if periods else None)
    rows = membership.preview(db, chosen) if chosen else []
    summary = {
        "due": len(rows),
        "no_fee": sum(1 for r in rows if r.note),
        "total": sum(r.fee_minor or 0 for r in rows if r.fee_minor),
        "confirmed": sum(1 for r in rows if r.state == "confirmed"),
    }
    return templates.TemplateResponse(request, "billing.html", _ctx(session, "finance", periods=periods, chosen=chosen, rows=rows, summary=summary, next_year=(periods[0].starts_on.year + 1) if periods else date.today().year + 1, msg=msg, err=err, sub="billing"))


@router.post("/app/finance/billing/period")
def billing_new_period(year: int = Form(...), session: SessionRecord = Depends(ui_session(perm="billing.run")), db: Session = Depends(get_db)) -> Response:
    if not 2000 <= year <= 2100:
        return _back("/app/finance/billing", err="Enter a sensible year")
    period = membership.create_period(db, year)
    db.commit()
    return RedirectResponse(f"/app/finance/billing?period={period.id}&msg=Period+{period.name}+ready", status_code=303)


@router.post("/app/finance/billing/{period_id}/{action}")
async def billing_action(period_id: int, action: str, request: Request, session: SessionRecord = Depends(ui_session(perm="billing.run")), db: Session = Depends(get_db)) -> Response:
    period = db.get(MembershipPeriod, period_id)
    if period is None:
        raise UiError(404, "Period not found")
    form = await request.form()
    base = f"/app/finance/billing?period={period_id}"
    if action == "prepare":
        report = membership.prepare(db, period)
        _audit(db, session, "membership.prepare", "membership_period", period.id, prepared=report.prepared)
        db.commit()
        return RedirectResponse(f"{base}&{urlencode({'msg': f'{report.prepared} renewals prepared'})}", status_code=303)
    if action == "bill":
        selected = [int(str(v)) for v in form.getlist("member") if str(v).isdigit()]
        if not selected and form.get("everyone") != "1":
            return RedirectResponse(f"{base}&err=Tick+the+members+to+bill%2C+or+use+%22Bill+everyone%22", status_code=303)
        report = membership.run_billing(db, period, session.email, member_ids=selected or None, issue=form.get("draft") != "1")
        _audit(db, session, "membership.bill", "membership_period", period.id, **report.as_dict())
        db.commit()
        text = f"{report.confirmed} confirmed, {report.invoiced} invoiced" + (f"; {len(report.skipped)} skipped: {'; '.join(report.skipped[:4])}" if report.skipped else "")
        return RedirectResponse(f"{base}&{urlencode({'msg': text})}", status_code=303)
    if action == "decline":
        member_id = int(str(form.get("member_id", "0")) or 0)
        renewal = db.scalar(select(MembershipRenewal).where(MembershipRenewal.period_id == period.id, MembershipRenewal.member_id == member_id))
        if renewal is None:
            return RedirectResponse(f"{base}&err=Prepare+the+renewals+first", status_code=303)
        membership.decline(db, renewal, session.email, "declined in the billing run")
        db.commit()
        return RedirectResponse(f"{base}&msg=Marked+as+not+renewing", status_code=303)
    raise UiError(404, "Unknown action")


@router.get("/app/finance/types", response_class=HTMLResponse)
def types_page(request: Request, msg: str = "", err: str = "", session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    members_by_type: dict[str, int] = dict(db.execute(select(func.lower(Member.membership_type), func.count()).group_by(func.lower(Member.membership_type))).all())
    types = db.scalars(select(MembershipType).order_by(MembershipType.name)).all()
    nominals = db.scalars(select(NominalCode).order_by(NominalCode.code)).all()
    return templates.TemplateResponse(request, "types.html", _ctx(session, "finance", types=types, nominals=nominals, counts=members_by_type, msg=msg, err=err, sub="types"))


@router.post("/app/finance/types")
async def types_save(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    try:
        for mtype in db.scalars(select(MembershipType)):
            raw = str(form.get(f"fee_{mtype.id}", "")).strip()
            mtype.fee_minor = _money(raw) if raw else None
            mtype.nominal_code = str(form.get(f"nominal_{mtype.id}", "")) or mtype.nominal_code
            mtype.online_joinable = form.get(f"join_{mtype.id}") == "1"
            mtype.active = form.get(f"active_{mtype.id}") == "1"
        new_name = str(form.get("new_name", "")).strip()
        if new_name:
            created = membership.ensure_type(db, new_name)
            raw = str(form.get("new_fee", "")).strip()
            created.fee_minor = _money(raw) if raw else None
        _audit(db, session, "membership.types", "membership_type", "all")
        db.commit()
    except finance.FinanceError as exc:
        db.rollback()
        return _back("/app/finance/types", err=str(exc))
    return _back("/app/finance/types", msg="Saved")


@router.post("/app/finance/types/raise")
def types_raise(percent: str = Form("0"), session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    try:
        value = float(percent.strip() or 0)
        changed = membership.raise_fees(db, value)
        _audit(db, session, "membership.fees_raised", "membership_type", "all", percent=value, changed=changed)
        db.commit()
    except (membership.MembershipError, ValueError) as exc:
        db.rollback()
        return _back("/app/finance/types", err=str(exc))
    return _back("/app/finance/types", msg=f"{changed} fee(s) changed by {value:g}% (rounded to the nearest 50p)")


# --------------------------------------------------------------------------- settings and email log


@router.get("/app/finance/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "finance_settings.html", _ctx(session, "finance", s=finance.all_settings(db), msg=msg, sub="settings"))


@router.post("/app/finance/settings")
async def settings_save(request: Request, session: SessionRecord = Depends(ui_session(perm="finance.admin")), db: Session = Depends(get_db)) -> Response:
    form = await request.form()
    before = finance.all_settings(db)
    finance.save_settings(db, {k: str(v) for k, v in form.items() if k != "csrf"})
    db.flush()
    _audit(db, session, "finance.settings", "settings", "finance", changes=audit.changes(before, finance.all_settings(db)))
    db.commit()
    return _back("/app/finance/settings", msg="Saved")


@router.get("/app/finance/emails", response_class=HTMLResponse)
def emails_page(request: Request, page: int = 1, session: SessionRecord = Depends(ui_session(perm="finance.read")), db: Session = Depends(get_db)) -> Response:
    rows, pager = _page(db, select(OutboundEmail).where(OutboundEmail.related_type != "sign_in").order_by(OutboundEmail.id.desc()), page)
    from app import mailer

    return templates.TemplateResponse(request, "emails.html", _ctx(session, "finance", rows=rows, pager=pager, provider=mailer.provider(), sub="emails"))
