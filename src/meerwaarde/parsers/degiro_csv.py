"""DEGIRO CSV exports (backlog item 13, docs/brokers/degiro.md): Transacties (Transactions.csv) and Rekeningoverzicht
(Account.csv), in Dutch or English, with a comma or semicolon as separator and either decimal notation.

Columns are found by their header name, not their position (DEGIRO added AutoFX columns over time). A value column
is followed by an unnamed column with its currency. The Transacties rows become the same transactions as the PDF
parser (`degiro.py`); the account rows are handed to `degiro_account.parse_rows`, so all its rules apply unchanged.
"""
from __future__ import annotations

import csv
import io
import re

from ..models import BUY, SELL, Transaction
from ..util import parse_dt
from . import degiro_account
from .classify import guess_class

BROKER = "DEGIRO"

TX_COLS = {
    "date": ("datum", "date"), "time": ("tijd", "time"), "product": ("product",), "isin": ("isin",),
    "exchange": ("beurs", "reference exchange", "exchange"), "venue": ("uitvoeringsplaats", "venue", "execution venue"),
    "qty": ("aantal", "quantity", "number"), "price": ("koers", "price"), "local": ("lokale waarde", "local value"),
    "value": ("waarde eur", "waarde", "value eur", "value"), "fx": ("wisselkoers", "exchange rate"),
    "autofx": ("autofx kosten", "autofx fee", "autofx"),
    "fees": ("transactiekosten en/of kosten van derden eur", "transactiekosten en/of kosten van derden", "transactiekosten",
             "transaction and/or third party fees eur", "transaction and/or third party fees", "transaction costs", "transaction"),
    "total": ("totaal eur", "totaal", "total eur", "total"), "order": ("order id", "order-id", "orderid"),
}
ACC_COLS = {
    "date": ("datum", "date"), "time": ("tijd", "time"), "valuta": ("valutadatum", "value date"), "product": ("product",),
    "isin": ("isin",), "desc": ("omschrijving", "description"), "fx": ("fx",), "mut": ("mutatie", "change"),
    "saldo": ("saldo", "balance"), "order": ("order id", "order-id", "orderid"),
}


def number(s: str) -> float:
    """'1.234,56', '1,234.56', '-1105,00', '-1105.00', '' -> float (0.0 for empty)."""
    s = (s or "").strip().replace(" ", "").replace(" ", "").replace("'", "")
    if not s:
        return 0.0
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,8}$", s) and s.count(",") == 1 else s.replace(",", "")
    return float(s)


def _dutch(v: float) -> str:
    """A number in the notation degiro_account expects (decimal comma, no thousands separator)."""
    txt = f"{v:.8f}".rstrip("0").rstrip(".")
    if "." not in txt:
        txt += ".00"
    return txt.replace(".", ",")


def _rows(text: str) -> tuple[list[str], list[list[str]]]:
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return [c.strip() for c in rows[0]], rows[1:]


def _index(header: list[str], cols: dict) -> dict:
    low = [h.lower().strip() for h in header]
    out = {}
    for key, names in cols.items():
        for name in names:                      # the most specific name first
            if name in low:
                out[key] = low.index(name)
                break
    return out


def kind_of(header: list[str]) -> str | None:
    low = {h.lower().strip() for h in header}
    if {"omschrijving", "mutatie"} <= low or {"description", "change"} <= low:
        return "account"
    if "isin" in low and (low & {"aantal", "quantity", "number"}) and (low & {"koers", "price"}):
        return "transactions"
    return None


def is_degiro_csv(text: str) -> bool:
    header, _ = _rows(text[:4000])
    return kind_of(header) is not None


def parse_text(text: str, filename: str = ""):
    """CSV text -> (transactions, AccountInfo or None, warnings)."""
    header, rows = _rows(text)
    kind = kind_of(header)
    if kind == "account":
        txns, info, warns = degiro_account.parse_rows(account_rows(header, rows), filename)
        return txns, info, warns
    if kind == "transactions":
        txns, warns = transactions(header, rows, filename)
        return txns, None, warns
    raise ValueError(f"{filename or 'file'}: not a DEGIRO Transacties or Rekeningoverzicht CSV")


def _get(r, ix, key, off=0):
    i = ix.get(key)
    if i is None or i + off >= len(r):
        return ""
    return r[i + off].strip()


def transactions(header, rows, filename=""):
    ix = _index(header, TX_COLS)
    missing = [k for k in ("date", "isin", "qty", "price", "total") if k not in ix]
    if missing:
        raise ValueError(f"{filename}: DEGIRO Transacties CSV without column(s) {', '.join(missing)}")
    txns, warns = [], []
    for i, r in enumerate(rows):
        try:
            qty = number(_get(r, ix, "qty"))
            if not qty:
                continue
            when = parse_dt(f"{_get(r, ix, 'date')} {_get(r, ix, 'time') or '00:00'}", "%d-%m-%Y %H:%M")
        except ValueError:
            warns.append(f"DEGIRO CSV: could not read row {i + 2} of {filename}: {', '.join(r)[:80]}")
            continue
        value = number(_get(r, ix, "value") or _get(r, ix, "local"))
        autofx = number(_get(r, ix, "autofx"))
        fees = number(_get(r, ix, "fees"))
        total = number(_get(r, ix, "total"))
        name = re.sub(r"\s+", " ", _get(r, ix, "product")).strip()
        isin = _get(r, ix, "isin")
        price_cur = _get(r, ix, "price", 1) if re.fullmatch(r"[A-Z]{3}", _get(r, ix, "price", 1)) else ""
        exchange, venue = _get(r, ix, "exchange"), _get(r, ix, "venue")
        txns.append(Transaction(broker=BROKER, ts=when, kind=BUY if qty > 0 else SELL, asset=isin, name=name,
                                asset_class=guess_class(name, isin), qty=qty, price=number(_get(r, ix, "price")),
                                price_cur=price_cur or None, gross_eur=abs(value), fees_eur=-(autofx + fees),
                                total_eur=total, note=exchange + (f"/{venue}" if venue else ""), source=filename,
                                order=-i, ref=_get(r, ix, "order")))
    pct = {t.asset for t in txns if t.price and t.qty and (t.gross_eur or 0) > 0
           and abs(abs(t.qty) * t.price / 100 - t.gross_eur) < 0.02 * max(1.0, t.gross_eur)}
    for t in txns:
        if t.asset in pct:
            t.asset_class = "Bond"
            t.note = (t.note + " price in %").strip()
    return txns, warns


def account_rows(header, rows) -> list[dict]:
    """CSV rows -> the row dicts degiro_account.parse_rows reads (newest first, as exported)."""
    ix = _index(header, ACC_COLS)
    out = []
    for r in rows:
        d = _get(r, ix, "date")
        if not re.fullmatch(r"\d{2}-\d{2}-\d{4}", d):
            continue
        cur, amount = _get(r, ix, "mut"), _get(r, ix, "mut", 1)
        if not re.fullmatch(r"[A-Z]{3}", cur):          # some versions put the amount first, then the currency
            cur, amount = amount, cur
        fx = _get(r, ix, "fx")
        out.append(dict(date=d, time=_get(r, ix, "time"), valuta=_get(r, ix, "valuta"), product=_get(r, ix, "product"),
                        isin=_get(r, ix, "isin"), desc=_get(r, ix, "desc"),
                        fx=_dutch(number(fx)) if fx else "",
                        mut=f"{cur} {_dutch(number(amount))}" if cur and amount else "",
                        saldo=f"{_get(r, ix, 'saldo', 1)} {_get(r, ix, 'saldo')}".strip(), page=0))
    return out
