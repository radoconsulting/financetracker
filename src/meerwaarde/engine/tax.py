"""Belgian meerwaardebelasting on 'andere financiële activa' (law of April 2026, in force from 1 January 2026).

Rules applied:
- 10% on the net gain realized in the tax year, above a yearly exemption per person (EUR 10,000 for 2026).
- Units held before 2026 use the closing price of 31-12-2025 as purchase value. If the original purchase price
  was higher, that price applies instead for sales until 31-12-2030, but only to reduce a gain; a loss is always
  measured from the 31-12-2025 price.
- Units bought from 2026 use their purchase price. First in, first out. Costs (commission, stock exchange tax)
  are not part of the calculation.
- Financial instruments, insurance contracts, crypto-assets, currency and investment gold form one regime:
  losses offset gains within the same year. Losses do not carry over.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from ..models import LAW_TYPES, law_type


@dataclass
class TaxRules:
    year: int = 2026
    rate: float = 0.10
    exemption: float = 10000.0
    reference_date: date = date(2025, 12, 31)
    use_original_if_higher: bool = True
    original_price_until: date = date(2030, 12, 31)
    zero_cost_when_unknown: bool = True   # units received in the tax year without a purchase price (e.g. staking)


@dataclass
class TaxLine:
    broker: str
    asset: str
    name: str
    law_type: str
    status: str                 # 'Sold' or 'Open'
    date: date | None
    qty: float
    price: float | None         # sale price or current price per unit (EUR)
    reference_price: float | None
    purchase_price: float | None  # average original purchase price of pre-2026 units (EUR, excl. costs)
    proceeds: float
    acquisition: float
    taxable_gain: float
    missing: str = ""           # what is missing to compute this line

    @property
    def key(self):
        return (self.broker, self.asset)


def unit_acquisition(lot, sale_price: float, ref_price: float | None, sale_date: date, rules: TaxRules) -> float | None:
    """Purchase value per unit for the tax, for one lot sold at sale_price."""
    held_before = lot.date.date() <= rules.reference_date
    if held_before:
        if ref_price is None:
            return None
        if sale_price >= ref_price:
            base = ref_price
            if rules.use_original_if_higher and sale_date <= rules.original_price_until and lot.gross_eur_unit:
                base = max(base, lot.gross_eur_unit)
            return min(base, sale_price)
        return ref_price
    if lot.gross_eur_unit is not None:
        return lot.gross_eur_unit
    return 0.0 if rules.zero_cost_when_unknown else None


def gain_for(lots, qty: float, sale_price: float, ref_price, sale_date: date, rules: TaxRules):
    """Taxable gain when selling `qty` units (first in, first out) at sale_price. Returns (gain, acquisition, missing)."""
    left, acq, missing = qty, 0.0, False
    for lot in lots:
        if left <= 1e-12:
            break
        t = min(lot.qty, left)
        left -= t
        u = unit_acquisition(lot, sale_price, ref_price, sale_date, rules)
        if u is None:
            missing = True
            u = 0.0
        acq += t * u
    sold = qty - max(left, 0.0)
    return sold * sale_price - acq, acq, missing


def max_without_tax(lots, sale_price: float, ref_price, sale_date: date, rules: TaxRules, room: float,
                    step: float = 1.0) -> float:
    """Largest quantity (first in, first out) whose taxable gain stays at or below `room`."""
    q0 = g0 = best = 0.0
    for lot in lots:
        u = unit_acquisition(lot, sale_price, ref_price, sale_date, rules)
        slope = sale_price - (u or 0.0)
        q1, g1 = q0 + lot.qty, g0 + slope * lot.qty
        if slope <= 0:
            if g1 <= room + 1e-9:
                best = q1
        elif g0 <= room + 1e-9:
            best = max(best, min(q1, q0 + (room - g0) / slope))
        q0, g0 = q1, g1
    return int(best / step + 1e-6) * step


class TaxCalculator:
    def __init__(self, portfolio, pricebook, rules: TaxRules | None = None, today: date | None = None):
        self.portfolio = portfolio
        self.prices = pricebook
        self.rules = rules or TaxRules()
        self.today = today or pricebook.today

    # ---------- sales already made in the tax year ----------
    def sold_lines(self, year: int | None = None) -> list[TaxLine]:
        """Sales in the tax year (or the given year; only from 2026 on are sales taxed)."""
        out = []
        r = self.rules
        year = r.year if year is None else year
        hmap = {(h.broker, h.asset): h for h in self.portfolio.holdings()}
        for row in self.portfolio.realized:
            if row.date.year != year or year <= r.reference_date.year:
                continue
            lt = law_type(row.asset_class)
            if not row.lots:                      # broker-reported currency results and fees
                if row.note == "Network fee":
                    continue
                out.append(TaxLine(row.broker, row.asset, row.name, lt, "Sold", row.date.date(), row.qty, None, None, None,
                                   row.proceeds_eur, row.cost_eur, row.pnl_eur))
                continue
            gross = row.gross_proceeds_eur if row.gross_proceeds_eur is not None else row.proceeds_eur
            sale_price = gross / row.qty if row.qty else 0.0
            pre = [l for l in row.lots if l.date.date() <= r.reference_date]
            proxy = hmap.get((row.broker, row.asset)) or _Proxy(row)
            ref = self.prices.reference(proxy).value if pre and self.prices.reference(proxy) else None
            gain, acq, missing = gain_for(row.lots, row.qty, sale_price, ref, row.date.date(), r)
            out.append(TaxLine(row.broker, row.asset, row.name, lt, "Sold", row.date.date(), row.qty, sale_price, ref,
                               _avg_original(pre), gross, acq, gain, "31-12-2025 price" if missing else ""))
        return out

    # ---------- open positions ----------
    def open_lines(self, sell: dict | None = None, price_override: dict | None = None) -> list[TaxLine]:
        """Taxable gain per open position. `sell` maps (broker, asset) -> qty; None means sell everything."""
        out = []
        r = self.rules
        for h in self.portfolio.holdings():
            k = (h.broker, h.asset)
            p_now = (price_override or {}).get(k)
            if p_now is None:
                pr = self.prices.now(h)
                p_now = pr.value if pr else None
            qty = h.qty if sell is None else min(float(sell.get(k, 0.0) or 0.0), h.qty)
            pre = [l for l in h.lots if l.date.date() <= r.reference_date]
            refp = self.prices.reference(h) if pre else None
            ref = refp.value if refp else None
            if p_now is None:
                out.append(TaxLine(h.broker, h.asset, h.name, law_type(h.asset_class), "Open", None, qty, None, ref,
                                   _avg_original(pre), 0.0, 0.0, 0.0, "current price"))
                continue
            gain, acq, missing = gain_for(h.lots, qty, p_now, ref, self.today, r)
            out.append(TaxLine(h.broker, h.asset, h.name, law_type(h.asset_class), "Open", None, qty, p_now, ref,
                               _avg_original(pre), qty * p_now, acq, gain, "31-12-2025 price" if missing else ""))
        return out

    def room_for(self, key, sold: list[TaxLine], simulated: list[TaxLine], exemption: float | None = None) -> float:
        ex = self.rules.exemption if exemption is None else exemption
        other = sum(l.taxable_gain for l in sold) + sum(l.taxable_gain for l in simulated if l.key != key)
        return max(0.0, ex - other)

    def max_without_tax(self, holding, room: float, price: float | None = None) -> float:
        pr = price if price is not None else (self.prices.now(holding).value if self.prices.now(holding) else None)
        if pr is None:
            return 0.0
        pre = [l for l in holding.lots if l.date.date() <= self.rules.reference_date]
        refp = self.prices.reference(holding) if pre else None
        step = 1e-8 if holding.asset_class == "Crypto" else (0.01 if holding.asset_class == "Cash/FX" else 1.0)
        return max_without_tax(holding.lots, pr, refp.value if refp else None, self.today, self.rules, room, step)

    @staticmethod
    def summarize(lines: list[TaxLine], rules: TaxRules, extra: dict | None = None) -> dict:
        """Per law type: sold, simulated/open, net; then the tax for all of it together."""
        per = {k: {"sold": 0.0, "open": 0.0} for k in LAW_TYPES}
        for l in lines:
            per[l.law_type]["sold" if l.status == "Sold" else "open"] += l.taxable_gain
        for k, v in (extra or {}).items():
            per[k]["sold"] += v
        net = sum(v["sold"] + v["open"] for v in per.values())
        taxable = max(0.0, net - rules.exemption)
        return {"per_type": per, "net": net, "exemption": rules.exemption, "taxable": taxable,
                "tax": rules.rate * taxable, "room": rules.exemption - net}


class _Proxy:
    """Minimal holding-like object for a position that has been sold completely."""
    def __init__(self, row):
        self.broker, self.asset, self.name, self.asset_class = row.broker, row.asset, row.name, row.asset_class
        self.lots = row.lots


def _avg_original(lots):
    known = [l for l in lots if l.gross_eur_unit]
    q = sum(l.qty for l in known)
    return (sum(l.qty * l.gross_eur_unit for l in known) / q) if q else None
