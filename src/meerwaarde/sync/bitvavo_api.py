"""Bitvavo through its REST API with a read-only key (backlog item 12, docs/decisions.md D28).

`GET /v2/account/history` (signed, paged, 100 per page) lists every transaction: buy, sell, staking, deposit,
withdrawal, ... `parse_history` maps them to the rows the PDF parser (parsers/bitvavo.py) makes. Two details aren't
pinned down by Bitvavo's documentation and are checked against the PDF by `reconcile()` on the first sync:
whether `sentAmount` of a buy includes the fee (`fee_in_sent`) and the time zone of the PDF (`tz`).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from ..models import BUY, CASH_IN, CASH_OUT, DEPOSIT, REWARD, SELL, WITHDRAWAL, Transaction
from ..parsers.bitvavo import BROKER, REWARD_TYPES

API = "https://api.bitvavo.com"
SKIP_TYPES = {"internal_transfer", "withdrawal_cancelled", "loan", "external_transferred_funds"}
REWARDS = REWARD_TYPES | {"distribution", "manually_assigned_bitvavo"}


def signature(secret: str, timestamp: int, method: str, path: str, body: str = "") -> str:
    """HMAC-SHA256 (hex) of timestamp + method + path (with /v2 and the query) + body."""
    return hmac.new(secret.encode(), f"{timestamp}{method}{path}{body}".encode(), hashlib.sha256).hexdigest()


class BitvavoError(Exception):
    pass


def fetch_history(key: str, secret: str, from_ms: int | None = None, get=None, now_ms=None, sleep=time.sleep) -> list[dict]:
    """All history items since `from_ms` (all when None)."""
    if get is None:
        import requests
        get = requests.get
    items, page = [], 1
    while True:
        params = {"page": page, "maxItems": 100}
        if from_ms:
            params["fromDate"] = int(from_ms)
        path = "/v2/account/history?" + urlencode(params)
        ts = int(now_ms() if now_ms else time.time() * 1000)
        headers = {"Bitvavo-Access-Key": key, "Bitvavo-Access-Timestamp": str(ts),
                   "Bitvavo-Access-Signature": signature(secret, ts, "GET", path), "Bitvavo-Access-Window": "20000"}
        r = get(API + path, headers=headers, timeout=30)
        code = getattr(r, "status_code", 200)
        data = r.json() if hasattr(r, "json") else json.loads(r.text)
        if code != 200 or (isinstance(data, dict) and data.get("errorCode")):
            msg = data.get("error") if isinstance(data, dict) else str(data)[:120]
            raise BitvavoError(f"Bitvavo: HTTP {code}: {msg}")
        items += data.get("items") or []
        if page >= int(data.get("totalPages") or 1):
            return items
        page += 1
        sleep(0.2)


def _f(v) -> float:
    try:
        return float(v) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def _ts(v: str, tz) -> datetime:
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(tz).replace(tzinfo=None, microsecond=0)


def brussels():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Brussels")
    except Exception:  # no time zone database: fall back to UTC (the reconciliation shows the offset)
        return timezone.utc


def parse_history(items: list[dict], filename: str = "bitvavo-api.json", fee_in_sent: bool = True,
                  tz=None) -> tuple[list[Transaction], list[str]]:
    tz = tz or brussels()
    txns, warnings = [], []
    for i, it in enumerate(sorted(items, key=lambda x: str(x.get("executedAt")))):
        typ = (it.get("type") or "").lower()
        ts = _ts(it.get("executedAt"), tz)
        sent_c, sent = (it.get("sentCurrency") or "").upper(), _f(it.get("sentAmount"))
        recv_c, recv = (it.get("receivedCurrency") or "").upper(), _f(it.get("receivedAmount"))
        fee_c, fee = (it.get("feesCurrency") or "").upper(), _f(it.get("feesAmount"))
        price = _f(it.get("priceAmount")) or None
        base = dict(broker=BROKER, ts=ts, asset_class="Crypto", ref=str(it.get("transactionId") or ""), source=filename, order=i)
        if typ in SKIP_TYPES:
            warnings.append(f"Bitvavo API: {typ} on {ts:%Y-%m-%d} skipped")
            continue
        if typ in ("buy", "sell"):
            coin = recv_c if typ == "buy" else sent_c
            eur_c = sent_c if typ == "buy" else recv_c
            if eur_c != "EUR":
                warnings.append(f"Bitvavo API: {ts:%Y-%m-%d} {typ} of {coin} against {eur_c}; only EUR markets are supported, skipped")
                continue
            fee_eur = fee if fee_c == "EUR" else 0.0
            if typ == "buy":
                paid = abs(sent) if fee_in_sent else abs(sent) + fee_eur
                txns.append(Transaction(**base, kind=BUY, asset=coin, name=coin, qty=abs(recv), price=price, price_cur="EUR",
                                        gross_eur=paid - fee_eur, fees_eur=fee_eur, total_eur=-paid))
            else:
                got = abs(recv)                      # received after the fee
                txns.append(Transaction(**base, kind=SELL, asset=coin, name=coin, qty=-abs(sent), price=price, price_cur="EUR",
                                        gross_eur=got + fee_eur, fees_eur=fee_eur, total_eur=got))
        elif typ == "deposit":
            cur, amt = (recv_c or sent_c), (recv or sent)
            if cur == "EUR":
                txns.append(Transaction(**{**base, "asset_class": "Cash/FX"}, kind=CASH_IN, asset="EUR", name="EUR",
                                        qty=abs(amt), amount_eur=abs(amt), currency="EUR"))
            else:
                txns.append(Transaction(**base, kind=DEPOSIT, asset=cur, name=cur, qty=abs(amt)))
        elif typ == "withdrawal":
            cur, amt = (sent_c or recv_c), (sent or recv)
            if cur == "EUR":
                txns.append(Transaction(**{**base, "asset_class": "Cash/FX"}, kind=CASH_OUT, asset="EUR", name="EUR",
                                        qty=-abs(amt), amount_eur=-abs(amt), currency="EUR"))
            else:
                txns.append(Transaction(**base, kind=WITHDRAWAL, asset=cur, name=cur, qty=-abs(amt),
                                        fee_qty=fee if fee_c == cur else 0.0, note=str(it.get("address") or "")))
        elif typ in REWARDS:
            if recv > 0 and recv_c and recv_c != "EUR":
                txns.append(Transaction(**base, kind=REWARD, asset=recv_c, name=recv_c, qty=recv, note=typ))
        else:
            warnings.append(f"Bitvavo API: unknown type {typ!r} on {ts:%Y-%m-%d} skipped")
    return txns, warnings


def reconcile(api_rows: list[Transaction], pdf_rows: list[Transaction]) -> dict:
    """Compare API rows with PDF rows that have the same transaction id: counts, time offset, and which fee rule fits."""
    pdf = {t.ref: t for t in pdf_rows if t.ref}
    pairs = [(a, pdf[a.ref]) for a in api_rows if a.ref in pdf and a.kind == pdf[a.ref].kind]
    offsets = sorted({round((a.ts - p.ts).total_seconds() / 3600, 2) for a, p in pairs})
    buys = [(a, p) for a, p in pairs if a.kind == BUY]
    total_ok = sum(1 for a, p in pairs if a.total_eur is not None and p.total_eur is not None and abs(a.total_eur - p.total_eur) < 0.02)
    with_total = sum(1 for a, p in pairs if a.total_eur is not None and p.total_eur is not None)
    qty_ok = sum(1 for a, p in pairs if abs((a.qty or 0) - (p.qty or 0)) < 1e-9)
    fee_rule = None
    if buys:
        same = sum(1 for a, p in buys if abs(a.total_eur - p.total_eur) < 0.02)
        plus = sum(1 for a, p in buys if abs(a.total_eur - a.fees_eur - p.total_eur) < 0.02)
        fee_rule = "included" if same >= plus else "added"
    return dict(api=len(api_rows), pdf=len(pdf_rows), matched=len(pairs), qty_ok=qty_ok, total_ok=total_ok,
                with_total=with_total, hour_offsets=offsets, buy_fee_rule=fee_rule)
