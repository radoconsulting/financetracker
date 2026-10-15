import io
import zipfile
from datetime import date, datetime

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


LYNX_EXTRA = """## Withholding Tax
USD
2026-03-31 ABCD(US0000000002) Cash Dividend USD 1.00 per Share - US Tax -1.50
Total -1.50
Total in EUR -1.32
## Transaction Fees
Date/Time Symbol Description Quantity Trade Price Amount Code
Stocks
USD
2026-01-16, 04:50:43 ABCD BE Securities Transaction Tax 2 55.0000 -0.39
Total -0.39
Total in EUR -0.35
"""


def test_lynx_withholding_and_stock_exchange_tax():
    lines = (["Activity Statement", "January 1, 2026 - October 13, 2026"] + LYNX_LINES.strip().split("\n")
             + LYNX_EXTRA.strip().split("\n"))
    txns, info, warns = lynx.parse_lines(lines, "s.pdf")
    div = [t for t in txns if t.kind == "dividend"][0]
    assert div.asset_class == "Stock"                       # the instrument type decides the dividend exemption
    wht = [t for t in txns if t.kind == "withholding"][0]
    assert wht.asset == "US0000000002" and wht.asset_class == "Stock" and wht.amount_eur == pytest.approx(-1.32)
    (tob,) = [t for t in txns if t.kind == "tob"]
    assert tob.asset == "US0000000002" and tob.amount_eur == pytest.approx(-0.35) and tob.qty == 2
    buy = [t for t in txns if t.kind == "buy"][0]
    assert buy.fees_eur == pytest.approx(0.9)              # the trade's own costs are unchanged (TOB is inside them)


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


# ---------------------------------------------------------------- CSV exports (backlog item 13)
DEGIRO_TX_NL = (
    "Datum,Tijd,Product,ISIN,Beurs,Uitvoeringsplaats,Aantal,Koers,,Lokale waarde,,Waarde EUR,Wisselkoers,AutoFX Kosten,"
    "Transactiekosten en/of kosten van derden EUR,Totaal EUR,Order ID\n"
    '14-10-2026,09:04,ACME WORLD UCITS ETF,XX0000000001,EAM,XAMS,10,"110,50",EUR,"-1105,00",EUR,"-1105,00",,"0,00","-3,00","-1108,00",abc-1\n'
    '13-10-2026,15:30,BETA CORP,XX0000000002,NSY,XNYS,-5,"20,00",USD,"100,00",USD,"90,00","1,1111","-0,23","-2,00","87,77",abc-2\n'
    '01-02-2026,10:00,BUND 2020/30,XX0000000003,TDG,,1000,"99,00",EUR,"-990,00",EUR,"-990,00",,"0,00","-2,00","-992,00",abc-3\n')
DEGIRO_TX_EN = (
    "Date;Time;Product;ISIN;Reference exchange;Venue;Quantity;Price;;Local value;;Value EUR;Exchange rate;AutoFX Fee;"
    "Transaction and/or third party fees EUR;Total EUR;Order ID\n"
    "14-10-2026;09:04;ACME WORLD UCITS ETF;XX0000000001;EAM;XAMS;10;110.50;EUR;-1105.00;EUR;-1105.00;;0.00;-3.00;-1108.00;abc-1\n")
DEGIRO_ACC = (
    "Datum,Tijd,Valutadatum,Product,ISIN,Omschrijving,FX,Mutatie,,Saldo,,Order Id\n"
    '15-10-2026,07:00,14-10-2026,BETA CORP,XX0000000002,Dividendbelasting,,USD,"-0,30",USD,"1,70",\n'
    '15-10-2026,07:00,14-10-2026,BETA CORP,XX0000000002,Dividend,,USD,"2,00",USD,"2,00",\n'
    '16-10-2026,07:00,16-10-2026,,,Valuta Debitering,"1,1000",USD,"-1,70",USD,"0,00",\n'
    '14-10-2026,09:04,14-10-2026,ACME WORLD UCITS ETF,XX0000000001,Transactiebelasting België,,EUR,"-1,33",EUR,"100,00",abc-1\n'
    '01-10-2026,08:00,01-10-2026,,,iDEAL storting,,EUR,"500,00",EUR,"500,00",\n')


