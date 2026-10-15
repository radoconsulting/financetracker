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
    from .engine.reynders import ReyndersBook
    from .manual import ManualBook
    from .profile import TaxProfile
    from .report import build_report
    files = [Path(f) for f in a.files]
    missing = [str(f) for f in files + [Path(x) for x in (a.manual, a.reynders) if x] if not f.exists()]
    if missing:
        sys.exit(f"File not found: {', '.join(missing)}")
    if not files and not a.manual:
        sys.exit("Give at least one PDF export or a --manual file")
    if a.csv_formats:
        from .parsers import mapped
        mapped.load(a.csv_formats)
    profile = TaxProfile.load(a.profile) if a.profile else TaxProfile()
    rules = TaxRules(year=a.year, use_original_if_higher=not a.no_original_price)
    rules.exemption = a.exemption if a.exemption is not None else profile.exemption_rules().base_for(a.year)
    opts = PortfolioOptions(own_wallet_withdrawals=not a.withdrawals_are_sales)
    overrides = Path(a.prices).read_text() if a.prices else None
    return build_report(files, fetch_prices=not a.offline, options=opts, rules=rules, overrides_csv=overrides,
                        today=date.fromisoformat(a.today) if a.today else None, profile=profile,
                        manual=ManualBook.load(a.manual) if a.manual else None,
                        reynders=ReyndersBook.load(a.reynders) if a.reynders else None)


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
    ry = rep.reynders_summary(lines)
    if ry["base"]:
        print(f"  Reynders tax (30% on the interest part of bond funds {_eur(ry['base'])}): {_eur(ry['tax'])}")
    sold = rep.tax_summary(rep.sold_lines())["people"]
    for pid, p in sold.items():
        who = "" if rep.household.single else f" ({rep.household.name(pid)})"
        print(f"  Tax-free room left after this year's sales{who}: {_eur(max(0.0, p['room']))}")

    h = rep.holdings()
    miss = h[h.price_eur.isna()]
    if len(miss):
        print("\nNo current price for: " + ", ".join(f"{r.name} ({r.asset})" for r in miss.itertuples()))
        print("Add them to a prices CSV (asset,price_now,price_31dec) and pass it with --prices.")
    for f in rep.reynders_flags():
        print("\n" + f.replace("on the Reynders tax panel (Meerwaardebelasting tab)", "in a --reynders file"))
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


