"""Tables (pandas) for the CLI and the app."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .engine.portfolio import Portfolio, PortfolioOptions
from .engine.tax import TaxCalculator, TaxRules
from .models import LAW_TYPES, law_type
from .parsers import combine, parse_file
from .prices import PriceBook


RULES_TEXT = [
    "10% on the net gain realized in the tax year above the exemption, per person. Partners each have their own "
    "exemption for assets they own.",
    "Units held before 2026 use the closing price of 31-12-2025 as purchase value. If you originally paid more, that "
    "price applies instead for sales until the end of 2030, but only to reduce a gain: a loss is always measured from "
    "the 31-12-2025 price.",
    "Units bought from 2026 use their purchase price. First in, first out per broker. Commissions and stock exchange "
    "tax are left out.",
    "Financial instruments, insurance contracts, crypto-assets, currency and investment gold form one regime. Losses "
    "offset gains within the year and don't carry over.",
    "Not included: the 33% regime for internal capital gains, holdings of 20% or more in a company, Reynders tax on "
    "bond funds, and income tax on staking rewards. Units received in the tax year without a purchase price count at "
    "zero cost.",
    "This is a calculation from your exports, not tax advice.",
]


@dataclass
class Simulation:
    """What-if sales on top of this year's real sales (the app's Simulator tab and the Excel export)."""
    sell: dict                 # (broker, asset) -> quantity to sell
    price_override: dict       # (broker, asset) -> price per unit (EUR) to sell at
    extra: dict                # law type -> taxable gain of sales not in the exports
    lines: list                # TaxLine per simulated sale (qty > 0)
    summary: dict              # TaxCalculator.summarize() of real + simulated + extra sales
    positions: pd.DataFrame    # per position: what is sold and the largest quantity that stays tax-free


@dataclass
class Report:
    portfolio: Portfolio
    prices: PriceBook
    tax: TaxCalculator
    warnings: list = field(default_factory=list)

    # ---------- P&L ----------
    def realized(self) -> pd.DataFrame:
        rows = [dict(broker=r.broker, date=r.date.date(), asset=r.asset, name=r.name, asset_class=r.asset_class,
                     qty=r.qty, proceeds_eur=round(r.proceeds_eur, 2), cost_eur=round(r.cost_eur, 2),
                     pnl_eur=round(r.pnl_eur, 2), note=r.note) for r in self.portfolio.realized]
        return pd.DataFrame(rows, columns=["broker", "date", "asset", "name", "asset_class", "qty", "proceeds_eur", "cost_eur", "pnl_eur", "note"])

    def holdings(self) -> pd.DataFrame:
        rows = []
        for h in self.portfolio.holdings():
            p = self.prices.now(h)
            value = h.qty * p.value if p else None
            rows.append(dict(broker=h.broker, asset=h.asset, name=h.name, asset_class=h.asset_class, qty=h.qty,
                             cost_eur=round(h.cost_eur, 2), price_eur=p.value if p else None,
                             price_date=p.on if p else None, price_source=p.source if p else "missing",
                             value_eur=round(value, 2) if value is not None else None,
                             unrealized_eur=round(value - h.cost_eur, 2) if value is not None else None, note=h.note))
        df = pd.DataFrame(rows, columns=["broker", "asset", "name", "asset_class", "qty", "cost_eur", "price_eur", "price_date",
                                         "price_source", "value_eur", "unrealized_eur", "note"])
        return df.sort_values("value_eur", ascending=False, na_position="last").reset_index(drop=True)

    def income(self) -> pd.DataFrame:
        rows = [dict(broker=i.broker, date=i.date.date(), asset=i.asset, name=i.name, kind=i.kind, amount_eur=round(i.amount_eur, 2),
                     amount_local=i.amount_local, currency=i.currency) for i in self.portfolio.income]
        return pd.DataFrame(rows, columns=["broker", "date", "asset", "name", "kind", "amount_eur", "amount_local", "currency"])

    def cash(self) -> pd.DataFrame:
        rows = [dict(broker=c.broker, date=c.date.date(), amount_eur=c.amount_eur, note=c.note) for c in self.portfolio.cash]
        return pd.DataFrame(rows, columns=["broker", "date", "amount_eur", "note"])

    def summary(self) -> pd.DataFrame:
        r, h, i = self.realized(), self.holdings(), self.income()
        out = []
        for b in ["All"] + self.portfolio.brokers():
            f = (lambda d: d) if b == "All" else (lambda d, b=b: d[d.broker == b])
            rz = f(r).pnl_eur.sum() if len(r) else 0.0
            un = f(h).unrealized_eur.sum(min_count=1) if len(h) else 0.0
            inc = f(i).amount_eur.sum() if len(i) else 0.0
            out.append(dict(broker=b, realized=round(rz, 2), unrealized=round(un or 0.0, 2), income=round(inc, 2),
                            total=round(rz + (un or 0.0) + inc, 2), value=round(f(h).value_eur.sum(), 2) if len(h) else 0.0,
                            cost_open=round(f(h).cost_eur.sum(), 2) if len(h) else 0.0,
                            missing_prices=int(f(h).price_eur.isna().sum()) if len(h) else 0))
        return pd.DataFrame(out)

    def by_year(self) -> pd.DataFrame:
        r = self.realized()
        if r.empty:
            return pd.DataFrame(columns=["broker", "year", "pnl_eur"])
        r["year"] = pd.to_datetime(r["date"]).dt.year
        return r.groupby(["broker", "year"], as_index=False).pnl_eur.sum().round(2)

    # ---------- tax ----------
    def tax_lines(self, sell: dict | None = None, price_override: dict | None = None) -> pd.DataFrame:
        lines = self.tax.sold_lines() + self.tax.open_lines(sell=sell, price_override=price_override)
        rows = [dict(broker=l.broker, asset=l.asset, name=l.name, type=LAW_TYPES[l.law_type][0], law_type=l.law_type, status=l.status,
                     date=l.date, qty=l.qty, price_eur=l.price, price_31dec_eur=l.reference_price,
                     avg_purchase_eur=l.purchase_price, proceeds_eur=round(l.proceeds, 2), acquisition_eur=round(l.acquisition, 2),
                     taxable_gain_eur=round(l.taxable_gain, 2), missing=l.missing) for l in lines]
        return pd.DataFrame(rows)

    def simulate(self, sell: dict | None = None, price_override: dict | None = None,
                 extra: dict | None = None) -> Simulation:
        """Sell the given quantities on top of this year's sales and other (extra) sales.

        For every position, `max_qty` is the largest quantity of that position that can be sold on top of all
        other sales (real, extra and the other simulated ones) without going over the exemption.
        """
        sell, price_override, extra = dict(sell or {}), dict(price_override or {}), dict(extra or {})
        rules = self.tax.rules
        sold = self.tax.sold_lines()
        lines = [l for l in self.tax.open_lines(sell=sell, price_override=price_override) if l.qty > 0]
        summary = TaxCalculator.summarize(sold + lines, rules, extra)
        sold_net = sum(l.taxable_gain for l in sold) + sum(extra.values())
        rows = []
        for h in self.portfolio.holdings():
            k = (h.broker, h.asset)
            line = next((l for l in lines if l.key == k), None)
            others = sum(l.taxable_gain for l in lines if l.key != k)
            room = max(0.0, rules.exemption - sold_net - others)
            now = self.prices.now(h)
            price = price_override.get(k) or (now.value if now else None)
            m = self.tax.max_without_tax(h, room, price) if price else 0.0
            rows.append(dict(key=f"{h.broker}|{h.asset}", broker=h.broker, asset=h.asset, name=h.name,
                             type=LAW_TYPES[law_type(h.asset_class)][0], held=h.qty, price=price,
                             sell=min(float(sell.get(k, 0.0) or 0.0), h.qty),
                             gain=line.taxable_gain if line else 0.0, proceeds=line.proceeds if line else 0.0,
                             max_qty=m, max_value=m * price if price else None))
        cols = ["key", "broker", "asset", "name", "type", "held", "price", "sell", "gain", "proceeds", "max_qty", "max_value"]
        positions = pd.DataFrame(rows, columns=cols).sort_values("max_value", ascending=False, na_position="last")
        return Simulation(sell, price_override, extra, lines, summary, positions.reset_index(drop=True))


def build_report(files, fetch_prices: bool = True, cache_dir=None, options: PortfolioOptions | None = None,
                 rules: TaxRules | None = None, overrides_csv: str | None = None, today: date | None = None,
                 parsed: list | None = None) -> Report:
    results = parsed if parsed is not None else [parse_file(f) for f in files]
    txns, statements, warnings = combine(results)
    pf = Portfolio(txns, statements, options)
    rules = rules or TaxRules()
    pb = PriceBook(statements, fetch=fetch_prices, cache_dir=cache_dir, reference_date=rules.reference_date, today=today)
    if overrides_csv:
        pb.load_overrides_csv(overrides_csv)
    calc = TaxCalculator(pf, pb, rules, today=today)
    return Report(pf, pb, calc, warnings + pf.warnings)
