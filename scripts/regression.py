"""End-to-end check on the real exports in private/.

    python scripts/regression.py            # compare with private/baseline.json
    python scripts/regression.py --update   # write a new baseline (explain why in the commit message)

Runs offline with the prices in private/prices.csv and value date 2026-10-14, so results are reproducible.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "private"
sys.path.insert(0, str(ROOT / "src"))

from meerwaarde.engine.tax import TaxCalculator  # noqa: E402
from meerwaarde.report import build_report  # noqa: E402

TOLERANCE = 0.05
VALUE_DATE = date(2026, 10, 14)


def metrics() -> dict:
    files = sorted((PRIVATE / "exports").glob("*.pdf"))
    if not files:
        sys.exit("No exports in private/exports/")
    prices = (PRIVATE / "prices.csv").read_text()
    rep = build_report(files, fetch_prices=False, overrides_csv=prices, today=VALUE_DATE)
    out: dict = {"files": [f.name for f in files]}
    s = rep.summary()
    out["summary"] = {r.broker: {k: round(float(getattr(r, k)), 2) for k in ("realized", "unrealized", "income", "total", "value", "cost_open")}
                      for r in s.itertuples()}
    out["realized_by_year"] = {f"{r.broker}|{r.year}": round(float(r.pnl_eur), 2) for r in rep.by_year().itertuples()}
    sold, opened = rep.tax.sold_lines(), rep.tax.open_lines()
    t = TaxCalculator.summarize(sold + opened, rep.tax.rules)
    out["tax"] = {"per_type": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in t["per_type"].items()},
                  "net": round(t["net"], 2), "tax": round(t["tax"], 2)}
    room = rep.tax.rules.exemption - sum(l.taxable_gain for l in sold)
    out["tax"]["room_after_sales"] = round(room, 2)
    out["max_without_tax"] = {f"{h.broker}|{h.asset}": round(rep.tax.max_without_tax(h, max(0.0, room)), 8)
                              for h in rep.portfolio.holdings()}
    out["rows"] = {f"{k[0]}|{k[1]}": n for k, n in sorted(Counter((t.broker, t.kind) for t in rep.portfolio.transactions).items())}
    out["warnings"] = len(rep.warnings)
    return out


def diff(a, b, path=""):
    problems = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                problems.append(f"{path}{k}: {'missing now' if k not in a else 'new'}")
            else:
                problems += diff(a[k], b[k], f"{path}{k}.")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > TOLERANCE:
            problems.append(f"{path.rstrip('.')}: now {a:,.2f}, baseline {b:,.2f} (Δ {a - b:+,.2f})")
    elif a != b:
        problems.append(f"{path.rstrip('.')}: now {a!r}, baseline {b!r}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="write the current results as the new baseline")
    a = ap.parse_args()
    now = metrics()
    base_file = PRIVATE / "baseline.json"
    if a.update or not base_file.exists():
        base_file.write_text(json.dumps(now, indent=2, sort_keys=True))
        print(f"Baseline written to {base_file}")
        return
    problems = diff(now, json.loads(base_file.read_text()))
    if problems:
        print("Regression differences:")
        for p in problems:
            print("  - " + p)
        sys.exit(1)
    s = now["summary"]["All"]
    print(f"OK · total profit €{s['total']:,.2f} · meerwaardebelasting €{now['tax']['tax']:,.2f} · "
          f"room left €{now['tax']['room_after_sales']:,.2f}")


if __name__ == "__main__":
    main()