def test_degiro_transacties_csv_dutch_and_english():
    from meerwaarde.parsers import degiro_csv
    nl, info, warns = degiro_csv.parse_text(DEGIRO_TX_NL, "Transactions.csv")
    assert info is None and warns == []
    buy = next(t for t in nl if t.asset == "XX0000000001")
    assert (buy.kind, buy.qty, buy.price, buy.price_cur, buy.gross_eur, buy.fees_eur, buy.total_eur, buy.ref) == \
        ("buy", 10, 110.5, "EUR", 1105.0, 3.0, -1108.0, "abc-1")
    assert buy.ts == datetime(2026, 10, 14, 9, 4) and buy.asset_class == "ETF"
    sell = next(t for t in nl if t.asset == "XX0000000002")
    assert (sell.kind, sell.qty, sell.gross_eur, sell.fees_eur, sell.total_eur) == ("sell", -5, 90.0, pytest.approx(2.23), 87.77)
    assert next(t for t in nl if t.asset == "XX0000000003").asset_class == "Bond"     # quoted in %
    en, _i, _w = degiro_csv.parse_text(DEGIRO_TX_EN, "Transactions.csv")
    assert [(t.qty, t.total_eur, t.fees_eur) for t in en] == [(10, -1108.0, 3.0)]


def test_degiro_account_csv_uses_the_statement_rules():
    from meerwaarde.parsers import degiro_csv
    txns, info, warns = degiro_csv.parse_text(DEGIRO_ACC, "Account.csv")
    kinds = {t.kind for t in txns}
    assert kinds == {"dividend", "withholding", "tob", "cash_in"}
    div = next(t for t in txns if t.kind == "dividend")
    assert div.amount_eur == pytest.approx(2.0 / 1.1, abs=0.01)                      # converted at the next USD debit
    tob = next(t for t in txns if t.kind == "tob")
    assert (tob.amount_eur, tob.ref) == (-1.33, "separate")
    assert info.deposits == 500.0


def test_degiro_csv_numbers():
    from meerwaarde.parsers.degiro_csv import number
    assert [number(x) for x in ("1.234,56", "1,234.56", "-1105,00", "-1105.00", "", "0,5")] == \
        [1234.56, 1234.56, -1105.0, -1105.0, 0.0, 0.5]


def test_parse_file_reads_csv_bytes_and_drops_the_same_trade_from_a_pdf():
    from meerwaarde.parsers import ParseResult, combine, parse_file
    r = parse_file(DEGIRO_TX_NL.encode("utf-8-sig"), filename="Transactions.csv")
    assert r.broker == "DEGIRO" and len(r.transactions) == 3
    pdf_like = ParseResult("DEGIRO", [t for t in parse_file(DEGIRO_TX_NL.encode(), filename="x.csv").transactions], filename="pdf")
    txns, _s, _w = combine([r, pdf_like])
    assert len(txns) == 3                                                            # same rows in two files: once
    with pytest.raises(ValueError, match="not recognised"):
        parse_file(b"a,b,c\n1,2,3\n", filename="other.csv")


BITVAVO_CSV = (
    "Timezone,Date,Time,Type,Currency,Amount,Quote Currency,Quote Price,Received / Paid Currency,Received / Paid Amount,"
    "Fee currency,Fee amount,Status,Transaction ID,Address\n"
    "Europe/Amsterdam,2026-10-01,10:00:00,buy,BTC,0.002,EUR,50000,EUR,-100.25,EUR,0.25,Completed,t1,\n"
    "Europe/Amsterdam,2026-10-02,11:00:00,staking,ETH,0.01,,,,,,,Completed,t2,\n"
    "Europe/Amsterdam,2026-10-03,12:00:00,withdrawal,BTC,-0.0005,,,,,BTC,0.00001,Completed,t3,bc1qexample\n"
    "Europe/Amsterdam,2026-10-04,12:00:00,deposit,EUR,500,,,,,,,Completed,t4,\n"
    "Europe/Amsterdam,2026-10-05,12:00:00,buy,ETH,0.1,EUR,2000,EUR,-200.5,EUR,0.5,Canceled,t5,\n")


