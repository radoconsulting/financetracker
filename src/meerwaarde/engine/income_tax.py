"""Belgian tax on dividends and interest received through foreign brokers (backlog item 2).

- Foreign brokers (DEGIRO, LYNX/IBKR) don't withhold the Belgian 30% (roerende voorheffing): the income is declared
  in the tax return and taxed at 30%.
- The Belgian tax is on the amount received after foreign withholding tax; the foreign tax is not credited.
- Dividend exemption: dividends from **shares** up to a yearly amount per person (€833 for income 2025 and 2026)
  are exempt. Distributions from funds and ETFs don't qualify. Interest has no exemption here (the savings-account
  exemption is for regulated Belgian savings deposits only).
Sources: docs/domain/belgian-taxes.md.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..models import DIVIDEND, INTEREST, WITHHOLDING

DIVIDEND_EXEMPTION = {2023: 800.0, 2024: 833.0, 2025: 833.0, 2026: 833.0}
SHARE_CLASSES = {"Stock"}
CATEGORIES = {
    "shares": "Dividends from shares",
    "funds": "Distributions from funds and ETFs",
    "interest": "Interest",
}


@dataclass
class IncomeTaxRules:
    rate: float = 0.30
    exemption: dict[int, float] = field(default_factory=lambda: dict(DIVIDEND_EXEMPTION))

    def exemption_for(self, year: int) -> float:
        known = [y for y in self.exemption if y <= year]
        return self.exemption[max(known)] if known else self.exemption[min(self.exemption)]


@dataclass
class IncomeLine:
    person: str
    broker: str
    category: str              # shares | funds | interest
    gross: float               # before foreign tax
    foreign_tax: float         # negative = withheld abroad
    count: int = 0

    @property
    def net(self) -> float:
        return self.gross + self.foreign_tax


@dataclass
class IncomeTax:
    person: str
    year: int
    lines: list[IncomeLine]
    exemption_available: float
    rate: float
    interest_paid: float = 0.0   # debit interest: not deductible, shown for completeness

    def total(self, category: str, what: str = "net") -> float:
        return sum(getattr(l, what) for l in self.lines if l.category == category)

    @property
    def exemption_used(self) -> float:
        return min(max(0.0, self.total("shares")), self.exemption_available)

    @property
    def taxable(self) -> float:
        return (max(0.0, self.total("shares")) - self.exemption_used + max(0.0, self.total("funds"))
                + max(0.0, self.total("interest")))

    @property
    def tax(self) -> float:
        return self.rate * self.taxable

    @property
    def foreign_tax(self) -> float:
        return sum(l.foreign_tax for l in self.lines)


def _category(row) -> str:
    if row.kind == INTEREST:
        return "interest"
    cls = row.asset_class or ""
    if cls in SHARE_CLASSES:
        return "shares"
    if row.kind == WITHHOLDING and cls == "Cash/FX":
        return "interest" if "interest" in (row.name or "").lower() else "funds"
    return "funds"


def income_tax(income_rows, household, year: int, rules: IncomeTaxRules | None = None,
               other: dict[str, dict[str, float]] | None = None,
               exemption: float | None = None) -> dict[str, IncomeTax]:
    """Dividend and interest tax for one income year, per person.

    `income_rows`: IncomeRow list (portfolio.income). `other`: person -> {dividends_shares, dividends_funds, interest,
    foreign_tax} for income not in the exports (added as a broker line "Other")."""
    rules = rules or IncomeTaxRules()
    acc = defaultdict(lambda: [0.0, 0.0, 0])        # (person, broker, category) -> [gross, foreign tax, count]
    paid = defaultdict(float)
    for r in income_rows:
        if r.date.year != year or r.kind not in (DIVIDEND, INTEREST, WITHHOLDING):
            continue
        cat = _category(r)
        for pid, sh in household.share(r.broker, r.asset).items():
            a = acc[(pid, r.broker, cat)]
            if r.kind == WITHHOLDING:
                a[1] += r.amount_eur * sh
            elif r.kind == INTEREST and r.amount_eur < 0:
                paid[pid] += r.amount_eur * sh
            else:
                a[0] += r.amount_eur * sh
                a[2] += 1
    for pid, d in (other or {}).items():
        if pid not in household.ids:
            continue
        for cat, key in (("shares", "dividends_shares"), ("funds", "dividends_funds"), ("interest", "interest")):
            if d.get(key):
                acc[(pid, "Other", cat)][0] += float(d[key])
        if d.get("foreign_tax"):
            acc[(pid, "Other", "funds" if not d.get("dividends_shares") else "shares")][1] += -abs(float(d["foreign_tax"]))
    ex = rules.exemption_for(year) if exemption is None else exemption
    out = {}
    for pid in household.ids:
        lines = [IncomeLine(p, b, c, round(v[0], 2), round(v[1], 2), v[2]) for (p, b, c), v in sorted(acc.items()) if p == pid]
        out[pid] = IncomeTax(pid, year, lines, ex, rules.rate, round(paid[pid], 2))
    return out
