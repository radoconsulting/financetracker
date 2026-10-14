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
