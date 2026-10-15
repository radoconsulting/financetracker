"""Reynders tax (art. 19bis WIB92) on bond funds and bond ETFs (backlog item 9, docs/decisions.md D25).

Funds with more than 10% of their assets in debt claims (25% for units bought before 2018): when units are sold, 30%
on the interest part of the gain, no exemption, independent of the 10% meerwaardebelasting. The part taxed here is
taken out of the gain for the 10% (setting). A foreign broker doesn't withhold it: it is declared in the tax return.

Base per unit, per FIFO lot, first method that has the data:
1. TIS method: TIS (taxable income per share) at the sale minus TIS at the purchase, each in EUR at its date.
2. Asset test: the real gain (sale price − purchase price, without costs) × the share of debt claims you enter.
3. Worst case: the whole real gain (what a broker does without information), flagged as such.
The base is limited to the real gain of the lot (setting `cap_at_gain`); a sale at a loss pays nothing.

Which funds are subject is your call per fund (`subject`); bond ETFs without an entry are only flagged.
Stored as JSON (the app keeps it in `<data dir>/reynders.json`; the CLI takes `--reynders FILE`):

{"funds": [{"asset": "IE0000000000", "subject": true, "currency": "USD", "debt_pct": 0.98,
            "tis": [{"date": "2023-05-02", "value": 1.10}, {"date": "2026-10-01", "value": 4.25}],
            "source": "https://…", "note": ""}]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

TIS = "TIS"
ASSET_TEST = "asset test"
WORST_CASE = "worst case (100% of the gain)"


@dataclass
class ReyndersRules:
    rate: float = 0.30
    cap_at_gain: bool = True           # base never above the real gain of the lot
    deduct_from_cgt: bool = True       # the part taxed here isn't taxed again at 10%
    flag_classes: tuple = ("Bond ETF",)  # classes flagged as probably subject when there is no entry


@dataclass
class ReyndersFund:
    asset: str
    subject: bool | None = True
    currency: str = "EUR"              # currency the provider publishes the TIS in
    debt_pct: float | None = None      # share of debt claims (0–1), for the asset test
    tis: list = field(default_factory=list)   # [(date, value per share)], any order
    source: str = ""                   # where you found the TIS (provider page)
    note: str = ""

    def tis_at(self, on: date) -> tuple[date, float] | None:
        """The latest published TIS on or before `on`."""
        pts = sorted((d, v) for d, v in self.tis if d <= on)
        return pts[-1] if pts else None


@dataclass
class ReyndersBook:
    funds: dict[str, ReyndersFund] = field(default_factory=dict)

    def get(self, asset: str) -> ReyndersFund | None:
        f = self.funds.get(asset)
        return f if f and f.subject else None

    def to_dict(self) -> dict:
        out = []
        for a, f in sorted(self.funds.items()):
            d = dict(asset=a, subject=f.subject, currency=f.currency,
                     tis=[dict(date=d.isoformat(), value=v) for d, v in sorted(f.tis)])
            for k in ("debt_pct", "source", "note"):
                if getattr(f, k) not in (None, ""):
                    d[k] = getattr(f, k)
            out.append(d)
        return {"funds": out}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ReyndersBook":
        book = cls()
        for r in (d or {}).get("funds") or []:
            a = str(r.get("asset") or "").strip()
            if not a:
                continue
            tis = [(date.fromisoformat(str(t["date"])[:10]), float(t["value"])) for t in r.get("tis") or []
                   if t.get("date") and t.get("value") not in (None, "")]
            dp = r.get("debt_pct")
            book.funds[a] = ReyndersFund(a, r.get("subject", True), r.get("currency") or "EUR",
                                         None if dp in (None, "") else float(dp), tis, str(r.get("source") or ""),
                                         str(r.get("note") or ""))
        return book

    @classmethod
    def load(cls, path: str | Path | None) -> "ReyndersBook":
        if not path or not Path(path).exists():
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8") or "{}"))

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _day(v) -> date:
    return v.date() if isinstance(v, datetime) else v


class Reynders:
    """Per-lot Reynders base for a sale; `eur_per(currency, on)` converts TIS published in another currency."""

    def __init__(self, book: ReyndersBook | None = None, rules: ReyndersRules | None = None, eur_per=None):
        self.book = book or ReyndersBook()
        self.rules = rules or ReyndersRules()
        self.eur_per = eur_per or (lambda cur, on: 1.0 if cur in ("", "EUR", None) else None)

    def applies(self, asset: str) -> bool:
        return self.book.get(asset) is not None

    def _tis_eur(self, f: ReyndersFund, on: date) -> float | None:
        p = f.tis_at(on)
        if p is None:
            return None
        k = self.eur_per(f.currency, p[0])
        return None if k is None else p[1] * k

    def unit_base(self, asset: str, lot, sale_price: float, sale_date: date) -> tuple[float, str]:
        """Reynders base per unit of `lot` sold at `sale_price` on `sale_date`, and the method used ("" = not subject)."""
        f = self.book.get(asset)
        if f is None:
            return 0.0, ""
        real_gain = sale_price - (lot.gross_eur_unit or 0.0)
        base, method = None, ""
        t_sale, t_buy = self._tis_eur(f, _day(sale_date)), self._tis_eur(f, _day(lot.date))
        if t_sale is not None and t_buy is not None:
            base, method = max(0.0, t_sale - t_buy), TIS
        elif t_sale is not None and not any(d <= _day(lot.date) for d, _ in f.tis) and f.debt_pct is None:
            base, method = max(0.0, t_sale), TIS          # bought before the first published TIS: from zero
        elif f.debt_pct is not None:
            base, method = max(0.0, real_gain) * f.debt_pct, ASSET_TEST
        else:
            base, method = max(0.0, real_gain), WORST_CASE
        if self.rules.cap_at_gain:
            base = min(base, max(0.0, real_gain))
        return base, method

    def cgt_deduction(self, asset: str, lot, sale_price: float, sale_date: date, cgt_gain_unit: float) -> float:
        """Per unit: how much of the 10% gain is taken out because it is taxed as Reynders income."""
        if not self.rules.deduct_from_cgt:
            return 0.0
        base, _ = self.unit_base(asset, lot, sale_price, sale_date)
        return min(base, max(0.0, cgt_gain_unit))

    def for_sale(self, asset: str, lots, qty: float, sale_price: float, sale_date: date) -> tuple[float, str]:
        """Total base when selling `qty` units first in, first out, and the methods used."""
        left, total, methods = qty, 0.0, []
        for lot in lots:
            if left <= 1e-12:
                break
            t = min(lot.qty, left)
            left -= t
            b, m = self.unit_base(asset, lot, sale_price, sale_date)
            total += t * b
            if m and m not in methods:
                methods.append(m)
        return total, ", ".join(methods)

    def flagged(self, holdings) -> list[str]:
        """Positions that look like bond funds but have no entry yet."""
        return [f"{h.name} ({h.asset}) looks like a bond fund: Reynders tax (30% on the interest part) probably "
                "applies when you sell. Enter its TIS or bond share on the Reynders tax panel (Meerwaardebelasting tab)."
                for h in holdings if h.asset_class in self.rules.flag_classes and h.asset not in self.book.funds]
