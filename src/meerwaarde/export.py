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

SHEETS = ("Summary", "Positions", "Sales", "Income", "Meerwaardebelasting", "Simulator", "Prices", "How calculated")


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


def _table(ws, cols: list[Col], rows: list[dict], top: int = 1, freeze: bool = True):
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
    Col("taxable_gain_eur", "Taxable gain (€)", EUR, 16), Col("missing", "Missing", width=16),
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
    _write(ws, row, 1, f"Meerwaardebelasting {rules.year} if you sold everything on {rep.tax.today:%d-%m-%Y}", SECTION)
    _header(ws, row + 1, ["Type (as the law defines it)", "Sold this year", "If you sold the rest", "Net"])
    first = row + 2
    for n, (k, (name, _nl)) in enumerate(LAW_TYPES.items()):
        r = first + n
        _write(ws, r, 1, name)
        _write(ws, r, 2, f'=SUMIFS({t["taxable_gain_eur"]},{t["type"]},$A{r},{t["status"]},"Sold")', fmt=EUR)
        _write(ws, r, 3, f'=SUMIFS({t["taxable_gain_eur"]},{t["type"]},$A{r},{t["status"]},"Open")', fmt=EUR)
        _write(ws, r, 4, f"=B{r}+C{r}", fmt=EUR)
    last = first + len(LAW_TYPES) - 1
    tot = last + 1
    _write(ws, tot, 1, "All types", BOLD, border=TOTAL_BORDER)
    for c in (2, 3, 4):
        L = get_column_letter(c)
        _write(ws, tot, c, f"=SUM({L}{first}:{L}{last})", BOLD, EUR, border=TOTAL_BORDER)

    r = tot + 2
    _write(ws, r, 1, "Net taxable gain")
    _write(ws, r, 2, f"=D{tot}", fmt=EUR)
    _write(ws, r + 1, 1, "Exemption (change it here)")
    _write(ws, r + 1, 2, rules.exemption, INPUT, EUR, INPUT_FILL)
    _write(ws, r + 2, 1, "Taxable above the exemption")
    _write(ws, r + 2, 2, f"=MAX(0,B{r}-B{r + 1})", fmt=EUR)
    _write(ws, r + 3, 1, "Rate (change it here)")
    _write(ws, r + 3, 2, rules.rate, INPUT, PCT, INPUT_FILL)
    _write(ws, r + 4, 1, "Meerwaardebelasting", BOLD, border=TOTAL_BORDER)
    _write(ws, r + 4, 2, f"=B{r + 2}*B{r + 3}", BOLD, EUR, border=TOTAL_BORDER)
    _write(ws, r + 5, 1, "Tax-free room left after this year's sales")
    _write(ws, r + 5, 2, f"=MAX(0,B{r + 1}-B{tot})", fmt=EUR)
    _write(ws, r + 7, 1, "All types fall under 'andere financiële activa': one rate and one exemption, and losses "
                         "offset gains across types within the year. Blue figures on yellow can be changed.", NOTE)
    _write(ws, r + 8, 1, "This is a calculation from your exports, not tax advice. See 'How calculated'.", NOTE)
    return {"exemption": f"'Summary'!$B${r + 1}", "rate": f"'Summary'!$B${r + 3}"}


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
    for n, (k, (name, _nl)) in enumerate(LAW_TYPES.items()):
        _write(ws, first + n, 1, name)
        _write(ws, first + n, 2, float(sim.extra.get(k, 0.0) or 0.0), INPUT, EUR, INPUT_FILL)
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
    items = [
        ("Sold this year (in your exports)", f'=SUMIFS({t["taxable_gain_eur"]},{t["status"]},"Sold")', BODY),
        ("Other sales (above)", f"=SUM(B{first}:B{last_extra})", BODY),
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
        "Simulator: the sales simulated in the app, other sales you entered, and per position the largest quantity you "
        "can still sell without tax.",
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
        f"Exemption €{rules.exemption:,.2f} · rate {rules.rate:.0%}",
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
def build_workbook(rep: Report, simulation: Simulation | None = None) -> Workbook:
    sim = simulation if simulation is not None else rep.simulate()
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
    _table(sheets["Prices"], PRICES, _price_rows(rep))

    refs = _summary(ws_sum, rep, R)
    _simulator(sheets["Simulator"], rep, sim, R, refs)
    _explain(sheets["How calculated"], rep, sim)

    for ws in wb.worksheets:
        _page(ws)
    wb.calculation.fullCalcOnLoad = True      # Excel computes the formulas when the file is opened
    wb.properties.creator = "meerwaarde"
    wb.properties.title = "Profit and meerwaardebelasting"
    return wb


def to_xlsx(rep: Report, target: str | Path | None = None, simulation: Simulation | None = None):
    """Write the workbook to `target` and return its path, or return the bytes when no target is given."""
    wb = build_workbook(rep, simulation)
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
