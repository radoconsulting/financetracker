"""Allocation look-through (backlog item 15, docs/decisions.md D30).

Exposure by region, country, sector, currency and asset class, looking through funds to what they hold, and the
overlap between funds. Per position, first source that has it:
1. your own breakdown in `<data dir>/exposure.json` ({asset: {asset_class, countries, sectors, currencies, top}});
2. ETFs and funds: the justETF profile (top 10 holdings with ISINs, the 4 largest countries and sectors + "Other");
3. by class: a share is 100% its company (country from the ISIN prefix, sector unknown), crypto, cash, gold and
   insurance are their own bucket, a fund without data is "Unknown".
Currency exposure is approximated from the countries (the currency of each country's market), except for cash
(its own currency). Overlap between two funds = Σ min(weight in A, weight in B) over their shared top-10 holdings:
a lower bound, because only the top 10 are known.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REGIONS = {
    "North America": ("United States", "Canada"),
    "Europe": ("United Kingdom", "France", "Germany", "Switzerland", "Netherlands", "Belgium", "Ireland", "Sweden",
               "Denmark", "Norway", "Finland", "Spain", "Italy", "Austria", "Portugal", "Luxembourg", "Poland", "Greece"),
    "Japan": ("Japan",),
    "Asia-Pacific (developed)": ("Australia", "Hong Kong", "Singapore", "New Zealand"),
    "Emerging markets": ("China", "Taiwan", "India", "South Korea", "Brazil", "Saudi Arabia", "South Africa", "Mexico",
                         "Indonesia", "Thailand", "Malaysia", "United Arab Emirates", "Turkey", "Chile", "Qatar",
                         "Philippines", "Kuwait", "Peru", "Colombia", "Hungary", "Czech Republic", "Egypt"),
}
REGION_OF = {c: r for r, cs in REGIONS.items() for c in cs}
EURO = ("France", "Germany", "Netherlands", "Belgium", "Ireland", "Finland", "Spain", "Italy", "Austria", "Portugal",
        "Luxembourg", "Greece")
CURRENCY_OF = {**{c: "EUR" for c in EURO}, "United States": "USD", "Canada": "CAD", "United Kingdom": "GBP",
               "Switzerland": "CHF", "Japan": "JPY", "Sweden": "SEK", "Denmark": "DKK", "Norway": "NOK",
               "Australia": "AUD", "Hong Kong": "HKD", "Singapore": "SGD", "China": "CNY", "Taiwan": "TWD",
               "India": "INR", "South Korea": "KRW", "Brazil": "BRL"}
ISIN_COUNTRY = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "FR": "France", "DE": "Germany",
                "CH": "Switzerland", "NL": "Netherlands", "BE": "Belgium", "IE": "Ireland", "SE": "Sweden", "DK": "Denmark",
                "NO": "Norway", "FI": "Finland", "ES": "Spain", "IT": "Italy", "AT": "Austria", "PT": "Portugal",
                "LU": "Luxembourg", "JP": "Japan", "AU": "Australia", "HK": "Hong Kong", "SG": "Singapore", "CN": "China",
                "TW": "Taiwan", "IN": "India", "KR": "South Korea", "BR": "Brazil", "JE": "United Kingdom",
                "GG": "United Kingdom", "BM": "United States", "KY": "China"}
CLASS_BUCKET = {"Stock": "Equity", "ETF": "Equity", "Fund": "Equity", "Turbo": "Equity (leveraged)", "Bond ETF": "Bonds",
                "Bond": "Bonds", "Crypto": "Crypto", "Cash/FX": "Cash", "Gold": "Gold", "Insurance": "Insurance"}
DIMENSIONS = ("asset_class", "region", "country", "sector", "currency")
NOT_REGIONAL = "Not regional (crypto, gold, cash, policies)"
OTHER = "Other (beyond the funds' largest countries)"


@dataclass
class Exposure:
    asset_class: dict          # bucket -> weight
    countries: dict
    sectors: dict
    currencies: dict
    top: list = field(default_factory=list)     # [(isin, name, weight)]
    source: str = ""
    as_of: str = ""
    holdings: int | None = None


def _norm(d: dict) -> dict:
    d = {k: float(v) for k, v in (d or {}).items() if v}
    total = sum(d.values())
    return {k: v / total for k, v in d.items()} if total > 0 else {}


def _currencies_from(countries: dict) -> dict:
    out = defaultdict(float)
    for c, w in countries.items():
        out[CURRENCY_OF.get(c, "Other" if c not in ("Unknown",) else "Unknown")] += w
    return dict(out)


def load_overrides(path) -> dict:
    p = Path(path) if path else None
    return json.loads(p.read_text(encoding="utf-8") or "{}") if p and p.exists() else {}


def exposure_for(h, fetcher=None, overrides: dict | None = None, errors: list | None = None) -> Exposure:
    """Exposure of one holding (see the module docstring for the order of sources)."""
    o = (overrides or {}).get(h.asset)
    bucket = CLASS_BUCKET.get(h.asset_class, "Other")
    if o:
        countries = _norm(o.get("countries") or {"Unknown": 1})
        ac = o.get("asset_class")
        ac = {ac: 1.0} if isinstance(ac, str) else (ac or {bucket: 1.0})
        return Exposure(_norm(ac), countries, _norm(o.get("sectors") or {"Unknown": 1}),
                        _norm(o.get("currencies") or _currencies_from(countries)),
                        [tuple(t) for t in o.get("top") or []], "your breakdown", str(o.get("as_of") or ""))
    if h.asset_class == "Cash/FX":
        return Exposure({"Cash": 1.0}, {"—": 1.0}, {"Cash": 1.0}, {h.asset if len(h.asset) == 3 else "EUR": 1.0}, source="class")
    if h.asset_class == "Crypto":
        return Exposure({"Crypto": 1.0}, {"—": 1.0}, {"Crypto": 1.0}, {"Crypto": 1.0}, [(h.asset, h.name, 1.0)], "class")
    if h.asset_class in ("Gold", "Insurance"):
        return Exposure({bucket: 1.0}, {"—": 1.0}, {bucket: 1.0}, {"USD" if h.asset_class == "Gold" else "EUR": 1.0},
                        source="class")
    if h.asset_class in ("Stock", "Turbo", "Bond"):
        country = ISIN_COUNTRY.get(h.asset[:2], "Unknown") if len(h.asset) == 12 else "Unknown"
        return Exposure({bucket: 1.0}, {country: 1.0}, {"Unknown (single shares)" if h.asset_class != "Bond" else "Bonds": 1.0},
                        _currencies_from({country: 1.0}), [(h.asset, h.name, 1.0)] if h.asset_class == "Stock" else [],
                        "the ISIN's country")
    d = None
    if fetcher and len(h.asset) == 12:
        try:
            d = fetcher.justetf_profile(h.asset)
        except Exception as e:  # network trouble must never break a report
            if errors is not None:
                errors.append(f"{h.asset} (composition): {e}")
    if d and (d.get("countries") or d.get("top")):
        countries = _norm(d.get("countries") or {"Unknown": 1})
        return Exposure({bucket: 1.0}, countries, _norm(d.get("sectors") or {"Unknown": 1}), _currencies_from(countries),
                        [t for t in d.get("top") or [] if t[2]], "justETF", d.get("as_of", ""), d.get("holdings"))
    return Exposure({bucket: 1.0}, {"Unknown": 1.0}, {"Unknown": 1.0}, {"Unknown": 1.0}, source="no data")


@dataclass
class Allocation:
    positions: list            # [dict(broker, asset, name, value, asset_class, source, as_of, holdings)]
    totals: dict               # dimension -> {name: €}
    top: list                  # [(isin, name, €, [via ...])] look-through holdings, largest first
    overlap: list              # [(fund A, fund B, overlap share, shared count)]
    total: float
    notes: list = field(default_factory=list)


def allocation(rep, overrides: dict | None = None, fetch: bool | None = None) -> Allocation:
    fetcher = rep.prices.fetcher if fetch in (None, True) else None
    rows, totals = [], {d: defaultdict(float) for d in DIMENSIONS}
    top = defaultdict(lambda: [None, 0.0, set()])
    funds: dict = {}                       # isin -> (name, {isin: weight}): one entry per fund, whatever the broker
    for h in rep.portfolio.holdings():
        p = rep.prices.now(h)
        if not p:
            continue
        value = h.qty * p.value
        if value <= 0.005:
            continue
        e = exposure_for(h, fetcher, overrides, rep.prices.errors)
        rows.append(dict(broker=h.broker, asset=h.asset, name=h.name, value=round(value, 2),
                         asset_class=", ".join(e.asset_class), source=e.source, as_of=e.as_of, holdings=e.holdings))
        for k, w in e.asset_class.items():
            totals["asset_class"][k] += value * w
        for c, w in e.countries.items():
            totals["country"][c] += value * w
            region = NOT_REGIONAL if c == "—" else ("Unknown" if c == "Unknown" else REGION_OF.get(c, OTHER))
            totals["region"][region] += value * w
        for s, w in e.sectors.items():
            totals["sector"][s] += value * w
        for c, w in e.currencies.items():
            totals["currency"][c] += value * w
        for isin, name, w in e.top:
            if not isin:
                continue
            t = top[isin]
            t[0] = t[0] or name
            t[1] += value * w
            t[2].add(h.name if len(e.top) > 1 or w < 1 else "direct")
        if len(e.top) > 1 and h.asset not in funds:
            funds[h.asset] = (h.name, {i: w for i, _n, w in e.top if i})
    total = sum(r["value"] for r in rows)
    overlap = []
    items = list(funds.values())
    for i, (a, ta) in enumerate(items):
        for b, tb in items[i + 1:]:
            common = set(ta) & set(tb)
            if common:
                overlap.append((a, b, sum(min(ta[x], tb[x]) for x in common), len(common)))
    overlap.sort(key=lambda x: -x[2])
    notes = []
    if any(r["source"] == "justETF" for r in rows):
        notes.append("Funds: justETF shows the 4 largest countries and sectors; the rest is in 'Other'. Overlap and the "
                     "look-through holdings use each fund's top 10 only, so they are lower bounds.")
    if any(r["source"] == "no data" for r in rows):
        notes.append("Some funds have no composition data (offline, or not on justETF): they count as 'Unknown'. "
                     "You can enter their breakdown in exposure.json.")
    notes.append("Currency is approximated from the countries a fund invests in (a currency-hedged share class differs).")
    return Allocation(sorted(rows, key=lambda r: -r["value"]),
                      {d: dict(sorted(v.items(), key=lambda kv: -kv[1])) for d, v in totals.items()},
                      sorted(((i, n, v, sorted(via)) for i, (n, v, via) in top.items()), key=lambda x: -x[2]),
                      overlap, total, notes)
