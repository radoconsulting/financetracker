"""Switch planner (backlog item 7): sell positions and buy a target fund.

For each candidate target: the one-time cost now (meerwaardebelasting on the gain above this year's room, Reynders tax
on bond funds, stock exchange tax on the sale and the purchase, commissions, spread), the yearly effect (dividend tax no longer paid
when moving from a distributing to an accumulating fund, minus the 10% that may come back as meerwaardebelasting on
the reinvested dividends; the difference in running costs), the extra stock exchange tax when the target is sold
one day, break-even and the net effect over a horizon. Plus a plan that spreads the sales over the years so each
year stays within the exemption, and what that costs in benefits you wait for. Choices: docs/decisions.md D22.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..funds import Fund, lookup
from ..models import BUY, DIVIDEND, SELL, WITHHOLDING
from .harvest import CostModel, _cap, observed_tob_rate, tob_for
from .tax import gain_for, max_without_tax

DIVIDEND_TAX = 0.30


@dataclass
class Source:
    broker: str
    asset: str
    name: str
    qty: float
    price: float
    ref: float | None
    value: float
    ter: float | None
    tob_rate: float
    tob_source: str
    dividends_year: float          # net dividends over the last 12 months, scaled to the current quantity
    shares: dict
    lots: list

    @property
    def key(self):
        return (self.broker, self.asset)


@dataclass
class SwitchCase:
    target: Fund
    target_tob: float
    target_tob_source: str
    value: float
    cgt_now: float
    tob_sell: float
    tob_buy: float
    commission: float
    spread: float
    dividend_tax_saved: float      # per year
    dividend_tax_back: float       # per year: meerwaardebelasting that may come back on the reinvested dividends
    ter_saved: float               # per year (negative = the target costs more)
    exit_tob_extra: float          # once, when the target is sold one day (vs selling the source then)
    horizon: int
    notes: list = field(default_factory=list)
    reynders_now: float = 0.0      # Reynders tax on selling bond-fund sources (30%, no exemption)

    @property
    def costs(self) -> float:
        return self.tob_sell + self.tob_buy + self.commission + self.spread

    @property
    def now(self) -> float:
        return self.cgt_now + self.reynders_now + self.costs

    @property
    def yearly(self) -> float:
        return self.dividend_tax_saved - self.dividend_tax_back + self.ter_saved

    @property
    def break_even(self) -> float | None:
        return self.now / self.yearly if self.yearly > 0 else None

    @property
    def net(self) -> float:
        return self.yearly * self.horizon - self.now - self.exit_tob_extra


@dataclass
class YearStep:
    year: int
    units: dict                    # (broker, asset) -> quantity
    value: float
    gain: float
    costs: float


@dataclass
class SpreadPlan:
    steps: list[YearStep]
    done: bool
    benefit_waited: float          # yearly benefit not received while units wait for a later year
    costs: float

    @property
    def years(self) -> int:
        return len([s for s in self.steps if s.units])


def _qty_at(portfolio, broker, asset, when: datetime) -> float:
    return sum(t.qty for t in portfolio.transactions
               if t.broker == broker and t.asset == asset and t.kind in (BUY, SELL) and t.ts <= when)


def dividends_per_unit(portfolio, broker, asset, today: date) -> float:
    """Net dividends per unit over the last 12 months (each payment divided by the units held then)."""
    start = datetime.combine(today - timedelta(days=365), datetime.min.time())
    total = 0.0
    for r in portfolio.income:
        if r.broker == broker and r.asset == asset and r.kind in (DIVIDEND, WITHHOLDING) and r.date > start:
            q = _qty_at(portfolio, broker, asset, r.date)
            if q > 0:
                total += r.amount_eur / q
    return total


def sources_for(rep, keys, costs: CostModel | None = None) -> list[Source]:
    costs = costs or CostModel()
    rules = rep.tax.rules
    out = []
    for h in rep.portfolio.holdings():
        if (h.broker, h.asset) not in keys and h.asset not in keys:
            continue
        now = rep.prices.now(h)
        if not now:
            continue
        pre = [l for l in h.lots if l.date.date() <= rules.reference_date]
        refp = rep.prices.reference(h) if pre else None
        rate, src = tob_for(rep.portfolio, h, costs)
        fund = lookup(h.asset)
        div = dividends_per_unit(rep.portfolio, h.broker, h.asset, rep.tax.today) * h.qty
        out.append(Source(h.broker, h.asset, h.name, h.qty, now.value, refp.value if refp else None, h.qty * now.value,
                          fund.ter if fund else None, rate, src, div, rep.household.share(h.broker, h.asset),
                          [copy.copy(l) for l in h.lots]))
    return out


def target_tob(rep, fund: Fund) -> tuple[float, str]:
    for b in rep.portfolio.brokers():
        seen = observed_tob_rate(rep.portfolio, b, fund.isin)
        if seen is not None:
            return seen, f"charged by {b} on your earlier trades"
    if fund.tob_rate is not None:
        return fund.tob_rate, fund.tob_note or fund.source or "entered"
    return 0.0132, "unknown: assumed 1.32% (worst case for an accumulating fund)"


def compare(rep, sources: list[Source], targets: list[Fund], horizon: int = 10,
            costs: CostModel | None = None, tax_reinvested: bool = True) -> list[SwitchCase]:
    """`tax_reinvested`: count 10% meerwaardebelasting later on the dividends an accumulating fund reinvests
    (conservative: assumes your gains then exceed the exemption)."""
    costs = costs or CostModel()
    sell = {s.key: s.qty for s in sources}
    sim = rep.simulate(sell=sell)
    cgt_now = sim.summary["tax"] - rep.simulate().summary["tax"]
    reynders_now = sim.reynders.get("tax", 0.0)
    value = sum(s.value for s in sources)
    by_broker: dict[str, float] = {}
    for s in sources:
        by_broker[s.broker] = by_broker.get(s.broker, 0.0) + s.value
    cases = []
    for f in targets:
        rate, src = target_tob(rep, f)
        notes = []
        tob_sell = sum(_cap(s.value, s.tob_rate) for s in sources)
        tob_buy = sum(_cap(v, rate) for v in by_broker.values())
        commission = (sum(costs.order_cost(s.broker, s.value) for s in sources)
                      + sum(costs.order_cost(b, v) for b, v in by_broker.items()))
        spread = 2 * costs.spread * value
        div = sum(s.dividends_year for s in sources)
        saved = DIVIDEND_TAX * div if f.accumulating else 0.0
        back = rep.tax.rules.rate * div if (f.accumulating and tax_reinvested) else 0.0
        if f.ter is None:
            ter_saved = 0.0
            notes.append("Running cost of the target unknown: not counted.")
        else:
            ter_saved = 0.0
            for s in sources:
                if s.ter is None:
                    notes.append(f"Running cost of {s.name} unknown: assumed equal to the target's.")
                else:
                    ter_saved += (s.ter - f.ter) * s.value
        exit_extra = sum((rate - s.tob_rate) * s.value for s in sources)
        if not f.verified:
            notes.append(f"{f.ticker or f.isin}: not all facts are verified (stock exchange tax rate, TER): check them "
                         "(KID, justETF, a small test order) before buying.")
        if reynders_now:
            notes.append(f"Includes €{reynders_now:,.0f} Reynders tax on the bond funds sold (30% on the interest part).")
        cases.append(SwitchCase(f, rate, src, value, cgt_now, tob_sell, tob_buy, commission, spread, saved, back,
                                ter_saved, exit_extra, horizon, notes, reynders_now))
    return cases


def spread_plan(rep, sources: list[Source], case: SwitchCase, costs: CostModel | None = None,
                max_years: int = 15) -> SpreadPlan:
    """Sell a part each year, as much as fits in that year's exemption (prices kept at today's level)."""
    costs = costs or CostModel()
    rules, hh = rep.tax.rules, rep.household
    er = rep.exemption_rules()
    people = rep.tax_summary(rep.sold_lines())["people"]
    lots = {s.key: [copy.copy(l) for l in s.lots] for s in sources}
    order = sorted(sources, key=lambda s: costs.order_cost(s.broker, s.value))
    total = sum(s.value for s in sources) or 1.0
    steps, waited, spent = [], 0.0, 0.0
    for i in range(max_years):
        year = rules.year + i
        when = rep.tax.today if i == 0 else date(year, 6, 30)
        room = {p: (people[p]["exemption"] - people[p]["net"]) if i == 0 else er.base_for(year) for p in hh.ids}
        units, value, gain, cost = {}, 0.0, 0.0, 0.0
        by_broker: dict[str, float] = {}
        for s in order:
            pool = lots[s.key]
            if not pool:
                continue
            eff = min((room[p] / sh for p, sh in s.shares.items() if sh > 0), default=0.0)
            deduct = rep.tax.deduct_for(s.asset, s.price, when)
            q = max_without_tax(pool, s.price, s.ref, when, rules, max(0.0, eff), 1.0, deduct)
            if q <= 0:
                continue
            g, _a, _m = gain_for(pool, q, s.price, s.ref, when, rules, deduct)
            if rep.tax.reynders.applies(s.asset):
                cost += rep.tax.reynders.rules.rate * rep.tax.reynders.for_sale(s.asset, pool, q, s.price, when)[0]
            left = q
            while left > 1e-9 and pool:              # take the oldest lots
                t = min(pool[0].qty, left)
                pool[0].qty -= t
                left -= t
                if pool[0].qty <= 1e-9:
                    pool.pop(0)
            for p, sh in s.shares.items():
                room[p] -= g * sh
            v = q * s.price
            units[s.key] = q
            value += v
            gain += g
            by_broker[s.broker] = by_broker.get(s.broker, 0.0) + v
            cost += _cap(v, s.tob_rate) + costs.order_cost(s.broker, v) + 2 * costs.spread * v
        cost += sum(_cap(v, case.target_tob) + costs.order_cost(b, v) for b, v in by_broker.items())
        steps.append(YearStep(year, units, value, gain, cost))
        spent += cost
        remaining = sum(sum(l.qty for l in pool) * s.price for s in sources for pool in [lots[s.key]])
        if remaining <= 1e-6:
            return SpreadPlan(steps, True, waited, spent)
        waited += case.yearly * remaining / total
    return SpreadPlan(steps, False, waited, spent)


def is_distributing(name: str, asset: str) -> bool | None:
    f = lookup(asset)
    if f and f.accumulating is not None:
        return not f.accumulating
    n = (name or "").upper()
    if "DIST" in n or "(D)" in n:
        return True
    if "ACC" in n or "(A)" in n:
        return False
    return None


def default_sources(rep) -> set:
    """Distributing ETFs you hold: the natural candidates to switch."""
    return {(h.broker, h.asset) for h in rep.portfolio.holdings()
            if h.asset_class in ("ETF", "Fund") and is_distributing(h.name, h.asset)}


def source_fund(s: Source) -> Fund:
    """What we know about a position as a fund (for the comparison and for the Gemini question)."""
    f = lookup(s.asset)
    if f:
        return f
    dist = is_distributing(s.name, s.asset)
    return Fund(s.asset, s.name, ter=s.ter, accumulating=None if dist is None else not dist, verified=False)


def net_spread(case: SwitchCase, plan: SpreadPlan) -> float:
    """Net effect over the horizon when the sales are spread over the years (no meerwaardebelasting)."""
    return case.yearly * case.horizon - plan.costs - plan.benefit_waited - case.exit_tob_extra


def funds_to_json(funds: list[Fund]) -> list[dict]:
    return [{k: v for k, v in f.__dict__.items()} for f in funds]


def funds_from_json(rows: list[dict]) -> list[Fund]:
    out = []
    for r in rows or []:
        try:
            out.append(Fund(**{k: r.get(k) for k in Fund.__dataclass_fields__ if k in r}))
        except TypeError:
            continue
    return out
