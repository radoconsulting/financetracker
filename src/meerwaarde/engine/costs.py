"""Fees and costs (backlog item 17, docs/decisions.md D34).

Per year and broker:
- stock exchange tax: the per-trade tax rows the brokers list (LYNX statements, DEGIRO account statement);
- commissions and conversion costs: the trades' costs (fees_eur) minus that stock exchange tax;
- other fees: standalone fee rows (exchange connection, money market fund, currency conversion commission);
- network fees: coins spent on crypto withdrawals (their FIFO cost);
- running costs: for funds with a known TER, the value held at each month-end × TER / 12 (the TER is taken out of
  the fund's price, so this is a cost you don't see on a statement).
And the yearly level today: trading costs of the last 12 months + running costs on today's value; × 10 for a decade.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..funds import lookup
from ..models import BUY, SELL

KINDS = ("Stock exchange tax", "Commissions and conversion", "Other fees", "Network fees", "Running costs (TER)")


@dataclass
class Costs:
    rows: list                      # [dict(year, broker, kind, amount)]
    yearly_now: dict                # kind -> € per year at today's level
    unknown_ter: list               # funds held without a known TER
    notes: list = field(default_factory=list)

    @property
    def per_year_now(self) -> float:
        return sum(self.yearly_now.values())


def costs(rep, fetch: bool | None = None) -> Costs:
    pf = rep.portfolio
    today = rep.tax.today
    acc: dict = defaultdict(float)
    tob = defaultdict(float)
    for t in pf.tob:
        tob[(t.date.year, t.broker)] += -t.amount_eur
        acc[(t.date.year, t.broker, KINDS[0])] += -t.amount_eur
    trade_fees = defaultdict(float)
    for t in pf.transactions:
        if t.kind in (BUY, SELL) and t.fees_eur:
            trade_fees[(t.ts.year, t.broker)] += t.fees_eur
    for (y, b), v in trade_fees.items():
        acc[(y, b, KINDS[1])] += max(0.0, v - tob.get((y, b), 0.0))
    for r in pf.realized:
        if r.kind == "fee":
            acc[(r.date.year, r.broker, KINDS[2])] += -r.proceeds_eur
        elif r.note == "Network fee":
            acc[(r.date.year, r.broker, KINDS[3])] += r.cost_eur

    # running costs: month-end values × TER / 12
    from .performance import PriceSeries, _at, month_ends, units_over_time
    units = units_over_time(pf)
    unknown, ter_of = set(), {}
    for (b, asset) in units:
        cls = next((t.asset_class for t in pf.transactions if t.asset == asset), "")
        if cls in ("ETF", "Bond ETF", "Fund"):
            f = lookup(asset)
            if f and f.ter is not None:
                ter_of[asset] = f.ter
            else:
                unknown.add(asset)
    if ter_of:
        first = min(t.ts.date() for t in pf.transactions if t.asset in ter_of)
        fetch = rep.prices.fetcher is not None if fetch is None else fetch
        ps = PriceSeries(rep, first, today, fetch)
        for (b, asset), steps in units.items():
            if asset not in ter_of:
                continue
            for d in month_ends(first, today):
                q = _at(steps, d)
                if q > 1e-9:
                    acc[(d.year, b, KINDS[4])] += q * (ps.price(asset, d) or 0.0) * ter_of[asset] / 12

    rows = [dict(year=y, broker=b, kind=k, amount=round(v, 2)) for (y, b, k), v in sorted(acc.items()) if abs(v) > 0.005]

    # today's level: trading costs of the last 12 months + TER on today's value
    since = today - timedelta(days=365)
    now = defaultdict(float)
    for t in pf.tob:
        if t.date.date() > since:
            now[KINDS[0]] += -t.amount_eur
    fees_12 = sum(t.fees_eur for t in pf.transactions if t.kind in (BUY, SELL) and t.fees_eur and t.ts.date() > since)
    now[KINDS[1]] = max(0.0, fees_12 - now[KINDS[0]])
    now[KINDS[2]] = sum(-r.proceeds_eur for r in pf.realized if r.kind == "fee" and r.date.date() > since)
    now[KINDS[3]] = sum(r.cost_eur for r in pf.realized if r.note == "Network fee" and r.date.date() > since)
    for h in pf.holdings():
        if h.asset in ter_of:
            p = rep.prices.now(h)
            if p:
                now[KINDS[4]] += h.qty * p.value * ter_of[h.asset]
    names = {h.asset: h.name for h in pf.holdings()}
    notes = ["Running costs (TER) are an estimate: the value at each month-end × the fund's yearly TER / 12. They are "
             "already in the fund's price, so they appear on no statement."]
    if unknown:
        notes.append(f"No TER known for {len(unknown)} fund(s): " + ", ".join(sorted(names.get(a, a) for a in unknown))
                     + ". Their running costs are missing (add them to the fund list on the Switch tab).")
    return Costs(rows, {k: round(now.get(k, 0.0), 2) for k in KINDS}, sorted(names.get(a, a) for a in unknown), notes)
