"""LYNX 'Jaaroverzicht' (annual tax overview) PDF: a check, not a source of transactions (docs/decisions.md D32).

LYNX's summary for the tax return: dividends and interest received in and outside Belgium, the tax withheld, the
amount for code 1444-11, the account value on 1 January and 31 December, and the dividend and interest rows behind
them. The same income is already in the Activity Statements, so reading it as transactions would count it twice;
the app compares it with its own figures for that year instead (Tax report). Page 1 is only the LYNX logo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

_EUR = r"-?€-?[\d,]+\.\d{2}"


@dataclass
class AnnualOverview:
    broker: str = "LYNX"
    year: int | None = None
    period: str = ""
    totals: dict = field(default_factory=dict)        # key -> EUR (see LINES)
    code_1444: float | None = None                    # the amount LYNX puts in code 1444-11
    values: dict = field(default_factory=dict)        # CASH/AANDELEN/OPTIES/OVERIGE/TOTAAL -> (1 January, 31 December)
    dividends: list = field(default_factory=list)     # [(date, name, gross_local, currency, eur, tax_eur, net_eur)]
    interest: list = field(default_factory=list)      # [(date, description, eur)]
    interest_tax: list = field(default_factory=list)  # [(date, description, eur)]
    source: str = ""


LINES = {
    "dividends_be": "ontvangen bruto dividenden belgië", "dividend_tax_be": "betaalde dividendbelasting belgië",
    "dividends_foreign": "ontvangen bruto dividenden buiten belgië",
    "dividend_tax_foreign": "betaalde dividendbelasting buiten belgië",
    "interest_be": "ontvangen interest belgië", "interest_foreign": "ontvangen interest buiten belgië",
}


def is_annual_overview(text: str) -> bool:
    t = (text or "").lower()
    return "ontvangen bruto dividenden" in t and ("1444" in t or "vermogensbestanddelen" in t)


def _eur(s: str) -> float:
    return float(s.replace("€", "").replace(",", ""))


def _date(s: str):
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


def parse_text(text: str, filename: str = "") -> AnnualOverview:
    ov = AnnualOverview(source=filename)
    m = re.search(r"Periode\s+(\d{2}/\d{2}/(\d{4}))\s*-\s*(\d{2}/\d{2}/\d{4})", text)
    if m:
        ov.period, ov.year = f"{m.group(1)} – {m.group(3)}", int(m.group(2))
    low = text.lower()
    for key, label in LINES.items():
        # the label is followed by its amount; 'belgië' must not match 'buiten belgië'
        for mm in re.finditer(re.escape(label) + r"\s+(" + _EUR + ")", low):
            start = mm.start()
            if key.endswith("_be") and low[max(0, start - 7):start].endswith("buiten "):
                continue
            ov.totals[key] = _eur(mm.group(1))
            break
    m = re.search(r"1444-11\s+(" + _EUR + ")", text)
    if m:
        ov.code_1444 = _eur(m.group(1))
    for name in ("CASH", "AANDELEN", "OPTIES", "OVERIGE", "TOTAAL"):
        m = re.search(rf"^{name}\s+({_EUR})\s+({_EUR})", text, re.M)
        if m:
            ov.values[name] = (_eur(m.group(1)), _eur(m.group(2)))
    section = None
    for line in text.splitlines():
        s = line.strip()
        lo = s.lower()
        if lo.startswith("ontvangen dividenden"):
            section = "div"
        elif lo.startswith("rente overzicht"):
            section = "int"
        elif lo.startswith("ingehouden belasting interesten"):
            section = "int_tax"
        elif lo.startswith("overige mutaties") or lo.startswith("disclaimer"):
            section = None
        m = re.match(r"([A-Z][a-z]{2} \d{1,2}, \d{4})\s+(.*)$", s)
        if not (m and section):
            continue
        d, rest = _date(m.group(1)), m.group(2)
        if section == "div":
            mm = re.match(r"(.*?)\s+([$€£]|[A-Z]{3})?(-?[\d,]+\.\d{2})\s+\S*?(-?[\d,]+\.\d{2})\s+(" + _EUR + r")\s+(" + _EUR
                          + r")\s+(" + _EUR + r")$", rest)
            if mm:
                cur = {"$": "USD", "€": "EUR", "£": "GBP"}.get(mm.group(2) or "", mm.group(2) or "")
                ov.dividends.append((d, mm.group(1).strip(), float(mm.group(3).replace(",", "")), cur,
                                     _eur(mm.group(5)), _eur(mm.group(6)), _eur(mm.group(7))))
        else:
            mm = re.match(r"(.*?)\s+(" + _EUR + r")\s+(" + _EUR + r")$", rest)
            if mm:
                (ov.interest if section == "int" else ov.interest_tax).append((d, mm.group(1).strip(), _eur(mm.group(3))))
    return ov


def parse(source, filename: str = "") -> AnnualOverview:
    with pdfplumber.open(source) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    return parse_text(text, filename)
