"""Positions no export covers: funds at a bank, tak 21/23 policies, savings, investment gold (backlog item 8, D24).

Each position becomes ordinary BUY/SELL transactions, so FIFO, profit and the meerwaardebelasting treat it like a
broker position. Stored as JSON (the app keeps it in `<data dir>/manual.json`; the CLI takes `--manual FILE`):

{
  "positions": [{
    "id": "p1", "broker": "Bank", "asset": "BE0000000000", "name": "Some fund", "asset_class": "Fund",
    "trades": [{"date": "2021-03-01", "qty": 10, "price": 100.0, "costs": 2.5},
               {"date": "2026-05-04", "qty": -4, "price": 130.0}],
    "price_now": 125.0, "price_now_date": "2026-10-01", "price_31dec": 120.0, "note": ""
  }]
}

Prices are per unit in EUR. A policy or a savings account without units is entered with quantity 1 and its value as
the price. `price_31dec` is the value on 31-12-2025, needed for units held on that date.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .models import ASSET_CLASSES, BUY, SELL, Transaction

SOURCE = "manual entry"
DEFAULT_BROKER = "Manual"


@dataclass
class ManualTrade:
    date: date
    qty: float                     # units; negative = sold
    price: float                   # per unit in EUR, before costs
    costs: float = 0.0             # entry fee, commission, stock exchange tax (EUR); in profit, not in the tax


@dataclass
class ManualPosition:
    asset: str                     # ISIN, or your own code (e.g. POLICY-1) when there is none
    name: str = ""
    asset_class: str = "Fund"
    broker: str = DEFAULT_BROKER   # bank, insurer or dealer; ownership shares per broker apply to it
    trades: list[ManualTrade] = field(default_factory=list)
    price_now: float | None = None
    price_now_date: date | None = None
    price_31dec: float | None = None
    note: str = ""
    id: str = ""

    @property
    def qty(self) -> float:
        return sum(t.qty for t in self.trades)

    def problems(self) -> list[str]:
        """What is wrong with this entry; an empty list means it can be used."""
        out = []
        label = self.name or self.asset or "position"
        if not self.asset:
            out.append(f"{label}: no asset code")
        if self.asset_class not in ASSET_CLASSES:
            out.append(f"{label}: unknown class {self.asset_class!r}")
        if not self.trades:
            out.append(f"{label}: no purchase")
        held = 0.0
        for t in sorted(self.trades, key=lambda t: t.date):
            if t.price is None or t.price < 0:
                out.append(f"{label}: trade on {t.date} has no price")
            held += t.qty
            if held < -1e-9:
                out.append(f"{label}: sells more than was bought on {t.date}")
                break
        return out

    def transactions(self) -> list[Transaction]:
        out = []
        for i, t in enumerate(sorted(self.trades, key=lambda t: t.date)):
            if abs(t.qty) < 1e-12:
                continue
            gross = abs(t.qty) * t.price
            buy = t.qty > 0
            out.append(Transaction(
                broker=self.broker or DEFAULT_BROKER, ts=datetime.combine(t.date, datetime.min.time()),
                kind=BUY if buy else SELL, asset=self.asset, name=self.name or self.asset, asset_class=self.asset_class,
                qty=t.qty, price=t.price, price_cur="EUR", gross_eur=gross, fees_eur=t.costs,
                total_eur=-(gross + t.costs) if buy else gross - t.costs, order=i, ref=self.id, source=SOURCE,
                note=self.note))
        return out


def _date(v) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _num(v) -> float | None:
    return None if v in (None, "") else float(v)


@dataclass
class ManualBook:
    positions: list[ManualPosition] = field(default_factory=list)

    def valid(self) -> list[ManualPosition]:
        return [p for p in self.positions if not p.problems()]

    def warnings(self) -> list[str]:
        return [f"Manual position {w}; left out" for p in self.positions for w in p.problems()[:1]]

    def transactions(self) -> list[Transaction]:
        return [t for p in self.valid() for t in p.transactions()]

    def apply_prices(self, pricebook):
        """Prices entered with a position go into the PriceBook (below your own price overrides, above online)."""
        for p in self.valid():
            pricebook.set_manual(p.asset, now=p.price_now, now_date=p.price_now_date, reference=p.price_31dec)

    def new_id(self) -> str:
        used = {p.id for p in self.positions}
        n = len(self.positions) + 1
        while f"p{n}" in used:
            n += 1
        return f"p{n}"

    # ---------- JSON ----------
    def to_dict(self) -> dict:
        out = []
        for p in self.positions:
            d = dict(id=p.id or "", broker=p.broker, asset=p.asset, name=p.name, asset_class=p.asset_class,
                     trades=[dict(date=t.date.isoformat(), qty=t.qty, price=t.price, **({"costs": t.costs} if t.costs else {}))
                             for t in sorted(p.trades, key=lambda t: t.date)])
            for k in ("price_now", "price_31dec", "note"):
                if getattr(p, k) not in (None, ""):
                    d[k] = getattr(p, k)
            if p.price_now_date:
                d["price_now_date"] = p.price_now_date.isoformat()
            out.append(d)
        return {"positions": out}

    @classmethod
    def from_dict(cls, d: dict | None) -> "ManualBook":
        book = cls()
        for i, p in enumerate((d or {}).get("positions") or []):
            trades = [ManualTrade(_date(t["date"]), float(t.get("qty") or 0.0), _num(t.get("price")),
                                  float(t.get("costs") or 0.0)) for t in p.get("trades") or []]
            book.positions.append(ManualPosition(
                asset=str(p.get("asset") or "").strip(), name=str(p.get("name") or ""),
                asset_class=p.get("asset_class") or "Fund", broker=str(p.get("broker") or DEFAULT_BROKER),
                trades=trades, price_now=_num(p.get("price_now")), price_now_date=_date(p.get("price_now_date")),
                price_31dec=_num(p.get("price_31dec")), note=str(p.get("note") or ""), id=str(p.get("id") or f"p{i + 1}")))
        return book

    @classmethod
    def load(cls, path: str | Path | None) -> "ManualBook":
        if not path or not Path(path).exists():
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8") or "{}"))

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def asset_code(name: str) -> str:
    """A code for a position without an ISIN, from its name (e.g. 'Tak 23 Pensioen' -> 'TAK-23-PENSIOEN')."""
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:24] or "MANUAL"
