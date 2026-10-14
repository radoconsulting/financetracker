"""Command line: `meerwaarde report ...` and `meerwaarde app`."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from .engine.portfolio import PortfolioOptions
from .engine.tax import TaxRules
from .models import LAW_TYPES


def _eur(v):
    return "—" if v is None else f"€{v:,.2f}"


def _check_name(value, ext, flag):
    if value and not value.lower().endswith(ext):
        sys.exit(f"{flag} takes the output file name (ending in {ext}), got {value!r}. "
                 f"Put {flag} after the PDF files, or give it a name: {flag} results{ext}")


def _build(a):
    from .profile import TaxProfile
    from .report import build_report
    files = [Path(f) for f in a.files]
    missing = [str(f) for f in files if not f.exists()]
    if missing:
        sys.exit(f"File not found: {', '.join(missing)}")
    profile = TaxProfile.load(a.profile) if a.profile else TaxProfile()
    rules = TaxRules(year=a.year, use_original_if_higher=not a.no_original_price)
    rules.exemption = a.exemption if a.exemption is not None else profile.exemption_rules().base_for(a.year)
    opts = PortfolioOptions(own_wallet_withdrawals=not a.withdrawals_are_sales)
    overrides = Path(a.prices).read_text() if a.prices else None
    return build_report(files, fetch_prices=not a.offline, options=opts, rules=rules, overrides_csv=overrides,
                        today=date.fromisoformat(a.today) if a.today else None, profile=profile)


def _out_path(value, out, default):
    return Path(value) if value else Path(out or ".") / default


def cmd_report(a):
    _check_name(a.xlsx, ".xlsx", "--xlsx")
    rep = _build(a)
    rules = rep.tax.rules

    s = rep.summary()
    print("\nProfit per broker (EUR)")
    print(s.to_string(index=False))
    lines = rep.sold_lines() + rep.tax.open_lines()
    t = rep.tax_summary(lines)
    print(f"\nMeerwaardebelasting {rules.year} if you sold everything today")
    for k, v in t["per_type"].items():
        if abs(v["sold"]) + abs(v["open"]) > 0.005:
            print(f"  {LAW_TYPES[k][0]:<24} sold {_eur(v['sold']):>14}   open {_eur(v['open']):>14}")
    print(f"  Net taxable gain          {_eur(t['net'])}")
    print(f"  Exemption                 {_eur(t['exemption'])}")
    print(f"  Tax (10%)                 {_eur(t['tax'])}")
    sold = rep.tax_summary(rep.sold_lines())["people"]
    for pid, p in sold.items():
        who = "" if rep.household.single else f" ({rep.household.name(pid)})"
        print(f"  Tax-free room left after this year's sales{who}: {_eur(max(0.0, p['room']))}")

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
        print(f"Excel workbook written to {to_xlsx(rep, _out_path(a.xlsx, a.out, default_name(rep)))}")


def cmd_harvest(a):
    from .engine.harvest import CostModel, plan_harvest
    rep = _build(a)
    plan = plan_harvest(rep, keep_carry=not a.use_all, costs=CostModel(spread=a.spread), exclude=set(a.exclude or []))
    print(f"\nYear-end {plan.year}: use the rest of the exemption before 31 December")
    for pid, name in plan.persons:
        s = plan.status[pid]
        who = "" if len(plan.persons) == 1 else f"{name}: "
        print(f"  {who}exemption {_eur(s['exemption'])}, net gain so far {_eur(s['net'])}, left {_eur(s['room'])}"
              + (f", kept for the carry-forward {_eur(s['reserve'])}" if s["reserve"] else "") + f", to harvest {_eur(s['target'])}")
    if plan.lines:
        print("\n  Sell and buy back:")
        for l in plan.lines:
            print(f"  {l.broker:<8} {l.name[:42]:<42} {l.qty:>12.8g} × {l.price:>10,.4f}  gain {_eur(l.gain):>11}  "
                  f"costs {_eur(l.costs):>9} (stock exchange tax {l.tob_rate:.2%}, {l.tob_source})  net {_eur(l.net)}")
        print(f"\n  Gain harvested {_eur(plan.gain)} · tax saved later up to {_eur(plan.tax_saved)} · costs {_eur(plan.costs)}"
              f" · net {_eur(plan.net)}")
    else:
        print("  Nothing worth harvesting.")
    if plan.losses:
        print("\n  Positions at a taxable loss:")
        for l in plan.losses:
            print(f"  {l.broker:<8} {l.name[:42]:<42} loss {_eur(l.loss):>11}  tax it removes this year {_eur(l.tax_saved_now)}")
    print()
    for n in plan.notes:
        print("  Note: " + n)


def cmd_tax_report(a):
    _check_name(a.xlsx, ".xlsx", "--xlsx")
    _check_name(a.pdf, ".pdf", "--pdf")
    from .engine.income_tax import CATEGORIES
    from .taxreport import tax_year_report
    rep = _build(a)
    ytr = tax_year_report(rep, a.year)
    print(f"\n{ytr.title}\n{ytr.subtitle}")
    for pid, name in ytr.persons:
        p, it = ytr.cgt["people"][pid], ytr.income[pid]
        if len(ytr.persons) > 1:
            print(f"\n{name}")
        e = ytr.exemption.get(pid)
        print(f"  Meerwaardebelasting: net gain {_eur(p['net'])}, exemption {_eur(p['exemption'])}"
              + (f" (incl. {_eur(e.carried)} carried forward)" if e and e.carried else "") + f", tax {_eur(p['tax'])}")
        for l in it.lines:
            print(f"  {CATEGORIES[l.category]:<34} {l.broker:<8} net {_eur(l.net):>12}  (foreign tax {_eur(l.foreign_tax)})")
        print(f"  Dividend exemption used {_eur(it.exemption_used)}, taxable {_eur(it.taxable)}, tax (30%) {_eur(it.tax)}"
              + (f", already withheld in Belgium {_eur(it.be_withheld)}" if abs(it.be_withheld) > 0.005 else "")
              + f", to pay {_eur(it.to_pay)}")
    print("\nStock exchange tax")
    for r in ytr.tob.itertuples():
        print(f"  {r.broker:<8} {_eur(r.amount_eur):>12}  {r.basis}")
    print("\nChecklist")
    for i, c in enumerate(ytr.checklist, 1):
        print(f"  {i}. {c}")
    for n in ytr.notes:
        print(f"  Note: {n}")
    if a.xlsx is not None or a.pdf is None:
        from .export import tax_report_xlsx
        print(f"\nExcel written to {tax_report_xlsx(ytr, _out_path(a.xlsx, a.out, f'tax-report-{ytr.year}.xlsx'))}")
    if a.pdf is not None or a.xlsx is None:
        from .pdf_report import tax_report_pdf
        print(f"PDF written to {tax_report_pdf(ytr, _out_path(a.pdf, a.out, f'tax-report-{ytr.year}.pdf'))}")


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

    def common(x):
        x.add_argument("files", nargs="+", help="PDF exports (Bitvavo transaction history, DEGIRO Transacties, LYNX activity statements)")
        x.add_argument("--profile", help="tax profile JSON: partner, ownership, other sales and income, exemptions per year "
                                         "(the app keeps it in its data folder as profile.json)")
        x.add_argument("--prices", help="CSV with your own prices: asset,price_now,price_31dec (EUR)")
        x.add_argument("--offline", action="store_true", help="don't look up prices online")
        x.add_argument("--year", type=int, default=2026, help="tax year (default 2026)")
        x.add_argument("--exemption", type=float, help="own exemption of the tax year in EUR (default: 10000 for 2026, or the profile's)")
        x.add_argument("--no-original-price", action="store_true", help="never use the original purchase price instead of the 31-12-2025 price")
        x.add_argument("--withdrawals-are-sales", action="store_true", help="treat crypto withdrawals as leaving the portfolio instead of going to your own wallet")
        x.add_argument("--today", help="value date for prices (YYYY-MM-DD), mainly for testing")
        x.add_argument("--out", help="output folder")

    r = sub.add_parser("report", help="Read exports and print profit and tax; optionally write CSV files or an Excel workbook")
    common(r)
    r.add_argument("--xlsx", nargs="?", const="", metavar="FILE",
                   help="write an Excel workbook (default name meerwaarde-<date>.xlsx, in --out when given)")
    r.add_argument("-v", "--verbose", action="store_true", help="show all notes about the data")
    r.set_defaults(func=cmd_report)

    h = sub.add_parser("harvest", help="Year-end: which lots to sell and buy back to use the rest of this year's exemption")
    common(h)
    h.add_argument("--use-all", action="store_true", help="also use the €1,000 that would otherwise carry forward")
    h.add_argument("--spread", type=float, default=0.0005, help="half the bid-ask spread per trade, as a fraction (default 0.0005)")
    h.add_argument("--exclude", action="append", metavar="ASSET", help="leave out an ISIN or ticker (repeatable)")
    h.set_defaults(func=cmd_harvest)

    t = sub.add_parser("tax-report", help="Yearly tax report (meerwaardebelasting, dividends and interest, stock exchange "
                                          "tax, checklist) as Excel and PDF")
    common(t)
    t.add_argument("--xlsx", nargs="?", const="", metavar="FILE", help="write only the Excel version (default tax-report-<year>.xlsx)")
    t.add_argument("--pdf", nargs="?", const="", metavar="FILE", help="write only the PDF version (default tax-report-<year>.pdf)")
    t.set_defaults(func=cmd_tax_report)

    w = sub.add_parser("app", help="Start the web app")
    w.add_argument("--port", type=int, default=8501)
    w.add_argument("--host", default="localhost", help="use 0.0.0.0 to reach it from other devices")
    w.add_argument("--data-dir", help="folder where uploads and your prices are kept (default ~/.local/share/meerwaarde)")
    w.set_defaults(func=cmd_app)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
