import io
import zipfile
from datetime import date

import pytest

from meerwaarde.parsers import degiro, lynx
from meerwaarde.parsers.classify import guess_class
from meerwaarde.prices import fetch


DEGIRO_TEXT = """
 Datum      Tijd    Product                       ISIN             Beurs                           Aantal          Koers     Lokale waarde
 12-10-2026 15:22   ISHRS MSCI CNTRY WRLD         IE00B6R52259     TDG         XGAT                   96    111,1600 EUR    -10.671,36 EUR -10.671,36    0,0000      0,00           -1,00          -10.672,36
                    UCITS ETF USD
 25-11-2024 16:03   EXAMPLE THERAPEUTICS, INC.    US0000000001     NDQ         CDED                  -378     4,1100 USD      1.553,58 USD    1.479,89   1,0498     -3,70           -2,00            1.474,19
 18-06-2026 10:46   VANGUARD FTSE ALL-            IE00BK5BQT80     XET         XETA                  -126   165,2000 EUR     20.815,20 EUR   20.815,20   0,0000      0,00                           20.815,20
"""


def test_degiro_lines():
    txns, warns = degiro.parse_text(DEGIRO_TEXT, "t.pdf")
    assert not warns and len(txns) == 3
    buy, sell_usd, sell_nofee = txns
    assert buy.kind == "buy" and buy.qty == 96 and buy.total_eur == pytest.approx(-10672.36) and buy.fees_eur == pytest.approx(1.0)
    assert "UCITS ETF USD" in buy.name and buy.asset_class == "ETF"
    assert sell_usd.kind == "sell" and sell_usd.gross_eur == pytest.approx(1479.89) and sell_usd.fees_eur == pytest.approx(5.70)
    assert sell_nofee.total_eur == pytest.approx(20815.20) and sell_nofee.fees_eur == 0


LYNX_LINES = """## Account Information
Base Currency EUR
## Net Asset Value
Starting Value 100.00
Dividends 8.80
Ending Value 120.00
## Mark-to-Market Performance Summary
Stocks
ABCD 10 10 50.0000 60.0000 100.00 0.00 0.00 0.00 100.00
Forex
USD 100.00 120.00 0.85000 0.90000 5.00 0.00 0.00 0.00 5.00
## Realized & Unrealized Performance Summary
Forex
USD 0.00 1.00 -3.00 0.00 0.00 -2.00 0.00 0.00 0.00 0.00 0.00 -2.00
## Open Positions
Stocks
USD
ABCD 12 1 50.5 606.00 60.0000 720.00 114.00
## Forex Balances
Forex
USD 120.00 0.86 -103.20 0.90000 108.00 4.80
## Trades
Stocks
USD
2026-01-16,
ABCD 2 55.0000 55.1000 -110.00 -1.00 111.00 0.00 0.20 O
04:50:43
Total ABCD 2 -110.00 -1.00 111.00 0.00 0.20
Total -110.00 -1.00 111.00 0.00 0.20
Total in EUR -99.00 -0.90 99.90 0.00 0.18
## Dividends
USD
ABCD(US0000000002) Cash Dividend USD 1.00 per Share
2026-03-31 10.00
Total 10.00
Total in EUR 8.80
## Financial Instrument Information
Stocks
ABCD EXAMPLE CORP 12345678 US0000000002 ABCD NASDAQ 1 COMMON
"""


def test_lynx_lines():
    lines = ["Activity Statement", "January 1, 2026 - October 13, 2026"] + LYNX_LINES.strip().split("\n")
    txns, info, warns = lynx.parse_lines(lines, "s.pdf")
    assert info.period_start == date(2026, 1, 1) and info.period_end == date(2026, 10, 13)
    assert info.fx_prior["USD"] == 0.85 and info.prior_prices["ABCD"] == 50.0
    buy = [t for t in txns if t.kind == "buy"][0]
    assert buy.asset == "US0000000002" and buy.asset_class == "Stock"
    assert buy.gross_eur == pytest.approx(99.0) and buy.fees_eur == pytest.approx(0.9)
    div = [t for t in txns if t.kind == "dividend"][0]
    assert div.amount_eur == pytest.approx(8.8)
    fx = [t for t in txns if t.kind == "fx_result"][0]
    assert fx.amount_eur == -2.0
    assert info.forex["USD"]["qty"] == 120.0 and info.positions["ABCD"]["value_eur"] == pytest.approx(720 * 0.9)


@pytest.mark.parametrize("name,cls", [
    ("ISHARES CORE S&P 500 UCITS ETF", "ETF"),
    ("ISHARES $ CORP BOND UCITS ETF", "Bond ETF"),
    ("BUNDESOBL.V.19/24 S.180", "Bond"),
    ("EUR/USD SL 1.1777 FN 1.1777 R 0.10 COMMERZBANK BEST TURBO SHORT", "Turbo"),
    ("ANHEUSER-BUSCH INBEV SA", "Stock"),
])
def test_guess_class(name, cls):
    assert guess_class(name) == cls


def test_price_response_parsers():
    assert fetch.parse_bitvavo_price({"market": "BTC-EUR", "price": "74655"}) == 74655.0
    candles = [[1767222000000, "1", "1", "1", "74470", "1"], [1767218400000, "1", "1", "1", "74421", "1"]]
    assert fetch.parse_bitvavo_candle_close(candles, 1767218400000) == 74421.0
    assert fetch.parse_justetf_quote({"latestQuote": {"raw": 131.69}, "latestQuoteDate": "2026-10-13"}) == (131.69, date(2026, 10, 13))
    series = {"series": [{"date": "2025-12-30", "value": {"raw": 44.38}}, {"date": "2025-12-31", "value": {"raw": 44.47}},
                         {"date": "2026-01-02", "value": {"raw": 45.38}}]}
    assert fetch.parse_justetf_series_at(series, date(2025, 12, 31)) == 44.47
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("eurofxref-hist.csv", "Date,USD,JPY,\n2025-12-31,1.1746,184.1,\n2025-12-30,1.1700,183.0,\n")
    rates = fetch.parse_ecb_hist(buf.getvalue())
    assert rates["USD"]["2025-12-31"] == 1.1746
