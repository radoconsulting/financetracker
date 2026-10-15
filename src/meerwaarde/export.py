"""Excel export: one workbook with every table of the report.

How the workbook is built (docs/decisions.md D12):
- Results of the engine (FIFO costs, purchase values for the tax, taxable gains, the largest quantity without tax)
  are written as values, exactly as the app shows them.
- Arithmetic on those values is a formula (value = quantity × price, profit = proceeds − cost) and so is every
  total, so the workbook stays consistent when a price, the exemption or the rate is changed in Excel.
- Cells meant to be changed are blue on light yellow: the exemption and rate on Summary, other sales on Simulator.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import LAW_TYPES
from .report import RULES_TEXT, Report, Simulation

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ---------------------------------------------------------------- formats and styles
EUR = '€ #,##0.00;[Red]-€ #,##0.00;"–"'
PRICE = "#,##0.00########"
QTY = "General"
DATE = "dd-mm-yyyy"
PCT = "0%"
YEAR = "0"
COUNT = '0;-0;"–"'

FONT = "Arial"
BODY = Font(name=FONT, size=10)
BOLD = Font(name=FONT, size=10, bold=True)
TITLE = Font(name=FONT, size=14, bold=True, color="1F3A5F")
SECTION = Font(name=FONT, size=11, bold=True, color="1F3A5F")
NOTE = Font(name=FONT, size=9, italic=True, color="666666")
HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
INPUT = Font(name=FONT, size=10, color="0000FF")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
TOTAL_BORDER = Border(top=Side(style="thin", color="1F3A5F"))
WRAP = Alignment(wrap_text=True, vertical="top")
HEAD_ALIGN = Alignment(wrap_text=True, vertical="center")

SHEETS = ("Summary", "Positions", "Sales", "Income", "Meerwaardebelasting", "Exemption", "Income tax", "Simulator",
          "Year-end", "Switch", "Reynders tax", "Performance", "Manual positions", "Prices", "How calculated")


@dataclass
class Col:
    key: str
    header: str
    fmt: str | None = None
    width: float = 12.0
    formula: str | None = None    # e.g. "={qty}{r}*{price_eur}{r}": {key} is that column's letter, {r} the row


# ---------------------------------------------------------------- helpers
def _clean(v):
    """pandas/numpy scalars to plain Python values openpyxl can write; NaN/NaT become empty cells."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT:
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def _write(ws, row, col, value, font=BODY, fmt=None, fill=None, align=None, border=None):
    c = ws.cell(row, col, value)
    c.font = font
    if fmt:
        c.number_format = fmt
    if fill:
        c.fill = fill
    if align:
        c.alignment = align
    if border:
        c.border = border
    return c


def _width(ws, col: int, width: float):
    letter = get_column_letter(col)
    ws.column_dimensions[letter].width = max(ws.column_dimensions[letter].width or 0, width)


def _header(ws, row, headers, start_col=1):
    for i, h in enumerate(headers, start_col):
        _write(ws, row, i, h, HEAD, fill=HEAD_FILL, align=HEAD_ALIGN)
    ws.row_dimensions[row].height = 28


def _table(ws, cols: list[Col], rows: list[dict], top: int = 1, freeze: bool = True, filters: bool = True):
    """Header at `top`, one row per record. Returns (first data row, last data row, column letters by key)."""
    letters = {c.key: get_column_letter(i) for i, c in enumerate(cols, 1)}
    _header(ws, top, [c.header for c in cols])
    for i, c in enumerate(cols, 1):
        _width(ws, i, c.width)
    for n, rec in enumerate(rows):
        r = top + 1 + n
        for i, c in enumerate(cols, 1):
            v = c.formula.format(r=r, **letters) if c.formula else rec.get(c.key)
            _write(ws, r, i, v, fmt=c.fmt)
    first, last = top + 1, top + len(rows)
    if filters:
        ws.auto_filter.ref = f"A{top}:{get_column_letter(len(cols))}{max(top, last)}"
    if freeze:
        ws.freeze_panes = f"A{top + 1}"
    return first, last, letters


def _rng(sheet: str, letter: str, first: int, last: int) -> str:
    return f"'{sheet}'!${letter}${first}:${letter}${max(first, last)}"


def _page(ws):
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True


# ---------------------------------------------------------------- data sheets
POSITIONS = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("asset_class", "Class", width=10), Col("qty", "Quantity", QTY, 12),
    Col("cost_eur", "Cost incl. costs (€)", EUR, 16), Col("price_eur", "Price (€)", PRICE, 13),
    Col("price_date", "Price date", DATE, 11), Col("price_source", "Price source", width=24),
    Col("value_eur", "Value (€)", EUR, 16, '=IF({price_eur}{r}="","",{qty}{r}*{price_eur}{r})'),
    Col("unrealized_eur", "Unrealized (€)", EUR, 16, '=IF({value_eur}{r}="","",{value_eur}{r}-{cost_eur}{r})'),
    Col("note", "Note", width=50),
]
SALES = [
    Col("broker", "Broker", width=10), Col("date", "Date", DATE, 11), Col("asset", "Asset", width=15),
    Col("name", "Name", width=36), Col("asset_class", "Class", width=10), Col("qty", "Quantity", QTY, 12),
    Col("proceeds_eur", "Proceeds after costs (€)", EUR, 16), Col("cost_eur", "Cost incl. costs (€)", EUR, 16),
    Col("pnl_eur", "Profit (€)", EUR, 15, "={proceeds_eur}{r}-{cost_eur}{r}"), Col("note", "Note", width=40),
]
INCOME = [
    Col("broker", "Broker", width=10), Col("date", "Date", DATE, 11), Col("asset", "Asset", width=15),
    Col("name", "Name", width=36), Col("kind", "Kind", width=12), Col("amount_eur", "Amount (€)", EUR, 15),
    Col("amount_local", "Amount (local)", "#,##0.00", 14), Col("currency", "Currency", width=10),
]
TAX = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("type", "Type (as the law defines it)", width=20), Col("status", "Status", width=8),
    Col("date", "Sale date", DATE, 11), Col("qty", "Quantity", QTY, 12), Col("price_eur", "Price (€)", PRICE, 13),
    Col("price_31dec_eur", "Price 31-12-2025 (€)", PRICE, 13),
    Col("avg_purchase_eur", "Original purchase price, average (€)", PRICE, 15),
    Col("proceeds_eur", "Proceeds (€)", EUR, 16), Col("acquisition_eur", "Purchase value used (€)", EUR, 16),
    Col("taxable_gain_eur", "Taxable gain (€)", EUR, 16),
    Col("reynders_base_eur", "Interest part, Reynders tax (€)", EUR, 16), Col("missing", "Missing", width=16),
]
SIM = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("type", "Type", width=20), Col("held", "Held", QTY, 12), Col("price", "Price used (€)", PRICE, 13),
    Col("sell", "Selling", QTY, 12), Col("gain", "Taxable gain (€)", EUR, 16), Col("proceeds", "Proceeds (€)", EUR, 16),
    Col("max_qty", "Max without tax", QTY, 13),
    Col("max_value", "Max without tax (€)", EUR, 16, '=IF({price}{r}="","",{max_qty}{r}*{price}{r})'),
]
PRICES = [
    Col("asset", "Asset", width=15), Col("name", "Name", width=36), Col("asset_class", "Class", width=10),
    Col("price_now", "Price now (€)", PRICE, 13), Col("now_date", "Date", DATE, 11), Col("now_source", "Source", width=24),
    Col("price_ref", "Price 31-12-2025 (€)", PRICE, 13), Col("ref_date", "Date", DATE, 11),
    Col("ref_source", "Source", width=24),
]

