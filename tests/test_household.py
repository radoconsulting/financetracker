"""Exemption carry-forward, ownership shares and per-person tax (synthetic data)."""
from datetime import date

import pytest

from meerwaarde.engine.household import ExemptionRules, Household, Person, exemption_schedule, summarize_people
from meerwaarde.engine.tax import TaxLine, TaxRules
from meerwaarde.profile import TaxProfile

R = TaxRules()
DAY = date(2026, 6, 1)


def line(broker, gain, status="Sold", lt="fin", asset="A"):
    return TaxLine(broker, asset, asset, lt, status, DAY, 1, 1, 1, None, 0, 0, gain)


def test_unused_exemption_adds_at_most_1000_a_year():
    sched = exemption_schedule({2026: 0.0, 2027: 0.0, 2028: 10500.0}, ExemptionRules(), 2028)
    assert [e.available for e in sched] == [10000, 11000, 12000]
    assert sched[2].used == 10500 and sched[2].used_carry == 2000      # carried amounts are used first
    assert sched[2].carry_out == pytest.approx(1000)                   # 1,500 of the own 10,000 unused -> max 1,000


def test_partly_used_year_carries_the_unused_part():
    sched = exemption_schedule({2026: 9600.0}, ExemptionRules(), 2027)
    assert sched[0].carry_out == pytest.approx(400)
    assert sched[1].available == pytest.approx(10400)


def test_carry_forward_is_capped_at_15000_and_expires_after_five_years():
    sched = exemption_schedule({}, ExemptionRules(), 2033)
    by = {e.year: e for e in sched}
    assert by[2031].available == 15000                                  # 10,000 + 5 x 1,000
    assert by[2032].available == 15000 and by[2032].expired == 1000     # the 2026 amount ran out
    assert max(e.available for e in sched) == 15000


def test_strict_rule_needs_a_year_without_gain():
    rules = ExemptionRules(carry_rule="none_used")
    sched = exemption_schedule({2026: 500.0, 2027: 0.0}, rules, 2028)
    assert [e.carry_out for e in sched] == [0, 1000, 1000]


def test_indexed_base_from_the_year_it_is_known():
    rules = ExemptionRules(base={2026: 10000.0, 2028: 10400.0})
    assert [rules.base_for(y) for y in (2026, 2027, 2028, 2030)] == [10000, 10000, 10400, 10400]


def test_ownership_shares_default_to_the_first_person():
    hh = Household([Person("you", "You"), Person("partner", "P")], brokers={"B": {"you": 1, "partner": 1}},
                   positions={"C|X": {"partner": 1}})
    assert hh.share("A", "Z") == {"you": 1.0}
    assert hh.share("B", "Z") == {"you": 0.5, "partner": 0.5}
    assert hh.share("C", "X") == {"partner": 1.0}


def test_each_partner_has_their_own_exemption_and_losses_stay_personal():
    hh = Household([Person("you", "You"), Person("partner", "P")], brokers={"Joint": {"you": 0.5, "partner": 0.5}})
    lines = [line("Joint", 24000.0), line("Mine", -3000.0, lt="crypto", asset="C")]
    s = summarize_people(lines, R, hh, {"you": 10000, "partner": 10000})
    you, partner = s["people"]["you"], s["people"]["partner"]
    assert you["net"] == pytest.approx(12000 - 3000) and you["tax"] == 0
    assert partner["net"] == pytest.approx(12000) and partner["tax"] == pytest.approx(200)
    assert s["tax"] == pytest.approx(200)
    assert you["per_type"]["crypto"]["losses"] == pytest.approx(-3000)


def test_profile_round_trip(tmp_path):
    p = TaxProfile()
    p.set_partner("Sam")
    p.household.brokers["LYNX"] = {"you": 0.5, "partner": 0.5}
    p.year(2027).exemption_base = 10250
    p.year(2026).other_gains["partner"] = {"fin": 1200.0}
    p.year(2026).other_income["you"] = {"dividends_shares": 100.0}
    f = tmp_path / "profile.json"
    p.save(f)
    q = TaxProfile.load(f)
    assert [x.name for x in q.household.persons] == ["You", "Sam"]
    assert q.household.share("LYNX") == {"you": 0.5, "partner": 0.5}
    assert q.exemption_rules().base_for(2027) == 10250
    assert q.get(2026).gains_for("partner") == {"fin": 1200.0}
    assert q.get(2026).income_for("you")["dividends_shares"] == 100.0
    q.set_partner(None)
    assert q.household.single and q.household.share("LYNX") == {"you": 1.0}
