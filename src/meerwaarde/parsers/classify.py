"""Guess an asset class from a product name. Users can override any guess."""
from __future__ import annotations

import re

_ETF = re.compile(r"\b(ETF|UCITS|ISHARES|ISHRS|ISHS|VANGUARD|SPDR|STST|XTRACKERS|XTR|AMUNDI|LYXOR|INVESCO|WISDOMTREE|VANECK|HSBC .*ETF)\b", re.I)
_BONDY = re.compile(r"\b(BOND|BD|CORP|COVERED|TREASURY|TREAS|AGGREGATE|AGG|GOVT|GOV|INFLATION|TIPS)\b", re.I)
_BOND = re.compile(r"(BUNDESOBL|BUNDESANL|\bOLO\b|\bOAT\b|\bBTP\b|\bBOND\b \d|\d+(?:[.,]\d+)?% ?\d{2,4}|STAATSLENING)", re.I)
_TURBO = re.compile(r"(TURBO|\bSL \d|\bFN \d|SPRINTER|SPEEDER|\bMINI (LONG|SHORT)|WARRANT|\d+(?:\.\d+)?TC[IP]OPEN)", re.I)


def guess_class(name: str, isin: str = "") -> str:
    n = name or ""
    if _TURBO.search(n):
        return "Turbo"
    if _BOND.search(n):
        return "Bond"
    if _ETF.search(n):
        return "Bond ETF" if _BONDY.search(n) else "ETF"
    return "Stock"