MANUAL = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("asset_class", "Class", width=10), Col("type", "Type (as the law defines it)", width=20),
    Col("date", "Date", DATE, 11), Col("qty", "Quantity (− = sold)", QTY, 12), Col("price_eur", "Price (€)", PRICE, 13),
    Col("costs_eur", "Costs (€)", EUR, 12),
    Col("cash_eur", "Cash effect after costs (€)", EUR, 16, '=IF({qty}{r}="","",-{qty}{r}*{price_eur}{r}-{costs_eur}{r})'),
    Col("price_now_eur", "Price now (€)", PRICE, 13), Col("price_now_date", "Price date", DATE, 11),
    Col("price_31dec_eur", "Value 31-12-2025 (€)", PRICE, 13), Col("note", "Note", width=40),
]

REYNDERS = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("status", "Status", width=8), Col("date", "Sale date", DATE, 11), Col("qty", "Quantity", QTY, 12),
    Col("price_eur", "Price (€)", PRICE, 13), Col("base_eur", "Interest part, taxed at 30% (€)", EUR, 18),
    Col("method", "Method", width=28),
]


def _reynders_sheet(ws, table, rate: float, notes: list[str]) -> None:
    """Lines subject to the Reynders tax with a live total: tax = base × rate (an input)."""
    first, last, L = _table(ws, REYNDERS, _records(table))
    r = max(last, first - 1) + 2
    b = L["base_eur"]
    _write(ws, r, 1, "Rate", BOLD)
    _write(ws, r, 2, rate, INPUT, PCT, INPUT_FILL)
    _write(ws, r + 1, 1, "Interest part", BOLD)
    _write(ws, r + 1, 2, f"=SUM({b}{first}:{b}{max(first, last)})" if last >= first else 0, fmt=EUR)
    _write(ws, r + 2, 1, "Reynders tax", BOLD, border=TOTAL_BORDER)
    _write(ws, r + 2, 2, f"=B{r + 1}*B{r}", BOLD, EUR, border=TOTAL_BORDER)
    for i, n in enumerate(notes):
        _write(ws, r + 4 + i, 1, n, NOTE)


PERF = [
    Col("broker", "Broker", width=10), Col("invested", "Money in (€)", EUR, 15), Col("returned", "Money out (€)", EUR, 15),
    Col("value", "Value today (€)", EUR, 15),
    Col("gain", "Gain (€)", EUR, 14, "={value}{r}+{returned}{r}-{invested}{r}"),
    Col("xirr", "Money-weighted (XIRR, per year)", "0.0%", 16), Col("twr", "Time-weighted, total", "0.0%", 14),
    Col("twr_annual", "Time-weighted, per year", "0.0%", 14), Col("benchmark_value", "Benchmark value today (€)", EUR, 17),
    Col("benchmark_xirr", "Benchmark XIRR (per year)", "0.0%", 15),
]
PERF_SERIES = [
    Col("date", "Date", DATE, 11), Col("value", "Value (€)", EUR, 15), Col("invested", "Net money in (€)", EUR, 15),
    Col("benchmark", "Benchmark (€)", EUR, 15),
]


def _performance_sheet(ws, rep: Report, perf) -> None:
    from openpyxl.chart import LineChart, Reference
    first, last, _L = _table(ws, PERF, _records(rep.performance_table(perf)), filters=False)
    top = max(last, first) + 3
    _write(ws, top - 1, 1, f"Every month, all brokers · benchmark: {perf.benchmark[1]} ({perf.benchmark[0]})"
           + (f", prices {perf.benchmark_source}" if perf.benchmark_source else ""), BOLD)
    sf, sl, _ = _table(ws, PERF_SERIES, _records(rep.performance_series(perf)), top=top, freeze=False, filters=False)
    if sl >= sf:
        chart = LineChart()
        chart.title = "Value, money in and the benchmark"
        chart.height, chart.width = 9, 22
        chart.y_axis.title = "€"
        chart.y_axis.numFmt = "#,##0"
        data = Reference(ws, min_col=2, max_col=4, min_row=top, max_row=sl)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=1, min_row=sf, max_row=sl))
        ws.add_chart(chart, f"F{top}")
    r = sl + 2
    for n in perf.notes:
        _write(ws, r, 1, n, NOTE)
        r += 1


REYNDERS_NOTES = [
    "Status Open = if you sold everything today. Per-person shares follow the broker/position ownership.",
    "Method: TIS = TIS at the sale minus TIS at the purchase; asset test = real gain × bond share; worst case = the whole "
    "real gain (no TIS or bond share entered). The interest part is taken out of the gain for the 10%.",
]


def _price_rows(rep: Report) -> list[dict]:
    rows, seen = [], set()
    ref_date = rep.tax.rules.reference_date
    for h in rep.portfolio.holdings():
        if h.asset in seen:
            continue
        seen.add(h.asset)
        now = rep.prices.now(h)
        needs_ref = any(l.date.date() <= ref_date for l in h.lots)
        ref = rep.prices.reference(h) if needs_ref else None
        rows.append(dict(asset=h.asset, name=h.name, asset_class=h.asset_class,
                         price_now=now.value if now else None, now_date=now.on if now else None,
                         now_source=now.source if now else "missing",
                         price_ref=ref.value if ref else None, ref_date=ref.on if ref else None,
                         ref_source=ref.source if ref else ("missing" if needs_ref else "not needed (bought in 2026)")))
    return rows


