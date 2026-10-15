"""Performance against a benchmark (backlog item 14, docs/decisions.md D27).

Investor cash flows are built from the trades, not from deposits into the broker account (not every export has
those): a purchase is money in (negative), a sale and income are money out (positive), a standalone fee is money in.
Cash at the broker is therefore outside the measured portfolio, and currency positions (Cash/FX) are left out on both
sides. With the value today that gives:
- money-weighted return (XIRR), per broker and in total;
- time-weighted return: Modified Dietz per month, chained, from month-end values;
- a value series per month (units held × the price on that day: online history, else the last trade price);
- the same flows invested in a benchmark fund (default iShares MSCI ACWI): its value series and XIRR.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..models import (BUY, DEPOSIT, DIVIDEND, FEE, INTEREST, REWARD, SELL, WITHDRAWAL, WITHHOLDING)

DEFAULT_BENCHMARK = ("IE00B6R52259", "iShares MSCI ACWI UCITS ETF (Acc)")
EXCLUDED = ("Cash/FX",)


# ---------------------------------------------------------------- XIRR
def xirr(flows: list[tuple[date, float]], guess: float = 0.08) -> float | None:
    """Annual money-weighted return of dated flows (negative = invested). None when it isn't defined."""
    flows = [(d, a) for d, a in flows if abs(a) > 1e-9]
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    d0 = min(d for d, _ in flows)
    ts = [((d - d0).days / 365.0, a) for d, a in flows]

    def npv(r):
        return sum(a / (1 + r) ** t for t, a in ts)

    def dnpv(r):
        return sum(-t * a / (1 + r) ** (t + 1) for t, a in ts)

    r = guess
    for _ in range(50):                       # Newton first
        try:
            f, df = npv(r), dnpv(r)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(df) < 1e-12:
            break
        nr = r - f / df
        if nr <= -0.9999 or not math.isfinite(nr):
            break
        if abs(nr - r) < 1e-10:
            return nr
        r = nr
    lo, hi = -0.9999, 10.0                    # bisection as a fallback
    try:
        flo, fhi = npv(lo), npv(hi)
    except OverflowError:
        return None
    if flo * fhi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        fm = npv(mid)
        if abs(fm) < 1e-7:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


# ---------------------------------------------------------------- flows and units
@dataclass
class Flow:
    broker: str
    on: date
    amount: float        # investor view: negative = money in, positive = money out
    what: str


def investor_flows(portfolio, excluded=EXCLUDED) -> list[Flow]:
    out = []
    redeemed = {(r.broker, r.asset, r.date.date()): r.proceeds_eur for r in portfolio.realized
                if r.note.startswith("Redeemed") and r.asset_class not in excluded}
    for t in portfolio.transactions:
        cls = portfolio.options.class_overrides.get(t.asset, t.asset_class)
        if cls in excluded:
            continue
        if t.kind in (BUY, SELL) and t.total_eur is not None:
            amount = t.total_eur
            if t.kind == SELL and (t.broker, t.asset, t.ts.date()) in redeemed and abs(amount) < 0.005:
                amount = redeemed[(t.broker, t.asset, t.ts.date())]      # bond repaid: payout not in the trade row
            out.append(Flow(t.broker, t.ts.date(), amount, t.kind))
    for i in portfolio.income:
        if i.kind in (DIVIDEND, INTEREST, WITHHOLDING) and i.asset_class not in excluded:
            out.append(Flow(i.broker, i.date.date(), i.amount_eur, i.kind))
    for r in portfolio.realized:
        if r.kind == FEE:
            out.append(Flow(r.broker, r.date.date(), r.proceeds_eur, "fee"))
    return sorted(out, key=lambda f: f.on)