def cmd_switch(a):
    from .engine.harvest import CostModel
    from .engine.switch import compare, default_sources, net_spread, source_fund, sources_for, spread_plan
    from .funds import CATALOG, WORLD_TARGETS, Fund, lookup
    rep = _build(a)
    costs = CostModel(spread=a.spread)
    keys = set(a.sell or []) or default_sources(rep)
    srcs = sources_for(rep, keys, costs)
    if not srcs:
        sys.exit("Nothing to switch: give --sell ISIN for a position you hold.")
    targets = [lookup(i) or Fund(i, i, verified=False) for i in (a.target or WORLD_TARGETS)]
    if a.suggest:
        from .suggest import suggest
        res = suggest(a.gemini_key or os.environ.get("GEMINI_API_KEY", ""), [source_fund(s) for s in srcs],
                      model=a.gemini_model, search=not a.no_search, exclude=tuple(t.isin for t in targets))
        if res.error:
            print(f"Gemini: {res.error}")
        for r in res.rejected:
            print(f"Gemini suggestion left out: {r}")
        targets += [f for f in res.funds if f.isin not in {t.isin for t in targets}]
        for title, url in res.citations:
            print(f"  source: {title} {url}")
    cases = sorted(compare(rep, srcs, targets, a.horizon, costs, tax_reinvested=not a.no_tax_on_reinvested),
                   key=lambda c: c.net, reverse=True)
    print(f"\nSwitch {', '.join(f'{s.qty:g} {s.name} ({s.broker})' for s in srcs)}: value {_eur(cases[0].value)}")
    print(f"Meerwaardebelasting if sold now: {_eur(cases[0].cgt_now)}\n")
    plans = {c.target.isin: spread_plan(rep, srcs, c, costs) for c in cases}
    cases.sort(key=lambda c: net_spread(c, plans[c.target.isin]), reverse=True)
    print(f"  {'Target':<44} {'Stock exch. tax':>15} {'All now':>11} {'Yearly':>9} {'Break-even':>10} "
          f"{f'Net {a.horizon}y now':>13} {f'Net {a.horizon}y spread':>15}")
    for c in cases:
        be = "never" if c.break_even is None else f"{c.break_even:.1f} yrs"
        flag = "" if c.target.verified else "  (unverified)"
        print(f"  {(c.target.ticker + ' ' + c.target.name)[:44]:<44} {c.target_tob:>14.2%} {_eur(c.now):>11} "
              f"{_eur(c.yearly):>9} {be:>10} {_eur(c.net):>13} {_eur(net_spread(c, plans[c.target.isin])):>15}{flag}")
    best = cases[0]
    plan = plans[best.target.isin]
    print(f"\nSpread within the exemption, into {best.target.ticker or best.target.isin} (prices kept at today's level):")
    for st in plan.steps:
        print(f"  {st.year}: " + ", ".join(f"{q:g} × {k[0]} {k[1]}" for k, q in st.units.items())
              + f"  gain {_eur(st.gain)}, costs {_eur(st.costs)}")
    print(f"  {'Done' if plan.done else 'Not done'} after {plan.years} year(s): no meerwaardebelasting (instead of "
          f"{_eur(best.cgt_now)} now), yearly benefit you wait for {_eur(plan.benefit_waited)}, costs {_eur(plan.costs)}.")
    for n in best.notes:
        print(f"  Note: {n}")


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
    if ytr.reynders.get("base"):
        print(f"\nReynders tax: interest part {_eur(ytr.reynders['base'])}, tax (30%) {_eur(ytr.reynders['tax'])}")
        if len(ytr.persons) > 1:
            for pid, name in ytr.persons:
                print(f"  {name}: {_eur(ytr.reynders['people'][pid]['tax'])}")
    print("\nStock exchange tax")
    for r in ytr.tob.itertuples():
        print(f"  {r.broker:<8} {_eur(r.amount_eur):>12}  {r.basis}")
    if not ytr.checks.empty:
        print("\nCheck against the broker's annual overview")
        for r in ytr.checks.itertuples():
            theirs = "-" if r.broker_eur is None else f"{r.broker_eur:,.2f}"
            diff = "" if r.difference is None or r.difference != r.difference else f"  (difference {r.difference:+,.2f})"
            print(f"  {r.broker:<8} {r.item:<40} broker {theirs:>12}  app {r.app_eur:>12,.2f}{diff}")
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


def _pct(v):
    return "—" if v is None else f"{v:+.1%}"


def cmd_performance(a):
    _check_name(a.xlsx, ".xlsx", "--xlsx")
    from .engine.performance import DEFAULT_BENCHMARK
    rep = _build(a)
    bench = (a.benchmark, a.benchmark) if a.benchmark else DEFAULT_BENCHMARK
    perf = rep.performance(bench)
    print(f"\nPerformance {perf.start:%d-%m-%Y} – {perf.end:%d-%m-%Y} · benchmark {bench[1]} ({bench[0]})")
    print(f"  {'':<8} {'money in':>12} {'money out':>12} {'value':>12} {'gain':>12} {'XIRR/yr':>8} {'TWR/yr':>8} "
          f"{'bench value':>12} {'bench XIRR':>10}")
    for l in perf.lines:
        bv = "—" if l.benchmark_value is None else f"{l.benchmark_value:,.0f}"
        print(f"  {l.broker:<8} {l.invested:>12,.0f} {l.returned:>12,.0f} {l.value:>12,.0f} {l.gain:>12,.0f} "
              f"{_pct(l.xirr):>8} {_pct(l.twr_annual):>8} {bv:>12} {_pct(l.benchmark_xirr):>10}")
    for n in perf.notes:
        print(f"  Note: {n}")
    if a.xlsx is not None:
        from .export import to_xlsx
        print(f"Excel workbook written to {to_xlsx(rep, _out_path(a.xlsx, a.out, 'performance.xlsx'), perf=perf)}")


