"""Savings accounts and pension savings (backlog item 22, docs/decisions.md D39).

Stored in `<data dir>/savings.json`:
{"accounts": [{"name": "Spaarrekening", "bank": "Bank", "regulated": true, "owner": "you" | "partner" | "joint",
               "balance": 0.0, "interest": {"2026": 0.0}}],
 "pension": [{"name": "Pensioenspaarfonds", "provider": "Bank", "person": "you", "option": 1050 | 1350,
              "birth_year": 1985, "start_year": 2015, "value": 0.0, "contributions": {"2025": 1050.0}}]}

Rules (sources in docs/domain/belgian-taxes.md, checked 15 October 2026):
- regulated savings: interest exempt up to €1,020 per person (income 2024–2026; €2,040 for a joint account of
  partners = half each), 15% above it; non-regulated accounts: 30% from the first euro;
- pension savings: reduction 30% on up to €1,050, or 25% on up to €1,350 (the higher option must be asked for every
  year; between €1,050 and €1,260 it gives less than the basic one); at 60 (or the 10th anniversary for contracts
  started after 55) 8% on the contributions plus a notional 4.75% a year; contributions after that pay no 8%.
Neither is part of the meerwaardebelasting.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SAVINGS_EXEMPTION = {2024: 1020.0, 2025: 1020.0, 2026: 1020.0}
SAVINGS_RATE, OTHER_RATE = 0.15, 0.30
PENSION = {1050: (1050.0, 0.30), 1350: (1350.0, 0.25)}
LEVY_RATE, NOTIONAL = 0.08, 0.0475


def exemption_for(year: int) -> float:
    known = [y for y in SAVINGS_EXEMPTION if y <= year]
    return SAVINGS_EXEMPTION[max(known)] if known else SAVINGS_EXEMPTION[min(SAVINGS_EXEMPTION)]


@dataclass
class Account:
    name: str
    bank: str = ""
    regulated: bool = True
    owner: str = "you"                       # person id, or "joint"
    balance: float = 0.0
    interest: dict = field(default_factory=dict)   # year (str) -> interest received (€)


@dataclass
class Pension:
    name: str
    provider: str = ""
    person: str = "you"
    option: int = 1050
    birth_year: int | None = None
    start_year: int | None = None
    value: float = 0.0
    contributions: dict = field(default_factory=dict)   # year (str) -> € paid in


@dataclass
class SavingsBook:
    accounts: list = field(default_factory=list)
    pension: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"accounts": [a.__dict__ for a in self.accounts], "pension": [p.__dict__ for p in self.pension]}

    @classmethod
    def from_dict(cls, d: dict | None) -> "SavingsBook":
        d = d or {}
        acc = [Account(**{k: v for k, v in a.items() if k in Account.__dataclass_fields__}) for a in d.get("accounts") or []]
        pen = [Pension(**{k: v for k, v in p.items() if k in Pension.__dataclass_fields__}) for p in d.get("pension") or []]
        return cls(acc, pen)

    @classmethod
    def load(cls, path) -> "SavingsBook":
        p = Path(path) if path else None
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8") or "{}")) if p and p.exists() else cls()

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


@dataclass
class SavingsTax:
    person: str
    year: int
    regulated: float
    exempt: float
    taxable_15: float
    other: float              # non-regulated interest, 30%

    @property
    def tax(self) -> float:
        return round(SAVINGS_RATE * self.taxable_15 + OTHER_RATE * self.other, 2)


def savings_tax(book: SavingsBook, persons: list[str], year: int) -> dict[str, SavingsTax]:
    reg = {p: 0.0 for p in persons}
    other = {p: 0.0 for p in persons}
    for a in book.accounts:
        v = float((a.interest or {}).get(str(year), 0.0) or 0.0)
        owners = persons if a.owner == "joint" else [a.owner if a.owner in persons else persons[0]]
        for p in owners:
            (reg if a.regulated else other)[p] += v / len(owners)
    ex = exemption_for(year)
    return {p: SavingsTax(p, year, round(reg[p], 2), round(min(reg[p], ex), 2), round(max(0.0, reg[p] - ex), 2),
                          round(other[p], 2)) for p in persons}


@dataclass
class PensionYear:
    contract: str
    person: str
    year: int
    paid: float
    reduction: float
    note: str = ""


def pension_reduction(p: Pension, year: int) -> PensionYear:
    paid = float((p.contributions or {}).get(str(year), 0.0) or 0.0)
    cap, rate = PENSION.get(int(p.option or 1050), PENSION[1050])
    red = round(rate * min(paid, cap), 2)
    note = ""
    if int(p.option or 1050) == 1350 and 1050 < paid < 1260:
        note = (f"Between €1,050 and €1,260 the 25% option gives less (€{red:,.2f}) than paying €1,050 at 30% "
                f"(€{0.30 * 1050:,.2f}).")
    elif int(p.option or 1050) == 1050 and paid > 1050:
        note = "Paid above €1,050 without asking for the €1,350 option: the extra gives no reduction."
    if p.birth_year and year > p.birth_year + 64:
        note = (note + " " if note else "") + "Contributions after the year you turn 64 give no reduction."
        red = 0.0
    return PensionYear(p.name, p.person, year, paid, red, note)


def advance_levy(p: Pension) -> tuple[int | None, float]:
    """(year due, estimated 8% levy): contributions until then plus 4.75% a year notional return."""
    if not p.birth_year:
        return None, 0.0
    due = p.birth_year + 60
    if p.start_year and p.start_year > p.birth_year + 55:
        due = p.start_year + 10
    base = 0.0
    for y, v in (p.contributions or {}).items():
        y = int(y)
        if y <= due:
            base += float(v or 0.0) * (1 + NOTIONAL) ** max(0, due - y)
    return due, round(LEVY_RATE * base, 2)
