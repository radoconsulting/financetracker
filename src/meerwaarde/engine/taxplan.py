"""Multi-year tax plan 2026–2030 (backlog item 20, docs/decisions.md D37).

Each year until the original-price window closes (31-12-2030), per person: sell and buy back lots for as much gain as
that year's exemption leaves room for (incl. carry-forward, keeping the €1,000 that carries forward by default), so
the purchase value goes up without tax. Positions are taken cheapest-first per euro of benefit (the costs of the
year-end harvest: stock exchange tax both ways, commissions, spread). The benefit of a lot counts the tax saved later
on the gain harvested and, before 2031, what keeping a higher original price is worth: lots bought above their
31-12-2025 value lose that original price as purchase value after 2030 (D1/unit_acquisition).

Prices grow by `growth` a year from today's price (one rate for everything; cash and bonds don't grow). The plan is
compared with doing nothing: the meerwaardebelasting if everything were sold in `final_year` (default 2031), per
person, after that year's exemption, minus the plan's costs. Same lots and rules as the rest of the app (FIFO per
broker, unit_acquisition); quantities in whole units except crypto.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime

from ..models import Lot
from .harvest import SKIP_CLASSES, CostModel, _cap, tob_for
from .household import exemption_schedule
from .tax import gain_for, max_without_tax, unit_acquisition

LAST_WINDOW_YEAR = 2030


@dataclass
class PlanLine:
    year: int
    broker: str
    asset: str
    name: str
    qty: float
    price: float
    gain: float            # taxable gain realised (fits in the exemption)
    window: float          # of the benefit: original-price value kept (before 2031)
    costs: float


@dataclass
class PlanYear:
    year: int
    room: dict             # person -> exemption used for the plan
    lines: list
    gain: float
    costs: float


@dataclass
class TaxPlan:
    years: list
    tax_without: float     # meerwaardebelasting in final_year if nothing is done
    tax_with: float        # same, after following the plan
    costs: float
    final_year: int
    growth: float
    notes: list = field(default_factory=list)

    @property
    def saving(self) -> float:
        return self.tax_without - self.tax_with - self.costs


def _price(p_now: float, today: date, when: date, growth: float, cls: str) -> float:
    if cls in ("Cash/FX", "Bond", "Bond ETF", "Insurance"):
        return p_now
    return p_now * (1 + growth) ** max(0.0, (when - today).days / 365.25)


def _final_tax(rep, pools, ref, prices_at, final_year: int, net_before: dict[int, dict], er) -> float:
    """Meerwaardebelasting if everything were sold in `final_year`, per person (after that year's exemption)."""
    rules, hh = rep.tax.rules, rep.household
    when = date(final_year, 12, 15)
    gains = {p: 0.0 for p in hh.ids}
    for key, (h, lots) in pools.items():
        q = sum(l.qty for l in lots)
        if q <= 1e-9:
            continue
        price = prices_at(h, when)
        g, _a, _m = gain_for(lots, q, price, ref.get(key), when, rules)
        for p, s in hh.share(h.broker, h.asset).items():
            gains[p] += g * s
    tax = 0.0
    for p in hh.ids:
        nets = dict(net_before.get(p, {}))
        nets[final_year] = gains[p]
        avail = exemption_schedule(nets, er, final_year)[-1].available
        tax += rules.rate * max(0.0, gains[p] - avail)
    return tax


def plan(rep, growth: float = 0.05, keep_carry: bool = True, costs: CostModel | None = None, final_year: int = 2031,
         last_year: int = LAST_WINDOW_YEAR, min_gain: float = 25.0, exclude=()) -> TaxPlan:
    costs = costs or CostModel()
    rules, hh, today = rep.tax.rules, rep.household, rep.tax.today
    er = rep.exemption_rules()
    pools, ref, cls_of, now_price, tob = {}, {}, {}, {}, {}
    for h in rep.portfolio.holdings():
        if h.asset_class in SKIP_CLASSES or h.asset in exclude or rep.tax.reynders.applies(h.asset):
            continue
        p = rep.prices.now(h)
        if not p or not p.value:
            continue
        key = (h.broker, h.asset)
        pools[key] = (h, [copy.copy(l) for l in h.lots])
        r = rep.prices.reference(h) if any(l.date.date() <= rules.reference_date for l in h.lots) else None
        ref[key] = r.value if r else None
        cls_of[key], now_price[key] = h.asset_class, p.value
        tob[key] = tob_for(rep.portfolio, h, costs)[0]

    def prices_at(h, when):
        return _price(now_price[(h.broker, h.asset)], today, when, growth, cls_of[(h.broker, h.asset)])

    # past years and this year's sales so far, per person
    net_before = {p: {y: v for y, v in rep.net_by_year(p, rules.year).items()} for p in hh.ids}
    base_pools = {k: (h, [copy.copy(l) for l in lots]) for k, (h, lots) in pools.items()}
    tax_without = _final_tax(rep, base_pools, ref, prices_at, final_year, net_before, er)

    years, total_costs = [], 0.0
    nets = {p: dict(v) for p, v in net_before.items()}
    for y in range(max(today.year, rules.year), last_year + 1):
        when = date(y, 12, 15) if y > today.year else today
        room = {}
        for p in hh.ids:
            sched = exemption_schedule(nets[p], er, y)
            avail = sched[-1].available if sched else er.base_for(y)
            used = max(0.0, nets[p].get(y, 0.0))
            left = max(0.0, avail - used)
            reserve = min(er.carry_per_year, left) if keep_carry else 0.0
            if keep_carry and er.carry_rule == "none_used" and used <= 0:
                reserve = left                         # any gain would lose the carry-forward
            room[p] = max(0.0, left - reserve)
        lines, left = [], dict(room)
        cands = list(pools)
        while cands:
            best = None
            for key in cands:
                h, lots = pools[key]
                shares = hh.share(h.broker, h.asset)
                eff = min((left[p] / s for p, s in shares.items() if s > 0), default=0.0)
                if eff < min_gain:
                    continue
                price = prices_at(h, when)
                step = 1e-8 if cls_of[key] == "Crypto" else 1.0
                q = max_without_tax(lots, price, ref.get(key), when, rules, eff, step)
                if q <= 0:
                    continue
                gain, _acq, missing = gain_for(lots, q, price, ref.get(key), when, rules)
                if missing or gain < min_gain:
                    continue
                window = 0.0
                if y <= last_year:                     # original price above the 31-12-2025 value: lost after 2030
                    left_q = q
                    for lot in lots:
                        if left_q <= 1e-12:
                            break
                        t = min(lot.qty, left_q)
                        left_q -= t
                        if lot.date.date() <= rules.reference_date and ref.get(key) is not None:
                            kept = unit_acquisition(lot, price, ref[key], when, rules) - ref[key]
                            window += t * max(0.0, kept)
                value = q * price
                cost = 2 * _cap(value, tob[key]) + 2 * costs.order_cost(h.broker, value) + 2 * costs.spread * value
                benefit = rules.rate * (gain + window)
                if benefit <= cost:
                    continue
                if best is None or cost / benefit < best[0]:
                    best = (cost / benefit, key, q, price, gain, window, cost, shares)
            if best is None:
                break
            _r, key, q, price, gain, window, cost, shares = best
            h, lots = pools[key]
            left_q, kept = q, []
            for lot in lots:                            # FIFO: the sold units come back as a new lot at this price
                t = min(lot.qty, left_q)
                lot.qty -= t
                left_q -= t
            lots[:] = [l for l in lots if l.qty > 1e-12]
            lots.append(Lot(datetime.combine(when, datetime.min.time()), q, price, price, "harvest"))
            lots.sort(key=lambda l: l.date)
            for p, s in shares.items():
                left[p] -= gain * s
                nets[p][y] = nets[p].get(y, 0.0) + gain * s
            lines.append(PlanLine(y, h.broker, h.asset, h.name, q, round(price, 4), round(gain, 2), round(window, 2),
                                  round(cost, 2)))
            cands.remove(key)
        yc = sum(l.costs for l in lines)
        total_costs += yc
        years.append(PlanYear(y, {p: round(v, 2) for p, v in room.items()}, lines, round(sum(l.gain for l in lines), 2),
                              round(yc, 2)))
    tax_with = _final_tax(rep, pools, ref, prices_at, final_year, nets, er)
    notes = [f"Prices grow {growth:.0%} a year from today's (cash and bonds stay flat); the real prices on the day you "
             "trade decide the gain.",
             "Each year's sales and purchases back should happen before mid-December so they settle in that year.",
             "The general anti-abuse rule still applies to selling and buying back the same fund; a similar fund avoids "
             "the question (see the year-end tab).",
             "Funds subject to the Reynders tax and insurance contracts are left out."]
    if keep_carry:
        notes.append("€1,000 of each year's exemption is kept so it carries forward (worth the same, without costs).")
    return TaxPlan(years, round(tax_without, 2), round(tax_with, 2), round(total_costs, 2), final_year, growth, notes)
