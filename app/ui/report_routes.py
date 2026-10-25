"""Treasurer and committee reports: the monthly money report, income by nominal, receipts and payments, debtors, membership numbers."""

from __future__ import annotations

import calendar
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app import finance, reports
from app.db import get_db
from app.models import SessionRecord
from app.report_pdf import render_committee_pdf
from app.ui.routes import UiError, _csv_response, _ctx, templates, ui_session

router = APIRouter(include_in_schema=False)


def _date(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value) if value else fallback
    except ValueError:
        return fallback


def _month(year: int | None, month: int | None) -> tuple[int, int]:
    today = date.today()
    y, m = year or today.year, month or today.month
    return (y, m) if 1 <= m <= 12 and 2000 <= y <= 2100 else (today.year, today.month)


def _bars(points: list[dict], width: int = 720, height: int = 150) -> dict:
    """Coordinates for the 12-month chart: pairs of bars (invoiced, received) scaled to the biggest figure."""
    peak = max([max(p["invoiced"], p["received"]) for p in points] + [1])
    slot = width / max(len(points), 1)
    bars = []
    for index, point in enumerate(points):
        x = index * slot
        for offset, key in ((0.12, "invoiced"), (0.5, "received")):
            h = round(point[key] / peak * (height - 24)) if point[key] > 0 else 0
            bars.append({"x": round(x + slot * offset, 1), "y": height - 18 - h, "w": round(slot * 0.36, 1), "h": h, "kind": key, "tip": f"{point['label']} {key}: £{point[key] / 100:,.2f}"})
    labels = [{"x": round(i * slot + slot / 2, 1), "text": p["label"]} for i, p in enumerate(points)]
    return {"bars": bars, "labels": labels, "width": width, "height": height, "peak": peak}