# ---------------------------------------------------------------- summary
def _summary(ws, rep: Report, R: dict) -> dict:
    rules = rep.tax.rules
    _write(ws, 1, 1, "Profit and meerwaardebelasting", TITLE)
    _write(ws, 2, 1, f"Value date {rep.tax.today:%d-%m-%Y} · tax year {rules.year} · amounts in EUR · "
                     "every figure on this sheet is a formula over the other sheets", NOTE)
    for col, w in zip("ABCDEFGH", (34, 16, 16, 16, 16, 16, 16, 14)):
        ws.column_dimensions[col].width = w

    # profit per broker
    row = 4
    _write(ws, row, 1, "Profit per broker", SECTION)
    _header(ws, row + 1, ["Broker", "Realized", "Unrealized", "Dividends & interest", "Total profit", "Value now",
                          "Still invested", "Missing prices"])
    brokers = rep.portfolio.brokers()
    first = row + 2
    s, p, i = R["Sales"], R["Positions"], R["Income"]
    for n, b in enumerate(brokers):
        r = first + n
        _write(ws, r, 1, b)
        _write(ws, r, 2, f"=SUMIFS({s['pnl_eur']},{s['broker']},$A{r})", fmt=EUR)
        _write(ws, r, 3, f"=SUMIFS({p['unrealized_eur']},{p['broker']},$A{r})", fmt=EUR)
        _write(ws, r, 4, f"=SUMIFS({i['amount_eur']},{i['broker']},$A{r})", fmt=EUR)
        _write(ws, r, 5, f"=B{r}+C{r}+D{r}", fmt=EUR)
        _write(ws, r, 6, f"=SUMIFS({p['value_eur']},{p['broker']},$A{r})", fmt=EUR)
        _write(ws, r, 7, f"=SUMIFS({p['cost_eur']},{p['broker']},$A{r})", fmt=EUR)
        _write(ws, r, 8, f'=SUMPRODUCT(({p["broker"]}=$A{r})*({p["price_eur"]}=""))', fmt=COUNT)
    last = first + len(brokers) - 1
    tot = last + 1
    _write(ws, tot, 1, "All", BOLD, border=TOTAL_BORDER)
    for c in range(2, 9):
        L = get_column_letter(c)
        _write(ws, tot, c, f"=SUM({L}{first}:{L}{last})" if brokers else 0, BOLD, EUR if c < 8 else COUNT,
               border=TOTAL_BORDER)
    _write(ws, tot + 1, 1, "Realized: sales after costs, first in first out. Unrealized: open positions at the latest "
                           "price. Still invested: purchase cost of what you still hold.", NOTE)

    # realized per year
    row = tot + 3
    _write(ws, row, 1, "Realized profit per year", SECTION)
    by_year = rep.by_year()
    years = sorted({int(y) for y in by_year["year"]}) if not by_year.empty else []
    if not years:
        _write(ws, row + 1, 1, "No sales yet.", NOTE)
        row += 2
    else:
        hdr = row + 1
        _header(ws, hdr, ["Year"] + brokers + ["Total"])
        for n, y in enumerate(years):
            r = hdr + 1 + n
            _write(ws, r, 1, y, fmt=YEAR, align=Alignment(horizontal="left"))
            for m, b in enumerate(brokers):
                L = get_column_letter(2 + m)
                _write(ws, r, 2 + m, f'=SUMIFS({s["pnl_eur"]},{s["broker"]},{L}${hdr},{s["date"]},">="&DATE($A{r},1,1),'
                                     f'{s["date"]},"<="&DATE($A{r},12,31))', fmt=EUR)
            _write(ws, r, 2 + len(brokers), f"=SUM(B{r}:{get_column_letter(1 + len(brokers))}{r})", BOLD, EUR)
        row = hdr + len(years) + 1

    # meerwaardebelasting
    row += 1
    t = R["Meerwaardebelasting"]
    hh = rep.household
    other = rep.other_gains()
    avail = rep.exemptions()
    _write(ws, row, 1, f"Meerwaardebelasting {rules.year} if you sold everything on {rep.tax.today:%d-%m-%Y}", SECTION)
    _header(ws, row + 1, ["Type (as the law defines it)", "Sold this year", "Other sales (not in the exports)",
                          "If you sold the rest", "Net"])
    first = row + 2
    for n, (k, (name, _nl)) in enumerate(LAW_TYPES.items()):
        r = first + n
        _write(ws, r, 1, name)
        _write(ws, r, 2, f'=SUMIFS({t["taxable_gain_eur"]},{t["type"]},$A{r},{t["status"]},"Sold")', fmt=EUR)
        _write(ws, r, 3, sum(other[p].get(k, 0.0) for p in hh.ids), INPUT if hh.single else BODY, EUR,
               INPUT_FILL if hh.single else None)
        _write(ws, r, 4, f'=SUMIFS({t["taxable_gain_eur"]},{t["type"]},$A{r},{t["status"]},"Open")', fmt=EUR)
        _write(ws, r, 5, f"=B{r}+C{r}+D{r}", fmt=EUR)
    last = first + len(LAW_TYPES) - 1
    tot = last + 1
    _write(ws, tot, 1, "All types", BOLD, border=TOTAL_BORDER)
    for c in (2, 3, 4, 5):
        L = get_column_letter(c)
        _write(ws, tot, c, f"=SUM({L}{first}:{L}{last})", BOLD, EUR, border=TOTAL_BORDER)

    r = tot + 2
    if hh.single:
        pid = hh.ids[0]
        hist = rep.exemption_history(pid) if rules.year >= rep.exemption_rules().first_year else []
        carried = hist[-1].carried if hist else 0.0
        _write(ws, r, 1, "Net taxable gain")
        _write(ws, r, 2, f"=E{tot}", fmt=EUR)
        label = f"Exemption {rules.year} (change it here)"
        if carried:
            label = f"Exemption {rules.year}: own €{avail[pid] - carried:,.0f} + carried €{carried:,.0f} (change it here)"
        _write(ws, r + 1, 1, label)
        _write(ws, r + 1, 2, avail[pid], INPUT, EUR, INPUT_FILL)
        _write(ws, r + 2, 1, "Taxable above the exemption")
        _write(ws, r + 2, 2, f"=MAX(0,B{r}-B{r + 1})", fmt=EUR)
        _write(ws, r + 3, 1, "Rate (change it here)")
        _write(ws, r + 3, 2, rules.rate, INPUT, PCT, INPUT_FILL)
        _write(ws, r + 4, 1, "Meerwaardebelasting", BOLD, border=TOTAL_BORDER)
        _write(ws, r + 4, 2, f"=B{r + 2}*B{r + 3}", BOLD, EUR, border=TOTAL_BORDER)
        _write(ws, r + 5, 1, "Tax-free room left after this year's sales")
        _write(ws, r + 5, 2, f"=MAX(0,B{r + 1}-B{tot}-C{tot})", fmt=EUR)
        end = r + 7
        refs = {"exemption": f"'Summary'!$B${r + 1}", "rate": f"'Summary'!$B${r + 3}"}
    else:
        summ = rep.tax_summary(rep.sold_lines() + rep.tax.open_lines())
        _header(ws, r, ["Person", "Net taxable gain", "Exemption available", "Taxable above the exemption",
                        "Meerwaardebelasting"])
        for n, pid in enumerate(hh.ids):
            pp = summ["people"][pid]
            rr = r + 1 + n
            _write(ws, rr, 1, hh.name(pid))
            _write(ws, rr, 2, round(pp["net"], 2), fmt=EUR)
            _write(ws, rr, 3, pp["exemption"], fmt=EUR)
            _write(ws, rr, 4, f"=MAX(0,B{rr}-C{rr})", fmt=EUR)
            _write(ws, rr, 5, f"=D{rr}*{rules.rate}", fmt=EUR)
        tr = r + 1 + len(hh.ids)
        _write(ws, tr, 1, "Together", BOLD, border=TOTAL_BORDER)
        for c in (2, 3, 4, 5):
            L = get_column_letter(c)
            _write(ws, tr, c, f"=SUM({L}{r + 1}:{L}{tr - 1})", BOLD, EUR, border=TOTAL_BORDER)
        _write(ws, tr + 1, 1, "Each person is taxed on their share of every position (see How calculated), with their "
                              "own exemption; losses of one partner don't offset gains of the other.", NOTE)
        end = tr + 3
        refs = {}
    _write(ws, end, 1, "All types fall under 'andere financiële activa': one rate and one exemption, and losses "
                       "offset gains across types within the year. Blue figures on yellow can be changed.", NOTE)
    _write(ws, end + 1, 1, "This is a calculation from your exports, not tax advice. See 'How calculated'.", NOTE)
    return refs