def units_over_time(portfolio, excluded=EXCLUDED) -> dict[tuple, list[tuple[date, float]]]:
    """(broker, asset) -> [(date, units held after that day)], changes only."""
    own_wallet = portfolio.options.own_wallet_withdrawals
    held: dict[tuple, float] = defaultdict(float)
    steps: dict[tuple, list] = defaultdict(list)
    for t in portfolio.transactions:
        cls = portfolio.options.class_overrides.get(t.asset, t.asset_class)
        if cls in excluded:
            continue
        k = (t.broker, t.asset)
        if t.kind in (BUY, SELL, REWARD, DEPOSIT):
            held[k] += t.qty
        elif t.kind == WITHDRAWAL:
            held[k] -= t.fee_qty if own_wallet else abs(t.qty)
        else:
            continue
        held[k] = max(0.0, held[k]) if abs(held[k]) < 1e-9 else held[k]
        if steps[k] and steps[k][-1][0] == t.ts.date():
            steps[k][-1] = (t.ts.date(), held[k])
        else:
            steps[k].append((t.ts.date(), held[k]))
    return steps


def _at(steps: list, on: date, default=0.0):
    i = bisect_right(steps, (on, math.inf))
    return steps[i - 1][1] if i else default


class _Series:
    """Sorted (date, value) pairs; `at(day)` = the last value on or before the day."""

    def __init__(self, pairs):
        pairs = sorted(pairs)
        self.days = [d for d, _ in pairs]
        self.values = [v for _, v in pairs]

    def __bool__(self):
        return bool(self.days)

    def at(self, on: date):
        i = bisect_right(self.days, on)
        return self.values[i - 1] if i else None

    def first(self):
        return self.values[0] if self.values else None


