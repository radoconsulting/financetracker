"""Automatic sync with LYNX (IBKR Flex Web Service) and Bitvavo (read-only API key): backlog item 12, D28.

What is downloaded is kept in `<data dir>/sync/` (raw, like the uploaded PDFs) and parsed on every build:
- `sync/flex/flex-<timestamp>.xml`: each Flex download (365 days each; ids make overlaps harmless);
- `sync/bitvavo/history.json`: every API history item, merged by transaction id.

`merge_synced` decides what counts: rows already covered by an uploaded export win. LYNX: synced rows only after the
end of the newest LYNX statement (D6: the statement's EUR totals). Bitvavo: rows whose transaction id is in the PDF are
dropped, and so is anything up to the PDF's last row. Without an export for a broker, all its synced rows count.

Credentials only from environment variables or a secrets file outside the data folder (`MEERWAARDE_SECRETS` or
`--secrets`), never stored by the app: MEERWAARDE_IBKR_TOKEN, MEERWAARDE_IBKR_QUERY, MEERWAARDE_BITVAVO_KEY,
MEERWAARDE_BITVAVO_SECRET.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..models import Transaction

KEYS = ("MEERWAARDE_IBKR_TOKEN", "MEERWAARDE_IBKR_QUERY", "MEERWAARDE_BITVAVO_KEY", "MEERWAARDE_BITVAVO_SECRET")


def load_secrets(path: str | Path | None = None, env=None) -> dict:
    """Environment first; a KEY=VALUE file (MEERWAARDE_SECRETS or `path`) fills what's missing."""
    env = os.environ if env is None else env
    out = {k: env.get(k, "") for k in KEYS}
    p = path or env.get("MEERWAARDE_SECRETS")
    if p and Path(p).exists():
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip().removeprefix("set ").strip(), v.strip().strip('"').strip("'")
            if k in out and not out[k]:
                out[k] = v
    return out


@dataclass
class Synced:
    transactions: list
    warnings: list = field(default_factory=list)
    flex_positions: dict = field(default_factory=dict)     # newest Flex download: isin -> {qty, mark_eur, name, on}
    flex_period: tuple = (None, None)
    files: list = field(default_factory=list)


class SyncStore:
    def __init__(self, data_dir):
        self.root = Path(data_dir) / "sync"
        self.flex_dir = self.root / "flex"
        self.bitvavo_file = self.root / "bitvavo" / "history.json"

    # ---------- write ----------
    def save_flex(self, text: str, now: datetime | None = None) -> Path:
        self.flex_dir.mkdir(parents=True, exist_ok=True)
        p = self.flex_dir / f"flex-{(now or datetime.now()):%Y%m%d-%H%M%S}.xml"
        p.write_text(text, encoding="utf-8")
        return p

    def bitvavo_items(self) -> list[dict]:
        if not self.bitvavo_file.exists():
            return []
        return json.loads(self.bitvavo_file.read_text(encoding="utf-8") or "[]")

    def save_bitvavo(self, items: list[dict]) -> tuple[int, int]:
        """Merge by transaction id. Returns (new items, total)."""
        have = {str(i.get("transactionId")): i for i in self.bitvavo_items()}
        before = len(have)
        for it in items:
            have[str(it.get("transactionId"))] = it
        self.bitvavo_file.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(have.values(), key=lambda i: str(i.get("executedAt")))
        self.bitvavo_file.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        return len(have) - before, len(have)

    def bitvavo_since_ms(self) -> int | None:
        """Where the next incremental download starts: two days before the newest item (late postings)."""
        items = self.bitvavo_items()
        if not items:
            return None
        newest = max(str(i.get("executedAt")) for i in items)
        t = datetime.fromisoformat(newest.replace("Z", "+00:00")) - timedelta(days=2)
        return int(t.timestamp() * 1000)

    # ---------- read ----------
    def load(self, fee_in_sent: bool = True) -> Synced:
        from .bitvavo_api import parse_history
        from .flex import parse_flex
        out = Synced([])
        seen = set()
        newest = None
        for p in sorted(self.flex_dir.glob("*.xml")) if self.flex_dir.exists() else []:
            try:
                r = parse_flex(p.read_text(encoding="utf-8"), p.name)
            except Exception as e:  # one bad download must not break the report
                out.warnings.append(f"{p.name}: {e}")
                continue
            out.files.append(p.name)
            for t in r.transactions:
                if t.ref not in seen:
                    seen.add(t.ref)
                    out.transactions.append(t)
            out.warnings += [w for w in r.warnings if w not in out.warnings]
            if newest is None or (r.period[1] or datetime.min.date()) >= (newest.period[1] or datetime.min.date()):
                newest = r
        if newest:
            out.flex_positions, out.flex_period = newest.positions, newest.period
        items = self.bitvavo_items()
        if items:
            txns, warns = parse_history(items, self.bitvavo_file.name, fee_in_sent=fee_in_sent)
            out.transactions += txns
            out.warnings += warns
            out.files.append(str(self.bitvavo_file.relative_to(self.root)))
        return out


