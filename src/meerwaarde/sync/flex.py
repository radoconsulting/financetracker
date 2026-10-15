"""LYNX / Interactive Brokers through the Flex Web Service (backlog item 12, docs/decisions.md D28).

An Activity Flex Query (XML) with the sections Trades, Transaction Fees/Taxes, Cash Transactions, Transfers,
Corporate Actions and Open Positions. `parse_flex` turns it into the same rows the statement parser (parsers/lynx.py)
makes: EUR amounts from each row's `fxRateToBase` (the rate of that day), trade costs = commission + taxes (the PDF's
commission includes the stock exchange tax too), and the stock exchange tax also as information-only `TOB` rows.
Every row keeps its IBKR id in `ref`, so overlapping downloads never count twice.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime

from ..models import (BUY, CASH_IN, CASH_OUT, DEPOSIT, DIVIDEND, FEE, INTEREST, SELL, TOB, WITHDRAWAL, WITHHOLDING,
                      Transaction)
from ..parsers.lynx import BROKER, _asset_class

SEND = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
GET = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"

CASH_KINDS = {
    "dividends": DIVIDEND, "payment in lieu of dividends": DIVIDEND,
    "withholding tax": WITHHOLDING, "871(m) withholding": WITHHOLDING,
    "broker interest received": INTEREST, "broker interest paid": INTEREST, "bond interest received": INTEREST,
    "bond interest paid": INTEREST, "broker fees": FEE, "other fees": FEE, "commission adjustments": FEE,
}


@dataclass
class FlexResult:
    transactions: list
    positions: dict = field(default_factory=dict)     # isin -> {qty, mark_eur, name}
    period: tuple = (None, None)                      # (fromDate, toDate)
    account: str = ""
    warnings: list = field(default_factory=list)


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def _dt(v: str) -> datetime:
    """'2026-01-16;045043', '2026-01-16 04:50:43' or '2026-01-16' (time dropped: the statement rows are per day)."""
    return datetime.strptime((v or "")[:10], "%Y-%m-%d")


def _cls(a: dict) -> str:
    cat, sub = a.get("assetCategory", ""), a.get("subCategory", "")
    name = a.get("description") or a.get("symbol", "")
    if cat == "BOND":
        return "Bond"
    if cat in ("OPT", "FOP", "WAR", "IOPT"):
        return "Turbo"
    return _asset_class({"type": sub or cat}, name)


def _asset(a: dict) -> str:
    return a.get("isin") or a.get("symbol") or a.get("conid", "")


def parse_flex(text: str, filename: str = "flex.xml") -> FlexResult:
    root = ET.fromstring(text)
    if root.tag != "FlexQueryResponse":
        raise ValueError(f"{filename}: not a Flex Query result ({root.tag})")
    stmt = next(root.iter("FlexStatement"), None)
    if stmt is None:
        raise ValueError(f"{filename}: no FlexStatement in the file")
    res = FlexResult([], period=(_date(stmt.get("fromDate")), _date(stmt.get("toDate"))), account=stmt.get("accountId", ""))
    txns = res.transactions

    for i, a in enumerate(e.attrib for e in stmt.iter("Trade")):
        if a.get("levelOfDetail", "EXECUTION") not in ("EXECUTION", ""):
            continue                                    # orders/summaries repeat the executions
        fx = _f(a.get("fxRateToBase"), 1.0)
        cur = a.get("currency", "EUR")
        comm_fx = 1.0 if a.get("ibCommissionCurrency") in ("EUR", None, "") else (fx if a.get("ibCommissionCurrency") == cur else fx)
        if a.get("assetCategory") == "CASH":            # currency conversion: only its commission is a cost
            comm = _f(a.get("ibCommission")) * comm_fx
            if abs(comm) > 0.0049:
                txns.append(Transaction(broker=BROKER, ts=_dt(a.get("tradeDate") or a.get("dateTime")), kind=FEE,
                                        asset=a.get("symbol", ""), name=f"Currency conversion {a.get('symbol', '')}",
                                        asset_class="Cash/FX", amount_eur=round(comm, 2), source=filename,
                                        ref=f"flex:trade:{a.get('tradeID') or a.get('transactionID')}",
                                        note="Commission on a currency conversion"))
            continue
        qty = _f(a.get("quantity")) * _f(a.get("multiplier"), 1.0)
        if abs(qty) < 1e-12:
            continue
        proceeds = _f(a.get("proceeds"))                # local, negative for a buy
        costs = _f(a.get("ibCommission")) * comm_fx + _f(a.get("taxes")) * fx    # negative
        name = a.get("description") or a.get("symbol", "")
        txns.append(Transaction(broker=BROKER, ts=_dt(a.get("tradeDate") or a.get("dateTime")), kind=BUY if qty > 0 else SELL,
                                asset=_asset(a), name=name, asset_class=_cls(a), qty=qty, price=_f(a.get("tradePrice")),
                                price_cur=cur, gross_eur=abs(proceeds) * fx, fees_eur=-costs,
                                total_eur=proceeds * fx + costs, source=filename, order=i, note=a.get("symbol", ""),
                                ref=f"flex:trade:{a.get('tradeID') or a.get('transactionID')}"))

    for a in (e.attrib for e in stmt.iter("TransactionTax")):
        fx = _f(a.get("fxRateToBase"), 1.0)
        name = a.get("description") or a.get("symbol", "")
        txns.append(Transaction(broker=BROKER, ts=_dt(a.get("date") or a.get("reportDate")), kind=TOB, asset=_asset(a), name=name,
                                asset_class=_cls(a), qty=_f(a.get("quantity")), price=_f(a.get("tradePrice")) or None,
                                price_cur=a.get("currency"), currency=a.get("currency", "EUR"),
                                amount_eur=round(_f(a.get("taxAmount")) * fx, 2), source=filename,
                                note=a.get("taxDescription", ""), ref=f"flex:tax:{a.get('tradeId')}:{a.get('date')}"))

    for a in (e.attrib for e in stmt.iter("CashTransaction")):
        if a.get("levelOfDetail", "DETAIL") not in ("DETAIL", ""):
            continue
        typ = (a.get("type") or "").strip().lower()
        amount, fx, cur = _f(a.get("amount")), _f(a.get("fxRateToBase"), 1.0), a.get("currency", "EUR")
        when = _dt(a.get("settleDate") or a.get("dateTime") or a.get("reportDate"))
        ref = f"flex:cash:{a.get('transactionID')}"
        desc = a.get("description", "")
        if typ in ("deposits/withdrawals", "deposits & withdrawals"):
            txns.append(Transaction(broker=BROKER, ts=when, kind=CASH_IN if amount > 0 else CASH_OUT, asset=cur, name=desc,
                                    asset_class="Cash/FX", qty=amount, currency=cur, amount_eur=round(amount * fx, 2),
                                    source=filename, ref=ref))
            continue
        kind = CASH_KINDS.get(typ)
        if kind is None:
            res.warnings.append(f"LYNX Flex: cash transaction of type {a.get('type')!r} on {when:%Y-%m-%d} skipped")
            continue
        if kind == FEE:
            txns.append(Transaction(broker=BROKER, ts=when, kind=FEE, asset=cur, name=desc, asset_class="Cash/FX",
                                    amount_eur=round(amount * fx, 2), source=filename, ref=ref, note=desc))
            continue
        linked = bool(a.get("isin")) and kind in (DIVIDEND, WITHHOLDING) or (kind == INTEREST and typ.startswith("bond"))
        asset, acls = (_asset(a), _cls(a)) if linked else (cur, "Cash/FX")
        txns.append(Transaction(broker=BROKER, ts=when, kind=kind, asset=asset, name=desc if not linked else (a.get("description") or desc),
                                asset_class=acls, qty=0 if kind == DIVIDEND else amount, currency=cur,
                                amount_eur=round(amount * fx, 2), price=amount if kind == DIVIDEND else None,
                                source=filename, ref=ref, note=f"{amount:.2f} {cur}"))

    for a in (e.attrib for e in stmt.iter("Transfer")):
        qty = _f(a.get("quantity"))
        if abs(qty) < 1e-12 or a.get("assetCategory") == "CASH":
            continue
        txns.append(Transaction(broker=BROKER, ts=_dt(a.get("date") or a.get("dateTime") or a.get("reportDate")),
                                kind=DEPOSIT if qty > 0 else WITHDRAWAL, asset=_asset(a), name=a.get("description", ""),
                                asset_class=_cls(a), qty=qty, source=filename, ref=f"flex:transfer:{a.get('transactionID')}",
                                note=f"Transfer {a.get('direction', '')} {a.get('account', '')}".strip()))

    for a in (e.attrib for e in stmt.iter("CorporateAction")):
        res.warnings.append(f"LYNX Flex: corporate action on {a.get('description') or _asset(a)} "
                            f"({(a.get('reportDate') or a.get('dateTime') or '')[:10]}): not applied, check the position")

    for a in (e.attrib for e in stmt.iter("OpenPosition")):
        if a.get("levelOfDetail", "SUMMARY") not in ("SUMMARY", ""):
            continue
        res.positions[_asset(a)] = dict(qty=_f(a.get("position")) * _f(a.get("multiplier"), 1.0),
                                        mark_eur=_f(a.get("markPrice")) * _f(a.get("fxRateToBase"), 1.0),
                                        name=a.get("description", ""), on=_date(a.get("reportDate")))
    return res


def _date(v) -> date | None:
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date() if v else None
    except ValueError:
        try:
            return datetime.strptime(v[:8], "%Y%m%d").date()
        except ValueError:
            return None


# ---------------------------------------------------------------- download
class FlexError(Exception):
    pass


def _xml(text: str):
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise FlexError(f"unexpected answer from IBKR: {text[:120]!r}") from e


def fetch_flex(token: str, query_id: str, get=None, sleep=time.sleep, tries: int = 10) -> str:
    """SendRequest, then poll GetStatement until the report is ready. Returns the XML text."""
    if get is None:
        import requests
        get = requests.get
    r = get(SEND, params={"t": token, "q": query_id, "v": "3"}, headers={"User-Agent": "meerwaarde"}, timeout=30)
    root = _xml(r.text)
    if root.findtext("Status") != "Success":
        raise FlexError(f"IBKR refused the request: {root.findtext('ErrorCode')} {root.findtext('ErrorMessage')}")
    ref = root.findtext("ReferenceCode")
    url = root.findtext("Url") or GET
    for n in range(tries):
        sleep(2 + 3 * n)
        r = get(url, params={"t": token, "q": ref, "v": "3"}, headers={"User-Agent": "meerwaarde"}, timeout=60)
        if r.text.lstrip().startswith("<FlexQueryResponse"):
            return r.text
        root = _xml(r.text)
        code = root.findtext("ErrorCode")
        if code not in ("1019", "1018", "1021"):      # 1019: generation in progress; 1018: too many requests; 1021: retry
            raise FlexError(f"IBKR: {code} {root.findtext('ErrorMessage')}")
    raise FlexError("IBKR didn't finish the report in time; try again in a few minutes")
