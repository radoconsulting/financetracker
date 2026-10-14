"""PDF version of the yearly tax report (reportlab, no system dependencies)."""
from __future__ import annotations

import io
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .engine.income_tax import CATEGORIES
from .models import LAW_TYPES
from .taxreport import TaxYearReport

NAVY = colors.HexColor("#1F3A5F")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#EEF2F6")
LINE = colors.HexColor("#C9D3DE")

BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=8, leading=10, textColor=GREY)
CELL = ParagraphStyle("cell", parent=BASE, fontSize=8, leading=10)
CELL_R = ParagraphStyle("cellr", parent=CELL, alignment=TA_RIGHT)
H1 = ParagraphStyle("h1", parent=BASE, fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=NAVY)
H2 = ParagraphStyle("h2", parent=BASE, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=NAVY,
                    spaceBefore=10, spaceAfter=4)
H3 = ParagraphStyle("h3", parent=BASE, fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=6, spaceAfter=3)


def eur(v, dash=True) -> str:
    if v is None or (dash and abs(v) < 0.005):
        return "–"
    return f"-€{-v:,.2f}" if v < 0 else f"€{v:,.2f}"


def _p(text, style=CELL):
    return Paragraph(escape(str(text)), style)


def _table(rows, widths, right=(), total_rows=(), header=True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE), ("TOPPADDING", (0, 0), (-1, -1), 2),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8)]
    for c in right:
        style.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    for r in total_rows:
        style += [("FONT", (0, r), (-1, r), "Helvetica-Bold", 8), ("LINEABOVE", (0, r), (-1, r), 0.8, NAVY),
                  ("BACKGROUND", (0, r), (-1, r), LIGHT)]
    t.setStyle(TableStyle(style))
    return t


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(GREY)
    canvas.drawString(15 * mm, 10 * mm, f"{doc.title} · made with meerwaarde on {datetime.now():%d-%m-%Y} · not tax advice")
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"page {doc.page}")
    canvas.restoreState()


def _cgt(ytr: TaxYearReport, pid: str) -> list:
    p = ytr.cgt["people"][pid]
    rows = [["Type (as the law defines it)", "Gains", "Losses", "Net"]]
    for k, (name, _nl) in LAW_TYPES.items():
        v = p["per_type"][k]
        if abs(v["gains"]) + abs(v["losses"]) > 0.005:
            rows.append([name, eur(v["gains"]), eur(v["losses"]), eur(v["gains"] + v["losses"])])
    if len(rows) == 1:
        rows.append(["No sales in this year", "", "", ""])
    rows.append(["Net taxable gain", "", "", eur(p["net"], dash=False)])
    e = ytr.exemption.get(pid)
    carried = f" (incl. €{e.carried:,.0f} carried forward)" if e and e.carried else ""
    rows.append([f"Exemption available{carried}", "", "", eur(p["exemption"], dash=False)])
    rows.append(["Taxable above the exemption", "", "", eur(p["taxable"], dash=False)])
    rows.append(["Meerwaardebelasting (10%)", "", "", eur(p["tax"], dash=False)])
    out = [_table(rows, [80 * mm, 30 * mm, 30 * mm, 30 * mm], right=(1, 2, 3), total_rows=(len(rows) - 1,))]
    if e:
        frm = ", ".join(f"{y}: €{a:,.0f}" for y, a in e.carried_in) or "none"
        out.append(Spacer(1, 3))
        out.append(Paragraph(
            f"Exemption {ytr.year}: own amount €{e.base:,.0f}, carried forward from earlier years {escape(frm)}; used "
            f"€{e.used:,.2f}. Carried to the following years: €{e.carry_out:,.0f}"
            + (" (only if the rest of the year adds no gain)." if not ytr.complete else "."), SMALL))
    return out


def _income(ytr: TaxYearReport, pid: str) -> list:
    it = ytr.income[pid]
    rows = [["Income", "Broker", "Gross", "Foreign tax", "Net (taxable base)"]]
    for l in it.lines:
        rows.append([CATEGORIES[l.category], l.broker, eur(l.gross), eur(l.foreign_tax), eur(l.net)])
    if len(rows) == 1:
        rows.append(["No dividends or interest", "", "", "", ""])
    rows.append(["Dividend exemption (shares only)", "", "", "", eur(-it.exemption_used)])
    rows.append(["Taxable at 30%", "", "", "", eur(it.taxable, dash=False)])
    rows.append(["Tax to pay (30%)", "", "", "", eur(it.tax, dash=False)])
    return [_table(rows, [62 * mm, 28 * mm, 26 * mm, 26 * mm, 30 * mm], right=(2, 3, 4), total_rows=(len(rows) - 1,))]


