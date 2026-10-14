"""Year-end gain harvesting (synthetic data)."""
from datetime import date, datetime

import pytest

from meerwaarde.engine.harvest import CostModel, observed_tob_rate, plan_harvest
from meerwaarde.engine.household import Household, Person
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.models import BUY, SELL, TOB, Transaction
from meerwaarde.prices import PriceBook
from meerwaarde.profile import TaxProfile
from meerwaarde.report import Report

TODAY = date(2026, 11, 20)


def tx(when, kind, asset, qty=0.0, gross=None, cls="ETF", broker="DEGIRO", **kw):
    total = None if gross is None else (-gross if kind == BUY else gross)
    return Transaction(broker=broker, ts=when, kind=kind, asset=asset, name=f"Name {asset}", asset_class=cls, qty=qty,
                       gross_eur=gross, fees_eur=0.0, total_eur=total, source="x.pdf", **kw)


def make(extra=(), prices=None, profile=None):
    txns = [
        tx(datetime(2024, 1, 5), BUY, "ETF1", 1000, 40000),                      # 31-12-2025: 60, now 80 -> +20/unit
        tx(datetime(2024, 1, 5), TOB, "ETF1", amount_eur=-48.0, ref="separate"),  # 0.12% charged on that purchase
        tx(datetime(2024, 2, 1), BUY, "STK1", 100, 1000, cls="Stock"),            # 31-12-2025: 10, now 30 -> +20/unit
        tx(datetime(2024, 3, 1), BUY, "ACC1", 100, 10000),                        # 31-12-2025: 100, now 90 -> loss
        tx(datetime(2024, 3, 1), TOB, "ACC1", amount_eur=-132.0, ref="separate"), # 1.32%
        tx(datetime(2026, 3, 1), SELL, "ETF1", -150, 12000),                      # gain 150 x (80 - 60) = 3,000
        *extra,
    ]
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=TODAY)
    for a, now, ref in (("ETF1", 80.0, 60.0), ("STK1", 30.0, 10.0), ("ACC1", 90.0, 100.0)):
        pb.set_override(a, now=now, reference=ref)
    for a, (now, ref) in (prices or {}).items():
        pb.set_override(a, now=now, reference=ref)
    return Report(pf, pb, TaxCalculator(pf, pb, TaxRules(), today=TODAY), pf.warnings, profile or TaxProfile())


def test_observed_stock_exchange_tax_rate_is_snapped_to_a_legal_rate():
    rep = make()
    assert observed_tob_rate(rep.portfolio, "DEGIRO", "ETF1") == 0.0012
    assert observed_tob_rate(rep.portfolio, "DEGIRO", "ACC1") == 0.0132
    assert observed_tob_rate(rep.portfolio, "DEGIRO", "STK1") is None


def test_plan_fills_the_room_but_keeps_the_carry_forward():
    rep = make()
    plan = plan_harvest(rep, costs=CostModel(spread=0.0, commission={"DEGIRO": (0.0, 0.0)}))
    s = plan.status["you"]
    assert s["room"] == pytest.approx(7000) and s["reserve"] == 1000 and s["target"] == pytest.approx(6000)
    assert plan.gain == pytest.approx(6000, abs=20)
    # cheapest per euro of gain first: the ETF (0.12% x 2 on 80 per 20 of gain = 0.96%) beats the share
    # (0.35% x 2 on 30 per 20 = 1.05%), and it fills the whole target on its own
    assert [l.asset for l in plan.lines] == ["ETF1"] and plan.lines[0].qty == 300
    etf = plan.lines[0]
    assert etf.tob == pytest.approx(2 * 0.0012 * etf.value) and etf.tob_source.startswith("charged")
    assert all(l.asset != "ACC1" for l in plan.lines)                       # a loss position is never harvested
    assert plan.net == pytest.approx(0.10 * plan.gain - plan.costs)


def test_use_all_and_costs_that_eat_the_benefit():
    rep = make()
    assert plan_harvest(rep, keep_carry=False).status["you"]["target"] == pytest.approx(7000)
    pricey = plan_harvest(rep, costs=CostModel(spread=0.05))                 # 5% spread per trade: never worth it
    assert pricey.lines == [] and pricey.gain == 0


def test_losses_only_help_when_tax_is_due():
    rep = make()
    loss = next(l for l in plan_harvest(rep).losses if l.asset == "ACC1")
    assert loss.loss == pytest.approx(-1000) and loss.tax_saved_now == 0
    big = make(extra=[tx(datetime(2026, 6, 1), SELL, "ETF1", -600, 48000)])  # gain 3,000 + 12,000 > exemption
    loss = next(l for l in plan_harvest(big).losses if l.asset == "ACC1")
    assert loss.tax_saved_now == pytest.approx(100)


def test_joint_position_respects_both_rooms():
    prof = TaxProfile()
    prof.household = Household([Person("you", "You"), Person("partner", "P")],
                               positions={"DEGIRO|ETF1": {"you": 0.5, "partner": 0.5}})
    rep = make(profile=prof)
    plan = plan_harvest(rep, keep_carry=False, exclude={"STK1"})
    # the 2026 sale was joint: each has 10,000 - 1,500 left; ETF1 gain is split 50/50
    assert plan.status["you"]["room"] == pytest.approx(8500) and plan.status["partner"]["room"] == pytest.approx(8500)
    (etf,) = [l for l in plan.lines if l.asset == "ETF1"]
    assert etf.gain == pytest.approx(17000, abs=40)