# ---------------------------------------------------------------- exemption and income tax
EXEMPTION = [
    Col("person", "Person", width=14), Col("year", "Year", YEAR, 8), Col("base", "Own exemption (€)", EUR, 16),
    Col("carried_in", "Carried in (€)", EUR, 14), Col("available", "Available (€)", EUR, 15),
    Col("net_gain", "Net taxable gain (€)", EUR, 17), Col("used", "Used (€)", EUR, 15),
    Col("carry_out", "Carried forward (€)", EUR, 16), Col("expired", "Expired (€)", EUR, 12),
    Col("carried_from", "Carried in from", width=30),
]
INCOME_SUMMARY = [
    Col("year", "Year", YEAR, 8), Col("person", "Person", width=14),
    Col("shares_net", "Dividends from shares, net (€)", EUR, 17),
    Col("funds_net", "Distributions of funds and ETFs, net (€)", EUR, 19),
    Col("interest_net", "Interest and other income, net (€)", EUR, 16),
    Col("foreign_tax", "Foreign tax withheld (€)", EUR, 16), Col("exemption_used", "Dividend exemption used (€)", EUR, 17),
    Col("taxable", "Taxable at 30% (€)", EUR, 16,
        "=MAX(0,{shares_net}{r})-{exemption_used}{r}+MAX(0,{funds_net}{r})+MAX(0,{interest_net}{r})"),
    Col("tax", "Tax 30% (€)", EUR, 14, "={taxable}{r}*$B$2"),
    Col("be_withheld", "Already withheld in Belgium (€)", EUR, 17),
    Col("to_pay", "To pay; negative = refund (€)", EUR, 17, "={tax}{r}+{be_withheld}{r}"),
]
INCOME_DETAIL = [
    Col("year", "Year", YEAR, 8), Col("person", "Person", width=14), Col("broker", "Broker", width=10),
    Col("category", "Income", width=34), Col("received", "Payments", COUNT, 10), Col("gross_eur", "Gross (€)", EUR, 15),
    Col("foreign_tax_eur", "Foreign tax (€)", EUR, 15), Col("net_eur", "Net (€)", EUR, 15, "={gross_eur}{r}+{foreign_tax_eur}{r}"),
    Col("be_withheld_eur", "Belgian tax withheld (€)", EUR, 15),
]


def _exemption_sheet(ws, rep: Report):
    rule = rep.exemption_rules()
    _table(ws, EXEMPTION, _records(rep.exemption_table()))
    n = ws.max_row + 2
    _write(ws, n, 1, f"Unused exemption carries forward: at most €{rule.carry_per_year:,.0f} a year, usable for "
                     f"{rule.carry_years} years, carried amounts used first (rule: {rule.carry_rule}). Years before "
                     f"{rule.first_year} don't count. The current year is up to the value date.", NOTE)


def _income_tax_sheet(ws, rep: Report):
    _write(ws, 1, 1, "Tax on dividends and interest", TITLE)
    _write(ws, 2, 1, "Rate", BOLD)
    _write(ws, 2, 2, 0.30, INPUT, PCT, INPUT_FILL)
    _write(ws, 2, 3, "Foreign brokers don't withhold Belgian tax: declare these amounts. The 30% applies to the amount "
                     "after foreign tax; only dividends from shares get the exemption.", NOTE)
    _table(ws, INCOME_SUMMARY, _records(rep.income_tax_summary()), top=4)
    top = ws.max_row + 3
    _write(ws, top - 1, 1, "Per broker", SECTION)
    _table(ws, INCOME_DETAIL, _records(rep.income_tax_table()), top=top, freeze=False, filters=False)


# ---------------------------------------------------------------- year-end harvesting
HARVEST = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("qty", "Sell and buy back", QTY, 13), Col("price", "Price (€)", PRICE, 13),
    Col("value", "Value (€)", EUR, 15, "={qty}{r}*{price}{r}"), Col("gain", "Taxable gain (€)", EUR, 15),
    Col("tob_rate", "Stock exchange tax rate", "0.00%", 12), Col("tob_source", "Rate from", width=26),
    Col("tob", "Stock exchange tax ×2 (€)", EUR, 15), Col("commission", "Commission ×2 (€)", EUR, 14),
    Col("spread", "Spread ×2 (€)", EUR, 13), Col("costs", "Costs (€)", EUR, 14, "={tob}{r}+{commission}{r}+{spread}{r}"),
    Col("saved", "Tax saved later (€)", EUR, 15, "={gain}{r}*$B$3"), Col("net", "Net (€)", EUR, 14, "={saved}{r}-{costs}{r}"),
]
LOSSES = [
    Col("broker", "Broker", width=10), Col("asset", "Asset", width=15), Col("name", "Name", width=36),
    Col("qty", "Quantity", QTY, 13), Col("value", "Value (€)", EUR, 15), Col("loss", "Taxable loss if sold (€)", EUR, 15),
    Col("saved", "Tax it removes this year (€)", EUR, 15),
]


def _harvest_sheet(ws, plan) -> None:
    _write(ws, 1, 1, f"Year-end {plan.year}: use the rest of the exemption", TITLE)
    _write(ws, 2, 1, "Sell these lots and buy them back before 31 December: their purchase value goes up without tax. "
                     "Costs: stock exchange tax and commission on both trades, plus the spread.", NOTE)
    _write(ws, 3, 1, "Rate meerwaardebelasting", BOLD)
    _write(ws, 3, 2, 0.10, INPUT, PCT, INPUT_FILL)
    row = 5
    _header(ws, row, ["Person", "Exemption", "Net gain so far", "Left", "Kept for the carry-forward", "To harvest"])
    for pid, name in plan.persons:
        row += 1
        s = plan.status[pid]
        for c, v in enumerate([name, s["exemption"], s["net"], s["room"], s["reserve"], s["target"]], 1):
            _write(ws, row, c, v if c == 1 else round(v, 2), fmt=None if c == 1 else EUR)
    top = row + 3
    _write(ws, top - 1, 1, "Proposed sales", SECTION)
    rows = [dict(broker=l.broker, asset=l.asset, name=l.name, qty=l.qty, price=l.price, gain=round(l.gain, 2),
                 tob_rate=l.tob_rate, tob_source=l.tob_source, tob=round(l.tob, 2), commission=round(l.commission, 2),
                 spread=round(l.spread, 2)) for l in plan.lines]
    first, last, L = _table(ws, HARVEST, rows, top=top, freeze=False)
    if rows:
        tr = last + 1
        _write(ws, tr, 1, "Total", BOLD, border=TOTAL_BORDER)
        for key in ("value", "gain", "tob", "commission", "spread", "costs", "saved", "net"):
            c = L[key]
            col = [cc.key for cc in HARVEST].index(key) + 1
            _write(ws, tr, col, f"=SUM({c}{first}:{c}{last})", BOLD, EUR, border=TOTAL_BORDER)
        row = tr + 3
    else:
        _write(ws, top + 1, 1, "Nothing worth harvesting with the default settings.", NOTE)
        row = top + 4
    if plan.losses:
        _write(ws, row - 1, 1, "Positions at a taxable loss (they only help when this year's gains are above the exemption)",
               SECTION)
        _table(ws, LOSSES, [dict(broker=l.broker, asset=l.asset, name=l.name, qty=l.qty, value=round(l.value, 2),
                                 loss=round(l.loss, 2), saved=round(l.tax_saved_now, 2)) for l in plan.losses],
               top=row, freeze=False, filters=False)
        row = ws.max_row + 2
    for n in plan.notes:
        _write(ws, row, 1, "• " + n, NOTE)
        row += 1


