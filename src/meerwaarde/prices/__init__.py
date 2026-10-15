"""Prices for valuing holdings and for the 31-12-2025 reference value.

Order of preference for every price: your own override, the price entered with a manual position, the broker
statement, then
online sources (Bitvavo for crypto, justETF for ETFs, Yahoo Finance for shares,
the ECB for exchange rates). Every price carries its date and source.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .fetch import Fetcher


@dataclass
class Price:
    value: float          # EUR per unit
    on: date | None
    source: str
    local: float | None = None
    currency: str = "EUR"


class PriceBook:
    def __init__(self, statements=(), fetch: bool = True, cache_dir=None, reference_date: date = date(2025, 12, 31),
                 today: date | None = None):
        self.statements = list(statements)
        self.reference_date = reference_date
        self.today = today or date.today()
        self.fetcher = Fetcher(cache_dir=cache_dir) if fetch else None
        self.overrides_now: dict[str, float] = {}
        self.overrides_ref: dict[str, float] = {}
        self.manual_now: dict[str, Price] = {}       # prices entered with a manual position (manual.py)
        self.manual_ref: dict[str, Price] = {}
        self.errors: list[str] = []
        self._now_cache: dict = {}
        self._ref_cache: dict = {}

    # ---------- overrides ----------
    def set_override(self, asset: str, now: float | None = None, reference: float | None = None):
        if now is not None:
            self.overrides_now[asset] = float(now)
        if reference is not None:
            self.overrides_ref[asset] = float(reference)
        self._now_cache.pop(asset, None)
        self._ref_cache.pop(asset, None)

    def set_manual(self, asset: str, now: float | None = None, now_date: date | None = None,
                   reference: float | None = None):
        """Price entered with a manual position: below an override, above statements and online sources."""
        if now is not None:
            self.manual_now[asset] = Price(float(now), now_date or self.today, "Your entry (manual position)")
        if reference is not None:
            self.manual_ref[asset] = Price(float(reference), self.reference_date, "Your entry (manual position)")
        self._now_cache.pop(asset, None)
        self._ref_cache.pop(asset, None)

    def load_overrides_csv(self, text: str):
        """CSV with columns asset, price_now, price_31dec (EUR per unit; either may be empty)."""
        for row in csv.DictReader(io.StringIO(text)):
            a = (row.get("asset") or "").strip()
            if not a:
                continue
            now = (row.get("price_now") or "").strip()
            ref = (row.get("price_31dec") or row.get("price_reference") or "").strip()
            self.set_override(a, float(now) if now else None, float(ref) if ref else None)

    def overrides_csv(self) -> str:
        assets = sorted(set(self.overrides_now) | set(self.overrides_ref))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["asset", "price_now", "price_31dec"])
        for a in assets:
            w.writerow([a, self.overrides_now.get(a, ""), self.overrides_ref.get(a, "")])
        return buf.getvalue()

    # ---------- exchange rates ----------
    def eur_per(self, currency: str, on: date | None = None) -> float | None:
        if currency in (None, "", "EUR"):
            return 1.0
        on = on or self.today
        for s in reversed(self.statements):
            if s.period_end == on and currency in s.fx_close:
                return s.fx_close[currency]
            if s.period_start and s.period_start - timedelta(days=1) == on and currency in s.fx_prior:
                return s.fx_prior[currency]
        if self.fetcher:
            r = self.fetcher.ecb_rate(currency, on)
            if r:
                return 1.0 / r
        if on >= self.today - timedelta(days=7):
            for s in reversed(self.statements):
                if currency in s.fx_close:
                    return s.fx_close[currency]
        return None

    # ---------- current price ----------
    def now(self, holding) -> Price | None:
        a = holding.asset
        if a in self._now_cache:
            return self._now_cache[a]
        p = self._now(holding)
        self._now_cache[a] = p
        return p

    def _now(self, h) -> Price | None:
        a = h.asset
        if a in self.overrides_now:
            return Price(self.overrides_now[a], self.today, "Your price")
        if a in self.manual_now:
            return self.manual_now[a]
        if h.asset_class == "Cash/FX" and len(a) == 3:
            r = self.eur_per(a, self.today) or self._statement_fx(a)
            return Price(r, self.today, "ECB / statement rate") if r else None
        st = self._statement_close(a)
        fetched = None
        if self.fetcher:
            try:
                if h.asset_class == "Crypto":
                    v = self.fetcher.bitvavo_price(a)
                    if v is not None:
                        fetched = Price(v, self.today, "Bitvavo")
                elif len(a) == 12:
                    q = self.fetcher.justetf_quote(a) if h.asset_class in ("ETF", "Bond ETF", "Fund") else None
                    if q:
                        fetched = Price(q[0], q[1], "justETF")
                    else:
                        y = self.fetcher.yahoo_quote(a)
                        if y:
                            price, cur, on = y
                            k = self.eur_per(cur, on) if cur != "EUR" else 1.0
                            if k:
                                fetched = Price(price * k, on, f"Yahoo Finance ({cur})", price, cur)
            except Exception as e:  # network trouble must never break the report
                self.errors.append(f"{a}: {e}")
        cands = [p for p in (st, fetched) if p]
        if not cands:
            return None
        return max(cands, key=lambda p: p.on or date.min)

    def _statement_close(self, asset) -> Price | None:
        for s in reversed(self.statements):
            for sym, p in s.positions.items():
                if p.get("isin") == asset or sym == asset:
                    return Price(p["close_eur"], s.period_end, f"{s.broker} statement", p["close"], p["cur"])
        return None

    def _statement_fx(self, cur):
        for s in reversed(self.statements):
            if cur in s.fx_close:
                return s.fx_close[cur]
        return None

    # ---------- reference price (31-12-2025) ----------
    def reference(self, holding) -> Price | None:
        a = holding.asset
        if a in self._ref_cache:
            return self._ref_cache[a]
        p = self._reference(holding)
        self._ref_cache[a] = p
        return p

    def _reference(self, h) -> Price | None:
        a, ref = h.asset, self.reference_date
        if a in self.overrides_ref:
            return Price(self.overrides_ref[a], ref, "Your price")
        if a in self.manual_ref:
            return self.manual_ref[a]
        # statement that starts the day after the reference date: its 'prior' prices are the reference prices
        for s in self.statements:
            if s.period_start and s.period_start - timedelta(days=1) == ref:
                if h.asset_class == "Cash/FX" and a in s.fx_prior:
                    return Price(s.fx_prior[a], ref, f"{s.broker} statement")
                for sym, px in s.prior_prices.items():
                    isin = s.instruments.get(sym, {}).get("isin")
                    if isin == a or sym == a:
                        cur = s.positions.get(sym, {}).get("cur", s.base)
                        k = 1.0 if cur == s.base else s.fx_prior.get(cur)
                        if k:
                            return Price(px * k, ref, f"{s.broker} statement", px, cur)
            if s.period_end == ref:
                for sym, p in s.positions.items():
                    if p.get("isin") == a or sym == a:
                        return Price(p["close_eur"], ref, f"{s.broker} statement", p["close"], p["cur"])
        if h.asset_class == "Cash/FX" and len(a) == 3:
            r = self.eur_per(a, ref)
            return Price(r, ref, "ECB") if r else None
        if not self.fetcher:
            return None
        try:
            if h.asset_class == "Crypto":
                v = self.fetcher.bitvavo_close_at(a, ref)
                return Price(v, ref, "Bitvavo (price at midnight)") if v is not None else None
            if len(a) == 12:
                if h.asset_class in ("ETF", "Bond ETF", "Fund"):
                    v = self.fetcher.justetf_close_at(a, ref)
                    if v is not None:
                        return Price(v, ref, "justETF")
                y = self.fetcher.yahoo_close_at(a, ref)
                if y:
                    price, cur = y
                    k = self.eur_per(cur, ref) if cur != "EUR" else 1.0
                    if k:
                        return Price(price * k, ref, f"Yahoo Finance ({cur})", price, cur)
        except Exception as e:
            self.errors.append(f"{a} (31-12): {e}")
        return None

    # ---------- daily history (performance) ----------
    def history(self, asset: str, asset_class: str, start: date, end: date) -> tuple[dict, str]:
        """Daily prices in EUR between start and end, and their source; ({}, "") when no online source has them.
        Never raises: errors go to `self.errors`."""
        if not self.fetcher or asset_class == "Cash/FX":
            return {}, ""
        try:
            if asset_class == "Crypto":
                s = self.fetcher.bitvavo_series(asset, start, end)
                return (s, "Bitvavo") if s else ({}, "")
            if len(asset) == 12:
                if asset_class in ("ETF", "Bond ETF", "Fund"):
                    s = self.fetcher.justetf_series(asset, start, end)
                    if s:
                        return s, "justETF"
                y = self.fetcher.yahoo_series(asset, start, end)
                if y and y[0]:
                    series, cur = y
                    if cur == "EUR":
                        return series, "Yahoo Finance"
                    out = {}
                    for d, v in series.items():
                        k = self.eur_per(cur, d)
                        if k:
                            out[d] = v * k
                    return out, f"Yahoo Finance ({cur}, ECB rates)"
        except Exception as e:  # network trouble must never break a report
            self.errors.append(f"{asset} (history): {e}")
        return {}, ""