@router.get("/app/finance/reports", response_class=HTMLResponse)
def committee_page(request: Request, year: int | None = None, month: int | None = None, session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    y, m = _month(year, month)
    report = reports.committee_report(db, y, m)
    prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
    next_y, next_m = (y + 1, 1) if m == 12 else (y, m + 1)
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    return templates.TemplateResponse(
        request, "reports_committee.html",
        _ctx(session, "finance", r=report, chart=_bars(report["trend"]), prev=(prev_y, prev_m), nxt=(next_y, next_m), months=months, years=range(date.today().year - 6, date.today().year + 1), sub="reports", page="committee"),
    )


@router.get("/app/finance/reports/committee.pdf")
def committee_pdf(year: int | None = None, month: int | None = None, session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    y, m = _month(year, month)
    pdf = render_committee_pdf(reports.committee_report(db, y, m), finance.get_setting(db, "club_name"))
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="money-report-{y}-{m:02d}.pdf"'})


@router.get("/app/finance/reports/income", response_class=HTMLResponse)
def income_page(request: Request, start: str = "", end: str = "", basis: str = "invoiced", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, fy_end)
    if last < first:
        first, last = last, first
    ly_first, ly_last = reports.same_period_last_year(first, last)
    basis = "received" if basis == "received" else "invoiced"
    if basis == "invoiced":
        now, before = reports.invoiced_by_nominal(db, first, last), reports.invoiced_by_nominal(db, ly_first, ly_last)
        spent_now = spent_before = {}
    else:
        cash, cash_before = reports.cash_summary(db, first, last), reports.cash_summary(db, ly_first, ly_last)
        now, before, spent_now, spent_before = cash.receipts, cash_before.receipts, cash.payments, cash_before.payments
    labels = reports.nominal_labels(db)
    rows = [(c, labels.get(c, c), now.get(c, 0), before.get(c, 0)) for c in sorted(set(now) | set(before), key=lambda c: (c == "", c))]
    spent = [(c, labels.get(c, c), spent_now.get(c, 0), spent_before.get(c, 0)) for c in sorted(set(spent_now) | set(spent_before), key=lambda c: (c == "", c))]
    return templates.TemplateResponse(
        request, "reports_income.html",
        _ctx(session, "finance", rows=rows, spent=spent, start=first, end=last, ly=(ly_first, ly_last), basis=basis, written_off=reports.written_off_in(db, first, last) if basis == "invoiced" else 0, sub="reports", page="income"),
    )


@router.get("/app/finance/reports/income.csv")
def income_csv(start: str = "", end: str = "", basis: str = "invoiced", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, fy_end)
    ly_first, ly_last = reports.same_period_last_year(first, last)
    if basis == "received":
        cash, before = reports.cash_summary(db, first, last), reports.cash_summary(db, ly_first, ly_last)
        sections = [("receipts", cash.receipts, before.receipts), ("payments", cash.payments, before.payments)]
    else:
        sections = [("invoiced", reports.invoiced_by_nominal(db, first, last), reports.invoiced_by_nominal(db, ly_first, ly_last))]
    labels = reports.nominal_labels(db)
    rows = [[kind, code, labels.get(code, code), f"{now.get(code, 0) / 100:.2f}", f"{before.get(code, 0) / 100:.2f}"] for kind, now, before in sections for code in sorted(set(now) | set(before))]
    return _csv_response(f"income-{basis}-{first}-{last}.csv", ["section", "nominal", "label", f"{first} to {last}", f"{ly_first} to {ly_last}"], rows)


@router.get("/app/finance/reports/receipts-payments", response_class=HTMLResponse)
def receipts_payments_page(request: Request, start: str = "", end: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, fy_end)
    if last < first:
        first, last = last, first
    result = reports.receipts_and_payments(db, first, last)
    receipts = [(c, result.labels.get(c, c), v) for c, v in sorted(result.cash.receipts.items(), key=lambda kv: (kv[0] == "", kv[0]))]
    payments = [(c, result.labels.get(c, c), v) for c, v in sorted(result.cash.payments.items(), key=lambda kv: (kv[0] == "", kv[0]))]
    return templates.TemplateResponse(request, "reports_rp.html", _ctx(session, "finance", rp=result, receipts=receipts, payments=payments, start=first, end=last, sub="reports", page="rp"))


@router.get("/app/finance/reports/receipts-payments.csv")
def receipts_payments_csv(start: str = "", end: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, fy_end)
    result = reports.receipts_and_payments(db, first, last)
    rows: list[list] = [["opening balance", "", "", f"{result.opening / 100:.2f}"]]
    rows += [["receipt", c, result.labels.get(c, c), f"{v / 100:.2f}"] for c, v in sorted(result.cash.receipts.items())]
    rows += [["payment", c, result.labels.get(c, c), f"{-v / 100:.2f}"] for c, v in sorted(result.cash.payments.items())]
    rows += [["transfers in", "", "", f"{result.cash.transfers_in / 100:.2f}"], ["transfers out", "", "", f"{-result.cash.transfers_out / 100:.2f}"]]
    rows += [["not yet categorised", "", "", f"{(result.cash.waiting_in - result.cash.waiting_out) / 100:.2f}"], ["closing balance (from the lines)", "", "", f"{result.closing_actual / 100:.2f}"]]
    return _csv_response(f"receipts-payments-{first}-{last}.csv", ["line", "nominal", "label", "amount"], rows)


@router.get("/app/finance/reports/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, start: str = "", end: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, min(fy_end, date.today()))
    if last < first:
        first, last = last, first
    return templates.TemplateResponse(request, "reports_accounts.html", _ctx(session, "finance", accounts=reports.account_months(db, first, last), start=first, end=last, sub="reports", page="accounts"))


@router.get("/app/finance/reports/accounts.csv")
def accounts_csv(start: str = "", end: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    fy_start, fy_end = reports.fy_bounds(db)
    first, last = _date(start, fy_start), _date(end, min(fy_end, date.today()))
    rows = [[a.account.name, m.month.strftime("%Y-%m"), f"{m.opening / 100:.2f}", f"{m.money_in / 100:.2f}", f"{m.money_out / 100:.2f}", f"{m.closing / 100:.2f}"] for a in reports.account_months(db, first, last) for m in a.months]
    return _csv_response(f"accounts-by-month-{first}-{last}.csv", ["account", "month", "opening", "in", "out", "closing"], rows)


@router.get("/app/finance/reports/debtors", response_class=HTMLResponse)
def debtors_page(request: Request, as_at: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    day = _date(as_at, date.today())
    rows = reports.debtors_as_at(db, day)
    return templates.TemplateResponse(request, "reports_debtors.html", _ctx(session, "finance", rows=rows, buckets=reports.debtor_buckets(rows), as_at=day, total=sum(r.outstanding for r in rows), sub="reports", page="debtors"))


@router.get("/app/finance/reports/debtors.csv")
def debtors_csv(as_at: str = "", session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    day = _date(as_at, date.today())
    rows = reports.debtors_as_at(db, day)
    return _csv_response(f"debtors-{day}.csv", ["contact", "invoice", "issued", "due", "days_overdue", "outstanding"], [[r.contact_name, r.invoice.reference, r.invoice.issue_date, r.invoice.due_date, r.days_overdue, f"{r.outstanding / 100:.2f}"] for r in rows])


@router.get("/app/finance/reports/membership", response_class=HTMLResponse)
def membership_page(request: Request, session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    return templates.TemplateResponse(request, "reports_members.html", _ctx(session, "finance", stats=reports.membership_stats(db), sub="reports", page="membership"))


@router.get("/app/finance/reports/membership.csv")
def membership_csv(session: SessionRecord = Depends(ui_session(perm="reports.read")), db: Session = Depends(get_db)) -> Response:
    stats = reports.membership_stats(db)
    rows = [[r["type"], *[r["counts"][y] for y in stats["years"]]] for r in stats["rows"]]
    rows.append(["Total", *[stats["totals"][y] for y in stats["years"]]])
    return _csv_response("membership-by-type-and-year.csv", ["type", *stats["years"]], rows)


@router.get("/app/finance/reports/{other}")
def unknown_report(other: str) -> Response:
    raise UiError(404, "Unknown report")
