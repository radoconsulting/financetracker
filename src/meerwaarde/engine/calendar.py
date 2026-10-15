"""Dividend and cash-flow calendar (backlog item 19, docs/decisions.md D36).

Expected dividends for the next 12 months: every payment of the last 12 months is projected one year later, at the
same amount per unit (net of foreign withholding as it was then) for the units you hold now. Positions sold since
drop out. Belgian tax: 30% of the net amount through the tax return (Belgian companies' dividends had it withheld at
source). And when the year's taxes are due: dividends, interest and the meerwaardebelasting of an income year are
declared in the return of the next year and paid after the assessment.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..models import DIVIDEND, WITHHOLDING
from .switch import _qty_at

BE_RATE = 0.30


@dataclass
class Expected:
    on: date
    broker: str
    asset: str
    name: str
    gross: float          # € before foreign withholding
    foreign_tax: float    # € (negative)
    be_tax: float         # € Belgian tax due on it (30% of net; 0 when withheld at source in Belgium)
    based_on: date        # the payment it is projected from

    @property
    def net(self) -> float:
        return self.gross + self.foreign_tax


@dataclass
class Calendar:
    expected: list
    due: list = field(default_factory=list)     # [(what, when, amount)]
    notes: list = field(default_factory=list)


def _next_year(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:                           # 29 February
        return d + timedelta(days=365)


def calendar(rep, months: int = 12) -> Calendar:
    pf, today = rep.portfolio, rep.tax.today
    start = today - timedelta(days=365)
    holdings = {(h.broker, h.asset): h for h in pf.holdings()}
    tax_by_pay = defaultdict(float)
    for i in pf.income:
        if i.kind == WITHHOLDING:
            tax_by_pay[(i.broker, i.asset, i.date.date())] += i.amount_eur
    out = []
    for i in pf.income:
        d = i.date.date()
        if i.kind != DIVIDEND or not (start < d <= today) or i.amount_eur <= 0:
            continue
        h = holdings.get((i.broker, i.asset))
        if h is None:
            continue                             # sold since
        then = _qty_at(pf, i.broker, i.asset, datetime.combine(d, datetime.max.time()))
        if then <= 0:
            continue
        k = h.qty / then
        wht = sum(v for (b, a, wd), v in tax_by_pay.items() if b == i.broker and a == i.asset and abs((wd - d).days) <= 10)
        gross, foreign = i.amount_eur * k, (wht * k if not i.asset.startswith("BE") else 0.0)
        be_tax = 0.0 if i.asset.startswith("BE") else BE_RATE * (gross + foreign)
        on = _next_year(d)
        if today < on <= today + timedelta(days=31 * months):
            out.append(Expected(on, i.broker, i.asset, h.name, round(gross, 2), round(foreign, 2), round(be_tax, 2), d))
    out.sort(key=lambda e: e.on)

    due = []
    y = today.year
    inc = rep.income_tax(y)
    div_tax = sum(it.to_pay for it in inc.values())
    cgt = rep.tax_summary(rep.sold_lines())["tax"]
    when = f"with the tax return of {y + 1} (filed mid-{y + 1}, paid after the assessment, usually by mid-{y + 2})"
    if div_tax:
        due.append((f"Tax on dividends and interest received in {y} so far", when, round(div_tax, 2)))
    if cgt:
        due.append((f"Meerwaardebelasting on this year's sales so far", when, round(cgt, 2)))
    expected_tax = sum(e.be_tax for e in out if e.on.year == y)
    if expected_tax:
        due.append((f"Tax on the dividends still expected in {y}", when, round(expected_tax, 2)))
    notes = ["Projections repeat last year's payments per unit; companies and funds change their dividends. The share "
             "dividend exemption (shares only) lowers the tax due; the yearly figures above take it into account, the "
             "per-payment estimates don't."]
    return Calendar(out, due, notes)