def coverage(transactions: list, statements: list) -> dict:
    """What the uploaded exports cover: LYNX up to the newest statement's end; Bitvavo up to its last row and its ids."""
    cov = {}
    ends = [s.period_end for s in statements if getattr(s, "broker", "") == "LYNX" and s.period_end]
    if ends:
        cov["LYNX"] = max(ends)
    bv = [t for t in transactions if t.broker == "Bitvavo"]
    if bv:
        cov["Bitvavo"] = (max(t.ts for t in bv), {t.ref for t in bv if t.ref})
    return cov


def merge_synced(transactions: list, statements: list, synced: list) -> tuple[list, list[str]]:
    """The synced rows that add to the uploaded exports, and a note per broker on what was added."""
    cov = coverage(transactions, statements)
    added, notes, seen = [], [], set()
    counts: dict = {}
    for t in synced:
        if t.ref and t.ref in seen:
            continue
        seen.add(t.ref)
        if t.broker == "LYNX" and "LYNX" in cov and t.ts.date() <= cov["LYNX"]:
            continue
        if t.broker == "Bitvavo" and "Bitvavo" in cov:
            last, refs = cov["Bitvavo"]
            if t.ref in refs or t.ts <= last:
                continue
        added.append(t)
        counts[t.broker] = counts.get(t.broker, 0) + 1
    for b, n in sorted(counts.items()):
        since = cov.get(b)
        since = since[0] if isinstance(since, tuple) else since
        notes.append(f"{b}: {n} row(s) from the automatic sync" + (f", after your export (up to {since:%d-%m-%Y})" if since else ""))
    return added, notes


# ---------------------------------------------------------------- one sync run
@dataclass
class SyncResult:
    source: str
    ok: bool
    message: str
    details: dict = field(default_factory=dict)


def sync(data_dir, lynx: bool = True, bitvavo: bool = True, secrets: dict | None = None, get=None, sleep=None,
         pdf_rows: list | None = None) -> list[SyncResult]:
    """Download what's new and store it. `pdf_rows`: the uploaded Bitvavo rows, for the reconciliation."""
    import time as _time
    from .bitvavo_api import BitvavoError, fetch_history, parse_history, reconcile
    from .flex import FlexError, fetch_flex, parse_flex
    secrets = secrets if secrets is not None else load_secrets()
    store, results = SyncStore(data_dir), []
    sleep = sleep or _time.sleep
    if lynx:
        tok, q = secrets.get("MEERWAARDE_IBKR_TOKEN"), secrets.get("MEERWAARDE_IBKR_QUERY")
        if not (tok and q):
            results.append(SyncResult("LYNX", False, "no token or query id (MEERWAARDE_IBKR_TOKEN, MEERWAARDE_IBKR_QUERY)"))
        else:
            try:
                text = fetch_flex(tok, q, get=get, sleep=sleep)
                r = parse_flex(text)
                p = store.save_flex(text)
                results.append(SyncResult("LYNX", True, f"{len(r.transactions)} rows for {r.period[0]} – {r.period[1]}",
                                          dict(file=p.name, rows=len(r.transactions), warnings=r.warnings)))
            except (FlexError, ValueError, OSError) as e:
                results.append(SyncResult("LYNX", False, str(e)))
            except Exception as e:  # network errors from requests
                results.append(SyncResult("LYNX", False, f"{type(e).__name__}: {e}"))
    if bitvavo:
        key, sec = secrets.get("MEERWAARDE_BITVAVO_KEY"), secrets.get("MEERWAARDE_BITVAVO_SECRET")
        if not (key and sec):
            results.append(SyncResult("Bitvavo", False, "no API key or secret (MEERWAARDE_BITVAVO_KEY, MEERWAARDE_BITVAVO_SECRET)"))
        else:
            try:
                items = fetch_history(key, sec, store.bitvavo_since_ms(), get=get, sleep=sleep)
                new, total = store.save_bitvavo(items)
                details = dict(downloaded=len(items), new=new, total=total)
                if pdf_rows:
                    api_rows, _w = parse_history(store.bitvavo_items())
                    details["reconcile"] = reconcile(api_rows, pdf_rows)
                results.append(SyncResult("Bitvavo", True, f"{len(items)} items downloaded, {new} new", details))
            except (BitvavoError, ValueError, OSError) as e:
                results.append(SyncResult("Bitvavo", False, str(e)))
            except Exception as e:
                results.append(SyncResult("Bitvavo", False, f"{type(e).__name__}: {e}"))
    return results
