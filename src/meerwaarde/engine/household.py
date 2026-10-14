"""People, ownership and the yearly exemption of the meerwaardebelasting (backlog item 4).

- The exemption is per person. Partners each have their own; jointly owned assets are split by ownership share,
  and every person's gains, losses and exemption are computed on their share.
- Unused exemption carries forward: per year at most €1,000 is added, each addition can be used for 5 years, so the
  exemption reaches at most €15,000 (2026 amounts). Carried amounts are used before the year's own exemption,
  oldest first. Sources and the open question on the exact rule: docs/domain/meerwaardebelasting.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import LAW_TYPES

EXEMPTION_BASE = {2026: 10000.0}      # indexed yearly; later years are entered by the user when published


@dataclass
class Person:
    id: str
    name: str


@dataclass
class Household:
    persons: list[Person] = field(default_factory=lambda: [Person("you", "You")])
    brokers: dict[str, dict[str, float]] = field(default_factory=dict)     # broker -> {person id: share}
    positions: dict[str, dict[str, float]] = field(default_factory=dict)   # "broker|asset" -> {person id: share}

    @property
    def ids(self) -> list[str]:
        return [p.id for p in self.persons]

    @property
    def single(self) -> bool:
        return len(self.persons) == 1

    def name(self, pid: str) -> str:
        return next((p.name for p in self.persons if p.id == pid), pid)

    def share(self, broker: str, asset: str | None = None) -> dict[str, float]:
        """Ownership of a position as {person id: fraction}, summing to 1. Default: everything belongs to the first person."""
        raw = self.positions.get(f"{broker}|{asset}") or self.brokers.get(broker) or {}
        valid = {k: float(v) for k, v in raw.items() if k in self.ids and v and float(v) > 0}
        total = sum(valid.values())
        if total <= 0:
            return {self.ids[0]: 1.0}
        return {k: v / total for k, v in valid.items()}


@dataclass
class ExemptionRules:
    base: dict[int, float] = field(default_factory=lambda: dict(EXEMPTION_BASE))
    first_year: int = 2026
    carry_per_year: float = 1000.0
    carry_years: int = 5
    carry_rule: str = "partial"   # partial: unused part of the year's own exemption, max 1,000 | none_used: 1,000 only
                                  # in a year without a net gain

    def base_for(self, year: int) -> float:
        known = [y for y in self.base if y <= year]
        return self.base[max(known)] if known else self.base[min(self.base)]


@dataclass
class ExemptionYear:
    year: int
    base: float
    carried_in: list[tuple[int, float]]   # (year it was carried from, amount available)
    net_gain: float
    used: float
    used_carry: float
    used_base: float
    carry_out: float                      # added to the following years
    expired: float                        # carried amounts that ran out at the start of this year

    @property
    def carried(self) -> float:
        return sum(a for _, a in self.carried_in)

    @property
    def available(self) -> float:
        return self.base + self.carried

    @property
    def unused(self) -> float:
        return self.available - self.used


def exemption_schedule(net_by_year: dict[int, float], rules: ExemptionRules, until: int) -> list[ExemptionYear]:
    """Exemption per year from `rules.first_year` to `until`, given the net taxable gain of each year (before the
    exemption). A year missing from `net_by_year` counts as no gain."""
    carries: list[list] = []              # [year created, amount left]
    out = []
    for y in range(rules.first_year, until + 1):
        expired = sum(c[1] for c in carries if y - c[0] > rules.carry_years)
        carries = [c for c in carries if y - c[0] <= rules.carry_years and c[1] > 1e-9]
        base = rules.base_for(y)
        carried_in = [(c[0], c[1]) for c in carries]
        net = float(net_by_year.get(y, 0.0) or 0.0)
        used = min(max(net, 0.0), base + sum(c[1] for c in carries))
        left, used_carry = used, 0.0
        for c in carries:                 # carried amounts first, oldest first
            t = min(c[1], left)
            c[1] -= t
            left -= t
            used_carry += t
        used_base = left
        if rules.carry_rule == "none_used":
            carry = rules.carry_per_year if net <= 0 else 0.0
        else:
            carry = min(rules.carry_per_year, max(0.0, base - used_base))
        carries = [c for c in carries if c[1] > 1e-9]
        if carry > 0:
            carries.append([y, carry])
        out.append(ExemptionYear(y, base, carried_in, net, used, used_carry, used_base, carry, expired))
    return out


def summarize_people(lines, rules, household: Household, exemptions: dict[str, float],
                     extra: dict[str, dict[str, float]] | None = None) -> dict:
    """Meerwaardebelasting per person (their share of every line, plus `extra` gains per person and law type), and the
    total. Keys per person and for the total: per_type (sold, open, gains, losses), net, exemption, taxable, tax, room."""
    people = {}
    for pid in household.ids:
        per = {k: {"sold": 0.0, "open": 0.0, "gains": 0.0, "losses": 0.0} for k in LAW_TYPES}
        for l in lines:
            sh = household.share(l.broker, l.asset).get(pid, 0.0)
            if not sh:
                continue
            g = l.taxable_gain * sh
            per[l.law_type]["sold" if l.status == "Sold" else "open"] += g
            per[l.law_type]["gains" if g >= 0 else "losses"] += g
        for k, v in ((extra or {}).get(pid) or {}).items():
            if v:
                per[k]["sold"] += v
                per[k]["gains" if v >= 0 else "losses"] += v
        net = sum(v["sold"] + v["open"] for v in per.values())
        ex = float(exemptions.get(pid, rules.exemption))
        taxable = max(0.0, net - ex)
        people[pid] = dict(per_type=per, net=net, exemption=ex, taxable=taxable, tax=rules.rate * taxable, room=ex - net)
    total_per = {k: {f: sum(people[p]["per_type"][k][f] for p in people) for f in ("sold", "open", "gains", "losses")}
                 for k in LAW_TYPES}
    return {"per_type": total_per, "net": sum(p["net"] for p in people.values()),
            "exemption": sum(p["exemption"] for p in people.values()),
            "taxable": sum(p["taxable"] for p in people.values()), "tax": sum(p["tax"] for p in people.values()),
            "room": sum(p["room"] for p in people.values()), "people": people}