def _data_dir(a) -> Path:
    from .datadir import default_data_dir
    d = Path(a.data_dir).expanduser() if a.data_dir else default_data_dir()
    if not d.exists():
        sys.exit(f"Data folder not found: {d}")
    return d


def _print_sync(results):
    for r in results:
        print(f"  {r.source}: {'ok' if r.ok else 'not synced'} · {r.message}")
        rec = r.details.get("reconcile")
        if rec:
            print(f"    checked against your PDF: {rec['matched']} of {rec['api']} rows have the same id; quantity equal "
                  f"{rec['qty_ok']}, amount equal {rec['total_ok']} of {rec['with_total']}; time offset "
                  f"{', '.join(f'{h:g} h' for h in rec['hour_offsets']) or '-'}; fee in the amount sent: {rec['buy_fee_rule'] or '-'}")
        for w in r.details.get("warnings", []):
            print(f"    note: {w}")


def cmd_sync(a):
    from .datadir import DataDir
    from .sync import SyncStore, load_secrets, sync
    from .sync.flex import parse_flex
    d = _data_dir(a)
    if a.import_flex:
        text = Path(a.import_flex).read_text(encoding="utf-8")
        r = parse_flex(text, Path(a.import_flex).name)
        path = SyncStore(d).save_flex(text)
        print(f"Imported {len(r.transactions)} LYNX rows ({r.period[0]} – {r.period[1]}) into {path}")
        return
    secrets = load_secrets(a.secrets)
    both = not (a.lynx or a.bitvavo)
    pdf_rows = None
    if both or a.bitvavo:
        from .parsers import parse_file
        pdf_rows = [t for p in DataDir(d).pdfs() for r in [parse_file(p)] if r.broker == "Bitvavo" for t in r.transactions] or None
    results = sync(d, lynx=both or a.lynx, bitvavo=both or a.bitvavo, secrets=secrets, pdf_rows=pdf_rows)
    print(f"Sync into {d / 'sync'}")
    _print_sync(results)
    if not any(r.ok for r in results):
        sys.exit(1)


def cmd_plan(a):
    rep = _build(a)
    tp = rep.tax_plan(growth=a.growth / 100, keep_carry=not a.use_all)
    print(f"\nTax plan {tp.years[0].year if tp.years else ''}–2030 (prices +{tp.growth:.0%} a year)")
    for y in tp.years:
        print(f"  {y.year}: room {', '.join(f'{rep.household.name(p)} {_eur(v)}' for p, v in y.room.items())}, "
              f"gain {_eur(y.gain)}, costs {_eur(y.costs)}")
        for l in y.lines:
            print(f"      sell and buy back {l.qty:,.8g} {l.name[:40]} ({l.broker}) at ~{l.price:,.2f}: gain {_eur(l.gain)}"
                  + (f", of which original price kept {_eur(l.window)}" if l.window else ""))
    print(f"\n  If everything were sold in {tp.final_year}: without the plan {_eur(tp.tax_without)}, with it "
          f"{_eur(tp.tax_with)}; costs {_eur(tp.costs)}; net saving {_eur(tp.saving)}")
    for n in tp.notes:
        print(f"  Note: {n}")


def cmd_allocation(a):
    from .engine.allocation import load_overrides
    rep = _build(a)
    al = rep.allocation(load_overrides(a.exposure) if a.exposure else None)
    print(f"\nAllocation of €{al.total:,.0f} (looking through funds)")
    for dim in ("asset_class", "region", "currency", "sector"):
        items = [(k, v) for k, v in al.totals[dim].items() if v > 0.005][:8]
        print(f"  {dim.replace('_', ' ').capitalize():<12} " + " · ".join(f"{k} {v / al.total:.0%}" for k, v in items))
    print("\n  Largest holdings through everything you own (funds: top 10 only)")
    for isin, name, v, via in al.top[:10]:
        print(f"    {name[:34]:<34} €{v:>11,.0f}  {v / al.total:5.1%}  via {len(via)}")
    if al.overlap:
        print("\n  Overlap between funds (shared top-10 holdings)")
        for x, y, share, n in al.overlap[:8]:
            print(f"    {x[:30]:<30} × {y[:30]:<30} {share:6.1%} ({n} shared)")
    for n in al.notes:
        print(f"  Note: {n}")
    if a.xlsx is not None:
        from .export import to_xlsx
        print(f"Excel workbook written to {to_xlsx(rep, _out_path(a.xlsx, a.out, 'allocation.xlsx'), alloc=al)}")


