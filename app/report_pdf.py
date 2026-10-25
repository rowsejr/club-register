"""The committee money report as a PDF, laid out for one or two A4 pages to hand round a meeting."""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.reports import Compare

BRAND = colors.HexColor("#0b3d6e")


def _gbp(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}£{abs(minor) / 100:,.2f}"


def _comparison(title: str, rows: list[Compare], styles, small) -> list:
    if not rows:
        return [Paragraph(f"<b>{title}</b>", styles["Heading3"]), Paragraph("Nothing in the period.", small), Spacer(1, 4 * mm)]
    data = [["", "This month", "Same month last year", "Year to date", "Last year to date"]]
    data += [[f"{r.code} {r.label}".strip(), _gbp(r.month), _gbp(r.month_last_year), _gbp(r.ytd), _gbp(r.ytd_last_year)] for r in rows]
    data.append(["Total", _gbp(sum(r.month for r in rows)), _gbp(sum(r.month_last_year for r in rows)), _gbp(sum(r.ytd for r in rows)), _gbp(sum(r.ytd_last_year for r in rows))])
    table = Table(data, colWidths=[62 * mm, 28 * mm, 34 * mm, 28 * mm, 30 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.lightgrey),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
            ]
        )
    )
    return [Paragraph(f"<b>{title}</b>", styles["Heading3"]), table, Spacer(1, 5 * mm)]


def render_committee_pdf(report: dict, club_name: str) -> bytes:
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=9, leading=12)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=f"Money report {report['month_name']}")
    story: list = [Paragraph(f"{club_name}", styles["Title"]), Paragraph(f"<b>Money report for the committee: {report['month_name']}</b>", styles["Heading2"])]
    story.append(Paragraph(f"Financial year from {report['fy_start'].strftime('%d %B %Y')}. All figures in pounds.", small))
    story.append(Spacer(1, 4 * mm))

    positions = [["Account", "Balance at month end", "Lines to deal with"]]
    for p in report["positions"]:
        shown = p.statement if p.statement is not None else p.closing
        positions.append([p.account.name, _gbp(shown) + ("" if p.agrees else "  (check: lines add up to " + _gbp(p.closing) + ")"), str(p.waiting)])
    positions.append(["Total", _gbp(sum((p.statement if p.statement is not None else p.closing) for p in report["positions"])), ""])
    table = Table(positions, colWidths=[60 * mm, 85 * mm, 35 * mm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), BRAND), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [Paragraph("<b>Bank balances</b>", styles["Heading3"]), table, Spacer(1, 5 * mm)]

    story += _comparison("Income invoiced", report["invoiced"], styles, small)
    story += _comparison("Money received", report["received"], styles, small)
    story += _comparison("Money spent", report["spent"], styles, small)

    d = report["debtors"]
    b = d["buckets"]
    story.append(Paragraph(f"<b>Who owes the club</b>: {_gbp(d['total'])} from {d['people']} people. Not yet due {_gbp(b['not_due'])}; 1 to 30 days late {_gbp(b['d1_30'])}; 31 to 60 {_gbp(b['d31_60'])}; 61 to 90 {_gbp(b['d61_90'])}; over 90 days {_gbp(b['d90_plus'])}.", small))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"<b>Membership</b>: {report['members']['active']} memberships in force at the end of the month.", small))
    if report["waiting"]:
        story += [Spacer(1, 3 * mm), Paragraph(f"<b>Note</b>: {report['waiting']} bank line(s) are still waiting to be matched or categorised, so the figures above are not final.", small)]
    doc.build(story)
    return buffer.getvalue()