def month_ends(start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while True:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        if last >= end:
            break
        out.append(last)
        y, m = nxt.year, nxt.month
    return out + [end]


# ---------------------------------------------------------------- prices over time
class PriceSeries:
    """Price per unit in EUR on a day: the online series (last value on or before the day), else the last trade price
    on or before it, else the first trade price; today uses the report's current price."""

    def __init__(self, rep, start: date, end: date, fetch: bool = True):
        self.rep, self.start, self.end = rep, start, end
        self.series: dict[str, _Series] = {}
        self.source: dict[str, str] = {}
        self.trades: dict[str, list] = defaultdict(list)
        for t in rep.portfolio.transactions:
            if t.kind in (BUY, SELL) and t.gross_eur and t.qty:
                self.trades[t.asset].append((t.ts.date(), abs(t.gross_eur / t.qty)))
        self.trades = {a: _Series(v) for a, v in self.trades.items()}
        self.now = {}
        for h in rep.portfolio.holdings():
            p = rep.prices.now(h)
            if p:
                self.now[h.asset] = p.value
        self.classes = {t.asset: t.asset_class for t in rep.portfolio.transactions}
        self.fetch = fetch

    def _load(self, asset: str):
        if asset in self.series:
            return
        s, src = self.rep.prices.history(asset, self.classes.get(asset, ""), self.start - timedelta(days=7), self.end) \
            if self.fetch else ({}, "")
        self.series[asset] = _Series(s.items())
        self.source[asset] = src or "last trade price (approximate)"

    def price(self, asset: str, on: date) -> float | None:
        if on >= self.end and asset in self.now:
            return self.now[asset]
        self._load(asset)
        v = self.series[asset].at(on)
        if v is not None:
            return v
        tr = self.trades.get(asset)
        if tr:
            v = tr.at(on)
            return v if v is not None else tr.first()
        return self.now.get(asset)


# ---------------------------------------------------------------- result
@dataclass
class Point:
    on: date
    value: float              # portfolio value
    invested: float           # cumulative net money in (money in − money out)
    benchmark: float | None   # the same flows in the benchmark


@dataclass
class Line:
    broker: str               # broker or "All"
    invested: float           # money in
    returned: float           # money out (sales, income)
    value: float              # value today
    xirr: float | None
    twr: float | None         # cumulative time-weighted return
    twr_annual: float | None
    benchmark_value: float | None
    benchmark_xirr: float | None

    @property
    def gain(self) -> float:
        return self.value + self.returned - self.invested


@dataclass
class Performance:
    start: date
    end: date
    lines: list[Line]
    points: dict[str, list[Point]]          # broker / "All" -> monthly points
    benchmark: tuple                        # (isin, name)
    benchmark_source: str
    sources: dict[str, str]                 # asset -> price history source
    notes: list[str] = field(default_factory=list)

    def line(self, broker: str = "All") -> Line | None:
        return next((l for l in self.lines if l.broker == broker), None)


def _twr(points: list[Point], flows: list[Flow]) -> float | None:
    """Chained Modified Dietz per period between the points; external flow into the portfolio = −investor flow."""
    if len(points) < 2:
        return None
    growth = 1.0
    for a, b in zip(points, points[1:]):
        period = [f for f in flows if a.on < f.on <= b.on]
        days = max(1, (b.on - a.on).days)
        net_in = -sum(f.amount for f in period)
        weighted = -sum(f.amount * (b.on - f.on).days / days for f in period)
        denom = a.value + weighted
        if denom <= 1e-6:
            continue                      # empty portfolio in this period: no return to chain
        growth *= 1 + (b.value - a.value - net_in) / denom
    return growth - 1


def performance(rep, benchmark: tuple = DEFAULT_BENCHMARK, fetch: bool = True, excluded=EXCLUDED) -> Performance:
    pf = rep.portfolio
    end = rep.tax.today
    flows = investor_flows(pf, excluded)
    notes = []
    if not flows:
        return Performance(end, end, [], {}, benchmark, "", {}, ["No trades to measure."])
    start = flows[0].on
    units = units_over_time(pf, excluded)
    ps = PriceSeries(rep, start, end, fetch)
    dates = month_ends(start, end)
    if dates[0] != start:
        dates = [start - timedelta(days=1)] + dates

    # benchmark prices
    bseries, bsrc = rep.prices.history(benchmark[0], "ETF", start - timedelta(days=7), end) if fetch else ({}, "")
    bseries = _Series(bseries.items())

    def bprice(on: date):
        v = bseries.at(on)
        return v if v is not None else bseries.first()

    brokers = sorted({f.broker for f in flows})
    lines, points = [], {}
    for b in ["All"] + brokers:
        fl = [f for f in flows if b == "All" or f.broker == b]
        keys = [k for k in units if b == "All" or k[0] == b]
        bunits, invested_cum, pts = 0.0, 0.0, []
        fi = 0
        for d in dates:
            while fi < len(fl) and fl[fi].on <= d:
                f = fl[fi]
                invested_cum -= f.amount
                if bseries:
                    p = bprice(f.on)
                    if p:
                        bunits -= f.amount / p
                fi += 1
            value = 0.0
            for k in keys:
                q = _at(units[k], d)
                if q > 1e-9:
                    p = ps.price(k[1], d)
                    value += q * (p or 0.0)
            bval = bunits * bprice(d) if bseries and bprice(d) else None
            pts.append(Point(d, value, invested_cum, bval))
        points[b] = pts
        value_now = pts[-1].value
        cash = [(f.on, f.amount) for f in fl] + [(end, value_now)]
        twr = _twr(pts, fl)
        years = max((end - start).days / 365.0, 1 / 365)
        twr_a = (1 + twr) ** (1 / years) - 1 if twr is not None and years >= 1 and twr > -1 else None
        bval = pts[-1].benchmark
        bx = xirr([(f.on, f.amount) for f in fl] + [(end, bval)]) if bval is not None else None
        lines.append(Line(b, -sum(f.amount for f in fl if f.amount < 0), sum(f.amount for f in fl if f.amount > 0),
                          value_now, xirr(cash), twr, twr_a, bval, bx))

    approx = sorted({k[1] for k in units if ps.source.get(k[1], "").startswith("last trade")})
    if approx:
        notes.append(f"{len(approx)} position(s) have no price history online; their value between trades uses the last "
                     "trade price, so the chart and the time-weighted return are approximate for them.")
    if not bseries:
        notes.append(f"No price history for the benchmark {benchmark[0]} (offline or not found): no comparison.")
    elif bunits_negative(points):
        notes.append("At some point you took out more than the benchmark investment was worth; its value went below zero "
                     "for a while, so read the benchmark figures with care.")
    notes.append("Measured on what you invested in positions: cash left at the broker and currency positions are outside it. "
                 "Units received without a purchase (rewards, transfers in) count as gains.")
    return Performance(start, end, lines, points, benchmark, bsrc, dict(ps.source), notes)


def bunits_negative(points: dict) -> bool:
    return any(p.benchmark is not None and p.benchmark < -0.01 for pts in points.values() for p in pts)
