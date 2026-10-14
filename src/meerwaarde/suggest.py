"""Optional: ask Google Gemini for cheaper funds with similar content (bring your own API key).

Only fund names, ISINs, index and costs of the positions you want to replace are sent; no quantities, amounts or
other personal data. With Google Search grounding on, Gemini can look up current facts and its sources are returned.
Every suggestion is marked unverified: ISINs are checked (format and check digit), funds already in the catalog keep
the catalog's facts, and an unknown Belgian registration means the switch planner assumes the 1.32% stock exchange
tax. Uses the generateContent REST endpoint (docs: https://ai.google.dev/gemini-api/docs/generate-content/google-search).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date

import requests

from .funds import Fund, lookup, valid_isin

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODELS = ("gemini-2.5-flash",)


@dataclass
class Suggestion:
    funds: list[Fund] = field(default_factory=list)
    citations: list[tuple[str, str]] = field(default_factory=list)   # (title, url)
    rejected: list[str] = field(default_factory=list)
    model: str = ""
    text: str = ""
    error: str = ""


def build_prompt(sources: list[Fund], exclude: tuple = ()) -> str:
    lines = []
    for i, f in enumerate(sources, 1):
        ter = "unknown" if f.ter is None else f"{f.ter * 100:.2f}%"
        kind = "accumulating" if f.accumulating else ("distributing" if f.accumulating is False else "unknown policy")
        lines.append(f"{i}. {f.name} (ISIN {f.isin}), index: {f.index or 'unknown'}, TER {ter}, {kind}")
    excl = f"\nDon't suggest these ISINs: {', '.join(exclude)}." if exclude else ""
    return f"""You help a private investor resident in Belgium who wants to replace the fund(s) below with a cheaper fund
holding similar content.

Fund(s) to replace:
{chr(10).join(lines)}

Constraints and Belgian tax facts:
- UCITS ETFs listed on a major European exchange (Xetra, Euronext Amsterdam, London), available at DEGIRO and Interactive Brokers.
- Prefer accumulating share classes: distributions are taxed at 30% in Belgium.
- Belgian stock exchange tax (TOB) per trade: 0.12% (max EUR 1,300) for distributing funds and for accumulating funds
  NOT registered in Belgium; 1.32% (max EUR 4,000) for accumulating funds of which any share class of the same
  compartment is registered in Belgium (FSMA). A lower TER rarely makes up for 1.32% on every purchase and sale.
- Similar content: same or very close exposure (region, large/mid/small caps, number of holdings). If the exposure
  differs (for example world instead of US only), say so in "similarity".
- Fund size preferably above EUR 500 million.{excl}

Answer with a JSON array only, no other text, with 3 to 6 objects having exactly these keys:
"name", "isin", "ticker", "index", "ter_pct" (number, 0.07 means 0.07%), "accumulating" (true/false),
"domicile", "replication", "fund_size_eur_m" (number), "registered_in_belgium" (true, false or null if unknown),
"similarity" (one sentence), "reason" (one sentence: why it is cheaper overall), "source_urls" (array of URLs).
Use current facts; use null for anything you are not sure about."""


def request_body(prompt: str, search: bool = True) -> dict:
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2}}
    if search:
        body["tools"] = [{"google_search": {}}]
    return body


def parse_response(payload: dict) -> tuple[str, list[tuple[str, str]]]:
    """Text and grounding sources from a generateContent response."""
    cands = payload.get("candidates") or []
    if not cands:
        return "", []
    c = cands[0]
    text = "".join(p.get("text", "") for p in (c.get("content") or {}).get("parts") or [] if isinstance(p, dict))
    cites = []
    for ch in (c.get("groundingMetadata") or {}).get("groundingChunks") or []:
        web = ch.get("web") or {}
        if web.get("uri"):
            cites.append((web.get("title") or web["uri"], web["uri"]))
    return text, cites


def extract_json_array(text: str) -> list[dict]:
    """The first JSON array in the text (fenced code block or bare)."""
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidates = [m.group(1)] if m else []
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
    return []


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def to_funds(items: list[dict], model: str = "", today: date | None = None) -> tuple[list[Fund], list[str]]:
    funds, rejected, seen = [], [], set()
    stamp = f"Gemini {model}, {(today or date.today()).isoformat()}".strip()
    for it in items:
        isin = str(it.get("isin") or "").strip().upper()
        name = str(it.get("name") or "").strip()
        if not valid_isin(isin):
            rejected.append(f"{name or '?'}: ISIN '{isin}' isn't valid")
            continue
        if isin in seen:
            continue
        seen.add(isin)
        links = [u for u in (it.get("source_urls") or []) if isinstance(u, str) and u.startswith("http")]
        known = lookup(isin)
        if known:
            f = Fund(**{**known.__dict__, "reason": str(it.get("reason") or ""), "links": links})
            f.source = known.source + " (catalog; suggested by Gemini)"
            funds.append(f)
            continue
        ter = _num(it.get("ter_pct"))
        ter = ter / 100 if ter is not None and 0 <= ter <= 2 else None
        acc = it.get("accumulating") if isinstance(it.get("accumulating"), bool) else None
        reg = it.get("registered_in_belgium") if isinstance(it.get("registered_in_belgium"), bool) else None
        if reg is True and acc:
            tob, note = 0.0132, "registered in Belgium per Gemini: 1.32%"
        elif reg is False or acc is False:
            tob, note = 0.0012, ("distributing: 0.12%" if acc is False else "not registered in Belgium per Gemini: 0.12%")
        else:
            tob, note = None, "registration unknown"
        reason = " ".join(x for x in (str(it.get("similarity") or ""), str(it.get("reason") or "")) if x)
        funds.append(Fund(isin, name, str(it.get("ticker") or ""), str(it.get("index") or ""), ter, acc, tob, note,
                          _num(it.get("fund_size_eur_m")), stamp, verified=False, reason=reason, links=links))
    return funds, rejected


def suggest(api_key: str, sources: list[Fund], model: str | None = None, search: bool = True, exclude: tuple = (),
            timeout: int = 90, session=None) -> Suggestion:
    """Ask Gemini; never raises: problems come back in `Suggestion.error`."""
    if not api_key:
        return Suggestion(error="No Gemini API key given.")
    http = session or requests
    prompt = build_prompt(sources, exclude)
    tried = []
    for m in [model or DEFAULT_MODEL] + [x for x in FALLBACK_MODELS if x != (model or DEFAULT_MODEL)]:
        tried.append(m)
        try:
            r = http.post(ENDPOINT.format(model=m), headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                          json=request_body(prompt, search), timeout=timeout)
        except requests.RequestException as e:
            return Suggestion(model=m, error=f"Couldn't reach Gemini: {type(e).__name__}")
        if r.status_code == 404 and model is None:
            continue                                   # model name not available: try the next one
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", "")
            except ValueError:
                msg = r.text[:200]
            return Suggestion(model=m, error=f"Gemini answered {r.status_code}: {msg}")
        text, cites = parse_response(r.json())
        items = extract_json_array(text)
        if not items:
            return Suggestion(model=m, text=text, citations=cites, error="Gemini's answer had no usable list of funds.")
        funds, rejected = to_funds(items, m)
        return Suggestion(funds, cites, rejected, m, text)
    return Suggestion(error=f"None of these models is available for your key: {', '.join(tried)}")
