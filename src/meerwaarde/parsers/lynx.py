"""LYNX / Interactive Brokers 'Activity Statement' PDF (one per year, or year-to-date).

The statement is read section by section. Side-by-side sections (for example
Dividends next to Deposits & Withdrawals) are split into their own columns first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta

import pdfplumber

from ..models import (Transaction, BUY, SELL, DIVIDEND, INTEREST, WITHHOLDING, CASH_IN, CASH_OUT, FX_RESULT, FEE)
from ..util import num_us
from .classify import guess_class

BROKER = "LYNX"
HEADER_MIN, HEADER_MAX = 9.5, 12.0
NUM = r"-?[\d,]+(?:\.\d+)?"
DEC = r"-?[\d,]+\.\d+"


@dataclass
class StatementInfo:
    """What a statement says about positions and prices at its period end."""
    broker: str
    period_start: date | None = None
    period_end: date | None = None
    base: str = "EUR"
    positions: dict = field(default_factory=dict)      # symbol -> {qty, cur, close, cost_local, value_local, value_eur, cost_eur}
    forex: dict = field(default_factory=dict)          # currency -> {qty, cost_eur, close, value_eur}
    prior_prices: dict = field(default_factory=dict)   # symbol/currency -> price at period_start - 1 day (local currency)
    prior_qty: dict = field(default_factory=dict)      # symbol/currency -> quantity held at period_start - 1 day
    current_prices: dict = field(default_factory=dict) # symbol/currency -> price at period_end (local currency)
    fx_close: dict = field(default_factory=dict)       # currency -> EUR per unit at period_end
    fx_prior: dict = field(default_factory=dict)       # currency -> EUR per unit at period_start - 1 day
    instruments: dict = field(default_factory=dict)    # symbol -> {isin, name, type, exchange}
    nav: dict = field(default_factory=dict)            # Change in NAV items (EUR)
    realized_stock: dict = field(default_factory=dict) # symbol -> realized total reported by the broker (EUR)
    source: str = ""


# ---------- text extraction ----------
def _lines(words, ymin=-1.0, ymax=1e9, xmin=-1.0, xmax=1e9):
    sel = [w for w in words if ymin <= w["top"] < ymax and xmin <= w["x0"] < xmax]
    sel.sort(key=lambda w: (w["top"], w["x0"]))
    out, cur, top = [], [], None
    for w in sel:
        if cur and abs(w["top"] - top) > 2.5:
            out.append(cur)
            cur = []
        if not cur:
            top = w["top"]
        cur.append(w)
    if cur:
        out.append(cur)
    res = []
    for ln in out:
        ln.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in ln)
        is_head = all(HEADER_MIN <= w["size"] < HEADER_MAX for w in ln)
        res.append(("## " + text) if is_head else text)
    return res


def statement_lines(source) -> list[str]:
    lines = []
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            words = page.extract_words(extra_attrs=["size"], keep_blank_chars=False, use_text_flow=False)
            right = [w for w in words if HEADER_MIN <= w["size"] < HEADER_MAX and w["x0"] > page.width * 0.45]
            if not right:
                lines += _lines(words)
                continue
            split = min(w["x0"] for w in right) - 4
            top = min(w["top"] for w in right) - 2
            lines += _lines(words, ymax=top)
            lines += _lines(words, ymin=top, xmax=split)
            lines += _lines(words, ymin=top, xmin=split)
    return lines


# ---------- parsing ----------
_MONTH_RANGE = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4}) - ([A-Z][a-z]+ \d{1,2}, \d{4})")
_DATE_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}),?$")
_TIME_LINE = re.compile(r"^\d{2}:\d{2}:\d{2}$")
_CUR = re.compile(r"^[A-Z]{3}$")
_TRADE = re.compile(rf"^(?P<sym>[A-Z0-9][A-Z0-9.\-]*) (?:(?P<date>\d{{4}}-\d{{2}}-\d{{2}}),? (?:\d{{2}}:\d{{2}}:\d{{2}} )?)?(?P<qty>{NUM}) (?P<tp>{NUM}) (?P<cp>{NUM}) (?P<proc>{DEC}) (?P<comm>{DEC}) (?P<basis>{DEC}) (?P<rpl>{DEC}) (?P<mtm>{DEC})(?: (?P<code>[A-Za-z;]+))?$")
_FXTRADE = re.compile(rf"^(?P<pair>[A-Z]{{3}}\.[A-Z]{{3}}) (?:(?P<date>\d{{4}}-\d{{2}}-\d{{2}}),? (?:\d{{2}}:\d{{2}}:\d{{2}} )?)?(?P<qty>{NUM}) (?P<tp>{NUM}) (?P<proc>{DEC}) (?P<comm>{DEC})(?: (?P<mtm>{DEC}))?(?: (?P<code>[A-Za-z;]+))?$")
_TOTAL_EUR = re.compile(rf"^Total in (?P<base>[A-Z]{{3}}) (?P<nums>(?:{DEC} ?)+)$")
_DIV_DESC = re.compile(r"^(?P<sym>[A-Z0-9.\-]+)\((?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\) (?P<what>.*?)(?P<cur>[A-Z]{3}) (?P<rate>[\d.]+) per Share")
_DATED_AMOUNT = re.compile(rf"^(?P<date>\d{{4}}-\d{{2}}-\d{{2}}) (?P<amount>{DEC})$")
_DATED_DESC_AMOUNT = re.compile(rf"^(?P<date>\d{{4}}-\d{{2}}-\d{{2}}) (?P<desc>.+?) (?P<amount>{DEC})(?: [A-Za-z;]+)?$")
_OPENPOS = re.compile(rf"^(?P<sym>[A-Z0-9][A-Z0-9.\-]*) (?P<qty>{NUM}) (?P<mult>\d+) (?P<cp>{NUM}) (?P<cb>{DEC}) (?P<close>{NUM}) (?P<val>{DEC}) (?P<upl>{DEC})")
_FOREX_BAL = re.compile(rf"^(?P<cur>[A-Z]{{3}}) (?P<qty>{DEC}) (?P<cp>{NUM}) (?P<cb>{DEC}) (?P<close>{NUM}) (?P<val>{DEC}) (?P<upl>{DEC})")
_PERF = re.compile(rf"^(?P<sym>[A-Z0-9][A-Z0-9.\-]*) (?P<nums>(?:{DEC} ?){{12}})")
_MTM = re.compile(rf"^(?P<sym>[A-Z0-9][A-Z0-9.\-]*) (?P<pq>{NUM}) (?P<cq>{NUM}) (?P<pp>{NUM}|--) (?P<cpx>{NUM}|--) ")
_INSTR = re.compile(r"^(?P<sym>[A-Z0-9][A-Z0-9.\-]*) (?P<desc>.+?) (?P<conid>\d{4,}) (?P<isin>[A-Z]{2}[A-Z0-9]{9}\d) (?P<rest>.+)$")
_NAV_ITEMS = ["Starting Value", "Mark-to-Market", "Deposits & Withdrawals", "Dividends", "Withholding Tax",
              "Change in Dividend Accruals", "Interest", "Change in Interest Accruals", "Commissions",
              "Transaction Fees", "Other FX Translations", "Other Fees", "Sales Tax", "Ending Value"]


def _d(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def parse(source, filename: str = ""):
    return parse_lines(statement_lines(source), filename)


def parse_lines(lines: list[str], filename: str = ""):
    """Parse statement lines ('## ' marks a section title). Returns (transactions, StatementInfo, warnings)."""
    info = StatementInfo(broker=BROKER, source=filename)
    txns: list[Transaction] = []
    warnings: list[str] = []

    section, category, currency = None, None, None
    pending_date = None
    trades = []            # (dict, currency, category)
    blocks_eur = {}        # (section, currency) -> list of 'Total in EUR' number lists
    blocks_local = {}      # (section, currency) -> list of 'Total' number lists
    dividends, cash_rows = [], []   # (date, sym, isin, cur, amount, desc), (section, date, desc, cur, amount)
    div_desc = None

    for raw in lines:
        if raw.startswith("## "):
            title = raw[3:].strip()
            if title.startswith("Activity Statement") or title == section:
                continue          # a section continued on the next page keeps its category and currency
            section, category, currency, pending_date, div_desc = title, None, None, None, None
            continue
        line = raw.strip()
        if not line:
            continue
        m = _MONTH_RANGE.search(line)
        if m and info.period_end is None:
            info.period_start = datetime.strptime(m.group(1), "%B %d, %Y").date()
            info.period_end = datetime.strptime(m.group(2), "%B %d, %Y").date()
        if section == "Account Information" and re.match(r"^Base Currency [A-Z]{3}$", line):
            info.base = line.split()[-1]
        if line.startswith("Activity Statement -") or line.startswith("Generated:"):
            continue
        if section is None:
            continue
        if line in ("Stocks", "Forex", "Equity and Index Options", "Bonds", "Funds", "ETFs", "Futures", "Warrants", "Mutual Funds", "CFDs", "Crypto"):
            category = line
            continue
        if _CUR.match(line):
            currency = line
            continue
        tm = _TOTAL_EUR.match(line)
        if tm:
            nums = [num_us(x) for x in tm.group("nums").split()]
            blocks_eur.setdefault((section, category, currency), []).append(nums)
            continue
        if line.startswith("Total "):
            parts = line.split()
            nums = []
            for p in parts[1:]:
                try:
                    nums.append(num_us(p))
                except ValueError:
                    pass
            if nums and line.startswith("Total ") and not line.startswith("Total in"):
                blocks_local.setdefault((section, category, currency), []).append(nums)
            continue

        if section == "Net Asset Value":
            for item in _NAV_ITEMS:
                for mm in re.finditer(re.escape(item) + rf" ({DEC})", line):
                    info.nav[item] = num_us(mm.group(1))
            continue

        if section == "Mark-to-Market Performance Summary":
            mm = _MTM.match(line)
            if mm:
                key = mm.group("sym")
                info.prior_qty[key] = num_us(mm.group("pq"))
                if mm.group("pp") != "--":
                    info.prior_prices[key] = num_us(mm.group("pp"))
                if mm.group("cpx") != "--":
                    info.current_prices[key] = num_us(mm.group("cpx"))
                if category == "Forex":
                    if mm.group("pp") != "--":
                        info.fx_prior[key] = num_us(mm.group("pp"))
                    if mm.group("cpx") != "--":
                        info.fx_close[key] = num_us(mm.group("cpx"))
            continue

        if section == "Realized & Unrealized Performance Summary":
            mm = _PERF.match(line)
            if mm:
                nums = [num_us(x) for x in mm.group("nums").split()]
                realized = nums[5]
                if category == "Forex":
                    if abs(realized) > 0.004:
                        end = info.period_end or date.today()
                        txns.append(Transaction(broker=BROKER, ts=datetime.combine(end, datetime.min.time()), kind=FX_RESULT,
                                                asset=mm.group("sym"), name=f"{mm.group('sym')} cash (currency result)",
                                                asset_class="Cash/FX", amount_eur=realized, currency=info.base, source=filename,
                                                note="Realized currency result reported by the broker"))
                else:
                    info.realized_stock[mm.group("sym")] = realized
            continue

        if section == "Open Positions":
            mm = _OPENPOS.match(line)
            if mm and currency:
                info.positions[mm.group("sym")] = dict(qty=num_us(mm.group("qty")), cur=currency, close=num_us(mm.group("close")),
                                                       cost_local=num_us(mm.group("cb")), value_local=num_us(mm.group("val")))
            continue

        if section == "Forex Balances":
            mm = _FOREX_BAL.match(line)
            if mm and mm.group("cur") != info.base:
                info.forex[mm.group("cur")] = dict(qty=num_us(mm.group("qty")), cost_eur=-num_us(mm.group("cb")),
                                                   close=num_us(mm.group("close")), value_eur=num_us(mm.group("val")))
            continue

        if section == "Trades":
            dm = _DATE_LINE.match(line)
            if dm:
                pending_date = dm.group(1)
                continue
            if _TIME_LINE.match(line):
                continue
            mm = _TRADE.match(line)
            if mm and category != "Forex":
                d = mm.groupdict()
                d["date"] = d["date"] or pending_date
                trades.append((d, currency, category))
                pending_date = None
                continue
            mm = _FXTRADE.match(line)
            if mm:
                d = mm.groupdict()
                d["date"] = d["date"] or pending_date
                comm = num_us(d["comm"])
                if d["date"] and abs(comm) > 0:
                    txns.append(Transaction(broker=BROKER, ts=_d(d["date"]), kind=FEE, asset=d["pair"], name=f"Currency conversion {d['pair']}",
                                            asset_class="Cash/FX", amount_eur=comm, source=filename, note="Commission on a currency conversion"))
                pending_date = None
            continue

        if section == "Dividends":
            mm = _DIV_DESC.match(line)
            if mm:
                div_desc = mm.groupdict()
                continue
            mm = _DATED_AMOUNT.match(line)
            if mm and div_desc:
                dividends.append((mm.group("date"), div_desc["sym"], div_desc["isin"], currency or div_desc["cur"], num_us(mm.group("amount"))))
            continue

        if section in ("Withholding Tax", "Interest", "Deposits & Withdrawals"):
            mm = _DATED_DESC_AMOUNT.match(line)
            if mm and currency:
                cash_rows.append((section, mm.group("date"), mm.group("desc"), currency, num_us(mm.group("amount"))))
            continue

        if section == "Financial Instrument Information":
            mm = _INSTR.match(line)
            if mm:
                rest = mm.group("rest").split()
                info.instruments[mm.group("sym")] = dict(isin=mm.group("isin"), name=f"{mm.group('desc').title()} ({mm.group('sym')})",
                                                         type=rest[-1] if rest else "", exchange=rest[1] if len(rest) > 1 else "")
            continue

    # ---- FX scales per block: EUR per local unit, from the statement's own 'Total in EUR' lines ----
    def scale(sec, cur, idx=0, cat=None):
        if cur in (None, info.base):
            return 1.0
        loc, eur = blocks_local.get((sec, cat, cur)), blocks_eur.get((sec, cat, cur))
        if not loc or not eur:
            return info.fx_close.get(cur)
        lv, ev = loc[-1][idx] if len(loc[-1]) > idx else 0, eur[-1][idx] if len(eur[-1]) > idx else 0
        return (ev / lv) if lv else info.fx_close.get(cur)

    # ---- trades ----
    for i, (d, cur, cat) in enumerate(trades):
        sym = d["sym"]
        inst = info.instruments.get(sym, {})
        qty = num_us(d["qty"])
        proc, comm = num_us(d["proc"]), num_us(d["comm"])
        k = scale("Trades", cur, 0, cat)
        if k is None:
            warnings.append(f"LYNX: no EUR rate for {cur} trades in {filename}; {sym} trade skipped")
            continue
        name = inst.get("name") or sym
        acls = "Stock" if inst.get("type", "").upper() in ("COMMON", "STK", "REIT", "ADR") else guess_class(name + " ETF" if inst.get("type") == "ETF" else name)
        txns.append(Transaction(broker=BROKER, ts=_d(d["date"]) if d["date"] else datetime.combine(info.period_end, datetime.min.time()),
                                kind=BUY if qty > 0 else SELL, asset=inst.get("isin", sym), name=name, asset_class=acls,
                                qty=qty, price=num_us(d["tp"]), price_cur=cur, gross_eur=abs(proc) * k, fees_eur=-comm * k,
                                total_eur=(proc + comm) * k, source=filename, order=i, note=f"{sym}"))

    # ---- dividends: EUR per payment, scaled to the statement's EUR total for the currency ----
    for (dt, sym, isin, cur, amount) in dividends:
        k = scale("Dividends", cur, 0) or 1.0
        inst = info.instruments.get(sym, {})
        txns.append(Transaction(broker=BROKER, ts=_d(dt), kind=DIVIDEND, asset=isin, name=inst.get("name") or sym,
                                asset_class="ETF", qty=0, currency=cur, amount_eur=round(amount * k, 2), price=amount,
                                source=filename, note=f"{amount:.2f} {cur}"))
    for (sec, dt, desc, cur, amount) in cash_rows:
        k = scale(sec, cur, 0) or 1.0
        kind = {"Withholding Tax": WITHHOLDING, "Interest": INTEREST}.get(sec)
        if sec == "Deposits & Withdrawals":
            kind = CASH_IN if amount > 0 else CASH_OUT
        txns.append(Transaction(broker=BROKER, ts=_d(dt), kind=kind, asset=cur, name=desc, asset_class="Cash/FX",
                                qty=amount, currency=cur, amount_eur=round(amount * k, 2), source=filename))

    # ---- open positions in EUR ----
    for sym, p in info.positions.items():
        k = 1.0 if p["cur"] == info.base else (info.fx_close.get(p["cur"]) or scale("Open Positions", p["cur"], 1, "Stocks"))
        p["value_eur"] = p["value_local"] * (k or 1.0)
        p["close_eur"] = p["close"] * (k or 1.0)
        inst = info.instruments.get(sym, {})
        p["isin"] = inst.get("isin", sym)
        p["name"] = inst.get("name", sym)
    if not info.period_end:
        warnings.append(f"LYNX: no statement period found in {filename}")
    return txns, info, warnings
