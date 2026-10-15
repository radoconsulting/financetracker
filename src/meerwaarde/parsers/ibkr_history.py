"""IBKR / LYNX 'Transaction History' PDF (Client Portal → Performance & Reports → Transaction History).

One table: Date | Account | Description | Transaction Type | Symbol | Quantity | Price | Price Currency |
Gross Amount | Commission | Net Amount, all amounts in the base currency (EUR). Differences with the Activity
Statement (lynx.py), which stays the main source (docs/brokers/lynx.md, D31):
- trades carry the symbol, not the ISIN: `fill_isins` maps symbols to ISINs from other LYNX rows, the dividend
  descriptions ("IUSA(IE0031442068) Cash Dividend…") and the fund catalog;
- costs = Net − Gross: IBKR's commission plus the Belgian stock exchange tax (the Commission column has only the first);
- "FX Translations P&L" (revaluation of foreign cash) is not a transaction and is left out.
The rows only count after the newest Activity Statement's end (`combine`), like the Flex sync (D28).
"""
from __future__ import annotations

import re
from datetime import datetime

import pdfplumber

from ..models import (BUY, CASH_IN, CASH_OUT, DIVIDEND, FEE, INTEREST, SELL, WITHHOLDING, Transaction)
from ..util import num_us
from .classify import guess_class

BROKER = "LYNX"
HEADER = ["date", "account", "description", "transaction type", "symbol", "quantity", "price", "price currency",
          "gross amount", "commission", "net amount"]
_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")


def is_transaction_history(first_page_text: str) -> bool:
    t = (first_page_text or "").lower()
    return t.lstrip().startswith("transaction history") and "transaction type" in t and "net amount" in t


def read_rows(source) -> list[list[str]]:
    rows = []
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    cells = [(c or "").replace("\n", " ").strip() for c in raw]
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cells[0] if cells else ""):
                        rows.append(cells)
    return rows


def _n(s: str) -> float:
    s = (s or "").strip()
    return 0.0 if s in ("", "-") else num_us(s)


def parse_rows(rows: list[list[str]], filename: str = "") -> tuple[list[Transaction], list[str]]:
    txns, warnings, skipped_fx = [], [], 0
    for i, r in enumerate(rows):
        r = (r + [""] * 11)[:11]
        d, _acct, desc, typ, sym, qty, price, pcur, gross, comm, net = r
        when = datetime.strptime(d, "%Y-%m-%d")
        desc_flat = re.sub(r"\s+", "", desc)                     # the ISIN is often broken over two lines
        t = typ.lower().replace("  ", " ").strip()
        sym = "" if sym == "-" else sym
        base = dict(broker=BROKER, ts=when, source=filename, order=i)
        if t in ("buy", "sell"):
            q = abs(_n(qty)) * (1 if t == "buy" else -1)
            g, nv = _n(gross), _n(net)
            txns.append(Transaction(**base, kind=BUY if q > 0 else SELL, asset=sym, name=desc,
                                    asset_class=guess_class(desc + " " + sym), qty=q, price=_n(price) or None,
                                    price_cur=pcur if pcur not in ("", "-") else None, gross_eur=abs(g),
                                    fees_eur=round(abs(nv - g), 2), total_eur=nv, note=sym, ref="history"))
        elif t == "dividend":
            m = _ISIN.search(desc_flat)
            isin = m.group(1) if m else sym
            txns.append(Transaction(**base, kind=DIVIDEND, asset=isin, name=sym or desc, asset_class=guess_class(desc + " ETF" if "ETF" in desc.upper() else desc),
                                    qty=0.0, currency="EUR", amount_eur=_n(net), note=desc, ref="history"))
        elif t in ("foreign tax withholding", "withholding tax"):
            m = _ISIN.search(desc_flat)
            if m and "interest" not in desc.lower():
                txns.append(Transaction(**base, kind=WITHHOLDING, asset=m.group(1), name=sym or desc,
                                        asset_class=guess_class(desc), qty=0.0, currency="EUR", amount_eur=_n(net),
                                        note=desc, ref="history"))
            else:
                txns.append(Transaction(**base, kind=WITHHOLDING, asset="EUR", name=desc, asset_class="Cash/FX",
                                        qty=_n(net), currency="EUR", amount_eur=_n(net), note=desc, ref="history"))
        elif "interest" in t:
            txns.append(Transaction(**base, kind=INTEREST, asset="EUR", name=desc, asset_class="Cash/FX", qty=_n(net),
                                    currency="EUR", amount_eur=_n(net), note=desc, ref="history"))
        elif t in ("deposit", "withdrawal"):
            v = _n(net)
            txns.append(Transaction(**base, kind=CASH_IN if v > 0 else CASH_OUT, asset="EUR", name=desc, asset_class="Cash/FX",
                                    qty=v, currency="EUR", amount_eur=v, ref="history"))
        elif t.startswith("forex trade"):
            c = _n(comm)
            if abs(c) > 0.0049:
                txns.append(Transaction(**base, kind=FEE, asset=sym, name=f"Currency conversion {sym}", asset_class="Cash/FX",
                                        amount_eur=round(-abs(c), 2), note="Commission on a currency conversion", ref="history"))
        elif t == "adjustment" and "translation" in desc.lower():
            skipped_fx += 1
        elif t in ("other fee", "other fees", "commission adjustment", "fee"):
            txns.append(Transaction(**base, kind=FEE, asset="EUR", name=desc, asset_class="Cash/FX", amount_eur=_n(net),
                                    note=desc, ref="history"))
        else:
            warnings.append(f"LYNX Transaction History: '{typ}' on {d} ({desc[:40]}) left out")
    if skipped_fx:
        warnings.append("LYNX Transaction History: 'FX Translations P&L' left out (a revaluation of foreign cash, not a "
                        "transaction; the Activity Statement's currency result covers it)")
    return txns, warnings


def parse(source, filename: str = ""):
    return parse_rows(read_rows(source), filename)


def fill_isins(txns: list[Transaction], known: dict[str, str]) -> list[str]:
    """Replace symbols by ISINs (in place). `known`: symbol -> ISIN from other rows. Returns warnings."""
    from ..funds import CATALOG
    sym_map = dict(known)
    for t in txns:                                           # dividends in the file itself: 'IUSA(IE0031442068) …'
        m = re.match(r"([A-Z0-9.]+)\(([A-Z]{2}[A-Z0-9]{9}\d)\)", re.sub(r"\s+", "", t.note or ""))
        if m:
            sym_map.setdefault(m.group(1), m.group(2))
    for f in CATALOG.values() if isinstance(CATALOG, dict) else CATALOG:
        if getattr(f, "ticker", "") and getattr(f, "isin", ""):
            sym_map.setdefault(f.ticker.upper(), f.isin)
    missing = set()
    for t in txns:
        if t.kind in (BUY, SELL) and not _ISIN.fullmatch(t.asset or ""):
            isin = sym_map.get((t.asset or "").upper())
            if isin:
                t.asset = isin
            else:
                missing.add(t.asset)
    return [f"LYNX Transaction History: no ISIN known for {s}; upload an Activity Statement or use the Flex sync so the "
            "symbol can be matched" for s in sorted(missing)]
