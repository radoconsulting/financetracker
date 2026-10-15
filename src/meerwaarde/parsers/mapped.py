"""CSV exports of other brokers through a column mapping you make once (backlog item 13, docs/decisions.md D29).

Bolero, Saxo, Keytrade and others each export their own CSV. Rather than guessing formats without sample files, a
`CsvFormat` says which column holds what; it is recognised again by its header row. Formats are kept in
`<data dir>/csv_formats.json` and registered with `load()` before parsing.

Per row: a type column (values mapped to buy, sell, dividend, withholding, interest, fee, deposit, withdrawal), or
the sign of the quantity when there's no type column. Amounts in a foreign currency need an exchange-rate column
(EUR per unit, or units per EUR with `fx_inverted`) or an EUR amount column.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..models import (BUY, CASH_IN, CASH_OUT, DIVIDEND, FEE, INTEREST, SELL, WITHHOLDING, Transaction)
from .classify import guess_class

FIELDS = ("date", "time", "type", "isin", "name", "qty", "price", "currency", "fx", "costs", "amount", "amount_eur")
KINDS = {"buy": BUY, "sell": SELL, "dividend": DIVIDEND, "withholding": WITHHOLDING, "interest": INTEREST, "fee": FEE,
         "deposit": CASH_IN, "withdrawal": CASH_OUT}


@dataclass
class CsvFormat:
    name: str                                   # e.g. "Bolero transactions"
    broker: str                                 # e.g. "Bolero"
    header: list                                # the header row it is recognised by
    columns: dict                               # field -> column name
    delimiter: str = ","
    decimal: str = ","                          # "," or "."
    date_format: str = "%d/%m/%Y"
    types: dict = field(default_factory=dict)   # value in the type column -> buy/sell/dividend/...
    fx_inverted: bool = False                   # the rate column is units per EUR (EUR/USD 1.08) instead of EUR per unit

    def matches(self, header: list[str]) -> bool:
        return [h.strip().lower() for h in header] == [h.strip().lower() for h in self.header]


FORMATS: list[CsvFormat] = []


def load(path) -> list[CsvFormat]:
    """Register the formats saved in `path` (replaces what was registered)."""
    FORMATS.clear()
    p = Path(path) if path else None
    if p and p.exists():
        for d in json.loads(p.read_text(encoding="utf-8") or "[]"):
            FORMATS.append(CsvFormat(**{k: v for k, v in d.items() if k in CsvFormat.__dataclass_fields__}))
    return FORMATS


def save(path, formats: list[CsvFormat]):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps([asdict(f) for f in formats], indent=2, ensure_ascii=False), encoding="utf-8")


def sniff(text: str) -> tuple[list[str], str, list[list[str]]]:
    """Header, delimiter and the first rows of a CSV (for the mapping screen)."""
    text = text.lstrip("﻿")
    first = text.split("\n", 1)[0]
    delim = max((";", ",", "\t"), key=first.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return ([c.strip() for c in rows[0]] if rows else []), delim, rows[1:6]


def match(text: str) -> CsvFormat | None:
    header, _d, _r = sniff(text[:4000])
    return next((f for f in FORMATS if f.matches(header)), None)


def _num(s: str, decimal: str) -> float | None:
    s = (s or "").strip().replace(" ", "").replace(" ", "").replace("€", "")
    if not s:
        return None
    s = s.replace(".", "").replace(",", ".") if decimal == "," else s.replace(",", "")
    return float(s)


def parse_text(text: str, fmt: CsvFormat, filename: str = "") -> tuple[list[Transaction], list[str]]:
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿")), delimiter=fmt.delimiter) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    low = [h.strip().lower() for h in rows[0]]
    ix = {k: low.index(c.strip().lower()) for k, c in fmt.columns.items() if c and c.strip().lower() in low}
    types = {k.strip().lower(): v for k, v in fmt.types.items()}
    txns, warns = [], []
    for n, r in enumerate(rows[1:], start=2):
        get = lambda k: r[ix[k]].strip() if k in ix and ix[k] < len(r) else ""  # noqa: E731
        try:
            when = datetime.strptime(f"{get('date')} {get('time')}".strip(),
                                     f"{fmt.date_format} %H:%M" if get("time") and len(get("time")) <= 5 else
                                     (f"{fmt.date_format} %H:%M:%S" if get("time") else fmt.date_format))
        except ValueError:
            warns.append(f"{fmt.broker} CSV: row {n} of {filename}: unreadable date {get('date')!r}; skipped")
            continue
        try:
            qty, price = _num(get("qty"), fmt.decimal), _num(get("price"), fmt.decimal)
            costs, amount = _num(get("costs"), fmt.decimal), _num(get("amount"), fmt.decimal)
            amount_eur, fx = _num(get("amount_eur"), fmt.decimal), _num(get("fx"), fmt.decimal)
        except ValueError:
            warns.append(f"{fmt.broker} CSV: row {n} of {filename}: unreadable number; skipped")
            continue
        cur = (get("currency") or "EUR").upper()
        kind_name = types.get(get("type").lower()) if "type" in ix else ("buy" if (qty or 0) > 0 else "sell" if qty else None)
        kind = KINDS.get(kind_name or "")
        if kind is None:
            if get("type"):
                warns.append(f"{fmt.broker} CSV: type {get('type')!r} (row {n}) isn't mapped; skipped")
            continue
        if cur == "EUR":
            k = 1.0
        elif fx:
            k = 1.0 / fx if fmt.fx_inverted else fx
        else:
            k = None
        isin, name = get("isin"), get("name") or get("isin")
        base = dict(broker=fmt.broker, ts=when, asset=isin or cur, name=name, source=filename, order=n)

        def eur(local):
            if amount_eur is not None:
                return amount_eur
            return None if local is None or k is None else local * k

        if kind in (BUY, SELL):
            if not qty or price is None:
                warns.append(f"{fmt.broker} CSV: row {n} has no quantity or price; skipped")
                continue
            q = abs(qty) if kind == BUY else -abs(qty)
            if k is not None:
                gross, fees = abs(q) * price * k, abs(costs or 0.0) * k
            elif amount_eur is not None:                   # EUR value given, no rate: costs in cur can't be converted
                gross, fees = abs(amount_eur), 0.0
                if costs:
                    warns.append(f"{fmt.broker} CSV: row {n}: costs in {cur} left out (no exchange rate column)")
            else:
                warns.append(f"{fmt.broker} CSV: row {n} is in {cur} without an exchange rate or EUR amount; skipped")
                continue
            total = -(gross + fees) if kind == BUY else gross - fees
            txns.append(Transaction(**base, kind=kind, asset_class=guess_class(name, isin), qty=q, price=price, price_cur=cur,
                                    gross_eur=gross, fees_eur=fees, total_eur=total))
            continue
        value = eur(amount)
        if value is None:
            warns.append(f"{fmt.broker} CSV: row {n} ({kind}) is in {cur} without an exchange rate or EUR amount; skipped")
            continue
        if kind in (WITHHOLDING, FEE, CASH_OUT):
            value = -abs(value)
        elif kind in (DIVIDEND, CASH_IN):
            value = abs(value)
        if kind in (DIVIDEND, WITHHOLDING):
            txns.append(Transaction(**base, kind=kind, asset_class=guess_class(name, isin), qty=0.0, currency=cur,
                                    amount_eur=round(value, 2), price=amount))
        elif kind == INTEREST:
            txns.append(Transaction(**{**base, "asset": isin or cur}, kind=kind, asset_class="Cash/FX" if not isin else guess_class(name, isin),
                                    qty=amount or 0.0, currency=cur, amount_eur=round(value, 2)))
        elif kind == FEE:
            txns.append(Transaction(**base, kind=kind, asset_class="Cash/FX", amount_eur=round(value, 2), note=get("type")))
        else:
            txns.append(Transaction(**{**base, "asset": cur}, kind=kind, asset_class="Cash/FX", qty=amount or 0.0,
                                    currency=cur, amount_eur=round(value, 2)))
    return txns, warns