def tax_report_pdf(ytr: TaxYearReport, target=None):
    """Render the report; returns bytes, or writes to `target` (path) and returns it."""
    buf = io.BytesIO() if target is None else str(target)
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                            bottomMargin=18 * mm, title=ytr.title, author="meerwaarde")
    many = len(ytr.persons) > 1
    s: list = [Paragraph(escape(ytr.title), H1), Paragraph(escape(ytr.subtitle), SMALL), Spacer(1, 6)]

    # summary box
    cgt_tax = ytr.cgt["tax"]
    inc_tax = sum(i.tax for i in ytr.income.values())
    tob = float(ytr.tob["amount_eur"].sum()) if not ytr.tob.empty else 0.0
    s.append(_table([["Meerwaardebelasting", "Tax on dividends and interest", "Stock exchange tax paid"],
                     [eur(cgt_tax, dash=False), eur(inc_tax, dash=False), eur(tob, dash=False)]],
                    [60 * mm, 60 * mm, 60 * mm]))
    s.append(Paragraph("Estimates from your broker exports. Stock exchange tax was withheld by the brokers; it is shown "
                       "for completeness.", SMALL))

    s.append(Paragraph("1. Meerwaardebelasting (capital gains, 10%)", H2))
    for pid, name in ytr.persons:
        block = ([Paragraph(escape(name), H3)] if many else []) + _cgt(ytr, pid)
        s.append(KeepTogether(block))
    if not ytr.sales.empty:
        s.append(Paragraph(f"Sales in {ytr.year}", H3))
        rows = [["Date", "Broker", "Name", "Qty", "Proceeds", "Purchase value", "Taxable gain"]]
        for r in ytr.sales.itertuples():
            rows.append([f"{r.date:%d-%m-%Y}" if r.date else "", r.broker, _p(r.name), f"{r.qty:,.8g}",
                         eur(r.proceeds_eur), eur(r.acquisition_eur), eur(r.taxable_gain_eur, dash=False)])
        s.append(_table(rows, [18 * mm, 16 * mm, 62 * mm, 16 * mm, 23 * mm, 23 * mm, 22 * mm], right=(3, 4, 5, 6)))
        s.append(Paragraph("Purchase value: the 31-12-2025 price for units held before 2026 (or the higher original "
                           "price, never creating a loss); the purchase price for units bought later; costs left out."
                           + (" Per-person shares are in the Excel version." if many else ""), SMALL))

    s.append(Paragraph("2. Dividends and interest (30%)", H2))
    for pid, name in ytr.persons:
        block = ([Paragraph(escape(name), H3)] if many else []) + _income(ytr, pid)
        s.append(KeepTogether(block))
    s.append(Paragraph("The Belgian 30% applies to the amount after foreign tax. Dividends from shares are exempt up to "
                       f"€{next(iter(ytr.income.values())).exemption_available:,.0f} per person; distributions of funds "
                       "and ETFs are not.", SMALL))

    s.append(Paragraph("3. Stock exchange tax (beurstaks)", H2))
    rows = [["Broker", "Trades", "Amount", "Basis"]]
    for r in ytr.tob.itertuples():
        rows.append([r.broker, str(r.trades), eur(r.amount_eur), _p(r.basis)])
    s.append(_table(rows, [30 * mm, 18 * mm, 28 * mm, 104 * mm], right=(1, 2)))

    s.append(Paragraph("4. Checklist", H2))
    for i, c in enumerate(ytr.checklist, 1):
        s.append(Paragraph(f"[&nbsp;&nbsp;] {i}. {escape(c)}", BASE))
        s.append(Spacer(1, 2))
    rows = [["Broker", "Account held at", "Country"]] + [[a["broker"], a["institution"], a["country"]] for a in ytr.accounts]
    s.append(Spacer(1, 4))
    s.append(_table(rows, [30 * mm, 110 * mm, 40 * mm]))

    if ytr.notes:
        s.append(Paragraph("Notes", H2))
        for n in ytr.notes:
            s.append(Paragraph("• " + escape(n), BASE))
    s.append(Paragraph("How it's calculated", H2))
    for t in ytr.rules_text:
        s.append(Paragraph("• " + escape(t), SMALL))

    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue() if target is None else target