# ---------------------------------------------------------------- switch planner
SWITCH = [
    Col("fund", "Target", width=40), Col("isin", "ISIN", width=14), Col("tob", "Stock exchange tax", "0.00%", 11),
    Col("tob_from", "Rate from", width=28), Col("ter", "TER", "0.00%", 8),
    Col("cgt", "Meerwaardebelasting now (€)", EUR, 15), Col("costs", "Trading costs now (€)", EUR, 14),
    Col("now", "Cost if all now (€)", EUR, 14, "={cgt}{r}+{costs}{r}"),
    Col("div", "Dividend tax saved / yr (€)", EUR, 14), Col("back", "Back later as meerwaardebelasting / yr (€)", EUR, 16),
    Col("ter_saved", "Running cost saved / yr (€)", EUR, 14),
    Col("yearly", "Yearly effect (€)", EUR, 13, "={div}{r}-{back}{r}+{ter_saved}{r}"),
    Col("exit", "Extra stock exchange tax when sold later (€)", EUR, 16),
    Col("be", "Break-even (years)", "0.0", 11, '=IF({yearly}{r}>0,{now}{r}/{yearly}{r},"never")'),
    Col("net", "Net over the horizon, all now (€)", EUR, 16, "={yearly}{r}*$B$3-{now}{r}-{exit}{r}"),
    Col("net_spread", "Net over the horizon, spread (€)", EUR, 16), Col("checked", "Facts checked", width=9),
]


def _switch_sheet(ws, rep: Report, switch) -> None:
    from .engine.switch import compare, default_sources, net_spread, sources_for, spread_plan
    from .funds import CATALOG, WORLD_TARGETS
    if switch is None:
        srcs = sources_for(rep, default_sources(rep))
        cases = compare(rep, srcs, [CATALOG[i] for i in WORLD_TARGETS]) if srcs else []
        plans = {c.target.isin: spread_plan(rep, srcs, c) for c in cases}
        horizon = 10
    else:
        srcs, cases, plans, horizon = switch
    _write(ws, 1, 1, "Switch planner", TITLE)
    if not srcs or not cases:
        _write(ws, 2, 1, "Nothing to compare: no distributing ETFs held (pick positions in the app's Switch tab).", NOTE)
        return
    _write(ws, 2, 1, "Sell: " + ", ".join(f"{s.qty:g} {s.name} ({s.broker})" for s in srcs)
           + f" · value €{sum(s.value for s in srcs):,.2f} · dividends last 12 months €{sum(s.dividends_year for s in srcs):,.2f}",
           NOTE)
    _write(ws, 3, 1, "Horizon (years)", BOLD)
    _write(ws, 3, 2, horizon, INPUT, "0", INPUT_FILL)
    cases = sorted(cases, key=lambda c: net_spread(c, plans[c.target.isin]), reverse=True)
    rows = [dict(fund=f"{c.target.ticker} {c.target.name}".strip(), isin=c.target.isin, tob=c.target_tob,
                 tob_from=c.target_tob_source, ter=c.target.ter, cgt=round(c.cgt_now, 2), costs=round(c.costs, 2),
                 div=round(c.dividend_tax_saved, 2), back=round(c.dividend_tax_back, 2), ter_saved=round(c.ter_saved, 2),
                 exit=round(c.exit_tob_extra, 2), net_spread=round(net_spread(c, plans[c.target.isin]), 2),
                 checked="yes" if c.target.verified else "no") for c in cases]
    _table(ws, SWITCH, rows, top=5, freeze=False)
    best = cases[0]
    plan = plans[best.target.isin]
    row = 5 + len(rows) + 3
    _write(ws, row - 1, 1, f"Spread within the exemption, into {best.target.ticker or best.target.isin} "
                           "(today's prices; full exemption for this switch in later years)", SECTION)
    _header(ws, row, ["Year", "Sell (and buy the target)", "Value (€)", "Taxable gain (€)", "Costs (€)"])
    for st in plan.steps:
        row += 1
        _write(ws, row, 1, st.year, fmt=YEAR)
        _write(ws, row, 2, ", ".join(f"{q:g} × {k[0]} {k[1]}" for k, q in st.units.items()))
        _write(ws, row, 3, round(st.value, 2), fmt=EUR)
        _write(ws, row, 4, round(st.gain, 2), fmt=EUR)
        _write(ws, row, 5, round(st.costs, 2), fmt=EUR)
    row += 2
    for label, v in (("Meerwaardebelasting avoided (vs all now)", best.cgt_now),
                     ("Yearly benefit you wait for", plan.benefit_waited), ("Trading costs of the plan", plan.costs)):
        _write(ws, row, 1, label)
        _write(ws, row, 2, round(v, 2), fmt=EUR)
        row += 1
    row += 1
    for n in best.notes + ["Moving from an S&P 500 fund to a world fund changes what you own; that's a choice, not a cost.",
                           "Unchecked funds (suggested by Gemini) must be verified before buying."]:
        _write(ws, row, 1, "• " + n, NOTE)
        row += 1


# ---------------------------------------------------------------- simulator
def _simulator(ws, rep: Report, sim: Simulation, R: dict, refs: dict):
    t = R["Meerwaardebelasting"]
    _write(ws, 1, 1, "Simulator", TITLE)
    _write(ws, 2, 1, "The sales simulated in the app when this workbook was made, on top of this year's real sales. "
                     "Change the other sales below to see the tax change; quantities and the per-position figures "
                     "need the app (first in, first out).", NOTE)

    _write(ws, 4, 1, "Other sales this year that aren't in your exports (taxable gain; negative for a loss)", SECTION)
    _header(ws, 5, ["Type", "Taxable gain (€)"])
    first = 6
    hh = rep.household
    other = rep.other_gains()
    for n, (k, (name, _nl)) in enumerate(LAW_TYPES.items()):
        v = float(sim.extra.get(k, 0.0) or 0.0) + sum(other[p].get(k, 0.0) for p in hh.ids)
        _write(ws, first + n, 1, name)
        _write(ws, first + n, 2, v, INPUT if hh.single else BODY, EUR, INPUT_FILL if hh.single else None)
    last_extra = first + len(LAW_TYPES) - 1

    top = last_extra + 2
    table_top = top + 11
    rows = _records(sim.positions.assign(_selling=sim.positions["sell"] > 0)
                    .sort_values(["_selling", "max_value"], ascending=[False, False], na_position="last"))
    g0, g1 = table_top + 1, max(table_top + 1, table_top + len(rows))
    col = {c.key: get_column_letter(i) for i, c in enumerate(SIM, 1)}
    gain_rng = f"${col['gain']}${g0}:${col['gain']}${g1}"
    proceeds_rng = f"${col['proceeds']}${g0}:${col['proceeds']}${g1}"

    _write(ws, top, 1, "Result", SECTION)
    if not hh.single:                     # per person: values from the app (each has their own exemption)
        _header(ws, top + 1, ["Person", "Net taxable gain", "Exemption", "Room left", "Meerwaardebelasting"])
        for n, pid in enumerate(hh.ids):
            pp = sim.summary["people"][pid]
            for c, v in enumerate([hh.name(pid), pp["net"], pp["exemption"], pp["room"], pp["tax"]], 1):
                _write(ws, top + 2 + n, c, v, fmt=None if c == 1 else EUR)
        _write(ws, table_top - 1, 1, "Per position: what is sold, and the largest quantity you can sell on top of "
                                     "everything else without paying tax (for every owner)", SECTION)
        _table(ws, SIM, rows, top=table_top, freeze=False)
        ws.column_dimensions["A"].width = 44
        return
    items = [
        ("Sold this year (in your exports)", f'=SUMIFS({t["taxable_gain_eur"]},{t["status"]},"Sold")', BODY),
        ("Other sales (above; your profile's plus the app's)", f"=SUM(B{first}:B{last_extra})", BODY),
        ("Simulated sales (below)", f"=SUM({gain_rng})", BODY),
        ("Net taxable gain", f"=B{top + 1}+B{top + 2}+B{top + 3}", BOLD),
        ("Exemption (from Summary)", f"={refs['exemption']}", BODY),
        ("Tax-free room left (negative: over the exemption)", f"=B{top + 5}-B{top + 4}", BODY),
        ("Meerwaardebelasting", f"=MAX(0,B{top + 4}-B{top + 5})*{refs['rate']}", BOLD),
        ("Cash from the simulated sales", f"=SUM({proceeds_rng})", BODY),
    ]
    for n, (label, formula, font) in enumerate(items, 1):
        _write(ws, top + n, 1, label, font)
        _write(ws, top + n, 2, formula, font, EUR)

    _write(ws, table_top - 1, 1, "Per position: what is sold, and the largest quantity you can sell on top of everything "
                                 "else without paying tax", SECTION)
    _table(ws, SIM, rows, top=table_top, freeze=False)
    ws.column_dimensions["A"].width = 44