def cmd_refresh(a):
    from .alerts import read_log, refresh, run_every, send_test
    from .datadir import default_data_dir
    d = Path(a.data_dir).expanduser() if a.data_dir else default_data_dir()
    if not d.exists():
        sys.exit(f"Data folder not found: {d}")
    if a.test:
        errs = send_test(d)
        if errs:
            sys.exit("Test failed: " + "; ".join(errs))
        print("Test message sent.")
        return
    if a.sync and not a.dry_run:
        from .sync import load_secrets, sync
        s = load_secrets()
        print("Sync:")
        _print_sync(sync(d, lynx=bool(s["MEERWAARDE_IBKR_TOKEN"]), bitvavo=bool(s["MEERWAARDE_BITVAVO_KEY"]), secrets=s))
    if a.every:
        run_every(d, a.every, send=not a.dry_run, sync_first=a.sync)
        return
    r = refresh(d, send=not a.dry_run, fetch=not a.offline)
    print(f"Refreshed {r.positions} positions on {r.on}; {r.price_errors} price lookups failed.")
    for al in r.alerts:
        print(f"  [{al.kind}] {al.message(True)}")
    if not r.alerts:
        print("  No alerts.")
    if a.dry_run:
        print("Dry run: nothing sent, state and log unchanged.")
    elif r.sent:
        print("Notification sent.")
    for e in r.errors:
        print(f"  Not sent: {e}")
    if a.verbose:
        print(f"Log: {len(read_log(d))} runs in {d / 'alerts' / 'log.jsonl'}")
    if r.errors and not a.dry_run and r.alerts:
        sys.exit(1)


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
        x.add_argument("files", nargs="*", help="exports: PDF (Bitvavo, DEGIRO Transacties and Rekeningoverzicht, LYNX activity "
                                                "statements) or CSV (DEGIRO, Bitvavo, or a format in --csv-formats)")
        x.add_argument("--csv-formats", help="CSV formats you mapped (the app keeps them in its data folder as csv_formats.json)")
        x.add_argument("--profile", help="tax profile JSON: partner, ownership, other sales and income, exemptions per year "
                                         "(the app keeps it in its data folder as profile.json)")
        x.add_argument("--manual", help="JSON with positions no export covers: funds, tak 21/23 policies, savings, gold "
                                        "(the app keeps it in its data folder as manual.json)")
        x.add_argument("--reynders", help="JSON with bond funds subject to the Reynders tax: TIS values or bond share "
                                          "(the app keeps it in its data folder as reynders.json)")
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

    w2 = sub.add_parser("switch", help="Switch planner: sell positions and buy a target fund; compare targets, spread over years")
    common(w2)
    w2.add_argument("--sell", action="append", metavar="ISIN", help="position to switch (repeatable; default: distributing ETFs)")
    w2.add_argument("--target", action="append", metavar="ISIN", help="target fund (repeatable; default: world ETFs in the catalog)")
    w2.add_argument("--horizon", type=int, default=10, help="years to compare over (default 10)")
    w2.add_argument("--spread", type=float, default=0.0005, help="half the bid-ask spread per trade, fraction (default 0.0005)")
    w2.add_argument("--no-tax-on-reinvested", action="store_true",
                    help="don't count 10%% meerwaardebelasting later on dividends an accumulating fund reinvests")
    w2.add_argument("--suggest", action="store_true", help="ask Gemini for cheaper funds with similar content")
    w2.add_argument("--gemini-key", help="Gemini API key (default: the GEMINI_API_KEY environment variable)")
    w2.add_argument("--gemini-model", help="Gemini model (default gemini-3.8-flash, falls back to gemini-2.5-flash)")
    w2.add_argument("--no-search", action="store_true", help="don't let Gemini use Google Search")
    w2.set_defaults(func=cmd_switch)

    t = sub.add_parser("tax-report", help="Yearly tax report (meerwaardebelasting, dividends and interest, stock exchange "
                                          "tax, checklist) as Excel and PDF")
    common(t)
    t.add_argument("--xlsx", nargs="?", const="", metavar="FILE", help="write only the Excel version (default tax-report-<year>.xlsx)")
    t.add_argument("--pdf", nargs="?", const="", metavar="FILE", help="write only the PDF version (default tax-report-<year>.pdf)")
    t.set_defaults(func=cmd_tax_report)

    pe = sub.add_parser("performance", help="Money- and time-weighted return per broker, against a benchmark fund")
    common(pe)
    pe.add_argument("--benchmark", metavar="ISIN", help="benchmark fund (default iShares MSCI ACWI, IE00B6R52259)")
    pe.add_argument("--xlsx", nargs="?", const="", metavar="FILE", help="also write the Excel workbook (default performance.xlsx)")
    pe.set_defaults(func=cmd_performance)

    pl = sub.add_parser("plan", help="Multi-year tax plan to 2030: what to sell and buy back each year within the exemption")
    common(pl)
    pl.add_argument("--growth", type=float, default=5.0, help="expected yearly price growth in %% (default 5)")
    pl.add_argument("--use-all", action="store_true", help="also use the €1,000 that would otherwise carry forward")
    pl.set_defaults(func=cmd_plan)

    al = sub.add_parser("allocation", help="Exposure by asset class, region, currency and sector, looking through funds")
    common(al)
    al.add_argument("--exposure", help="JSON with your own breakdown per fund (the app keeps it as exposure.json)")
    al.add_argument("--xlsx", nargs="?", const="", metavar="FILE", help="also write the Excel workbook (default allocation.xlsx)")
    al.set_defaults(func=cmd_allocation)

    sy = sub.add_parser("sync", help="Download new LYNX (Flex Web Service) and Bitvavo (API) rows into the data folder")
    sy.add_argument("--data-dir", help="the app's data folder (default ~/.local/share/meerwaarde or MEERWAARDE_DATA)")
    sy.add_argument("--lynx", action="store_true", help="only LYNX")
    sy.add_argument("--bitvavo", action="store_true", help="only Bitvavo")
    sy.add_argument("--secrets", help="KEY=VALUE file with the credentials (default: environment, then MEERWAARDE_SECRETS)")
    sy.add_argument("--import-flex", metavar="XML", help="store a Flex Query result you downloaded yourself")
    sy.set_defaults(func=cmd_sync)

    rf = sub.add_parser("refresh", help="Refresh prices for the app's data folder and send alerts (run it on a schedule)")
    rf.add_argument("--data-dir", help="the app's data folder (default ~/.local/share/meerwaarde or MEERWAARDE_DATA)")
    rf.add_argument("--dry-run", action="store_true", help="evaluate and log the alerts, send nothing")
    rf.add_argument("--offline", action="store_true", help="use cached and your own prices only")
    rf.add_argument("--every", type=float, metavar="HOURS", help="keep running and refresh every HOURS (for a container)")
    rf.add_argument("--test", action="store_true", help="send a test message on the channels in alerts.json")
    rf.add_argument("--sync", action="store_true", help="sync LYNX and Bitvavo first (credentials from the environment)")
    rf.add_argument("-v", "--verbose", action="store_true")
    rf.set_defaults(func=cmd_refresh)

    w = sub.add_parser("app", help="Start the web app")
    w.add_argument("--port", type=int, default=8501)
    w.add_argument("--host", default="localhost", help="use 0.0.0.0 to reach it from other devices")
    w.add_argument("--data-dir", help="folder where uploads and your prices are kept (default ~/.local/share/meerwaarde)")
    w.set_defaults(func=cmd_app)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