def test_bitvavo_csv_gives_the_same_rows_as_the_pdf():
    from meerwaarde.parsers import parse_file
    r = parse_file(BITVAVO_CSV.encode(), filename="bitvavo.csv")
    assert r.broker == "Bitvavo"
    kinds = [(t.kind, t.asset) for t in r.transactions]
    assert kinds == [("buy", "BTC"), ("reward", "ETH"), ("withdrawal", "BTC"), ("cash_in", "EUR")]   # canceled row skipped
    buy = r.transactions[0]
    assert (buy.qty, buy.gross_eur, buy.fees_eur, buy.total_eur, buy.ref) == (0.002, 100.0, 0.25, -100.25, "t1")
    assert buy.ts == datetime(2026, 10, 1, 10, 0)
    assert r.transactions[2].fee_qty == 0.00001


def test_mapped_csv_format():
    from meerwaarde.parsers import mapped, parse_file
    text = ("Datum;Type;ISIN;Naam;Aantal;Koers;Munt;Wisselkoers;Kosten;Bedrag\n"
            "02/10/2026;Aankoop;XX0000000001;ACME WORLD UCITS ETF;10;50,00;EUR;;2,50;\n"
            "03/10/2026;Verkoop;XX0000000002;BETA CORP;-4;25,00;USD;1,25;1,00;\n"
            "04/10/2026;Dividend;XX0000000002;BETA CORP;;;USD;1,25;;5,00\n"
            "05/10/2026;Bewaarloon;;;;;EUR;;;3,00\n"
            "06/10/2026;Iets anders;;;;;EUR;;;1,00\n")
    header, delim, _rows = mapped.sniff(text)
    fmt = mapped.CsvFormat("Test bank", "Testbank", header, dict(date="Datum", type="Type", isin="ISIN", name="Naam",
                           qty="Aantal", price="Koers", currency="Munt", fx="Wisselkoers", costs="Kosten", amount="Bedrag"),
                           delimiter=delim, decimal=",", date_format="%d/%m/%Y", fx_inverted=True,
                           types={"Aankoop": "buy", "Verkoop": "sell", "Dividend": "dividend", "Bewaarloon": "fee"})
    mapped.FORMATS[:] = [fmt]
    try:
        r = parse_file(text.encode(), filename="bank.csv")
    finally:
        mapped.FORMATS.clear()
    assert r.broker == "Testbank"
    buy, sell, div, fee = r.transactions
    assert (buy.kind, buy.qty, buy.gross_eur, buy.fees_eur, buy.total_eur) == ("buy", 10, 500.0, 2.5, -502.5)
    assert (sell.qty, sell.gross_eur, sell.fees_eur, sell.total_eur) == (-4, 80.0, 0.8, 79.2)       # USD / 1.25
    assert (div.kind, div.amount_eur) == ("dividend", 4.0)
    assert (fee.kind, fee.amount_eur) == ("fee", -3.0)
    assert any("Iets anders" in w for w in r.warnings)


def test_mapped_formats_round_trip(tmp_path):
    from meerwaarde.parsers import mapped
    f = mapped.CsvFormat("A", "Bank A", ["x", "y"], {"date": "x"})
    mapped.save(tmp_path / "f.json", [f])
    assert mapped.load(tmp_path / "f.json") == [f]
    mapped.load(None)
    assert mapped.FORMATS == []


