"""Performance against a benchmark (synthetic data, no network)."""
from datetime import date, datetime, timedelta

import pytest

from meerwaarde.engine.performance import (investor_flows, month_ends, performance, units_over_time, xirr)
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.models import BUY, DIVIDEND, SELL, WITHDRAWAL, Transaction
from meerwaarde.prices import PriceBook
from meerwaarde.prices.fetch import parse_bitvavo_daily, parse_justetf_series, parse_yahoo_series
from meerwaarde.report import Report

TODAY = date(2026, 1, 1)


def tx(when, kind, asset, qty=0.0, total=None, gross=None, cls="ETF", broker="A", **kw):
    return Transaction(broker=broker, ts=datetime.combine(when, datetime.min.time()), kind=kind, asset=asset,
                       name=f"Fund {asset}", asset_class=cls, qty=qty, total_eur=total, gross_eur=gross, fees_eur=0.0,
                       source="x.pdf", **kw)


def make(txns, now: dict, history: dict | None = None, today=TODAY):
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=today)
    for a, v in now.items():
        pb.set_override(a, now=v)
    history = history or {}
    pb.history = lambda asset, cls, start, end: (history.get(asset, {}), "test" if asset in history else "")
    return Report(pf, pb, TaxCalculator(pf, pb, TaxRules(), today=today), pf.warnings)


def test_xirr_simple_and_undefined_cases():
    assert xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 1100)]) == pytest.approx(0.10, abs=1e-6)
    assert xirr([(date(2025, 1, 1), -1000), (date(2025, 7, 2), -1000), (date(2026, 1, 1), 2000)]) == pytest.approx(0.0, abs=1e-6)
    assert xirr([(date(2025, 1, 1), -1000), (date(2026, 1, 1), 100)]) == pytest.approx(-0.9, abs=1e-6)
    assert xirr([(date(2025, 1, 1), -1000)]) is None


def test_flows_from_trades_income_and_fees():
    txns = [tx(date(2025, 1, 1), BUY, "X", 10, -1000, 1000), tx(date(2025, 6, 1), SELL, "X", -5, 600, 600),
            tx(date(2025, 7, 1), DIVIDEND, "X", amount_eur=12.0)]
    flows = investor_flows(Portfolio(txns))
    assert [(f.on, f.amount, f.what) for f in flows] == [(date(2025, 1, 1), -1000, BUY), (date(2025, 6, 1), 600, SELL),
                                                       (date(2025, 7, 1), 12.0, DIVIDEND)]


def test_cash_positions_and_own_wallet_moves_are_not_flows():
    txns = [tx(date(2025, 1, 1), BUY, "BTC", 1, -100, 100, cls="Crypto"),
            tx(date(2025, 2, 1), WITHDRAWAL, "BTC", -1, cls="Crypto"),
            tx(date(2025, 3, 1), BUY, "USD", 100, -90, 90, cls="Cash/FX")]
    pf = Portfolio(txns)
    assert [f.amount for f in investor_flows(pf)] == [-100]
    steps = units_over_time(pf)
    assert steps[("A", "BTC")][-1] == (date(2025, 2, 1), 1)       # still yours on the Ledger
    assert ("A", "USD") not in steps


def test_one_buy_that_doubles():
    rep = make([tx(date(2025, 1, 1), BUY, "X", 10, -1000, 1000)], now={"X": 200.0},
               history={"X": {date(2024, 12, 31) + timedelta(days=i): 100 + i * 100 / 366 for i in range(367)}})
    perf = performance(rep, benchmark=("BENCH", "Bench"))
    all_ = perf.line("All")
    assert all_.value == pytest.approx(2000) and all_.invested == 1000 and all_.gain == pytest.approx(1000)
    assert all_.xirr == pytest.approx(1.0, rel=1e-3)
    assert all_.twr == pytest.approx(1.0, rel=1e-3) and all_.twr_annual == pytest.approx(1.0, rel=1e-2)
    assert perf.points["All"][-1].on == TODAY and len(perf.points["All"]) == 13
    assert any("benchmark" in n for n in perf.notes)               # no benchmark history: noted


def test_twr_ignores_the_timing_of_deposits_xirr_does_not():
    # price 100 -> 50 -> 100; buying more at 50 makes money (XIRR > 0) but the fund itself ended flat (TWR = 0)
    hist = {date(2025, 1, 1): 100.0, date(2025, 6, 30): 50.0, date(2025, 12, 31): 100.0}
    txns = [tx(date(2025, 1, 1), BUY, "X", 10, -1000, 1000), tx(date(2025, 6, 30), BUY, "X", 20, -1000, 1000)]
    perf = performance(make(txns, now={"X": 100.0}, history={"X": hist}), benchmark=("B", "B"))
    l = perf.line("All")
    assert l.value == pytest.approx(3000)
    assert l.twr == pytest.approx(0.0, abs=0.02)
    assert l.xirr > 0.3


def test_benchmark_gets_the_same_flows():
    hist = {date(2025, 1, 1): 100.0, date(2025, 12, 31): 100.0}
    bench = {date(2025, 1, 1): 10.0, date(2025, 6, 1): 12.0, date(2025, 12, 31): 15.0}
    txns = [tx(date(2025, 1, 1), BUY, "X", 10, -1000, 1000), tx(date(2025, 6, 1), SELL, "X", -5, 500, 500)]
    perf = performance(make(txns, now={"X": 100.0}, history={"X": hist, "B": bench}), benchmark=("B", "Bench"))
    l = perf.line("All")
    # benchmark: 100 units at 10, 41.67 sold at 12 for the €500 taken out, 58.33 left at 15
    assert l.benchmark_value == pytest.approx((100 - 500 / 12) * 15)
    assert l.benchmark_xirr > l.xirr                                 # the benchmark rose, the fund was flat
    assert perf.points["All"][-1].benchmark == pytest.approx(l.benchmark_value)


def test_per_broker_lines_and_fallback_to_trade_prices():
    txns = [tx(date(2025, 1, 1), BUY, "X", 10, -1000, 1000), tx(date(2025, 3, 1), BUY, "Y", 5, -500, 500, broker="B")]
    perf = performance(make(txns, now={"X": 110.0, "Y": 100.0}), benchmark=("Z", "Z"))
    assert [l.broker for l in perf.lines] == ["All", "A", "B"]
    assert perf.line("A").value == pytest.approx(1100) and perf.line("B").value == pytest.approx(500)
    assert perf.points["A"][1].value == pytest.approx(1000)          # end of January at the purchase price
    assert any("last trade price" in n for n in perf.notes)


def test_month_ends():
    assert month_ends(date(2025, 11, 15), date(2026, 1, 10)) == [date(2025, 11, 30), date(2025, 12, 31), date(2026, 1, 10)]


def test_series_parsers():
    assert parse_justetf_series({"series": [{"date": "2025-01-02", "value": {"raw": 10.5}}]}) == {date(2025, 1, 2): 10.5}
    ms = int(datetime(2025, 1, 2).timestamp() * 1000) + 3600_000
    assert list(parse_bitvavo_daily([[ms, "1", "1", "1", "42.0", "1"]]).values()) == [42.0]
    y = {"chart": {"result": [{"timestamp": [1735776000, 1735862400], "meta": {"currency": "USD"},
                               "indicators": {"quote": [{"close": [1.0, None]}]}}]}}
    assert parse_yahoo_series(y) == ({date(2025, 1, 2): 1.0}, "USD")
    assert parse_yahoo_series({}) is None
