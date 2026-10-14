from datetime import datetime

import pytest

from meerwaarde.engine.portfolio import Portfolio, PortfolioOptions
from meerwaarde.models import Transaction, BUY, SELL, REWARD, DEPOSIT, WITHDRAWAL, DIVIDEND


def tx(day, kind, asset, qty, total=None, gross=None, fees=0.0, cls="ETF", broker="B", **kw):
    return Transaction(broker=broker, ts=datetime(2024, 1, day), kind=kind, asset=asset, name=asset, asset_class=cls,
                       qty=qty, total_eur=total, gross_eur=gross, fees_eur=fees, **kw)


def test_fifo_realized_profit_includes_costs():
    p = Portfolio([
        tx(1, BUY, "X", 10, total=-101, gross=100, fees=1),
        tx(2, BUY, "X", 10, total=-201, gross=200, fees=1),
        tx(3, SELL, "X", -15, total=449, gross=450, fees=1),
    ])
    (r,) = p.realized
    assert r.cost_eur == pytest.approx(101 + 100.5)      # 10 from lot 1, 5 from lot 2
    assert r.pnl_eur == pytest.approx(449 - 201.5)
    (h,) = p.holdings()
    assert h.qty == pytest.approx(5)
    assert h.cost_eur == pytest.approx(100.5)


def test_own_wallet_withdrawal_and_return_keep_purchase_price():
    p = Portfolio([
        tx(1, BUY, "BTC", 1.0, total=-30000, gross=30000, cls="Crypto"),
        tx(2, WITHDRAWAL, "BTC", -1.0, cls="Crypto", fee_qty=0.0001),
        tx(3, DEPOSIT, "BTC", 0.9999, cls="Crypto"),
        tx(4, SELL, "BTC", -0.5, total=40000, gross=40000, cls="Crypto"),
    ])
    sales = [r for r in p.realized if r.note != "Network fee"]
    assert sales[0].cost_eur == pytest.approx(15000)
    assert not [w for w in p.warnings if "no purchase price" in w]


def test_unmatched_deposit_counts_at_zero_cost_with_a_note():
    p = Portfolio([tx(1, DEPOSIT, "ETH", 2.0, cls="Crypto"), tx(2, SELL, "ETH", -2.0, total=5000, gross=5000, cls="Crypto")])
    assert p.realized[0].cost_eur == 0
    assert any("no purchase price" in w for w in p.warnings)


def test_rewards_are_free_lots():
    p = Portfolio([tx(1, REWARD, "ADA", 10, cls="Crypto")])
    (h,) = p.holdings()
    assert h.cost_eur == 0 and h.lots[0].gross_eur_unit is None


def test_same_day_isin_swap_is_a_conversion():
    p = Portfolio([
        tx(1, BUY, "OLD", 100, total=-1000, gross=1000),
        tx(5, SELL, "OLD", -100, total=2000, gross=2000),
        tx(5, BUY, "NEW", 100, total=-2000, gross=2000),
    ])
    assert p.realized == []
    (h,) = p.holdings()
    assert h.asset == "NEW" and h.cost_eur == pytest.approx(1000)


def test_bond_redeemed_at_par():
    p = Portfolio([
        tx(1, BUY, "DE0001", 25000, total=-24096, gross=24093, fees=3, cls="Bond"),
        tx(9, SELL, "DE0001", -25000, total=0.0, gross=0.0, cls="Bond"),
    ])
    assert p.realized[0].pnl_eur == pytest.approx(904)


def test_dividends_are_income():
    p = Portfolio([tx(1, DIVIDEND, "X", 0, amount_eur=12.5)])
    assert p.income[0].amount_eur == 12.5
