"""Fund red flags (backlog item 18, docs/decisions.md D35).

For every fund you hold (ETF, fund, bond ETF), the warning signs that cost money year after year, each with an
estimate in euros and a cheaper alternative from the fund catalog:
- distributing: the 30% dividend tax on every distribution (an accumulating twin reinvests it untaxed until you sell);
- 1.32% stock exchange tax: a Belgian-registered accumulating fund costs 1.32% per trade instead of 0.12%;
- high running costs: TER clearly above the cheapest alternative on the same index (or a world fund);
- bond exposure: the Reynders tax (30% on the interest part) when you sell.
Facts come from the rate your broker charged before, then the catalog; nothing is guessed: unknown facts are said.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..funds import CATALOG, WORLD_TARGETS, Fund, lookup
from .harvest import observed_tob_rate
from .switch import dividends_per_unit, is_distributing

DIVIDEND_TAX = 0.30
TER_GAP = 0.0010          # flag a TER at least 0.10 percentage point above the alternative
FUND_CLASSES = ("ETF", "Fund", "Bond ETF")


@dataclass
class Flag:
    kind: str                 # distributing | tob | ter | bonds
    text: str
    per_year: float | None = None     # € per year it costs (estimate), None when it's per trade


@dataclass
class FundCheck:
    isin: str
    name: str
    value: float
    flags: list = field(default_factory=list)
    alternative: Fund | None = None
    alternative_why: str = ""

    @property
    def per_year(self) -> float:
        return sum(f.per_year or 0.0 for f in self.flags)


def alternative_for(f: Fund | None, isin: str) -> tuple[Fund | None, str]:
    """Cheapest accumulating fund at 0.12% on the same index, else the cheapest such world fund."""
    def ok(x: Fund) -> bool:
        return x.isin != isin and x.accumulating and (x.tob_rate or 1) <= 0.0012 and x.ter is not None
    same = [x for x in CATALOG.values() if f and f.index and x.index == f.index and ok(x)]
    if same:
        return min(same, key=lambda x: x.ter), f"same index ({f.index})"
    world = [CATALOG[i] for i in WORLD_TARGETS if i in CATALOG and ok(CATALOG[i])]
    if world:
        return min(world, key=lambda x: x.ter), "a world fund (no cheaper fund on the same index in the catalog)"
    return None, ""


def check_funds(rep) -> list[FundCheck]:
    pf, today = rep.portfolio, rep.tax.today
    held = defaultdict(lambda: dict(qty=0.0, value=0.0, brokers=set(), name="", cls=""))
    for h in pf.holdings():
        if h.asset_class not in FUND_CLASSES:
            continue
        p = rep.prices.now(h)
        d = held[h.asset]
        d["qty"] += h.qty
        d["value"] += h.qty * p.value if p else 0.0
        d["brokers"].add(h.broker)
        d["name"], d["cls"] = h.name, h.asset_class
    out = []
    for isin, d in held.items():
        f = lookup(isin)
        alt, why = alternative_for(f, isin)
        c = FundCheck(isin, d["name"], round(d["value"], 2), alternative=alt, alternative_why=why)
        dist = is_distributing(d["name"], isin)
        if dist:
            div = sum(dividends_per_unit(pf, b, isin, today) * sum(h.qty for h in pf.holdings() if h.asset == isin and h.broker == b)
                      for b in d["brokers"])
            c.flags.append(Flag("distributing", f"Distributing: 30% dividend tax on each distribution "
                                f"(last 12 months: €{div:,.0f} received)." if div else
                                "Distributing: 30% dividend tax on each distribution.", round(DIVIDEND_TAX * div, 2)))
        rate = next((r for r in (observed_tob_rate(pf, b, isin) for b in d["brokers"]) if r is not None), None)
        rate = rate if rate is not None else (f.tob_rate if f else None)
        if rate is not None and rate >= 0.0132 - 1e-9:
            extra = (rate - 0.0012) * d["value"]
            c.flags.append(Flag("tob", f"1.32% stock exchange tax per trade instead of 0.12%: €{extra:,.0f} extra if you "
                                "sold all of it, and the same on every purchase."))
        if f and f.ter is not None and alt and alt.ter is not None and f.ter - alt.ter >= TER_GAP:
            c.flags.append(Flag("ter", f"Running costs {f.ter:.2%} a year against {alt.ter:.2%} for {alt.ticker or alt.isin}.",
                                round((f.ter - alt.ter) * d["value"], 2)))
        elif f is None or f.ter is None:
            c.flags.append(Flag("ter", "Running costs (TER) unknown: not in the catalog. Check the fund's KID."))
        if d["cls"] == "Bond ETF" or rep.tax.reynders.applies(isin):
            c.flags.append(Flag("bonds", "Bond fund: Reynders tax (30% on the interest part) when you sell, with no "
                                "exemption; see the Reynders panel."))
        if c.flags:
            out.append(c)
    return sorted(out, key=lambda c: -(c.per_year or 0) - c.value / 1e9)