# ---------------------------------------------------------------- IBKR / LYNX Transaction History PDF
HISTORY_ROWS = [
    ["2026-10-14", "U***00000", "FX Translations P&L", "Adjustment", "-", "-", "-", "-", "-10.00", "-", "-10.00"],
    ["2026-09-30", "U***00000", "AAAA(XX000000000 1) Cash Dividend USD 0.10 per Share", "Dividend", "AAAA", "-", "-", "-", "20.00", "-", "20.00"],
    ["2026-02-19", "U***00000", "ACME WORLD", "Buy", "BBBB", "10.00", "100.00", "EUR", "-1,000.00", "-6.00", "-1,007.20"],
    ["2026-02-20", "U***00000", "ACME WORLD", "Sell", "BBBB", "4.00", "110.00", "EUR", "440.00", "-6.00", "433.47"],
    ["2026-01-16", "U***00000", "ACME S&P 500", "Buy", "AAAA", "5.00", "50.00", "EUR", "-250.00", "-6.00", "-256.30"],
    ["2025-03-05", "U***00000", "Withholding @ 20% on Credit Interest for Feb-2025", "Foreign Tax Withholding", "-", "-", "-", "-", "-0.02", "-", "-0.02"],
    ["2025-03-05", "U***00000", "EUR Credit Interest for Feb-2025", "Credit Interest", "-", "-", "-", "-", "0.10", "-", "0.10"],
    ["2025-01-13", "U***00000", "Electronic Fund Transfer", "Deposit", "-", "-", "-", "-", "5,000.00", "-", "5,000.00"],
    ["2023-09-15", "U***00000", "Net Amount in Base from Forex Trade: -1,000 EUR.USD", "Forex Trade Component", "EUR.USD", "-1,000.00", "1.07", "USD", "2.00", "-4.70", "2.00"],
    ["2023-09-16", "U***00000", "Something new", "Mystery", "-", "-", "-", "-", "1.00", "-", "1.00"],
]


def test_transaction_history_rows():
    from meerwaarde.parsers import ibkr_history
    txns, warns = ibkr_history.parse_rows(HISTORY_ROWS, "history.pdf")
    kinds = [(t.kind, t.asset) for t in txns]
    assert kinds == [("dividend", "XX0000000001"), ("buy", "BBBB"), ("sell", "BBBB"), ("buy", "AAAA"),
                     ("withholding", "EUR"), ("interest", "EUR"), ("cash_in", "EUR"), ("fee", "EUR.USD")]
    buy = txns[1]
    assert (buy.qty, buy.gross_eur, buy.fees_eur, buy.total_eur, buy.price, buy.ts) == \
        (10, 1000.0, 7.2, -1007.2, 100.0, datetime(2026, 2, 19))            # costs = commission + stock exchange tax
    sell = txns[2]
    assert (sell.qty, sell.gross_eur, sell.fees_eur, sell.total_eur) == (-4, 440.0, 6.53, 433.47)
    assert txns[0].amount_eur == 20.0 and txns[-1].amount_eur == -4.7
    assert any("FX Translations" in w for w in warns) and any("Mystery" in w for w in warns)


def test_transaction_history_symbols_and_coverage():
    from meerwaarde.parsers import ParseResult, combine, ibkr_history
    from meerwaarde.parsers.lynx import StatementInfo
    hist, _w = ibkr_history.parse_rows(HISTORY_ROWS, "history.pdf")
    st = StatementInfo("LYNX", date(2026, 1, 1), date(2026, 2, 19))
    stmt_row = __import__("meerwaarde.models", fromlist=["Transaction"]).Transaction(
        "LYNX", datetime(2026, 1, 16), "buy", "XX0000000002", qty=5, gross_eur=250, total_eur=-256.3, note="BBBB")
    results = [ParseResult("LYNX", [stmt_row], st, [], "statement.pdf"),
               ParseResult("LYNX", hist, None, [], "history.pdf", supplement=True)]
    txns, _s, notes = combine(results)
    added = [t for t in txns if t.source == "history.pdf"]
    assert [(t.kind, t.asset, t.ts.date()) for t in added] == [("dividend", "XX0000000001", date(2026, 9, 30)),
                                                              ("sell", "XX0000000002", date(2026, 2, 20))]
    assert any("2 of 10 rows" in n or "of 8 rows" in n for n in notes)
    # without a statement every row counts, and a symbol without a known ISIN is reported
    alone, _w = ibkr_history.parse_rows(HISTORY_ROWS, "history.pdf")
    warns = ibkr_history.fill_isins(alone, {})
    assert next(t for t in alone if t.kind == "buy" and t.note == "AAAA").asset == "XX0000000001"   # from its dividend
    assert any("BBBB" in w for w in warns)


def test_transaction_history_detection():
    from meerwaarde.parsers.ibkr_history import is_transaction_history
    assert is_transaction_history("Transaction History\nAugust 25, 2023 - October 14, 2026\nDate Account Description "
                                  "Transaction Type Symbol Quantity Price Price Currency Gross Amount Commission Net Amount")
    assert not is_transaction_history("Activity Statement\nInteractive Brokers")


