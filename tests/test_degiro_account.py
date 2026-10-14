"""DEGIRO account statement (Rekeningoverzicht): parser and how the engines use it. Invented data only."""
from datetime import datetime

import pytest

from meerwaarde.engine.household import Household
from meerwaarde.engine.income_tax import income_tax
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.models import BUY, SELL, Transaction
from meerwaarde.parsers import degiro_account


def row(date, time, desc, mut="", product="", isin="", fx="", saldo=""):
    return dict(date=date, time=time, valuta=date, product=product, isin=isin, desc=desc, fx=fx, mut=mut, saldo=saldo)


# newest first, as printed
ROWS = [
    row("05-03-2026", "07:01", "Valuta Creditering", "EUR 90,00"),
    row("05-03-2026", "07:01", "Valuta Debitering", "USD -100,00", fx="1,1111"),
    row("04-03-2026", "08:17", "Dividendbelasting", "USD -15,00", "EXAMPLE CORP", "US0000000001"),
    row("04-03-2026", "08:17", "Dividend", "USD 115,00", "EXAMPLE CORP", "US0000000001"),
    row("10-02-2026", "10:00", "Dividendbelasting", "EUR -9,00", "BELGIAN BREWER SA", "BE0000000002"),
    row("10-02-2026", "10:00", "Dividend", "EUR 30,00", "BELGIAN BREWER SA", "BE0000000002"),
    row("02-02-2026", "12:18", "Overboeking naar uw geldrekening bij flatexDEGIRO Bank SE: 100 EUR", saldo="EUR 5,00"),
    row("02-02-2026", "12:18", "Degiro Cash Sweep Transfer", "EUR -100,00"),
    row("01-02-2026", "04:20", "Flatex Interest Income", "EUR 0,00"),
    row("01-02-2026", "04:20", "Inkomsten uit Securities Lending - Januari", "EUR 1,20"),
    row("20-01-2026", "10:55", "Transactiebelasting België", "EUR -12,00", "SOME WORLD UCITS ETF", "IE0000000003"),
    row("20-01-2026", "10:55", "DEGIRO Transactiekosten en/of kosten van derden", "EUR -3,00", "SOME WORLD UCITS ETF", "IE0000000003"),
    row("20-01-2026", "10:55", "Koop 100 @ 100 EUR", "EUR -10.000,00", "SOME WORLD UCITS ETF", "IE0000000003"),
    row("21-10-2024", "09:00", "Contante Verrekening Obligatie", "EUR 1.000,00", "SOME BOND 19/24", "DE0000000004"),
    row("21-10-2024", "09:00", "VERLOPEN OBLIGATIE: Verkoop 1.000 @ 0 EUR", "EUR 0,00", "SOME BOND 19/24", "DE0000000004"),
    row("01-07-2023", "04:20", "Rente", "EUR -0,50"),
    row("01-03-2023", "08:00", "DEGIRO Aansluitingskosten 2023 (Xetra - XET)", "EUR -2,50"),
    row("01-02-2019", "11:55", "Storting", "EUR 12.000,00"),
    row("02-01-2019", "11:55", "Iets nieuws dat we niet kennen", "EUR 1,00"),
]


def test_parse_rows():
    txns, info, warns = degiro_account.parse_rows(ROWS, "Account.pdf")
    kinds = sorted(t.kind for t in txns)
    assert kinds == ["cash_in", "dividend", "dividend", "fee", "interest", "interest", "redemption", "tob",
                     "withholding", "withholding"]
    usd = next(t for t in txns if t.kind == "dividend" and t.currency == "USD")
    assert usd.amount_eur == pytest.approx(115 / 1.1111, abs=0.01)        # the statement's own conversion rate
    assert usd.price == 115.0
    tob = next(t for t in txns if t.kind == "tob")
    assert tob.ref == "separate" and tob.amount_eur == -12.0
    lending = next(t for t in txns if t.kind == "interest" and t.amount_eur > 0)
    assert lending.name == "Securities lending"
    assert info.trades == 2 and info.deposits == 12000.0
    assert info.cash_eur == pytest.approx(12000 - 10000 - 3 - 12 + 1.2 + 30 - 9 + 90 + 1000 - 0.5 - 2.5 + 1)
    assert info.unknown and any("unknown type" in w for w in warns)


def test_engines_use_the_account_statement():
    txns, info, _ = degiro_account.parse_rows(ROWS, "Account.pdf")
    trades = [
        Transaction("DEGIRO", datetime(2019, 10, 1), BUY, "DE0000000004", "SOME BOND 19/24", "Bond", qty=1000,
                    gross_eur=1000, fees_eur=0, total_eur=-1000),
        Transaction("DEGIRO", datetime(2024, 10, 16, 9, 0), SELL, "DE0000000004", "SOME BOND 19/24", "Bond", qty=-1000,
                    gross_eur=0, fees_eur=0, total_eur=0),
        Transaction("DEGIRO", datetime(2026, 1, 20, 10, 55), BUY, "IE0000000003", "SOME WORLD UCITS ETF", "ETF", qty=100,
                    gross_eur=10000, fees_eur=3, total_eur=-10003),
        Transaction("DEGIRO", datetime(2025, 5, 1), BUY, "BE0000000002", "BELGIAN BREWER SA", "Stock", qty=10,
                    gross_eur=500, fees_eur=0, total_eur=-500),
    ]
    pf = Portfolio(trades + txns)
    etf = next(h for h in pf.holdings() if h.asset == "IE0000000003")
    assert etf.cost_eur == pytest.approx(10015)                  # stock exchange tax added to the purchase cost
    assert etf.lots[0].gross_eur_unit == pytest.approx(100)      # ... but not to the price used for the tax
    bond = next(r for r in pf.realized if r.asset == "DE0000000004")
    assert bond.proceeds_eur == 1000 and "account statement" in bond.note
    fees = [r for r in pf.realized if r.kind == "fee"]
    assert sum(r.pnl_eur for r in fees) == pytest.approx(-2.5)     # connection fee; the unknown row is left out
    (it,) = income_tax(pf.income, Household(), 2026).values()
    assert it.total("shares") == pytest.approx(30 + 100 / 1.1111, abs=0.02)      # BE gross + US net of 15%, in EUR
    assert it.be_withheld == pytest.approx(-9.0)
    assert it.total("other") == pytest.approx(1.2)
    assert it.exemption_used == pytest.approx(it.total("shares"))
    assert it.to_pay == pytest.approx(0.30 * 1.2 - 9.0)          # exemption covers the shares -> Belgian tax refunded
