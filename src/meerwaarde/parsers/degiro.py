"""DEGIRO 'Transacties' PDF (Rekeningoverzicht > Transacties > exporteren als PDF)."""
from __future__ import annotations

import re

from ..models import Transaction, BUY, SELL
from ..util import num_eu, parse_dt
from .classify import guess_class

BROKER = "DEGIRO"
_N = r"-?[\d.]+(?:,\d+)?"
LINE = re.compile(
    r"^\s*(?P<date>\d\d-\d\d-\d{4}) (?P<time>\d\d:\d\d)\s+(?P<product>.*?)\s+(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\s+"
    r"(?P<exchange>\S+)\s+(?:(?P<venue>[A-Z]{4})\s+)?(?P<qty>" + _N + r")\s+(?P<price>" + _N + r") (?P<pcur>[A-Z]{3})\s+"
    r"(?P<local>" + _N + r") (?P<lcur>[A-Z]{3})\s+(?P<value>" + _N + r")\s+(?P<fx>" + _N + r")\s+(?P<autofx>" + _N + r")\s+"
    r"(?:(?P<fees>" + _N + r")\s+)?(?P<total>" + _N + r")\s*$")
_CONT = re.compile(r"^\s{6,}(?P<text>[A-Z0-9&$%().,'/ +\-]{2,45})\s*$")
_SKIP = re.compile(r"(Datum|Uitvoerings|Transactieko|plaats|derden|Pagina|flatexDEGIRO|Gebruikersnaam|kosten|Rekeningoverzicht)", re.I)


def parse_text(text: str, filename: str = "") -> tuple[list[Transaction], list[str]]:
    txns, warnings = [], []
    last = None
    cont_lines = 0
    for i, line in enumerate(text.split("\n")):
        m = LINE.match(line)
        if m:
            g = m.groupdict()
            qty = num_eu(g["qty"])
            value = num_eu(g["value"])           # EUR, negative for a buy
            autofx = num_eu(g["autofx"])         # EUR, negative
            fees = num_eu(g["fees"]) if g["fees"] else 0.0
            total = num_eu(g["total"])
            name = re.sub(r"\s+", " ", g["product"]).strip()
            last = Transaction(
                broker=BROKER, ts=parse_dt(f"{g['date']} {g['time']}", "%d-%m-%Y %H:%M"),
                kind=BUY if qty > 0 else SELL, asset=g["isin"], name=name,
                asset_class=guess_class(name, g["isin"]), qty=qty,
                price=num_eu(g["price"]), price_cur=g["pcur"], gross_eur=abs(value),
                fees_eur=-(autofx + fees), total_eur=total,
                note=f"{g['exchange']}" + (f"/{g['venue']}" if g["venue"] else ""), source=filename,
                order=-i)  # the export is newest first
            txns.append(last)
            cont_lines = 0
            continue
        if re.match(r"^\s*\d\d-\d\d-\d{4} \d\d:\d\d", line):
            warnings.append(f"DEGIRO: could not read the line starting {line.strip()[:60]!r}")
            last = None
            continue
        c = _CONT.match(line)
        if last is not None and c and cont_lines < 2 and not _SKIP.search(line):
            last.name = f"{last.name} {c.group('text').strip()}"
            last.asset_class = guess_class(last.name, last.asset)
            cont_lines += 1
        elif line.strip():
            last = None
    # Detect bonds quoted in percent (value = qty * price / 100)
    pct = {t.asset for t in txns if t.price and t.qty and abs(abs(t.qty) * t.price / 100 - (t.gross_eur or 0)) < 0.02 * max(1.0, t.gross_eur or 0) and (t.gross_eur or 0) > 0}
    for t in txns:
        if t.asset in pct:
            t.asset_class = "Bond"
            t.note = (t.note + " price in %").strip()
    return txns, warnings
