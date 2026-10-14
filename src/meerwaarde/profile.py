"""Your tax profile: people, ownership, and per-year inputs that aren't in the broker exports.

Stored as JSON (the app keeps it in `<data dir>/profile.json`; the CLI takes `--profile FILE`):

{
  "persons": [{"id": "you", "name": "You"}, {"id": "partner", "name": "Partner"}],
  "ownership": {"brokers": {"LYNX": {"you": 0.5, "partner": 0.5}}, "positions": {"DEGIRO|IE00...": {"you": 1}}},
  "carry_rule": "partial",
  "years": {"2026": {"exemption_base": 10000, "dividend_exemption": 833,
                      "other_gains": {"you": {"fin": 0, "crypto": 0}},
                      "declared_net": {"you": null},
                      "other_income": {"you": {"dividends_shares": 0, "dividends_funds": 0, "interest": 0,
                                               "foreign_tax": 0}}}}
}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .engine.household import ExemptionRules, Household, Person

INCOME_FIELDS = ("dividends_shares", "dividends_funds", "interest", "foreign_tax")


@dataclass
class YearInput:
    exemption_base: float | None = None          # meerwaardebelasting exemption of that year (indexed amount)
    dividend_exemption: float | None = None      # dividend exemption of that year
    other_gains: dict = field(default_factory=dict)    # person -> law type -> taxable gain of sales not in the exports
    declared_net: dict = field(default_factory=dict)   # person -> net taxable gain as declared (overrides the computed)
    other_income: dict = field(default_factory=dict)   # person -> INCOME_FIELDS -> amount (EUR) not in the exports

    def gains_for(self, pid: str) -> dict[str, float]:
        return {k: float(v) for k, v in (self.other_gains.get(pid) or {}).items() if v}

    def income_for(self, pid: str) -> dict[str, float]:
        d = self.other_income.get(pid) or {}
        return {k: float(d.get(k) or 0.0) for k in INCOME_FIELDS}

    def declared_for(self, pid: str) -> float | None:
        v = self.declared_net.get(pid)
        return None if v is None or v == "" else float(v)

    def is_empty(self) -> bool:
        return (self.exemption_base is None and self.dividend_exemption is None and not any(self.other_gains.values())
                and not any(v is not None for v in self.declared_net.values())
                and not any(any(d.values()) for d in self.other_income.values()))


@dataclass
class TaxProfile:
    household: Household = field(default_factory=Household)
    years: dict[int, YearInput] = field(default_factory=dict)
    carry_rule: str = "partial"

    def year(self, y: int) -> YearInput:
        return self.years.setdefault(int(y), YearInput())

    def get(self, y: int) -> YearInput:
        return self.years.get(int(y)) or YearInput()

    def exemption_rules(self, override: dict[int, float] | None = None) -> ExemptionRules:
        r = ExemptionRules(carry_rule=self.carry_rule)
        for y, yi in self.years.items():
            if yi.exemption_base is not None:
                r.base[int(y)] = float(yi.exemption_base)
        for y, v in (override or {}).items():
            r.base[int(y)] = float(v)
        return r

    # ---------- partner ----------
    def set_partner(self, name: str | None):
        """Add (or rename) the partner, or remove them with name=None."""
        you = self.household.persons[0]
        if name:
            self.household.persons = [you, Person("partner", name)]
        else:
            self.household.persons = [you]
            for d in list(self.household.brokers.values()) + list(self.household.positions.values()):
                d.pop("partner", None)

    # ---------- JSON ----------
    def to_dict(self) -> dict:
        return {
            "persons": [{"id": p.id, "name": p.name} for p in self.household.persons],
            "ownership": {"brokers": self.household.brokers, "positions": self.household.positions},
            "carry_rule": self.carry_rule,
            "years": {str(y): {k: v for k, v in yi.__dict__.items() if v not in (None, {}, [])}
                      for y, yi in sorted(self.years.items()) if not yi.is_empty()},
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "TaxProfile":
        d = d or {}
        persons = [Person(str(p["id"]), str(p.get("name") or p["id"])) for p in d.get("persons") or []] or [Person("you", "You")]
        own = d.get("ownership") or {}
        hh = Household(persons, {k: dict(v) for k, v in (own.get("brokers") or {}).items()},
                       {k: dict(v) for k, v in (own.get("positions") or {}).items()})
        years = {}
        for y, v in (d.get("years") or {}).items():
            years[int(y)] = YearInput(v.get("exemption_base"), v.get("dividend_exemption"), dict(v.get("other_gains") or {}),
                                      dict(v.get("declared_net") or {}), dict(v.get("other_income") or {}))
        return cls(hh, years, d.get("carry_rule") or "partial")

    @classmethod
    def load(cls, path: str | Path | None) -> "TaxProfile":
        if not path or not Path(path).exists():
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text() or "{}"))

    def save(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))
