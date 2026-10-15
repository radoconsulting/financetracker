"""Net worth over time (backlog item 16, docs/decisions.md D33).

Per month-end: the value of every position (units held that day × that day's price, as in engine/performance.py),
plus cash per broker reconstructed from the rows: deposits − withdrawals + sale proceeds − purchases + income + fees.
That cash is only right when the exports include every deposit; a broker whose reconstructed cash goes below
−MAX_GAP at some point is missing deposits, so its cash is left out (positions still count) and a note says so.
Split by broker and by asset class; manual positions are included like any broker.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from ..models import BUY, CASH_IN, CASH_OUT, DIVIDEND, FEE, INTEREST, SELL, WITHHOLDING
from .allocation import CLASS_BUCKET
from .performance import PriceSeries, _at, month_ends, units_over_time

MAX_GAP = 250.0      # € of negative reconstructed cash tolerated (rounding, costs booked elsewhere)


@dataclass
class NetWorth:
    dates: list
    by_broker: dict          # broker -> [value per date] (positions + cash when kept)
    by_class: dict           # asset-class bucket (+ "Cash") -> [value per date]
    cash: dict               # broker -> [cash per date] (only brokers whose cash is kept)
    total: list
    notes: list = field(default_factory=list)


def _cash_steps(portfolio) -> dict[str, list[tuple[date, float]]]:
    """Broker -> [(date, cash after that day)] from the cash effect of every row."""
    deltas: dict = defaultdict(lambda: defaultdict(float))
    for t in portfolio.transactions:
        d = t.ts.date()
        if t.kind in (BUY, SELL) and t.total_eur is not None:
            if portfolio.options.class_overrides.get(t.asset, t.asset_class) == "Cash/FX":
                continue
            deltas[t.broker][d] += t.total_eur
        elif t.kind in (CASH_IN, CASH_OUT):
            deltas[t.broker][d] += t.amount_eur if t.amount_eur is not None else t.qty
    for i in portfolio.income:
        if i.kind in (DIVIDEND, INTEREST, WITHHOLDING):
            deltas[i.broker][i.date.date()] += i.amount_eur
    for r in portfolio.realized:
        if r.kind == FEE:
            deltas[r.broker][r.date.date()] += r.proceeds_eur
    out = {}
    for b, days in deltas.items():
        run, steps = 0.0, []
        for d in sorted(days):
            run += days[d]
            steps.append((d, run))
        out[b] = steps
    return out


def networth(rep, fetch: bool | None = None, start: date | None = None) -> NetWorth:
    pf = rep.portfolio
    end = rep.tax.today
    txs = [t for t in pf.transactions if t.kind in (BUY, SELL, CASH_IN)]
    if not txs:
        return NetWorth([], {}, {}, {}, [], ["No data."])
    first = start or min(t.ts.date() for t in txs)
    dates = [d for d in month_ends(first, end) if d >= first]
    fetch = rep.prices.fetcher is not None if fetch is None else fetch
    ps = PriceSeries(rep, first, end, fetch)
    units = units_over_time(pf)
    classes = {}
    for t in pf.transactions:
        classes[(t.broker, t.asset)] = pf.options.class_overrides.get(t.asset, t.asset_class)
    by_broker = defaultdict(lambda: [0.0] * len(dates))
    by_class = defaultdict(lambda: [0.0] * len(dates))
    for (b, asset), steps in units.items():
        bucket = CLASS_BUCKET.get(classes.get((b, asset), ""), "Other")
        for i, d in enumerate(dates):
            q = _at(steps, d)
            if q > 1e-9:
                v = q * (ps.price(asset, d) or 0.0)
                by_broker[b][i] += v
                by_class[bucket][i] += v
    notes, cash = [], {}
    for b, steps in _cash_steps(pf).items():
        series = [_at(steps, d) for d in dates]
        low = min(series) if series else 0.0
        if low < -MAX_GAP:
            notes.append(f"{b}: cash left out; rebuilt from the rows it goes down to €{low:,.0f}, so some deposits aren't "
                         "in the exports (add the account statement, or the broker's deposits).")
            continue
        series = [max(0.0, v) for v in series]
        cash[b] = series
        for i, v in enumerate(series):
            by_broker[b][i] += v
            by_class["Cash"][i] += v
    total = [sum(v[i] for v in by_broker.values()) for i in range(len(dates))]
    approx = sorted({a for (_b, a) in units if ps.source.get(a, "").startswith("last trade")})
    if approx:
        notes.append(f"{len(approx)} position(s) without price history are valued at their last trade price between trades.")
    notes.append("Foreign-currency cash (e.g. USD at LYNX) is counted in euros at the time of each trade, not revalued.")
    return NetWorth(dates, dict(by_broker), dict(by_class), cash, total, notes)
