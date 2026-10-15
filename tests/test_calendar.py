"""Dividend and cash-flow calendar (synthetic data)."""
import io
from datetime import date, datetime

import pytest

from meerwaarde.engine.calendar import calendar
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.models import BUY, DIVIDEND, SELL, WITHHOLDING, Transaction
from meerwaarde.prices import PriceBook
from meerwaarde.report import Report

TODAY = date(2026, 10, 14)


def tx(when, kind, asset, **kw):
    return Transaction("A", datetime.combine(when, datetime.min.time()), kind, asset, name=asset, **kw)


def make(txns, now=None):
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=TODAY)
    for a, v in (now or {}).items():
        pb.set_override(a, now=v)
    return Report(pf, pb, TaxCalculator(pf, pb, TaxRules(), today=TODAY), pf.warnings)


def test_projects_last_years_payments_for_the_units_held_now():
    txns = [tx(date(2025, 1, 2), BUY, "US0000000001", qty=10, gross_eur=1000, total_eur=-1000, asset_class="Stock"),
            tx(date(2025, 11, 20), DIVIDEND, "US0000000001", amount_eur=10.0, asset_class="Stock"),
            tx(date(2025, 11, 20), WITHHOLDING, "US0000000001", amount_eur=-1.5, asset_class="Stock"),
            tx(date(2026, 3, 1), BUY, "US0000000001", qty=10, gross_eur=1000, total_eur=-1000, asset_class="Stock"),
            tx(date(2025, 1, 2), BUY, "BE0000000001", qty=5, gross_eur=500, total_eur=-500, asset_class="Stock"),
            tx(date(2025, 12, 1), DIVIDEND, "BE0000000001", amount_eur=7.0, asset_class="Stock"),
            tx(date(2025, 1, 2), BUY, "XX0000000003", qty=5, gross_eur=500, total_eur=-500, asset_class="ETF"),
            tx(date(2025, 12, 5), DIVIDEND, "XX0000000003", amount_eur=4.0, asset_class="ETF"),
            tx(date(2026, 1, 5), SELL, "XX0000000003", qty=-5, gross_eur=520, total_eur=520, asset_class="ETF"),
            tx(date(2025, 6, 1), DIVIDEND, "US0000000001", amount_eur=99.0, asset_class="Stock")]   # > 12 months before the projection window? no: still in the last year
    c = calendar(make(txns))
    us = [e for e in c.expected if e.asset == "US0000000001"]
    assert [(e.on, e.gross, e.foreign_tax) for e in us] == [(date(2026, 11, 20), 20.0, -3.0)]   # units doubled since
    assert us[0].be_tax == pytest.approx(0.3 * 17.0)
    be = next(e for e in c.expected if e.asset == "BE0000000001")
    assert be.be_tax == 0.0                                       # Belgian withholding already at source
    assert all(e.asset != "XX0000000003" for e in c.expected)     # sold since
    assert any("Tax on the dividends still expected in 2026" in d[0] for d in c.due)


def test_calendar_sheet():
    from openpyxl import load_workbook
    from meerwaarde.export import to_xlsx
    txns = [tx(date(2025, 1, 2), BUY, "US0000000001", qty=10, gross_eur=1000, total_eur=-1000, asset_class="Stock"),
            tx(date(2025, 11, 20), DIVIDEND, "US0000000001", amount_eur=10.0, asset_class="Stock")]
    ws = load_workbook(io.BytesIO(to_xlsx(make(txns, {"US0000000001": 100.0}))))["Calendar"]
    assert ws["A1"].value == "Expected on" and ws["F2"].value.startswith("=")