# ---------------------------------------------------------------- explanation
def _explain(ws, rep: Report, sim: Simulation):
    from . import __version__
    rules, o = rep.tax.rules, rep.portfolio.options
    ws.column_dimensions["A"].width = 120
    row = 1

    def line(text, font=BODY):
        nonlocal row
        c = _write(ws, row, 1, text, font, align=WRAP)
        n = max(1, math.ceil(len(str(text)) / 130))
        if n > 1:
            ws.row_dimensions[row].height = 13.5 * n
        row += 1
        return c

    def gap():
        nonlocal row
        row += 1

    yes = lambda b: "yes" if b else "no"  # noqa: E731
    line("How this workbook is calculated", TITLE)
    line(f"Made on {datetime.now():%d-%m-%Y %H:%M} with meerwaarde {__version__}. Value date "
         f"{rep.tax.today:%d-%m-%Y}. Tax year {rules.year}. Amounts in EUR.", NOTE)
    gap()
    line("Sheets", SECTION)
    for text in [
        "Summary: profit per broker and per year, and the meerwaardebelasting if you sold everything on the value date.",
        "Positions: open positions at the latest price, with the date and source of every price.",
        "Sales: every sale since the first transaction, first in first out, after costs.",
        "Income: dividends, interest and tax withheld.",
        "Meerwaardebelasting: sales in the tax year (status Sold) and every open position as if sold on the value date "
        "(status Open), with the purchase value the law allows and the taxable gain.",
        "Exemption: per person and year, the exemption available (own amount plus what earlier years carried forward), "
        "what was used and what carries forward.",
        "Income tax: dividends and interest per year, person and broker, with foreign tax, the dividend exemption and "
        "the 30% Belgian tax to declare.",
        "Simulator: the sales simulated in the app, other sales you entered, and per position the largest quantity you "
        "can still sell without tax.",
        "Year-end: lots to sell and buy back before 31 December to use the rest of the exemption, with the tax saved "
        "later against the costs now, and positions at a taxable loss.",
        "Switch: selling positions for a target fund: cost now, yearly effect, break-even, and a plan spread over "
        "the years within the exemption.",
        "Prices: current and 31-12-2025 prices with their date and source.",
    ]:
        line("• " + text)
    gap()
    line("Values and formulas", SECTION)
    for text in [
        "Results of the calculation (first-in-first-out costs, purchase values for the tax, taxable gains, the largest "
        "quantity without tax) are values: they are what the app shows and don't change when you edit the workbook.",
        "Arithmetic on them is a formula: value = quantity × price, unrealized = value − cost, profit = proceeds − cost, "
        "and every total. Change a price on Positions and the profit figures follow; the meerwaardebelasting sheets "
        "don't, because they need the first-in-first-out calculation.",
        "Blue figures on a yellow background are inputs: the exemption and the rate on Summary, other sales on Simulator.",
    ]:
        line("• " + text)
    gap()
    line("Tax rules applied", SECTION)
    for text in RULES_TEXT:
        line("• " + text)
    gap()
    line("Settings", SECTION)
    for text in [
        f"Crypto withdrawals go to your own wallet (not a sale): {yes(o.own_wallet_withdrawals)}",
        f"Same-day ISIN swaps are conversions (not a sale): {yes(o.detect_conversions)}",
        f"Bonds booked at 0 on maturity are repaid at 100%: {yes(o.bond_redemption_at_par)}",
        f"Original price instead of the 31-12-2025 price when higher, for sales until "
        f"{rules.original_price_until:%d-%m-%Y}: {yes(rules.use_original_if_higher)}",
        f"Exemption {rules.year}: own amount €{rules.exemption:,.2f} · rate {rules.rate:.0%} · carry-forward rule: "
        f"{rep.exemption_rules().carry_rule}",
        "People: " + ", ".join(p.name for p in rep.household.persons)
        + ("" if rep.household.single else " · ownership per broker: " + (", ".join(
            f"{b} " + "/".join(f"{rep.household.name(k)} {v:.0%}" for k, v in rep.household.share(b).items())
            for b in rep.portfolio.brokers()))),
        f"Online price lookups: {'on' if rep.prices.fetcher else 'off'}",
    ]:
        line("• " + text)
    sold = {k: v for k, v in sim.sell.items() if v}
    if sold or any(sim.extra.values()):
        line(f"• Simulator: {len(sold)} simulated sale(s); other sales entered: "
             + (", ".join(f"{LAW_TYPES[k][0]} €{v:,.2f}" for k, v in sim.extra.items() if v) or "none"))
    gap()
    files = sorted({t.source for t in rep.portfolio.transactions if t.source}
                   | {s.source for s in rep.portfolio.statements if getattr(s, "source", "")})
    line(f"Files read ({len(files)})", SECTION)
    for f in files:
        line("• " + f)
    gap()
    line(f"Notes about your data ({len(rep.warnings)})", SECTION)
    for w in rep.warnings or ["None."]:
        line("• " + w)
    if rep.prices.errors:
        gap()
        line(f"Price lookups that failed ({len(rep.prices.errors)})", SECTION)
        for e in rep.prices.errors:
            line("• " + e)