# ---------------------------------------------------------------- LYNX Jaaroverzicht (annual tax overview)
ANNUAL_TEXT = """Gegevens Overzicht
Periode 01/01/2025 - 31/12/2025 Ontvangen bruto dividenden buiten België €100.00
Ontvangen bruto dividenden België €0.00
Betaalde dividendbelasting België €0.00
Betaalde dividendbelasting buiten België €0.00
Ontvangen interest België €0.00
Ontvangen interest buiten België €0.80
Vermogensbestanddelen
1444-11 €100.80
CASH €10.00 €20.00 Inkomsten van kapitalen
AANDELEN €1,000.00 €2,500.00
TOTAAL €1,010.00 €2,520.00
Ontvangen dividenden buiten België
Datum Omschrijving Bruto Dividend Ingehouden belasting Dividend in EUR Belasting in EUR Netto dividend in EUR
Mar 27, 2025 ACME WORLD ETF $110.00 $0.00 €100.00 €0.00 €100.00
Totaal: €100.00 -€0.20 €99.80
Rente overzicht
Feb 5, 2025 EUR CREDIT INT FOR JAN-2025 €1.00 €1.00
Ingehouden belasting interesten
Feb 5, 2025 WITHHOLDING @ 20% ON CREDIT INT FOR JAN-2025 -€0.20 -€0.20
"""


def test_lynx_annual_overview_text():
    from meerwaarde.parsers.lynx_annual import is_annual_overview, parse_text
    assert is_annual_overview(ANNUAL_TEXT)
    ov = parse_text(ANNUAL_TEXT, "jaaroverzicht.pdf")
    assert ov.year == 2025 and ov.code_1444 == 100.8
    assert ov.totals["dividends_foreign"] == 100.0 and ov.totals["dividends_be"] == 0.0 and ov.totals["interest_foreign"] == 0.8
    assert ov.values["AANDELEN"] == (1000.0, 2500.0)
    assert ov.dividends == [(date(2025, 3, 27), "ACME WORLD ETF", 110.0, "USD", 100.0, 0.0, 100.0)]
    assert ov.interest == [(date(2025, 2, 5), "EUR CREDIT INT FOR JAN-2025", 1.0)]
    assert ov.interest_tax[0][2] == -0.2


def test_annual_overview_checks_in_the_report():
    from meerwaarde.engine.portfolio import Portfolio
    from meerwaarde.engine.tax import TaxCalculator, TaxRules
    from meerwaarde.models import BUY, DIVIDEND, INTEREST, WITHHOLDING, Transaction
    from meerwaarde.parsers.lynx_annual import parse_text
    from meerwaarde.prices import PriceBook
    from meerwaarde.report import Report
    txns = [Transaction("LYNX", datetime(2025, 1, 2), BUY, "XX0000000001", name="ACME WORLD ETF", asset_class="ETF", qty=25,
                        gross_eur=2000, total_eur=-2000),
            Transaction("LYNX", datetime(2025, 3, 27), DIVIDEND, "XX0000000001", amount_eur=100.0),
            Transaction("LYNX", datetime(2025, 2, 5), INTEREST, "EUR", asset_class="Cash/FX", amount_eur=1.0),
            Transaction("LYNX", datetime(2025, 2, 5), WITHHOLDING, "EUR", asset_class="Cash/FX", amount_eur=-0.2)]
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=date(2026, 3, 1))
    pb.set_override("XX0000000001", now=110.0, reference=100.0)
    rep = Report(pf, pb, TaxCalculator(pf, pb, TaxRules(year=2025), today=date(2026, 3, 1)), [],
                 overviews=[parse_text(ANNUAL_TEXT)])
    checks = rep.overview_checks(2025).set_index("item")
    assert checks.loc["Gross dividends", "difference"] == 0.0
    assert checks.loc["Interest after tax withheld", "app_eur"] == 0.8
    assert checks.loc["Code 1444-11 (dividends + interest)", "difference"] == 0.0
    assert checks.loc["Value of the securities on 31-12-2025", "app_eur"] == 2500.0
    assert rep.overview_checks(2024).empty
