"""Allocation look-through (synthetic data, no network)."""
from datetime import date, datetime

import pytest

from meerwaarde.engine.allocation import allocation, exposure_for
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.models import BUY, Holding, Lot, Transaction
from meerwaarde.prices import PriceBook
from meerwaarde.prices.fetch import parse_justetf_profile
from meerwaarde.report import Report

TODAY = date(2026, 10, 14)

PAGE = """<div data-testid="etf-holdings_top-holdings_container">
<span data-testid="tl_etf-holdings_top-holdings_weight">30.00%</span><div data-testid="tl_etf-holdings_top-holdings_count">out of 1,200</div>
<table><tr data-testid="etf-holdings_top-holdings_row"><td><a href="/en/stock-profiles/XX0000000101" title="Alpha &amp; Co"><span>Alpha &amp; Co</span></a></td>
<td><span data-testid="tl_etf-holdings_top-holdings_value_percentage">5.00%</span></td></tr>
<tr data-testid="etf-holdings_top-holdings_row"><td><a href="/en/stock-profiles/XX0000000102" title="Beta"><span>Beta</span></a></td>
<td><span data-testid="tl_etf-holdings_top-holdings_value_percentage">3.00%</span></td></tr></table>
<table><tr data-testid="etf-holdings_countries_row"><td data-testid="tl_etf-holdings_countries_value_name">United States</td>
<td><span data-testid="tl_etf-holdings_countries_value_percentage">60.00%</span></td></tr>
<tr data-testid="etf-holdings_countries_row"><td data-testid="tl_etf-holdings_countries_value_name">Japan</td>
<td><span data-testid="tl_etf-holdings_countries_value_percentage">10.00%</span></td></tr>
<tr data-testid="etf-holdings_countries_row"><td data-testid="tl_etf-holdings_countries_value_name">Other</td>
<td><span data-testid="tl_etf-holdings_countries_value_percentage">30.00%</span></td></tr></table>
<table><tr data-testid="etf-holdings_sectors_row"><td data-testid="tl_etf-holdings_sectors_value_name">Technology</td>
<td><span data-testid="tl_etf-holdings_sectors_value_percentage">40.00%</span></td></tr>
<tr data-testid="etf-holdings_sectors_row"><td data-testid="tl_etf-holdings_sectors_value_name">Other</td>
<td><span data-testid="tl_etf-holdings_sectors_value_percentage">60.00%</span></td></tr></table>
<div data-testid="tl_etf-holdings_reference-date">As of 31/08/2026</div></div>"""


def test_parse_justetf_profile():
    d = parse_justetf_profile(PAGE)
    assert d["top"] == [("XX0000000101", "Alpha & Co", 0.05), ("XX0000000102", "Beta", 0.03)]
    assert d["countries"] == {"United States": 0.6, "Japan": 0.1, "Other": 0.3}
    assert d["sectors"] == {"Technology": 0.4, "Other": 0.6}
    assert (d["holdings"], d["top_weight"], d["as_of"]) == (1200, 0.3, "31/08/2026")
    assert parse_justetf_profile("<html>no fund</html>") is None


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def justetf_profile(self, isin):
        if isin not in self.pages:
            raise OSError("not found")
        return parse_justetf_profile(self.pages[isin])


def holding(asset, cls, name="X"):
    return Holding("B", asset, name, cls, [Lot(datetime(2025, 1, 1), 1, 1, 1)])


def test_exposure_sources_by_class_and_override():
    f = FakeFetcher({"XX0000000001": PAGE})
    etf = exposure_for(holding("XX0000000001", "ETF"), f)
    assert etf.source == "justETF" and etf.countries["United States"] == pytest.approx(0.6)
    assert etf.currencies == {"USD": pytest.approx(0.6), "JPY": pytest.approx(0.1), "Other": pytest.approx(0.3)}
    share = exposure_for(holding("BE0000000001", "Stock", "Some Belgian share"), None)
    assert share.countries == {"Belgium": 1.0} and share.currencies == {"EUR": 1.0} and share.top == [("BE0000000001", "Some Belgian share", 1.0)]
    assert exposure_for(holding("USD", "Cash/FX"), None).currencies == {"USD": 1.0}
    assert exposure_for(holding("BTC", "Crypto"), None).asset_class == {"Crypto": 1.0}
    errors = []
    missing = exposure_for(holding("XX0000000009", "ETF"), f, errors=errors)
    assert missing.source == "no data" and errors
    own = exposure_for(holding("XX0000000009", "Fund"), f, {"XX0000000009": {"asset_class": {"Equity": 60, "Bonds": 40},
                                                                            "countries": {"Germany": 1}}})
    assert own.source == "your breakdown" and own.asset_class == {"Equity": 0.6, "Bonds": 0.4} and own.currencies == {"EUR": 1.0}


def make(txns, prices):
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=TODAY)
    for a, v in prices.items():
        pb.set_override(a, now=v)
    return Report(pf, pb, TaxCalculator(pf, pb, TaxRules(), today=TODAY), pf.warnings)


def tx(asset, qty, gross, cls="ETF", name=None):
    return Transaction("B", datetime(2025, 1, 1), BUY, asset, name=name or f"Fund {asset}", asset_class=cls, qty=qty,
                       gross_eur=gross, total_eur=-gross)


def test_portfolio_allocation_overlap_and_look_through():
    page2 = PAGE.replace("XX0000000102", "XX0000000103").replace("5.00%</span></td></tr>\n<tr", "2.00%</span></td></tr>\n<tr", 1)
    rep = make([tx("XX0000000001", 10, 1000), tx("XX0000000002", 10, 1000, name="Second fund"),
                tx("XX0000000101", 5, 500, cls="Stock", name="Alpha & Co"), tx("BTC", 1, 100, cls="Crypto")],
               {"XX0000000001": 100.0, "XX0000000002": 100.0, "XX0000000101": 100.0, "BTC": 1000.0})
    rep.prices.fetcher = FakeFetcher({"XX0000000001": PAGE, "XX0000000002": page2})
    al = allocation(rep)
    assert al.total == pytest.approx(4500)
    assert al.totals["asset_class"] == {"Equity": pytest.approx(3500), "Crypto": pytest.approx(1000)}
    assert al.totals["region"]["North America"] == pytest.approx(0.6 * 2000)
    # Alpha: 5% of fund 1 (€50) + 2% of fund 2 (€20) + €500 held directly
    alpha = next(t for t in al.top if t[0] == "XX0000000101")
    assert alpha[2] == pytest.approx(570) and alpha[3] == ["Fund XX0000000001", "Second fund", "direct"]
    a, b, share, n = al.overlap[0]
    assert {a, b} == {"Fund XX0000000001", "Second fund"} and share == pytest.approx(0.02) and n == 1


def test_offline_allocation_marks_funds_unknown():
    rep = make([tx("XX0000000001", 10, 1000)], {"XX0000000001": 100.0})
    al = allocation(rep)
    assert al.totals["country"] == {"Unknown": pytest.approx(1000)} and any("no composition data" in n for n in al.notes)
