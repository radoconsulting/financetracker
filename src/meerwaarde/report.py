"""Tables (pandas) for the CLI and the app."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .engine.household import ExemptionYear, exemption_schedule, summarize_people
from .engine.income_tax import CATEGORIES, IncomeTax, IncomeTaxRules, income_tax
from .engine.portfolio import Portfolio, PortfolioOptions
from .engine.tax import TaxCalculator, TaxRules
from .models import BUY, LAW_TYPES, SELL, law_type
from .parsers import combine, parse_file
from .prices import PriceBook
from .profile import TaxProfile


RULES_TEXT = [
    "10% on the net gain realized in the tax year above the exemption, per person. Partners each have their own "
    "exemption for assets they own; jointly owned assets are split by ownership share.",
    "Exemption €10,000 per person (2026, indexed later). Unused exemption adds at most €1,000 a year to the following "
    "years, for 5 years, so it can reach €15,000.",
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

INCOME_RULES_TEXT = [
    "Dividends and interest received through a foreign broker are declared in the tax return and taxed at 30%: the "
    "broker doesn't withhold Belgian tax.",
    "The 30% applies to the amount received after foreign withholding tax; the foreign tax is not refunded or credited.",
    "Dividends from shares are exempt up to €833 per person (income 2025 and 2026). Distributions from funds and ETFs "
    "don't qualify. Interest from a broker's cash account has no exemption.",
    "Dividends of Belgian companies had the Belgian 30% withheld at source, also at a foreign broker: that counts as "
    "paid. Declaring them is only useful to use the dividend exemption, which then gives a refund.",
    "Securities-lending income (DEGIRO) is counted at 30% as other movable income; check the treatment with your adviser.",
]

# Stock exchange tax rates for an estimate when the broker's export doesn't list it (docs/domain/belgian-taxes.md).
TOB_ESTIMATE = {"Stock": (0.0035, 1600.0), "Turbo": (0.0035, 1600.0), "ETF": (0.0012, 1300.0),
                "Bond ETF": (0.0012, 1300.0), "Fund": (0.0012, 1300.0), "Bond": (0.0012, 1300.0)}


@dataclass
class Simulation:
    """What-if sales on top of this year's real sales (the app's Simulator tab and the Excel export)."""
    sell: dict                 # (broker, asset) -> quantity to sell
    price_override: dict       # (broker, asset) -> price per unit (EUR) to sell at
    extra: dict                # law type -> taxable gain of sales not in the exports (first person)
    lines: list                # TaxLine per simulated sale (qty > 0)
    summary: dict              # summarize_people() of real + simulated + extra sales
    positions: pd.DataFrame    # per position: what is sold and the largest quantity that stays tax-free


@dataclass
class Report:
    portfolio: Portfolio
    prices: PriceBook
    tax: TaxCalculator
    warnings: list = field(default_factory=list)
    profile: TaxProfile = field(default_factory=TaxProfile)

    def __post_init__(self):
        self._sold: dict[int, list] = {}

    @property
    def household(self):
        return self.profile.household

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

    # ---------- meerwaardebelasting ----------
    def sold_lines(self, year: int | None = None) -> list:
        y = self.tax.rules.year if year is None else year
        if y not in self._sold:
            self._sold[y] = self.tax.sold_lines(y)
        return self._sold[y]

    def tax_lines(self, sell: dict | None = None, price_override: dict | None = None) -> pd.DataFrame:
        lines = self.sold_lines() + self.tax.open_lines(sell=sell, price_override=price_override)
        rows = [dict(broker=l.broker, asset=l.asset, name=l.name, type=LAW_TYPES[l.law_type][0], law_type=l.law_type, status=l.status,
                     date=l.date, qty=l.qty, price_eur=l.price, price_31dec_eur=l.reference_price,
                     avg_purchase_eur=l.purchase_price, proceeds_eur=round(l.proceeds, 2), acquisition_eur=round(l.acquisition, 2),
                     taxable_gain_eur=round(l.taxable_gain, 2), missing=l.missing) for l in lines]
        return pd.DataFrame(rows)

    def exemption_rules(self):
        return self.profile.exemption_rules({self.tax.rules.year: self.tax.rules.exemption})

    def net_by_year(self, pid: str, until: int | None = None) -> dict[int, float]:
        """Net taxable gain per year for one person: their share of the year's sales plus other gains from the profile,
        or the figure they declared when the profile has one."""
        until = self.tax.rules.year if until is None else until
        er = self.exemption_rules()
        out = {}
        for y in range(er.first_year, until + 1):
            yi = self.profile.get(y)
            declared = yi.declared_for(pid)
            if declared is not None:
                out[y] = declared
                continue
            own = sum(l.taxable_gain * self.household.share(l.broker, l.asset).get(pid, 0.0) for l in self.sold_lines(y))
            out[y] = own + sum(yi.gains_for(pid).values())
        return out

    def exemption_history(self, pid: str, until: int | None = None) -> list[ExemptionYear]:
        until = self.tax.rules.year if until is None else until
        return exemption_schedule(self.net_by_year(pid, until), self.exemption_rules(), until)

    def exemptions(self, year: int | None = None) -> dict[str, float]:
        """Exemption available per person in the year: the year's own amount plus what earlier years carried forward."""
        year = self.tax.rules.year if year is None else year
        er = self.exemption_rules()
        out = {}
        for pid in self.household.ids:
            hist = self.exemption_history(pid, year) if year >= er.first_year else []
            out[pid] = hist[-1].available if hist else er.base_for(year)
        return out

    def other_gains(self, year: int | None = None) -> dict[str, dict[str, float]]:
        yi = self.profile.get(self.tax.rules.year if year is None else year)
        return {pid: yi.gains_for(pid) for pid in self.household.ids}

    def tax_summary(self, lines: list, extra: dict | None = None, year: int | None = None) -> dict:
        """Meerwaardebelasting per person and in total for these lines, this year's other gains from the profile, and
        `extra` (law type -> gain for the first person, or person -> law type -> gain)."""
        merged = {pid: dict(v) for pid, v in self.other_gains(year).items()}
        if extra:
            per_person = extra if set(extra) <= set(self.household.ids) else {self.household.ids[0]: extra}
            for pid, d in per_person.items():
                for k, v in (d or {}).items():
                    merged.setdefault(pid, {})[k] = merged.get(pid, {}).get(k, 0.0) + float(v or 0.0)
        return summarize_people(lines, self.tax.rules, self.household, self.exemptions(year), merged)

    def simulate(self, sell: dict | None = None, price_override: dict | None = None,
                 extra: dict | None = None) -> Simulation:
        """Sell the given quantities on top of this year's sales and other (extra) sales.

        For every position, `max_qty` is the largest quantity of that position that can be sold on top of all
        other sales (real, extra and the other simulated ones) without going over the exemption of any owner.
        """
        sell, price_override, extra = dict(sell or {}), dict(price_override or {}), dict(extra or {})
        hh = self.household
        sold = self.sold_lines()
        lines = [l for l in self.tax.open_lines(sell=sell, price_override=price_override) if l.qty > 0]
        summary = self.tax_summary(sold + lines, extra)
        base = self.tax_summary(sold, extra)["people"]          # real + other + extra sales, no simulated ones
        rows = []
        for h in self.portfolio.holdings():
            k = (h.broker, h.asset)
            line = next((l for l in lines if l.key == k), None)
            shares = hh.share(h.broker, h.asset)
            rooms = []
            for pid, s in shares.items():
                others = sum(l.taxable_gain * hh.share(l.broker, l.asset).get(pid, 0.0) for l in lines if l.key != k)
                rooms.append(max(0.0, base[pid]["exemption"] - base[pid]["net"] - others) / s)
            room = min(rooms) if rooms else 0.0
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

    def exemption_table(self, until: int | None = None) -> pd.DataFrame:
        rows = []
        for pid in self.household.ids:
            for e in self.exemption_history(pid, until):
                rows.append(dict(person=self.household.name(pid), year=e.year, base=e.base, carried_in=e.carried,
                                 available=e.available, net_gain=round(e.net_gain, 2), used=round(e.used, 2),
                                 carry_out=e.carry_out, expired=e.expired,
                                 carried_from=", ".join(f"{y}: €{a:,.0f}" for y, a in e.carried_in)))
        return pd.DataFrame(rows, columns=["person", "year", "base", "carried_in", "available", "net_gain", "used",
                                           "carry_out", "expired", "carried_from"])

    # ---------- dividends and interest ----------
    def income_tax(self, year: int | None = None) -> dict[str, IncomeTax]:
        year = self.tax.rules.year if year is None else year
        yi = self.profile.get(year)
        other = {pid: yi.income_for(pid) for pid in self.household.ids}
        return income_tax(self.portfolio.income, self.household, year, IncomeTaxRules(), other, yi.dividend_exemption)

    def income_years(self) -> list[int]:
        years = {i.date.year for i in self.portfolio.income} | {y for y, yi in self.profile.years.items()
                                                                 if any(any(d.values()) for d in yi.other_income.values())}
        return sorted(years)

    def income_tax_table(self, years: list[int] | None = None) -> pd.DataFrame:
        rows = []
        for y in years or self.income_years():
            for pid, it in self.income_tax(y).items():
                for l in it.lines:
                    rows.append(dict(year=y, person=self.household.name(pid), broker=l.broker, category=CATEGORIES[l.category],
                                     received=l.count, gross_eur=l.gross, foreign_tax_eur=l.foreign_tax, net_eur=round(l.net, 2),
                                     be_withheld_eur=l.be_withheld))
        return pd.DataFrame(rows, columns=["year", "person", "broker", "category", "received", "gross_eur", "foreign_tax_eur",
                                           "net_eur", "be_withheld_eur"])

    def income_tax_summary(self, years: list[int] | None = None) -> pd.DataFrame:
        rows = []
        for y in years or self.income_years():
            for pid, it in self.income_tax(y).items():
                rows.append(dict(year=y, person=self.household.name(pid), shares_net=round(it.total("shares"), 2),
                                 funds_net=round(it.total("funds"), 2),
                                 interest_net=round(it.total("interest") + it.total("other"), 2),
                                 foreign_tax=round(it.foreign_tax, 2), exemption_used=round(it.exemption_used, 2),
                                 taxable=round(it.taxable, 2), tax=round(it.tax, 2), be_withheld=round(it.be_withheld, 2),
                                 to_pay=round(it.to_pay, 2)))
        return pd.DataFrame(rows, columns=["year", "person", "shares_net", "funds_net", "interest_net", "foreign_tax",
                                           "exemption_used", "taxable", "tax", "be_withheld", "to_pay"])

    # ---------- stock exchange tax ----------
    def tob_detail(self, year: int | None = None) -> pd.DataFrame:
        rows = [dict(broker=t.broker, date=t.date.date(), asset=t.asset, name=t.name, qty=t.qty, price=t.price,
                     amount_eur=t.amount_eur) for t in self.portfolio.tob if year is None or t.date.year == year]
        return pd.DataFrame(rows, columns=["broker", "date", "asset", "name", "qty", "price", "amount_eur"])

    def tob_summary(self, year: int) -> pd.DataFrame:
        """Per broker: stock exchange tax the export lists, or an estimate at the standard rates when it lists none."""
        rows = []
        for b in self.portfolio.brokers():
            trades = [t for t in self.portfolio.transactions if t.broker == b and t.kind in (BUY, SELL)
                      and t.ts.year == year and t.asset_class in TOB_ESTIMATE and t.gross_eur]
            reported = [t for t in self.portfolio.tob if t.broker == b and t.date.year == year]
            if reported:
                rows.append(dict(broker=b, trades=len(reported), amount_eur=round(-sum(t.amount_eur for t in reported), 2),
                                 basis="listed by the broker"))
            elif trades:
                est = sum(min(abs(t.gross_eur) * TOB_ESTIMATE[t.asset_class][0], TOB_ESTIMATE[t.asset_class][1]) for t in trades)
                rows.append(dict(broker=b, trades=len(trades), amount_eur=round(est, 2),
                                 basis="estimate: not in this export (0.12% funds/ETFs/bonds, 0.35% shares)"))
            else:
                rows.append(dict(broker=b, trades=0, amount_eur=0.0, basis="no trades subject to it"))
        return pd.DataFrame(rows, columns=["broker", "trades", "amount_eur", "basis"])


def build_report(files, fetch_prices: bool = True, cache_dir=None, options: PortfolioOptions | None = None,
                 rules: TaxRules | None = None, overrides_csv: str | None = None, today: date | None = None,
                 parsed: list | None = None, profile: TaxProfile | None = None) -> Report:
    """`rules.exemption` is the base exemption of `rules.year`; pass rules=None to take it from the profile."""
    results = parsed if parsed is not None else [parse_file(f) for f in files]
    txns, statements, warnings = combine(results)
    pf = Portfolio(txns, statements, options)
    profile = profile or TaxProfile()
    if rules is None:
        rules = TaxRules()
        rules.exemption = profile.exemption_rules().base_for(rules.year)
    pb = PriceBook(statements, fetch=fetch_prices, cache_dir=cache_dir, reference_date=rules.reference_date, today=today)
    if overrides_csv:
        pb.load_overrides_csv(overrides_csv)
    calc = TaxCalculator(pf, pb, rules, today=today)
    return Report(pf, pb, calc, warnings + pf.warnings, profile)
