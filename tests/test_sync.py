"""Automatic sync: IBKR Flex and Bitvavo API (invented data, no network)."""
import hashlib
import hmac
import json
from datetime import date, datetime

import pytest

from meerwaarde.models import (BUY, CASH_IN, DEPOSIT, DIVIDEND, FEE, INTEREST, REWARD, SELL, TOB, WITHDRAWAL,
                               WITHHOLDING, Transaction)
from meerwaarde.parsers.lynx import StatementInfo
from meerwaarde.report import build_report
from meerwaarde.sync import SyncStore, load_secrets, merge_synced, sync
from meerwaarde.sync.bitvavo_api import fetch_history, parse_history, reconcile, signature
from meerwaarde.sync.flex import FlexError, fetch_flex, parse_flex

FLEX = """<FlexQueryResponse queryName="t" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U0000000" fromDate="2026-01-01" toDate="2026-03-31" period="Last365CalendarDays" whenGenerated="2026-04-01;010101">
<OpenPositions>
 <OpenPosition assetCategory="STK" subCategory="ETF" symbol="AAA" description="ACME WORLD UCITS ETF" isin="XX0000000001" currency="USD" fxRateToBase="0.9" position="10" markPrice="50" multiplier="1" levelOfDetail="SUMMARY" reportDate="2026-03-31"/>
</OpenPositions>
<Trades>
 <Trade assetCategory="STK" subCategory="ETF" symbol="AAA" description="ACME WORLD UCITS ETF" isin="XX0000000001" currency="USD" fxRateToBase="0.9" tradeDate="2026-01-10" dateTime="2026-01-10;101010" quantity="6" multiplier="1" tradePrice="40" proceeds="-240" ibCommission="-2" ibCommissionCurrency="USD" taxes="-0.29" netCash="-242.29" buySell="BUY" levelOfDetail="EXECUTION" tradeID="111" transactionID="1"/>
 <Trade assetCategory="STK" subCategory="ETF" symbol="AAA" description="ACME WORLD UCITS ETF" isin="XX0000000001" currency="USD" fxRateToBase="0.9" tradeDate="2026-01-10" dateTime="2026-01-10;101011" quantity="4" multiplier="1" tradePrice="40" proceeds="-160" ibCommission="0" ibCommissionCurrency="USD" taxes="-0.19" netCash="-160.19" buySell="BUY" levelOfDetail="EXECUTION" tradeID="112" transactionID="2"/>
 <Trade assetCategory="STK" subCategory="COMMON" symbol="BBB" description="BETA CORP" isin="XX0000000002" currency="EUR" fxRateToBase="1" tradeDate="2026-02-01" quantity="-5" multiplier="1" tradePrice="20" proceeds="100" ibCommission="-3" ibCommissionCurrency="EUR" taxes="-0.35" netCash="96.65" buySell="SELL" levelOfDetail="EXECUTION" tradeID="113" transactionID="3"/>
 <Trade assetCategory="CASH" symbol="EUR.USD" description="EUR.USD" currency="USD" fxRateToBase="0.9" tradeDate="2026-01-09" quantity="300" proceeds="-330" ibCommission="-2" ibCommissionCurrency="EUR" levelOfDetail="EXECUTION" tradeID="114" transactionID="4"/>
 <Order assetCategory="STK" symbol="AAA" quantity="10" levelOfDetail="ORDER"/>
</Trades>
<TransactionTaxes>
 <TransactionTax assetCategory="STK" subCategory="ETF" symbol="AAA" description="ACME WORLD UCITS ETF" isin="XX0000000001" currency="USD" fxRateToBase="0.9" date="2026-01-10;101010" quantity="6" tradePrice="40" taxAmount="-0.29" taxDescription="BE Securities Transaction Tax" tradeId="111" levelOfDetail="SUMMARY"/>
</TransactionTaxes>
<CorporateActions><CorporateAction description="BBB SPLIT 2 FOR 1" isin="XX0000000002" reportDate="2026-03-01"/></CorporateActions>
<CashTransactions>
 <CashTransaction type="Dividends" assetCategory="STK" subCategory="ETF" symbol="AAA" description="AAA(XX0000000001) CASH DIVIDEND" isin="XX0000000001" currency="USD" fxRateToBase="0.8" amount="10" settleDate="2026-03-15" dateTime="2026-03-15;202000" levelOfDetail="DETAIL" transactionID="21"/>
 <CashTransaction type="Withholding Tax" assetCategory="STK" symbol="BBB" description="BBB(XX0000000002) TAX" isin="XX0000000002" currency="EUR" fxRateToBase="1" amount="-1.5" settleDate="2026-03-16" levelOfDetail="DETAIL" transactionID="22"/>
 <CashTransaction type="Broker Interest Received" assetCategory="CASH" description="EUR CREDIT INT" currency="EUR" fxRateToBase="1" amount="3" settleDate="2026-03-03" levelOfDetail="DETAIL" transactionID="23"/>
 <CashTransaction type="Deposits/Withdrawals" assetCategory="CASH" description="CASH RECEIPTS" currency="EUR" fxRateToBase="1" amount="1000" settleDate="2026-01-05" levelOfDetail="DETAIL" transactionID="24"/>
 <CashTransaction type="Dividends" isin="XX0000000001" currency="USD" fxRateToBase="0.8" amount="10" settleDate="2026-03-15" levelOfDetail="SUMMARY" transactionID="25"/>
</CashTransactions>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def by_kind(txns, kind):
    return [t for t in txns if t.kind == kind]


# ---------------------------------------------------------------- Flex
def test_flex_trades_in_eur_with_commission_and_tax_as_costs():
    r = parse_flex(FLEX, "f.xml")
    buys = by_kind(r.transactions, BUY)
    assert [b.qty for b in buys] == [6, 4]                      # executions stay separate, the order row is ignored
    b = buys[0]
    assert (b.asset, b.asset_class, b.price, b.price_cur) == ("XX0000000001", "ETF", 40.0, "USD")
    assert b.gross_eur == pytest.approx(216.0) and b.fees_eur == pytest.approx((2 + 0.29) * 0.9)
    assert b.total_eur == pytest.approx(-242.29 * 0.9) and b.ref == "flex:trade:111" and b.ts == datetime(2026, 1, 10)
    s = by_kind(r.transactions, SELL)[0]
    assert (s.qty, s.asset_class, s.gross_eur, s.fees_eur, s.total_eur) == (-5, "Stock", 100.0, 3.35, 96.65)


def test_flex_tax_income_cash_fees_positions_and_warnings():
    r = parse_flex(FLEX, "f.xml")
    tob = by_kind(r.transactions, TOB)[0]
    assert tob.amount_eur == pytest.approx(-0.26) and tob.asset == "XX0000000001"
    div = by_kind(r.transactions, DIVIDEND)
    assert len(div) == 1 and div[0].amount_eur == 8.0 and div[0].currency == "USD" and div[0].asset_class == "ETF"
    wh = by_kind(r.transactions, WITHHOLDING)[0]
    assert (wh.asset, wh.amount_eur) == ("XX0000000002", -1.5)
    it = by_kind(r.transactions, INTEREST)[0]
    assert (it.asset, it.asset_class, it.amount_eur) == ("EUR", "Cash/FX", 3.0)
    assert by_kind(r.transactions, CASH_IN)[0].amount_eur == 1000.0
    fee = by_kind(r.transactions, FEE)[0]
    assert fee.amount_eur == -2.0 and "conversion" in fee.name
    assert r.positions["XX0000000001"]["qty"] == 10 and r.positions["XX0000000001"]["mark_eur"] == 45.0
    assert r.period == (date(2026, 1, 1), date(2026, 3, 31))
    assert any("corporate action" in w for w in r.warnings)


def test_flex_rejects_other_xml():
    with pytest.raises(ValueError):
        parse_flex("<Something/>")


class R:
    def __init__(self, text="", status=200, data=None):
        self.text, self.status_code, self._data = text, status, data

    def json(self):
        return self._data


def test_fetch_flex_waits_while_the_report_is_generated():
    answers = iter([
        R("<FlexStatementResponse><Status>Success</Status><ReferenceCode>42</ReferenceCode><Url>https://x/Get</Url></FlexStatementResponse>"),
        R("<FlexStatementResponse><Status>Warn</Status><ErrorCode>1019</ErrorCode><ErrorMessage>in progress</ErrorMessage></FlexStatementResponse>"),
        R(FLEX)])
    calls = []

    def get(url, params, headers, timeout):
        calls.append((url, params))
        return next(answers)

    assert fetch_flex("tok", "123", get=get, sleep=lambda s: None).startswith("<FlexQueryResponse")
    assert calls[0][1] == {"t": "tok", "q": "123", "v": "3"} and calls[1][0] == "https://x/Get" and calls[1][1]["q"] == "42"


def test_fetch_flex_reports_a_refused_token():
    bad = R("<FlexStatementResponse><Status>Fail</Status><ErrorCode>1012</ErrorCode><ErrorMessage>Token has expired.</ErrorMessage></FlexStatementResponse>")
    with pytest.raises(FlexError, match="expired"):
        fetch_flex("tok", "1", get=lambda *a, **k: bad, sleep=lambda s: None)


# ---------------------------------------------------------------- Bitvavo
ITEMS = [
    {"transactionId": "t1", "executedAt": "2026-01-10T09:00:00.000Z", "type": "buy", "priceCurrency": "EUR", "priceAmount": "50000",
     "sentCurrency": "EUR", "sentAmount": "100.25", "receivedCurrency": "BTC", "receivedAmount": "0.002", "feesCurrency": "EUR", "feesAmount": "0.25"},
    {"transactionId": "t2", "executedAt": "2026-02-10T09:00:00.000Z", "type": "sell", "priceCurrency": "EUR", "priceAmount": "60000",
     "sentCurrency": "BTC", "sentAmount": "0.001", "receivedCurrency": "EUR", "receivedAmount": "59.85", "feesCurrency": "EUR", "feesAmount": "0.15"},
    {"transactionId": "t3", "executedAt": "2026-02-11T09:00:00.000Z", "type": "staking", "receivedCurrency": "ETH", "receivedAmount": "0.01"},
    {"transactionId": "t4", "executedAt": "2026-02-12T09:00:00.000Z", "type": "withdrawal", "sentCurrency": "BTC", "sentAmount": "0.0005",
     "feesCurrency": "BTC", "feesAmount": "0.00001", "address": "bc1qexample"},
    {"transactionId": "t5", "executedAt": "2026-01-01T08:00:00.000Z", "type": "deposit", "receivedCurrency": "EUR", "receivedAmount": "500"},
    {"transactionId": "t6", "executedAt": "2026-02-13T09:00:00.000Z", "type": "deposit", "receivedCurrency": "BTC", "receivedAmount": "0.0004"},
    {"transactionId": "t7", "executedAt": "2026-02-14T09:00:00.000Z", "type": "internal_transfer"},
]


def test_signature_is_hmac_of_timestamp_method_path_body():
    expect = hmac.new(b"sec", b"1700000000000GET/v2/account/history?page=1", hashlib.sha256).hexdigest()
    assert signature("sec", 1700000000000, "GET", "/v2/account/history?page=1") == expect


def test_history_maps_like_the_pdf_parser():
    from datetime import timezone
    txns, warns = parse_history(ITEMS, tz=timezone.utc)
    kinds = [t.kind for t in txns]
    assert kinds == [CASH_IN, BUY, SELL, REWARD, WITHDRAWAL, DEPOSIT]
    buy = by_kind(txns, BUY)[0]
    assert (buy.asset, buy.qty, buy.total_eur, buy.fees_eur, buy.gross_eur) == ("BTC", 0.002, -100.25, 0.25, 100.0)
    assert buy.ts == datetime(2026, 1, 10, 9, 0) and buy.ref == "t1"
    sell = by_kind(txns, SELL)[0]
    assert (sell.qty, sell.total_eur, sell.gross_eur) == (-0.001, 59.85, 60.0)
    w = by_kind(txns, WITHDRAWAL)[0]
    assert (w.qty, w.fee_qty, w.note) == (-0.0005, 0.00001, "bc1qexample")
    assert any("internal_transfer" in x for x in warns)
    added = by_kind(parse_history(ITEMS, tz=timezone.utc, fee_in_sent=False)[0], BUY)[0]
    assert added.total_eur == pytest.approx(-100.5)


def test_history_in_brussels_time_by_default():
    buy = by_kind(parse_history(ITEMS)[0], BUY)[0]
    assert buy.ts == datetime(2026, 1, 10, 10, 0)          # winter: UTC+1


def test_reconcile_finds_offset_and_fee_rule():
    from datetime import timedelta, timezone
    api, _ = parse_history(ITEMS, tz=timezone.utc)
    pdf = [Transaction("Bitvavo", t.ts + timedelta(hours=1), t.kind, t.asset, qty=t.qty, total_eur=t.total_eur, ref=t.ref)
           for t in api if t.kind in (BUY, SELL)]
    rec = reconcile(api, pdf)
    assert rec["matched"] == 2 and rec["qty_ok"] == 2 and rec["total_ok"] == 2 and rec["hour_offsets"] == [-1.0]
    assert rec["buy_fee_rule"] == "included"


def test_fetch_history_signs_and_pages():
    pages = {1: {"items": ITEMS[:3], "currentPage": 1, "totalPages": 2}, 2: {"items": ITEMS[3:], "currentPage": 2, "totalPages": 2}}
    seen = []

    def get(url, headers, timeout):
        page = int(url.split("page=")[1].split("&")[0])
        seen.append((url, headers))
        return R(data=pages[page])

    items = fetch_history("key", "sec", from_ms=123, get=get, now_ms=lambda: 1700000000000, sleep=lambda s: None)
    assert len(items) == len(ITEMS)
    url, h = seen[0]
    path = url.replace("https://api.bitvavo.com", "")
    assert "fromDate=123" in path and h["Bitvavo-Access-Key"] == "key"
    assert h["Bitvavo-Access-Signature"] == signature("sec", 1700000000000, "GET", path)


def test_fetch_history_raises_on_an_error_answer():
    from meerwaarde.sync.bitvavo_api import BitvavoError
    with pytest.raises(BitvavoError, match="invalid"):
        fetch_history("k", "s", get=lambda *a, **k: R(status=403, data={"errorCode": 305, "error": "invalid key"}),
                      sleep=lambda s: None)


# ---------------------------------------------------------------- merge, store, secrets
def tx(broker, ts, kind, asset, ref="", **kw):
    return Transaction(broker, ts, kind, asset, ref=ref, **kw)


def test_merge_keeps_only_what_the_exports_dont_cover():
    st = StatementInfo("LYNX", date(2026, 1, 1), date(2026, 3, 31))
    pdf = [tx("Bitvavo", datetime(2026, 2, 1, 12), BUY, "BTC", "t1", qty=1, total_eur=-1, gross_eur=1)]
    synced = [tx("LYNX", datetime(2026, 3, 31), BUY, "X", "flex:trade:1"), tx("LYNX", datetime(2026, 4, 1), BUY, "X", "flex:trade:2"),
              tx("Bitvavo", datetime(2026, 1, 15), BUY, "BTC", "t1"), tx("Bitvavo", datetime(2026, 2, 1, 11), BUY, "BTC", "t0"),
              tx("Bitvavo", datetime(2026, 2, 2), BUY, "BTC", "t9"), tx("Bitvavo", datetime(2026, 2, 2), BUY, "BTC", "t9")]
    added, notes = merge_synced(pdf, [st], synced)
    assert [t.ref for t in added] == ["flex:trade:2", "t9"]
    assert any("LYNX: 1 row" in n for n in notes)
    alone, _ = merge_synced([], [], synced[:2])
    assert len(alone) == 2                                  # no export for LYNX: everything synced counts


def test_store_dedups_flex_downloads_and_merges_bitvavo(tmp_path):
    store = SyncStore(tmp_path)
    store.save_flex(FLEX, datetime(2026, 4, 1, 1, 0, 0))
    store.save_flex(FLEX, datetime(2026, 4, 2, 1, 0, 0))     # same rows again
    assert store.save_bitvavo(ITEMS[:4]) == (4, 4)
    assert store.save_bitvavo(ITEMS[2:]) == (3, 7)
    loaded = store.load()
    assert len([t for t in loaded.transactions if t.broker == "LYNX"]) == len(parse_flex(FLEX).transactions)
    assert len([t for t in loaded.transactions if t.broker == "Bitvavo"]) == 6
    assert loaded.flex_positions["XX0000000001"]["qty"] == 10
    since = store.bitvavo_since_ms()
    assert since == int(datetime(2026, 2, 12, 9, 0, tzinfo=__import__("datetime").timezone.utc).timestamp() * 1000)


def test_secrets_from_environment_then_file(tmp_path):
    f = tmp_path / "secrets.env"
    f.write_text("# mine\nMEERWAARDE_IBKR_TOKEN=fromfile\nset MEERWAARDE_IBKR_QUERY=99\nMEERWAARDE_BITVAVO_KEY='k'\n")
    s = load_secrets(env={"MEERWAARDE_SECRETS": str(f), "MEERWAARDE_IBKR_TOKEN": "fromenv"})
    assert s["MEERWAARDE_IBKR_TOKEN"] == "fromenv" and s["MEERWAARDE_IBKR_QUERY"] == "99" and s["MEERWAARDE_BITVAVO_KEY"] == "k"
    assert s["MEERWAARDE_BITVAVO_SECRET"] == ""


def test_sync_end_to_end_and_report_uses_the_rows(tmp_path):
    def get(url, params=None, headers=None, timeout=None):
        if "SendRequest" in url:
            return R("<FlexStatementResponse><Status>Success</Status><ReferenceCode>7</ReferenceCode></FlexStatementResponse>")
        if "GetStatement" in url:
            return R(FLEX)
        return R(data={"items": ITEMS, "currentPage": 1, "totalPages": 1})

    secrets = {"MEERWAARDE_IBKR_TOKEN": "t", "MEERWAARDE_IBKR_QUERY": "q", "MEERWAARDE_BITVAVO_KEY": "k", "MEERWAARDE_BITVAVO_SECRET": "s"}
    res = sync(tmp_path, secrets=secrets, get=get, sleep=lambda s: None)
    assert [r.ok for r in res] == [True, True]
    missing = sync(tmp_path, secrets={k: "" for k in secrets}, get=get, sleep=lambda s: None)
    assert [r.ok for r in missing] == [False, False] and "MEERWAARDE_IBKR_TOKEN" in missing[0].message
    rep = build_report([], fetch_prices=False, today=date(2026, 4, 1), parsed=[], synced=SyncStore(tmp_path).load().transactions)
    held = {(h.broker, h.asset): h.qty for h in rep.portfolio.holdings()}
    assert held[("LYNX", "XX0000000001")] == 10 and held[("Bitvavo", "BTC")] == pytest.approx(0.002 - 0.001 - 0.00001)   # own wallet: still held


def test_sync_reports_network_errors_without_raising(tmp_path):
    def boom(*a, **k):
        raise OSError("offline")
    secrets = {"MEERWAARDE_IBKR_TOKEN": "t", "MEERWAARDE_IBKR_QUERY": "q", "MEERWAARDE_BITVAVO_KEY": "k", "MEERWAARDE_BITVAVO_SECRET": "s"}
    res = sync(tmp_path, secrets=secrets, get=boom, sleep=lambda s: None)
    assert [r.ok for r in res] == [False, False] and all("offline" in r.message for r in res)
