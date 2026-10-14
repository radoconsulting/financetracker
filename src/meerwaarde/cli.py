"""Command line: `meerwaarde report ...` and `meerwaarde app`."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from .engine.portfolio import PortfolioOptions
from .engine.tax import TaxCalculator, TaxRules
from .models import LAW_TYPES


def _eur(v):
    return "—" if v is None else f"€{v:,.2f}"


def cmd_report(a):
    from .report import build_report
    files = [Path(f) for f in a.files]
    if a.xlsx and not a.xlsx.lower().endswith(".xlsx"):
        sys.exit(f"--xlsx takes the workbook's file name (ending in .xlsx), got {a.xlsx!r}. "
                 "Put --xlsx after the PDF files, or give it a name: --xlsx results.xlsx")
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        sys.exit(f"File not found: {', '.join(missing)}")
    rules = TaxRules(year=a.year, exemption=a.exemption, use_original_if_higher=not a.no_original_price)
    opts = PortfolioOptions(own_wallet_withdrawals=not a.withdrawals_are_sales)
    overrides = Path(a.prices).read_text() if a.prices else None
    rep = build_report(files, fetch_prices=not a.offline, options=opts, rules=rules, overrides_csv=overrides,
                       today=date.fromisoformat(a.today) if a.today else None)

    s = rep.summary()
    print("\nProfit per broker (EUR)")
    print(s.to_string(index=False))
    lines = rep.tax.sold_lines() + rep.tax.open_lines()
    t = TaxCalculator.summarize(lines, rules)
    print(f"\nMeerwaardebelasting {rules.year} if you sold everything today")
    for k, v in t["per_type"].items():
        if abs(v["sold"]) + abs(v["open"]) > 0.005:
            print(f"  {LAW_TYPES[k][0]:<24} sold {_eur(v['sold']):>14}   open {_eur(v['open']):>14}")
    print(f"  Net taxable gain          {_eur(t['net'])}")
    print(f"  Exemption                 {_eur(t['exemption'])}")
    print(f"  Tax (10%)                 {_eur(t['tax'])}")
    sold_net = sum(l.taxable_gain for l in rep.tax.sold_lines())
    print(f"  Tax-free room left after this year's sales: {_eur(max(0.0, rules.exemption - sold_net))}")

    h = rep.holdings()
    miss = h[h.price_eur.isna()]
    if len(miss):
        print("\nNo current price for: " + ", ".join(f"{r.name} ({r.asset})" for r in miss.itertuples()))
        print("Add them to a prices CSV (asset,price_now,price_31dec) and pass it with --prices.")
    if rep.warnings and a.verbose:
        print("\nNotes")
        for w in rep.warnings:
            print("  - " + w)
    elif rep.warnings:
        print(f"\n{len(rep.warnings)} notes about the data (show them with --verbose).")
    if rep.prices.errors:
        print(f"{len(rep.prices.errors)} price lookups failed; those prices are missing or come from statements.")

    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        rep.realized().to_csv(out / "realized.csv", index=False)
        h.to_csv(out / "holdings.csv", index=False)
        rep.income().to_csv(out / "income.csv", index=False)
        rep.cash().to_csv(out / "cash.csv", index=False)
        rep.tax_lines().to_csv(out / "meerwaardebelasting.csv", index=False)
        s.to_csv(out / "summary.csv", index=False)
        print(f"\nCSV files written to {out}")

    if a.xlsx is not None:
        from .export import default_name, to_xlsx
        path = Path(a.xlsx) if a.xlsx else Path(a.out or ".") / default_name(rep)
        print(f"Excel workbook written to {to_xlsx(rep, path)}")


def cmd_app(a):
    try:
        import streamlit  # noqa: F401
    except ImportError:
        sys.exit("The web app needs Streamlit: pip install 'meerwaarde[app]'")
    app = Path(__file__).parent / "app" / "streamlit_app.py"
    env = dict(os.environ)
    if a.data_dir:
        env["MEERWAARDE_DATA"] = str(Path(a.data_dir).expanduser().resolve())
    cmd = [sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(a.port),
           "--server.address", a.host, "--browser.gatherUsageStats", "false"]
    if a.host != "localhost":
        cmd += ["--server.headless", "true"]
    sys.exit(subprocess.call(cmd, env=env))


def main(argv=None):
    p = argparse.ArgumentParser(prog="meerwaarde", description="Profit/loss and Belgian meerwaardebelasting from Bitvavo, DEGIRO and LYNX exports.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("report", help="Read exports and print profit and tax; optionally write CSV files or an Excel workbook")
    r.add_argument("files", nargs="+", help="PDF exports (Bitvavo transaction history, DEGIRO Transacties, LYNX activity statements)")
    r.add_argument("--out", help="folder for CSV files")
    r.add_argument("--xlsx", nargs="?", const="", metavar="FILE",
                   help="write an Excel workbook (default name meerwaarde-<date>.xlsx, in --out when given)")
    r.add_argument("--prices", help="CSV with your own prices: asset,price_now,price_31dec (EUR)")
    r.add_argument("--offline", action="store_true", help="don't look up prices online")
    r.add_argument("--year", type=int, default=2026, help="tax year (default 2026)")
    r.add_argument("--exemption", type=float, default=10000.0, help="exemption in EUR (default 10000)")
    r.add_argument("--no-original-price", action="store_true", help="never use the original purchase price instead of the 31-12-2025 price")
    r.add_argument("--withdrawals-are-sales", action="store_true", help="treat crypto withdrawals as leaving the portfolio instead of going to your own wallet")
    r.add_argument("--today", help="value date for prices (YYYY-MM-DD), mainly for testing")
    r.add_argument("-v", "--verbose", action="store_true", help="show all notes about the data")
    r.set_defaults(func=cmd_report)

    w = sub.add_parser("app", help="Start the web app")
    w.add_argument("--port", type=int, default=8501)
    w.add_argument("--host", default="localhost", help="use 0.0.0.0 to reach it from other devices")
    w.add_argument("--data-dir", help="folder where uploads and your prices are kept (default ~/.local/share/meerwaarde)")
    w.set_defaults(func=cmd_app)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
