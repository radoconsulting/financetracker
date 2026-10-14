"""Year-end gain harvesting (backlog item 6).

Exemption that isn't used by 31 December is gone (only €1,000 carries forward). Selling lots with a gain up to the
unused exemption, and buying them back, raises their purchase value for the meerwaardebelasting at no tax. This
module proposes which lots to sell, compares the tax saved later with the costs now (stock exchange tax on the sale
and on the purchase back, commissions, spread), and flags losses that could offset gains when tax is due this year.

Choices (docs/decisions.md D21):
- Positions are picked by cost per euro of gain (cheapest first); FIFO decides which lots go first within a position.
- By default €1,000 of room is kept so the carry-forward isn't lost (it's worth the same as harvesting it, without
  costs). Cash/FX positions are left out.
- The stock exchange tax rate per position comes from what the broker charged on earlier trades of that product
  (DEGIRO account statement, LYNX statement); otherwise the standard rate for its class.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..models import BUY, SELL
from .tax import gain_for

TOB_CAPS = {0.0012: 1300.0, 0.0035: 1600.0, 0.0132: 4000.0}
DEFAULT_TOB = {"ETF": 0.0012, "Bond ETF": 0.0012, "Fund": 0.0012, "Bond": 0.0012, "Stock": 0.0035, "Turbo": 0.0035}
DEFAULT_COMMISSION = {"DEGIRO": (3.0, 0.0), "LYNX": (8.5, 0.0), "Bitvavo": (0.0, 0.0025)}   # per order: fixed €, fraction


@dataclass
class CostModel:
    spread: float = 0.0005                       # half the bid-ask spread, per trade, as a fraction of the value
    commission: dict = field(default_factory=lambda: dict(DEFAULT_COMMISSION))
    default_commission: tuple = (5.0, 0.001)
    tob_override: dict = field(default_factory=dict)   # asset -> rate

    def order_cost(self, broker: str, value: float) -> float:
        fixed, pct = self.commission.get(broker, self.default_commission)
        return fixed + pct * value


@dataclass
class HarvestLine:
    broker: str
    asset: str
    name: str
    asset_class: str
    shares: dict                  # person -> ownership share
    qty: float
    price: float
    value: float
    gain: float                   # taxable gain realized now = the step-up of the purchase value
    tob_rate: float
    tob_source: str
    tob: float                    # sale + purchase back
    commission: float             # two orders
    spread: float                 # two trades
    rate: float = 0.10

    @property
    def costs(self) -> float:
        return self.tob + self.commission + self.spread

    @property
    def tax_saved(self) -> float:
        """Meerwaardebelasting saved later, if this gain would otherwise be taxed when sold."""
        return self.rate * self.gain

    @property
    def net(self) -> float:
        return self.tax_saved - self.costs


@dataclass
class LossLine:
    broker: str
    asset: str
    name: str
    qty: float
    value: float
    loss: float                   # taxable loss if sold now (negative)
    tax_saved_now: float          # tax this year it would remove (0 when the year is under the exemption)


@dataclass
class HarvestPlan:
    year: int
    persons: list                 # [(id, name)]
    status: dict                  # person -> {net, exemption, room, reserve, target, tax}
    keep_carry: bool
    lines: list[HarvestLine]
    losses: list[LossLine]
    notes: list[str]

    @property
    def gain(self) -> float:
        return sum(l.gain for l in self.lines)

    @property
    def tax_saved(self) -> float:
        return sum(l.tax_saved for l in self.lines)

    @property
    def costs(self) -> float:
        return sum(l.costs for l in self.lines)

    @property
    def net(self) -> float:
        return self.tax_saved - self.costs


def observed_tob_rate(portfolio, broker: str, asset: str) -> float | None:
    """The stock exchange tax rate the broker charged on earlier trades of this product, snapped to a legal rate."""
    gross = defaultdict(float)
    for t in portfolio.transactions:
        if t.broker == broker and t.asset == asset and t.kind in (BUY, SELL) and t.gross_eur:
            gross[t.ts.date()] += abs(t.gross_eur)
    taxed = defaultdict(float)
    for r in portfolio.tob:
        if r.broker == broker and r.asset == asset:
            taxed[r.date.date()] += -r.amount_eur
    # the most recent trade decides: a fund's rate changes when it gets registered in Belgium (VWCE went 0.12% -> 1.32%)
    for d in sorted((d for d in taxed if gross.get(d) and taxed[d] > 0), reverse=True):
        m = taxed[d] / gross[d]
        capped = [r for r, cap in TOB_CAPS.items() if abs(taxed[d] - cap) < 0.01]
        if capped:
            return capped[0]
        best = min(TOB_CAPS, key=lambda r: abs(r - m))
        if abs(best - m) <= 0.35 * best:
            return best
    return None


def tob_for(portfolio, h, costs: CostModel) -> tuple[float, str]:
    if h.asset in costs.tob_override:
        return costs.tob_override[h.asset], "your rate"
    seen = observed_tob_rate(portfolio, h.broker, h.asset)
    if seen is not None:
        return seen, "charged on your earlier trades"
    rate = DEFAULT_TOB.get(h.asset_class, 0.0)
    return rate, ("standard rate for its class" if rate else "none (not a security)")


def _cap(value: float, rate: float) -> float:
    return min(value * rate, TOB_CAPS.get(rate, float("inf")))


def plan_harvest(rep, keep_carry: bool = True, costs: CostModel | None = None, exclude=(), min_gain: float = 25.0) -> HarvestPlan:
    """Lots to sell (and buy back) before 31 December to use this year's remaining exemption."""
    costs = costs or CostModel()
    rules, hh, calc = rep.tax.rules, rep.household, rep.tax
    er = rep.exemption_rules()
    sold = rep.tax_summary(rep.sold_lines())["people"]
    status, rooms = {}, {}
    for pid in hh.ids:
        p = sold[pid]
        room = max(0.0, p["exemption"] - p["net"])
        reserve = 0.0
        if keep_carry and room > 0:
            if er.carry_rule == "none_used":
                reserve = room if p["net"] <= 0 else 0.0      # any gain this year already loses the carry-forward
            else:
                reserve = min(er.carry_per_year, room)
        rooms[pid] = room - reserve
        status[pid] = dict(net=p["net"], exemption=p["exemption"], room=room, reserve=reserve, target=room - reserve,
                           tax=p["tax"])

    # candidates
    cands = []
    for h in rep.portfolio.holdings():
        if h.asset_class == "Cash/FX" or h.asset in exclude or f"{h.broker}|{h.asset}" in exclude:
            continue
        now = rep.prices.now(h)
        if not now or not now.value:
            continue
        pre = [l for l in h.lots if l.date.date() <= rules.reference_date]
        refp = rep.prices.reference(h) if pre else None
        rate, src = tob_for(rep.portfolio, h, costs)
        cands.append(dict(h=h, price=now.value, ref=refp.value if refp else None, rate=rate, src=src,
                          shares=hh.share(h.broker, h.asset)))

    def build_line(c, room_eff):
        h, price = c["h"], c["price"]
        q = calc.max_without_tax(h, room_eff, price)
        if q <= 0:
            return None
        gain, _acq, missing = gain_for(h.lots, q, price, c["ref"], calc.today, rules)
        if missing or gain < min_gain:
            return None
        value = q * price
        line = HarvestLine(h.broker, h.asset, h.name, h.asset_class, c["shares"], q, price, value, gain, c["rate"], c["src"],
                           tob=2 * _cap(value, c["rate"]), commission=2 * costs.order_cost(h.broker, value),
                           spread=2 * costs.spread * value, rate=rules.rate)
        return line if line.net > 0 else None

    lines = []
    left = dict(rooms)
    while cands:
        best, best_c = None, None
        for c in cands:
            eff = min((left[p] / s for p, s in c["shares"].items() if s > 0), default=0.0)
            if eff < min_gain:
                continue
            line = build_line(c, eff)
            if line and (best is None or line.costs / line.gain < best.costs / best.gain):
                best, best_c = line, c
        if best is None:
            break
        lines.append(best)
        cands.remove(best_c)
        for p, s in best.shares.items():
            left[p] -= best.gain * s

    # losses: useful only when tax is due this year
    losses = []
    for h in rep.portfolio.holdings():
        if h.asset_class == "Cash/FX":
            continue
        now = rep.prices.now(h)
        if not now:
            continue
        pre = [l for l in h.lots if l.date.date() <= rules.reference_date]
        refp = rep.prices.reference(h) if pre else None
        gain, _a, missing = gain_for(h.lots, h.qty, now.value, refp.value if refp else None, calc.today, rules)
        if missing or gain > -1.0:
            continue
        saved = 0.0
        for p, s in hh.share(h.broker, h.asset).items():
            excess = max(0.0, sold[p]["net"] - sold[p]["exemption"])
            saved += rules.rate * min(-gain * s, excess)
        losses.append(LossLine(h.broker, h.asset, h.name, h.qty, h.qty * now.value, gain, saved))
    losses.sort(key=lambda l: l.loss)

    notes = [
        "The general anti-abuse rule still applies: selling and buying back the same fund only to raise its purchase "
        "value could be challenged. Discuss it with your adviser; buying a similar but different fund avoids the question.",
        "The sale has to happen in this tax year. The law doesn't say whether the trade date or the settlement date "
        "counts: leave a few trading days before 31 December.",
        "'Tax saved later' assumes the harvested gain would otherwise be taxed when you sell in a later year. If later "
        "gains stay under the exemption anyway, the saving is smaller.",
        "Prices are those of the value date; the gain changes with the price on the day you trade.",
    ]
    if keep_carry and any(s["reserve"] for s in status.values()):
        notes.append(f"€{er.carry_per_year:,.0f} of room is kept so it carries forward to next year (worth the same as "
                     "harvesting it, without trading costs).")
    if not any(s["room"] > 0 for s in status.values()):
        notes.append("No exemption left this year: nothing to harvest.")
    return HarvestPlan(rules.year, [(p.id, p.name) for p in hh.persons], status, keep_carry, lines, losses, notes)
