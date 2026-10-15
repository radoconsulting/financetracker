"""Bitvavo 'Transactieoverzicht' (Volledige geschiedenis): PDF, or the CSV export (backlog item 13).

Both are turned into the same row dicts and interpreted by `parse_rows` (pure, tested without files)."""
from __future__ import annotations

import re

import pdfplumber

from ..models import (Transaction, BUY, SELL, REWARD, DEPOSIT, WITHDRAWAL, CASH_IN, CASH_OUT)
from ..util import num_lead, parse_dt

BROKER = "Bitvavo"
OK_STATUS = {"completed", "distributed"}
REWARD_TYPES = {"staking", "fixed_staking", "manually_assigned", "rebate", "affiliate", "campaign", "airdrop", "reward"}


def _unit(cell: str) -> str:
    """Currency or coin code at the end of a cell like '-753.85 EUR' or '0.00009BTC'."""
    m = re.search(r"([A-Za-z][A-Za-z0-9]{1,9})\s*$", (cell or "").strip())
    return m.group(1).upper() if m else ""


def _header_index(row):
    keys = {}
    for i, c in enumerate(row):
        c = (c or "").replace("\n", " ").strip().lower()
        if c.startswith("date"): keys["date"] = i
        elif c == "type": keys["type"] = i
        elif c == "currency": keys["currency"] = i
        elif c == "amount": keys["amount"] = i
        elif c.startswith("quote price"): keys["quote"] = i
        elif c.startswith("received"): keys["paid"] = i
        elif c.startswith("fee"): keys["fee"] = i
        elif c == "status": keys["status"] = i
        elif c.startswith("transaction id"): keys["txid"] = i
        elif c.startswith("address"): keys["address"] = i
    return keys if {"date", "type", "currency", "amount"} <= keys.keys() else None


def read_rows(source) -> list[dict]:
    """All table rows as dicts, with rows split over a page break merged."""
    rows, keys = [], None
    with pdfplumber.open(source) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for raw in table:
                    cells = [(c or "").replace("\n", "") for c in raw]
                    k = _header_index(raw)
                    if k:
                        keys = k
                        continue
                    if not keys:
                        continue
                    get = lambda name: cells[keys[name]] if name in keys and keys[name] < len(cells) else ""
                    if not get("date").strip():
                        if rows:  # continuation of the previous row
                            for name in keys:
                                rows[-1][name] = rows[-1].get(name, "") + get(name)
                        continue
                    rows.append({name: get(name) for name in keys})
    return rows


CSV_COLS = {"date": ("date",), "time": ("time",), "type": ("type",), "currency": ("currency",), "amount": ("amount",),
            "quote": ("quote price", "price"), "paid_cur": ("received / paid currency", "received/paid currency"),
            "paid": ("received / paid amount", "received/paid amount"), "fee_cur": ("fee currency",),
            "fee": ("fee amount",), "status": ("status",), "txid": ("transaction id",), "address": ("address",)}


def is_bitvavo_csv(text: str) -> bool:
    head = text.lstrip("\ufeff").split("\n", 1)[0].lower().replace("received/paid", "received / paid")
    return "transaction id" in head and "received / paid" in head and "currency" in head


def csv_rows(text: str) -> list[dict]:
    """Bitvavo CSV -> the row dicts of the PDF reader ('paid' and 'fee' carry their unit at the end, as in the PDF)."""
    import csv
    import io
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        return []
    low = [h.strip().lower() for h in rows[0]]
    ix = {k: next((low.index(n) for n in names if n in low), None) for k, names in CSV_COLS.items()}

    def get(r, k):
        i = ix.get(k)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        out.append(dict(date=f"{get(r, 'date')} {get(r, 'time')}".strip(), type=get(r, "type"), currency=get(r, "currency"),
                        amount=get(r, "amount"), quote=get(r, "quote"),
                        paid=f"{get(r, 'paid')} {get(r, 'paid_cur')}".strip(), fee=f"{get(r, 'fee')} {get(r, 'fee_cur')}".strip(),
                        status=get(r, "status"), txid=get(r, "txid"), address=get(r, "address")))
    return out


def _when(s: str):
    """'2026-10-12 14:19:41' (PDF and CSV), also with milliseconds or a 'T'."""
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M"):
        try:
            return parse_dt(s, fmt)
        except ValueError:
            continue
    raise ValueError(s)


def parse(source, filename: str = "") -> tuple[list[Transaction], list[str]]:
    return parse_rows(read_rows(source), filename)


def parse_csv_text(text: str, filename: str = "") -> tuple[list[Transaction], list[str]]:
    return parse_rows(csv_rows(text), filename)


def parse_rows(rows: list[dict], filename: str = "") -> tuple[list[Transaction], list[str]]:
    txns, warnings = [], []
    for i, r in enumerate(rows):
        status = r.get("status", "").strip().lower()
        if status and status not in OK_STATUS:
            continue
        typ = r["type"].strip().lower()
        cur = r["currency"].strip().upper()
        try:
            ts = _when(r["date"])
        except ValueError:
            warnings.append(f"Bitvavo: skipped a row with an unreadable date: {r['date']!r}")
            continue
        amount = num_lead(r["amount"])
        quote = num_lead(r.get("quote", ""))
        paid = num_lead(r.get("paid", ""))
        fee_txt = r.get("fee", "").replace(" ", "")
        fee = num_lead(fee_txt)
        fee_cur = _unit(fee_txt) or "EUR"
        base = dict(broker=BROKER, ts=ts, asset=cur, name=cur, asset_class="Crypto", ref=r.get("txid", ""), source=filename, order=i)

        if cur == "EUR":
            if typ == "deposit":
                txns.append(Transaction(**{**base, "asset_class": "Cash/FX"}, kind=CASH_IN, qty=amount, amount_eur=amount, currency="EUR"))
            elif typ == "withdrawal":
                txns.append(Transaction(**{**base, "asset_class": "Cash/FX"}, kind=CASH_OUT, qty=amount, amount_eur=amount, currency="EUR"))
            continue

        if typ in ("buy", "sell"):
            quote_cur = _unit(r.get("paid", ""))
            if quote_cur and quote_cur != "EUR":
                warnings.append(f"Bitvavo: {ts:%Y-%m-%d} {typ} of {cur} was paid in {quote_cur}; only EUR markets are supported, row skipped")
                continue
            fee_eur = fee if fee_cur == "EUR" else 0.0
            if typ == "buy":
                gross = abs(paid) - fee_eur
                txns.append(Transaction(**base, kind=BUY, qty=abs(amount), price=quote, price_cur="EUR",
                                        gross_eur=gross, fees_eur=fee_eur, total_eur=-abs(paid)))
            else:
                gross = abs(paid) + fee_eur
                txns.append(Transaction(**base, kind=SELL, qty=-abs(amount), price=quote, price_cur="EUR",
                                        gross_eur=gross, fees_eur=fee_eur, total_eur=abs(paid)))
        elif typ == "deposit":
            txns.append(Transaction(**base, kind=DEPOSIT, qty=abs(amount)))
        elif typ == "withdrawal":
            txns.append(Transaction(**base, kind=WITHDRAWAL, qty=-abs(amount),
                                    fee_qty=fee if fee_cur == cur else 0.0, note=r.get("address", "")))
        elif typ in REWARD_TYPES or amount > 0:
            if amount > 0:
                txns.append(Transaction(**base, kind=REWARD, qty=amount, note=r["type"]))
        else:
            warnings.append(f"Bitvavo: unknown row type {r['type']!r} on {ts:%Y-%m-%d} skipped")
    return txns, warnings