# ---------------------------------------------------------------- workbook
def build_workbook(rep: Report, simulation: Simulation | None = None, harvest=None, switch=None, perf=None) -> Workbook:
    from .engine.harvest import plan_harvest
    sim = simulation if simulation is not None else rep.simulate()
    plan = harvest if harvest is not None else plan_harvest(rep)
    wb = Workbook()
    ws_sum = wb.active
    ws_sum.title = "Summary"
    sheets = {name: wb.create_sheet(name) for name in SHEETS[1:]}

    R: dict[str, dict] = {}   # sheet -> column key -> absolute range of the data rows

    def data_sheet(name, cols, rows):
        first, last, letters = _table(sheets[name], cols, rows)
        R[name] = {k: _rng(name, L, first, last) for k, L in letters.items()}

    data_sheet("Positions", POSITIONS, _records(rep.holdings()))
    data_sheet("Sales", SALES, _records(rep.realized()))
    data_sheet("Income", INCOME, _records(rep.income()))
    tax = rep.tax_lines()
    if not tax.empty:
        tax = tax.sort_values(["status", "taxable_gain_eur"], ascending=[False, False])
    data_sheet("Meerwaardebelasting", TAX, _records(tax))
    _performance_sheet(sheets["Performance"], rep, perf if perf is not None else rep.performance())
    _reynders_sheet(sheets["Reynders tax"], rep.reynders_table(), rep.tax.reynders.rules.rate,
                    REYNDERS_NOTES + rep.reynders_flags())
    _table(sheets["Manual positions"], MANUAL, _records(rep.manual_table()))
    _table(sheets["Prices"], PRICES, _price_rows(rep))
    _exemption_sheet(sheets["Exemption"], rep)
    _income_tax_sheet(sheets["Income tax"], rep)

    refs = _summary(ws_sum, rep, R)
    _simulator(sheets["Simulator"], rep, sim, R, refs)
    _harvest_sheet(sheets["Year-end"], plan)
    _switch_sheet(sheets["Switch"], rep, switch)
    _explain(sheets["How calculated"], rep, sim)

    for ws in wb.worksheets:
        _page(ws)
    wb.calculation.fullCalcOnLoad = True      # Excel computes the formulas when the file is opened
    wb.properties.creator = "meerwaarde"
    wb.properties.title = "Profit and meerwaardebelasting"
    return wb


def to_xlsx(rep: Report, target: str | Path | None = None, simulation: Simulation | None = None, harvest=None,
            switch=None, perf=None):
    """Write the workbook to `target` and return its path, or return the bytes when no target is given.
    `switch`: (sources, cases, plans, horizon) from the switch planner; default: distributing ETFs into world ETFs."""
    wb = build_workbook(rep, simulation, harvest, switch, perf)
    if target is None:
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def default_name(rep: Report) -> str:
    return f"meerwaarde-{rep.tax.today:%Y-%m-%d}.xlsx"


# ---------------------------------------------------------------- yearly tax report (backlog item 5)
TR_SALES = [
    Col("date", "Date", DATE, 11), Col("broker", "Broker", width=10), Col("asset", "Asset", width=15),
    Col("name", "Name", width=36), Col("type", "Type", width=20), Col("qty", "Quantity", QTY, 12),
    Col("price_eur", "Sale price (€)", PRICE, 13), Col("price_31dec_eur", "Price 31-12-2025 (€)", PRICE, 13),
    Col("proceeds_eur", "Proceeds (€)", EUR, 15), Col("acquisition_eur", "Purchase value used (€)", EUR, 16),
    Col("taxable_gain_eur", "Taxable gain (€)", EUR, 15),
]
TR_INCOME = [
    Col("date", "Date", DATE, 11), Col("broker", "Broker", width=10), Col("asset", "Asset", width=15),
    Col("name", "Name", width=44), Col("kind", "Kind", width=12), Col("amount_eur", "Amount (€)", EUR, 14),
    Col("amount_local", "Amount (local)", "#,##0.00", 14), Col("currency", "Currency", width=9),
]
TR_TOB = [
    Col("date", "Date", DATE, 11), Col("broker", "Broker", width=10), Col("asset", "Asset", width=15),
    Col("name", "Name", width=36), Col("qty", "Quantity", QTY, 10), Col("price", "Price", PRICE, 12),
    Col("amount_eur", "Stock exchange tax (€)", EUR, 16),
]


