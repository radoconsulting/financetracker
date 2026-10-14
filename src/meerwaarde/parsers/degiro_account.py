"""DEGIRO account statement ('Rekeningoverzicht' PDF: Inbox > Rekeningoverzicht > Exporteren).

Trades come from the Transacties export; this statement adds what that export misses: dividends and the tax
withheld on them, the Belgian stock exchange tax per trade, interest, securities-lending income, exchange connection
fees, deposits and withdrawals, and the payout of a bond at maturity.

Columns: Datum | Tijd | Valutadatum | Product | ISIN | Omschrijving | FX | Mutatie | Saldo. Long product names and
descriptions wrap onto the next line. Column boundaries are taken from the header row of each page.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

from ..models import (Transaction, DIVIDEND, WITHHOLDING, INTEREST, CASH_IN, CASH_OUT, FEE, TOB, REDEMPTION)
from ..util import num_eu
from .classify import guess_class

BROKER = "DEGIRO"
HEADER = ("Datum", "Tijd", "Valutadatum", "Product", "ISIN", "Omschrijving", "FX", "Mutatie", "Saldo")
FIELDS = ("date", "time", "valuta", "product", "isin", "desc", "fx", "mut", "saldo")
_DATE = re.compile(r"^\d{2}-\d{2}-\d{4}$")
_AMOUNT = re.compile(r"^(?P<cur>[A-Z]{3}) (?P<v>-?[\d.]+(?:,\d+)?)$")
_TRADE = re.compile(r"^(?:[A-Z :]+: )?(Koop|Verkoop) [\d.,]+ @ [\d.,]+ [A-Z]{3}$")

# description -> kind; checked in order, first match wins (lower case, 'startswith' unless noted)
RULES = [
    ("dividendbelasting", WITHHOLDING),
    ("dividend", DIVIDEND),
    ("coupon", INTEREST),
    ("transactiebelasting", TOB),
    ("contante verrekening obligatie", REDEMPTION),
    ("inkomsten uit securities lending", INTEREST),
    ("flatex interest", INTEREST),
    ("rente", INTEREST),
    ("storting", CASH_IN),
    ("ideal storting", CASH_IN),
    ("flatex deposit", CASH_IN),
    ("terugstorting", CASH_OUT),
    ("flatex terugstorting", CASH_OUT),
    ("degiro aansluitingskosten", FEE),
    ("degiro exchange connection fee", FEE),
    ("verrekening welkomstactie", FEE),
    ("conversie geldmarktfonds", FEE),
]
# known rows that carry nothing new: trades and their costs are in the Transacties export; the rest moves cash between
# the DEGIRO account and the flatex bank account, or converts currency
IGNORE = ("degiro transactiekosten", "degiro cash sweep transfer", "overboeking", "processed flatex withdrawal",
          "valuta creditering", "valuta debitering")


@dataclass
class AccountInfo:
    period_start: str = ""
    period_end: str = ""
    deposits: float = 0.0
    withdrawals: float = 0.0
    cash_eur: float = 0.0            # DEGIRO account + flatex bank account (sweep transfers left out)
    cash_other: dict = field(default_factory=dict)
    trades: int = 0                  # Koop/Verkoop rows (the Transacties export should have the same number)
    unknown: list = field(default_factory=list)


def statement_rows(source) -> list[dict]:
    """One dict per booking (fields: date, time, valuta, product, isin, desc, fx, mut, saldo, page)."""
    rows: list[dict] = []
    with pdfplumber.open(source) as pdf:
        for pno, page in enumerate(pdf.pages):
            words = page.extract_words(keep_blank_chars=False)
            head = {w["text"]: w for w in words if w["text"] in HEADER and w["top"] < page.height / 2}
            if "Datum" not in head or "Omschrijving" not in head:
                continue
            xs = [head[h]["x0"] for h in HEADER if h in head]
            if len(xs) != len(HEADER):
                continue
            bounds = [x - 2 for x in xs] + [1e9]
            top0 = head["Datum"]["top"] + 3
            lines: dict[float, list] = defaultdict(list)
            for w in words:
                if w["top"] <= top0:
                    continue
                key = next((k for k in lines if abs(k - w["top"]) < 2.5), w["top"])
                lines[key].append(w)
            for top in sorted(lines):
                f = {k: [] for k in FIELDS}
                for w in sorted(lines[top], key=lambda w: w["x0"]):
                    i = max([j for j in range(len(FIELDS)) if w["x0"] >= bounds[j]] or [0])
                    f[FIELDS[i]].append(w["text"])
                f = {k: " ".join(v) for k, v in f.items()}
                if f["date"] and not _DATE.match(f["date"]):
                    break                                    # page footer
                if _DATE.match(f["date"]):
                    f["page"] = pno + 1
                    rows.append(f)
                elif rows:                                   # wrapped product name or description
                    for k in ("product", "desc"):
                        if f[k]:
                            rows[-1][k] = (rows[-1][k] + " " + f[k]).strip()
    return rows


def _amount(s: str):
    m = _AMOUNT.match(s.strip()) if s else None
    return (m.group("cur"), num_eu(m.group("v"))) if m else (None, None)


def _kind(desc: str):
    d = desc.lower().strip()
    if d.startswith(IGNORE) or _TRADE.match(desc.strip()):
        return None
    for prefix, kind in RULES:
        if d.startswith(prefix):
            return kind
    return "unknown"


def parse_rows(rows: list[dict], filename: str = ""):
    """Rows (newest first, as printed) -> (transactions, AccountInfo, warnings)."""
    info = AccountInfo()
    warnings: list[str] = []
    txns: list[Transaction] = []
    rows = list(reversed(rows))                                    # oldest first
    # EUR per unit of foreign currency, from the statement's own currency conversions (FX = foreign per EUR)
    rates = defaultdict(list)                                      # currency -> [(datetime, eur per unit)]
    for r in rows:
        cur, v = _amount(r["mut"])
        if r["desc"].lower().startswith("valuta debitering") and cur and cur != "EUR" and r["fx"]:
            try:
                rates[cur].append((_ts(r), 1.0 / num_eu(r["fx"])))
            except (ValueError, ZeroDivisionError):
                pass

    def to_eur(cur, v, when):
        if cur in (None, "EUR"):
            return v, None
        cands = rates.get(cur) or []
        after = [x for x in cands if x[0] >= when]
        best = min(after, key=lambda x: x[0]) if after else (max(cands, key=lambda x: x[0]) if cands else None)
        if not best:
            return None, f"DEGIRO account statement: no {cur} conversion found for {when:%Y-%m-%d}"
        return round(v * best[1], 2), None

    for i, r in enumerate(rows):
        when = _ts(r)
        cur, v = _amount(r["mut"])
        desc = r["desc"].strip()
        low = desc.lower()
        if cur == "EUR" and not low.startswith(("degiro cash sweep transfer", "overboeking")):
            info.cash_eur += v
        elif cur and cur != "EUR":
            info.cash_other[cur] = info.cash_other.get(cur, 0.0) + v
        if _TRADE.match(desc):
            info.trades += 1
        kind = _kind(desc)
        if kind is None or v is None:
            continue
        if kind == "unknown":
            info.unknown.append(f"{r['date']} {desc} {r['mut']}")
            continue
        if kind == INTEREST and abs(v) < 0.005:
            continue                                               # 'Flatex Interest Income 0,00'
        eur, problem = to_eur(cur, v, when)
        if problem:
            warnings.append(problem + f" ({desc}); amount left out")
            continue
        name = re.sub(r"\s+", " ", r["product"]).strip() or desc
        isin = r["isin"].strip()
        common = dict(broker=BROKER, ts=when, source=filename, order=i, currency=cur, note=desc)
        if kind in (DIVIDEND, WITHHOLDING) or (kind == INTEREST and isin):
            txns.append(Transaction(kind=kind, asset=isin or cur, name=name, asset_class=guess_class(name, isin),
                                    qty=0.0, price=v, amount_eur=eur, **common))
        elif kind == INTEREST:
            nm = "Securities lending" if "securities lending" in low else desc
            txns.append(Transaction(kind=INTEREST, asset=cur, name=nm, asset_class="Cash/FX", qty=v, price=v,
                                    amount_eur=eur, **common))
        elif kind == TOB:
            txns.append(Transaction(kind=TOB, asset=isin, name=name, asset_class=guess_class(name, isin), qty=0.0,
                                    amount_eur=eur, ref="separate", **common))
        elif kind == REDEMPTION:
            txns.append(Transaction(kind=REDEMPTION, asset=isin, name=name, asset_class="Bond", qty=0.0, amount_eur=eur,
                                    **common))
        elif kind in (CASH_IN, CASH_OUT):
            if kind == CASH_IN:
                info.deposits += eur
            else:
                info.withdrawals += -eur
            txns.append(Transaction(kind=kind, asset=cur, name=desc, asset_class="Cash/FX", qty=v, amount_eur=eur, **common))
        elif kind == FEE:
            what = "Money market fund result" if low.startswith("conversie geldmarktfonds") else desc
            txns.append(Transaction(kind=FEE, asset=isin or cur, name=what, asset_class="Cash/FX", amount_eur=eur,
                                    **{**common, "note": what}))
    if rows:
        info.period_start, info.period_end = rows[0]["date"], rows[-1]["date"]
    if info.unknown:
        warnings.append(f"DEGIRO account statement: {len(info.unknown)} booking(s) of an unknown type were left out, "
                        f"e.g. {info.unknown[0]}")
    return txns, info, warnings


def _ts(r) -> datetime:
    return datetime.strptime(f"{r['date']} {r['time'] or '00:00'}", "%d-%m-%Y %H:%M")


def parse(source, filename: str = ""):
    return parse_rows(statement_rows(source), filename)


def is_account_statement(first_page_text: str) -> bool:
    t = first_page_text.lower()
    return "rekeningoverzicht" in t and "omschrijving" in t and "mutatie" in t
