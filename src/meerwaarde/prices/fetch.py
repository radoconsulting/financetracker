"""Online price sources, with a small on-disk cache.

Every function returns None when the source has no answer; callers fall back to the
next source or to a price you enter yourself.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (meerwaarde portfolio tool)", "Accept": "application/json,text/plain,*/*"}
NOW_TTL = 6 * 3600            # current prices: refresh after 6 hours
HIST_TTL = 365 * 24 * 3600    # historical prices don't change


def default_cache_dir() -> Path:
    base = os.environ.get("MEERWAARDE_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "meerwaarde")
    return Path(base)


class Fetcher:
    def __init__(self, cache_dir=None, timeout: float = 15.0):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self._ecb = None
        self._yahoo_symbols: dict = {}

    # ---------- http with cache ----------
    def _get(self, url: str, ttl: int, binary: bool = False, params: dict | None = None):
        key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
        folder = self.cache_dir / ("now" if ttl <= NOW_TTL else "history")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (key + (".bin" if binary else ".json"))
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            return path.read_bytes() if binary else json.loads(path.read_text())
        r = requests.get(url, params=params, headers=UA, timeout=self.timeout)
        r.raise_for_status()
        if binary:
            path.write_bytes(r.content)
            return r.content
        data = r.json()
        path.write_text(json.dumps(data))
        return data

    def clear_current(self):
        """Forget cached current prices so the next request fetches them again."""
        folder = self.cache_dir / "now"
        if folder.exists():
            for f in folder.iterdir():
                f.unlink(missing_ok=True)

    # ---------- Bitvavo (crypto, EUR) ----------
    def bitvavo_price(self, coin: str) -> float | None:
        data = self._get("https://api.bitvavo.com/v2/ticker/price", NOW_TTL, params={"market": f"{coin}-EUR"})
        return parse_bitvavo_price(data)

    def bitvavo_close_at(self, coin: str, day: date) -> float | None:
        """Price at midnight (Brussels) at the end of `day`: close of the last hourly candle of that day."""
        end_local = datetime(day.year, day.month, day.day, 23, 0, tzinfo=timezone(timedelta(hours=1)))
        start_ms = int(end_local.timestamp() * 1000)
        data = self._get(f"https://api.bitvavo.com/v2/{coin}-EUR/candles", HIST_TTL,
                         params={"interval": "1h", "start": start_ms - 3 * 3600 * 1000, "end": start_ms + 2 * 3600 * 1000})
        return parse_bitvavo_candle_close(data, start_ms)

    # ---------- justETF (ETFs, EUR) ----------
    def justetf_quote(self, isin: str):
        data = self._get(f"https://www.justetf.com/api/etfs/{isin}/quote", NOW_TTL,
                         params={"locale": "en", "currency": "EUR", "isin": isin})
        return parse_justetf_quote(data)

    def justetf_close_at(self, isin: str, day: date) -> float | None:
        data = self._get(f"https://www.justetf.com/api/etfs/{isin}/performance-chart", HIST_TTL,
                         params={"locale": "en", "currency": "EUR", "valuesType": "MARKET_VALUE", "reduceData": "false",
                                 "includeDividends": "false", "dateFrom": (day - timedelta(days=7)).isoformat(),
                                 "dateTo": (day + timedelta(days=1)).isoformat()})
        return parse_justetf_series_at(data, day)

    # ---------- Yahoo Finance (shares; unofficial) ----------
    def yahoo_symbol(self, isin: str) -> str | None:
        if isin in self._yahoo_symbols:
            return self._yahoo_symbols[isin]
        data = self._get("https://query2.finance.yahoo.com/v1/finance/search", HIST_TTL, params={"q": isin, "quotesCount": 5, "newsCount": 0})
        quotes = [q for q in data.get("quotes", []) if q.get("symbol")]
        sym = quotes[0]["symbol"] if quotes else None
        self._yahoo_symbols[isin] = sym
        return sym

    def yahoo_quote(self, isin: str):
        sym = self.yahoo_symbol(isin)
        if not sym:
            return None
        data = self._get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", NOW_TTL, params={"range": "5d", "interval": "1d"})
        return parse_yahoo_last(data)

    def yahoo_close_at(self, isin: str, day: date):
        sym = self.yahoo_symbol(isin)
        if not sym:
            return None
        p1 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) - 7 * 86400
        p2 = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + 2 * 86400
        data = self._get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", HIST_TTL,
                         params={"period1": p1, "period2": p2, "interval": "1d"})
        return parse_yahoo_at(data, day)

    # ---------- ECB reference rates ----------
    def ecb_rate(self, currency: str, day: date) -> float | None:
        """Units of `currency` per EUR on `day` (or the last publication before it)."""
        if self._ecb is None:
            raw = self._get("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip", 24 * 3600, binary=True)
            self._ecb = parse_ecb_hist(raw)
        rows = self._ecb.get(currency)
        if not rows:
            return None
        for back in range(0, 10):
            d = (day - timedelta(days=back)).isoformat()
            if d in rows:
                return rows[d]
        return None


# ---------- response parsers (kept separate so they can be tested offline) ----------
def parse_bitvavo_price(data) -> float | None:
    if isinstance(data, dict) and data.get("price") not in (None, ""):
        return float(data["price"])
    return None


def parse_bitvavo_candle_close(data, start_ms: int) -> float | None:
    if not isinstance(data, list) or not data:
        return None
    rows = sorted((int(c[0]), float(c[4])) for c in data)
    best = [c for t, c in rows if t <= start_ms]
    return best[-1] if best else rows[0][1]


def _raw(v):
    if isinstance(v, dict):
        v = v.get("raw", v.get("value"))
    return float(v) if v not in (None, "") else None


def parse_justetf_quote(data):
    if not isinstance(data, dict):
        return None
    price = _raw(data.get("latestQuote"))
    d = data.get("latestQuoteDate")
    if price is None:
        return None
    return price, (date.fromisoformat(d[:10]) if d else None)


def parse_justetf_series_at(data, day: date) -> float | None:
    series = data.get("series") if isinstance(data, dict) else None
    if not series:
        return None
    best = None
    for pt in series:
        d = pt.get("date")
        if d and d[:10] <= day.isoformat():
            best = _raw(pt.get("value"))
    return best


def parse_yahoo_last(data):
    try:
        res = data["chart"]["result"][0]
        meta = res["meta"]
        price = meta.get("regularMarketPrice")
        ts = meta.get("regularMarketTime")
        on = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else None
        return float(price), meta.get("currency", "USD"), on
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_yahoo_at(data, day: date):
    try:
        res = data["chart"]["result"][0]
        closes = res["indicators"]["quote"][0]["close"]
        best = None
        for ts, c in zip(res["timestamp"], closes):
            if c is None:
                continue
            if datetime.fromtimestamp(ts, tz=timezone.utc).date() <= day:
                best = float(c)
        return (best, res["meta"].get("currency", "USD")) if best is not None else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def parse_ecb_hist(raw: bytes) -> dict:
    """{currency: {YYYY-MM-DD: units per EUR}} from the ECB's eurofxref-hist.zip."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name = [n for n in z.namelist() if n.endswith(".csv")][0]
        text = z.read(name).decode("utf-8")
    out: dict = {}
    for row in csv.DictReader(io.StringIO(text)):
        d = row.get("Date")
        for cur, v in row.items():
            if cur in ("Date", "", None) or not v or v.strip() in ("N/A", ""):
                continue
            out.setdefault(cur.strip(), {})[d] = float(v)
    return out