def _tr_report_sheet(ws, ytr) -> None:
    from .engine.income_tax import CATEGORIES
    for col, w in zip("ABCDEF", (52, 16, 16, 16, 18, 40)):
        ws.column_dimensions[col].width = w
    _write(ws, 1, 1, ytr.title, TITLE)
    _write(ws, 2, 1, ytr.subtitle, NOTE)
    _write(ws, 4, 1, "Rate meerwaardebelasting")
    _write(ws, 4, 2, 0.10, INPUT, PCT, INPUT_FILL)
    _write(ws, 5, 1, "Rate on dividends and interest")
    _write(ws, 5, 2, 0.30, INPUT, PCT, INPUT_FILL)
    _write(ws, 6, 1, "Blue figures on yellow can be changed; everything else follows.", NOTE)
    many = len(ytr.persons) > 1
    row = 8
    cgt_cells, inc_cells = [], []

    _write(ws, row, 1, f"1. Meerwaardebelasting {ytr.year} (capital gains, 10%)", SECTION)
    row += 1
    for pid, name in ytr.persons:
        p = ytr.cgt["people"][pid]
        if many:
            _write(ws, row, 1, name, BOLD)
            row += 1
        _header(ws, row, ["Type (as the law defines it)", "Gains", "Losses", "Net"])
        first = row + 1
        for n, (k, (tname, _nl)) in enumerate(LAW_TYPES.items()):
            r = first + n
            v = p["per_type"][k]
            _write(ws, r, 1, tname)
            _write(ws, r, 2, round(v["gains"], 2), fmt=EUR)
            _write(ws, r, 3, round(v["losses"], 2), fmt=EUR)
            _write(ws, r, 4, f"=B{r}+C{r}", fmt=EUR)
        last = first + len(LAW_TYPES) - 1
        r = last + 1
        e = ytr.exemption.get(pid)
        _write(ws, r, 1, "Net taxable gain", BOLD, border=TOTAL_BORDER)
        _write(ws, r, 4, f"=SUM(D{first}:D{last})", BOLD, EUR, border=TOTAL_BORDER)
        label = "Exemption available"
        if e and e.carried:
            label += f" (own €{e.base:,.0f} + carried €{e.carried:,.0f})"
        _write(ws, r + 1, 1, label)
        _write(ws, r + 1, 4, p["exemption"], INPUT, EUR, INPUT_FILL)
        _write(ws, r + 2, 1, "Taxable above the exemption")
        _write(ws, r + 2, 4, f"=MAX(0,D{r}-D{r + 1})", fmt=EUR)
        _write(ws, r + 3, 1, "Meerwaardebelasting", BOLD, border=TOTAL_BORDER)
        _write(ws, r + 3, 4, f"=D{r + 2}*$B$4", BOLD, EUR, border=TOTAL_BORDER)
        cgt_cells.append(f"D{r + 3}")
        if e:
            _write(ws, r + 4, 1, f"Exemption used €{e.used:,.2f}; carried forward to later years €{e.carry_out:,.0f}"
                                 + ("" if ytr.complete else " (if the rest of the year adds no gain)"), NOTE)
        row = r + 6

    _write(ws, row, 1, f"2. Dividends and interest {ytr.year} (30%)", SECTION)
    row += 1
    for pid, name in ytr.persons:
        it = ytr.income[pid]
        if many:
            _write(ws, row, 1, name, BOLD)
            row += 1
        _header(ws, row, ["Income", "Broker", "Gross", "Foreign tax", "Net (taxable base)", "Belgian tax withheld"])
        first = row + 1
        for n, l in enumerate(it.lines):
            r = first + n
            _write(ws, r, 1, CATEGORIES[l.category])
            _write(ws, r, 2, l.broker)
            _write(ws, r, 3, l.gross, fmt=EUR)
            _write(ws, r, 4, l.foreign_tax, fmt=EUR)
            _write(ws, r, 5, f"=C{r}+D{r}", fmt=EUR)
            _write(ws, r, 6, l.be_withheld, fmt=EUR)
        last = max(first, first + len(it.lines) - 1)
        r = first + max(1, len(it.lines))
        rng = lambda c: f"{c}{first}:{c}{last}"  # noqa: E731
        _write(ws, r, 1, "Dividend exemption available (shares only)")
        _write(ws, r, 5, it.exemption_available, INPUT, EUR, INPUT_FILL)
        _write(ws, r + 1, 1, "Dividend exemption used")
        _write(ws, r + 1, 5, f'=MIN(MAX(0,SUMIFS({rng("E")},{rng("A")},"{CATEGORIES["shares"]}")),E{r})', fmt=EUR)
        _write(ws, r + 2, 1, "Taxable at 30%")
        _write(ws, r + 2, 5, f'=MAX(0,SUMIFS({rng("E")},{rng("A")},"{CATEGORIES["shares"]}"))-E{r + 1}'
                             f'+MAX(0,SUMIFS({rng("E")},{rng("A")},"{CATEGORIES["funds"]}"))'
                             f'+MAX(0,SUMIFS({rng("E")},{rng("A")},"{CATEGORIES["interest"]}"))'
                             f'+MAX(0,SUMIFS({rng("E")},{rng("A")},"{CATEGORIES["other"]}"))', fmt=EUR)
        _write(ws, r + 3, 1, "Tax (30%)")
        _write(ws, r + 3, 5, f"=E{r + 2}*$B$5", fmt=EUR)
        _write(ws, r + 4, 1, "Already withheld in Belgium (Belgian companies' dividends)")
        _write(ws, r + 4, 5, f"=SUM({rng('F')})", fmt=EUR)
        _write(ws, r + 5, 1, "To pay on dividends and interest (negative = refund)", BOLD, border=TOTAL_BORDER)
        _write(ws, r + 5, 5, f"=E{r + 3}+E{r + 4}", BOLD, EUR, border=TOTAL_BORDER)
        inc_cells.append(f"E{r + 5}")
        row = r + 7

    _write(ws, row, 1, f"3. Stock exchange tax {ytr.year}", SECTION)
    _header(ws, row + 1, ["Broker", "Trades", "Amount", "", "", "Basis"])
    first = row + 2
    for n, t in enumerate(ytr.tob.itertuples()):
        r = first + n
        _write(ws, r, 1, t.broker)
        _write(ws, r, 2, int(t.trades), fmt=COUNT)
        _write(ws, r, 3, float(t.amount_eur), fmt=EUR)
        _write(ws, r, 6, t.basis, NOTE)
    r = first + max(1, len(ytr.tob))
    _write(ws, r, 1, "Total", BOLD, border=TOTAL_BORDER)
    _write(ws, r, 3, f"=SUM(C{first}:C{r - 1})", BOLD, EUR, border=TOTAL_BORDER)
    tob_cell = f"C{r}"
    row = r + 2

    _write(ws, row, 1, "Totals", SECTION)
    _write(ws, row + 1, 1, "Meerwaardebelasting")
    _write(ws, row + 1, 2, "=" + "+".join(cgt_cells), BOLD, EUR)
    _write(ws, row + 2, 1, "To pay on dividends and interest (negative = refund)")
    _write(ws, row + 2, 2, "=" + "+".join(inc_cells), BOLD, EUR)
    _write(ws, row + 3, 1, "Stock exchange tax (already withheld by the brokers)")
    _write(ws, row + 3, 2, f"={tob_cell}", fmt=EUR)
    _write(ws, row + 5, 1, "Estimates from your broker exports, not tax advice.", NOTE)
    _page(ws)


def _tr_checklist_sheet(ws, ytr) -> None:
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 120
    row = 1
    _write(ws, row, 1, "Checklist", TITLE)
    row += 2
    for i, c in enumerate(ytr.checklist, 1):
        _write(ws, row, 1, "☐")
        _write(ws, row, 2, f"{i}. {c}", align=WRAP)
        ws.row_dimensions[row].height = 13.5 * max(1, math.ceil(len(c) / 125))
        row += 1
    row += 1
    _write(ws, row, 1, "Foreign accounts", SECTION)
    row += 1
    _header(ws, row, ["Broker", "Account held at", "Country"], start_col=1)
    ws.column_dimensions["C"].width = 16
    for a in ytr.accounts:
        row += 1
        _write(ws, row, 1, a["broker"])
        _write(ws, row, 2, a["institution"])
        _write(ws, row, 3, a["country"])
    row += 2
    if ytr.notes:
        _write(ws, row, 1, "Notes", SECTION)
        for n in ytr.notes:
            row += 1
            _write(ws, row, 2, "• " + n, align=WRAP)
        row += 2
    _write(ws, row, 1, "How it's calculated", SECTION)
    for t in ytr.rules_text:
        row += 1
        _write(ws, row, 2, "• " + t, NOTE, align=WRAP)
        ws.row_dimensions[row].height = 12 * max(1, math.ceil(len(t) / 135))
    _page(ws)


def tax_report_workbook(ytr) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Report"
    _tr_report_sheet(ws, ytr)
    sales = wb.create_sheet(f"Sales {ytr.year}")
    cols = list(TR_SALES)
    if len(ytr.persons) > 1:
        cols += [Col(f"share_{pid}", f"Share {name}", PCT, 12) for pid, name in ytr.persons]
    first, last, L = _table(sales, cols, _records(ytr.sales))
    if last >= first:
        _write(sales, last + 1, 1, "Total", BOLD, border=TOTAL_BORDER)
        g = L["taxable_gain_eur"]
        _write(sales, last + 1, len(TR_SALES), f"=SUM({g}{first}:{g}{last})", BOLD, EUR, border=TOTAL_BORDER)
    if ytr.reynders.get("base"):
        _reynders_sheet(wb.create_sheet(f"Reynders {ytr.year}"), ytr.reynders_detail, ytr.reynders["rate"], REYNDERS_NOTES[1:])
    _table(wb.create_sheet(f"Income {ytr.year}"), TR_INCOME, _records(ytr.income_detail))
    _table(wb.create_sheet("Stock exchange tax"), TR_TOB, _records(ytr.tob_detail))
    ex = wb.create_sheet("Exemption")
    rows = [dict(person=ytr.person_name(pid), year=e.year, base=e.base, carried_in=e.carried, available=e.available,
                 net_gain=round(e.net_gain, 2), used=round(e.used, 2), carry_out=e.carry_out, expired=e.expired,
                 carried_from=", ".join(f"{y}: €{a:,.0f}" for y, a in e.carried_in))
            for pid, hist in ytr.history.items() for e in hist]
    _table(ex, EXEMPTION, rows)
    _tr_checklist_sheet(wb.create_sheet("Checklist"), ytr)
    for w in wb.worksheets[1:]:
        _page(w)
    wb.calculation.fullCalcOnLoad = True
    wb.properties.creator = "meerwaarde"
    wb.properties.title = ytr.title
    return wb


def tax_report_xlsx(ytr, target: str | Path | None = None):
    wb = tax_report_workbook(ytr)
    if target is None:
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
