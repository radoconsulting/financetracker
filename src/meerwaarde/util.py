"""Small parsing helpers."""
from __future__ import annotations

import re
from datetime import datetime

import pdfplumber


def pdf_text(path_or_file, layout: bool = True) -> str:
    """Text of every page, keeping the visual layout so columns stay on one line."""
    with pdfplumber.open(path_or_file) as pdf:
        return "\n".join((p.extract_text(layout=layout) or "") for p in pdf.pages)


def num_eu(s: str) -> float:
    """'1.234,56' -> 1234.56 (DEGIRO, Dutch notation)."""
    s = s.strip().replace(" ", "")
    if not s:
        return 0.0
    return float(s.replace(".", "").replace(",", "."))


def num_us(s: str) -> float:
    """'1,234.56' -> 1234.56 (IBKR/LYNX notation)."""
    s = s.strip().replace(" ", "")
    if s in ("", "--"):
        return 0.0
    return float(s.replace(",", ""))


_LEAD_NUM = re.compile(r"^\s*(-?[0-9]+(?:\.[0-9]+)?(?:[eE]-?\d+)?)")


def num_lead(s) -> float:
    """Leading number of a cell like '22719 EUR' or '0.00009BTC'."""
    if s is None:
        return 0.0
    m = _LEAD_NUM.match(str(s).replace(" ", ""))
    return float(m.group(1)) if m else 0.0


def parse_dt(s: str, fmt: str) -> datetime:
    return datetime.strptime(s.strip(), fmt)
