"""Meerwaarde web app. Start it with `meerwaarde app`."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from meerwaarde.engine.portfolio import Portfolio, PortfolioOptions
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.engine.harvest import DEFAULT_COMMISSION, CostModel, plan_harvest
from meerwaarde.engine.income_tax import CATEGORIES
from meerwaarde.engine.reynders import Reynders, ReyndersBook, ReyndersFund, ReyndersRules
from meerwaarde.engine.switch import (compare, default_sources, funds_from_json, funds_to_json, net_spread, source_fund,
                                      sources_for, spread_plan)
from meerwaarde.funds import CATALOG, WORLD_TARGETS, Fund, lookup
from meerwaarde import suggest as gemini
from meerwaarde import alerts as alerting
from meerwaarde.sync import SyncStore, load_secrets, sync as run_sync
from meerwaarde.export import XLSX_MIME, default_name, tax_report_xlsx, to_xlsx
from meerwaarde.manual import ManualBook, ManualPosition, ManualTrade, asset_code
from meerwaarde.models import ASSET_CLASSES, LAW_TYPES, law_type
from meerwaarde.parsers import combine, mapped, parse_file
from meerwaarde.pdf_report import tax_report_pdf
from meerwaarde.prices import PriceBook
from meerwaarde.prices.fetch import Fetcher
from meerwaarde.profile import INCOME_FIELDS, TaxProfile
from meerwaarde.report import INCOME_RULES_TEXT, RULES_TEXT, Report
from meerwaarde.taxreport import tax_year_report

st.set_page_config(page_title="Meerwaarde", page_icon="📈", layout="wide")

DATA = Path(os.environ.get("MEERWAARDE_DATA") or Path.home() / ".local" / "share" / "meerwaarde")
UPLOADS = DATA / "uploads"
PRICES_CSV = DATA / "prices.csv"
PROFILE = DATA / "profile.json"
FUNDS_JSON = DATA / "funds.json"
MANUAL_JSON = DATA / "manual.json"
REYNDERS_JSON = DATA / "reynders.json"
CACHE = DATA / "cache"
INBOX = DATA / "inbox"
CSV_FORMATS = DATA / "csv_formats.json"
UPLOADS.mkdir(parents=True, exist_ok=True)
INBOX.mkdir(parents=True, exist_ok=True)
mapped.load(CSV_FORMATS)

EUR = "euro"
LAW_NAMES = {k: v[0] for k, v in LAW_TYPES.items()}


# ---------------------------------------------------------------- data loading
@st.cache_data(show_spinner=False)
def parse_cached(digest: str, data: bytes, name: str, formats: str = ""):
    mapped.load(CSV_FORMATS)
    return parse_file(data, filename=name)


def formats_key() -> str:
    return hashlib.sha1(CSV_FORMATS.read_bytes()).hexdigest() if CSV_FORMATS.exists() else ""


def stored_files():
    """Uploads, then the inbox folder (files you save there yourself, e.g. a monthly DEGIRO CSV)."""
    found = [p for folder in (UPLOADS, INBOX) for ext in ("*.pdf", "*.csv") for p in folder.glob(ext)]
    return sorted(found, key=lambda p: (p.parent != UPLOADS, p.name.lower()))


def save_uploads(files):
    added = 0
    for f in files:
        data = f.getvalue()
        digest = hashlib.sha1(data).hexdigest()[:12]
        if any(p.name.startswith(digest) for p in UPLOADS.iterdir()):
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", f.name)
        (UPLOADS / f"{digest}__{safe}").write_bytes(data)
        added += 1
    return added


def display_name(p: Path) -> str:
    return p.name.split("__", 1)[-1]


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.title("Meerwaarde")
    st.caption("Profit and Belgian meerwaardebelasting across Bitvavo, DEGIRO and LYNX.")
    key = st.session_state.setdefault("uploader_key", 0)
    up = st.file_uploader("Add exports (PDF or CSV)", type=["pdf", "csv"], accept_multiple_files=True, key=f"up{key}",
                          help="Bitvavo transaction history (PDF or CSV), DEGIRO Transacties and Rekeningoverzicht (PDF or CSV), "
                               "LYNX activity statements (one per year) or a Transaction History, or a CSV format you mapped on the Data tab. Files you "
                               f"save in {INBOX} are read too.")
    if up:
        n = save_uploads(up)
        st.session_state.uploader_key += 1
        if n:
            st.toast(f"Added {n} file{'s' if n > 1 else ''}")
        st.rerun()

    parsed, failed = [], []
    for p in stored_files():
        data = p.read_bytes()
        try:
            with st.spinner(f"Reading {display_name(p)}…"):
                parsed.append((p, parse_cached(hashlib.sha1(data).hexdigest(), data, display_name(p), formats_key())))
        except Exception as e:  # keep the app usable when one file can't be read
            failed.append((p, str(e)))

    if parsed or failed:
        st.subheader("Files")
        for p, r in parsed:
            c1, c2 = st.columns([5, 1])
            period = ""
            if r.statement is not None and r.statement.period_end:
                period = f" · {r.statement.period_start:%d-%m-%Y} – {r.statement.period_end:%d-%m-%Y}"
            where = " · inbox" if p.parent == INBOX else ""
            c1.markdown(f"**{r.broker}** · {display_name(p)}  \n<small>{len(r.transactions)} rows{period}{where}</small>", unsafe_allow_html=True)
            if c2.button("✕", key=f"rm-{p.name}", help="Remove this file"):
                p.unlink()
                st.rerun()
        for p, err in failed:
            c1, c2 = st.columns([5, 1])
            c1.error(f"{display_name(p)}: {err}")
            if c2.button("✕", key=f"rm-{p.name}", help="Remove this file"):
                p.unlink()
                st.rerun()

    st.subheader("Sync")
    secrets = load_secrets()
    have_lynx = bool(secrets["MEERWAARDE_IBKR_TOKEN"] and secrets["MEERWAARDE_IBKR_QUERY"])
    have_bv = bool(secrets["MEERWAARDE_BITVAVO_KEY"] and secrets["MEERWAARDE_BITVAVO_SECRET"])
    st.caption(("✅" if have_lynx else "➖") + " LYNX (Flex)  ·  " + ("✅" if have_bv else "➖") + " Bitvavo (API)",
               help="Credentials come from environment variables or the file in MEERWAARDE_SECRETS (outside the data "
                    "folder): MEERWAARDE_IBKR_TOKEN, MEERWAARDE_IBKR_QUERY, MEERWAARDE_BITVAVO_KEY, "
                    "MEERWAARDE_BITVAVO_SECRET. They are never stored by the app.")
    if st.button("Sync now", width="stretch", disabled=not (have_lynx or have_bv)):
        with st.spinner("Downloading from the brokers…"):
            bv_pdf = [t for _, r in parsed if r.broker == "Bitvavo" for t in r.transactions]
            st.session_state.sync_results = run_sync(DATA, lynx=have_lynx, bitvavo=have_bv, secrets=secrets,
                                                     pdf_rows=bv_pdf or None)
        st.rerun()
    for res in st.session_state.get("sync_results", []):
        (st.success if res.ok else st.error)(f"{res.source}: {res.message}")
        rec = res.details.get("reconcile")
        if rec:
            st.caption(f"Checked against your PDF: {rec['matched']} of {rec['api']} rows have the same id; quantity "
                       f"equal {rec['qty_ok']}, amount equal {rec['total_ok']} of {rec['with_total']}; time offset "
                       f"{', '.join(f'{h:g} h' for h in rec['hour_offsets']) or '—'}; fee in the amount sent: "
                       f"{rec['buy_fee_rule'] or '—'}.")
    stored = SyncStore(DATA)
    if stored.flex_dir.exists() or stored.bitvavo_file.exists():
        n_flex = len(list(stored.flex_dir.glob("*.xml"))) if stored.flex_dir.exists() else 0
        st.caption(f"Stored: {n_flex} LYNX download(s), {len(stored.bitvavo_items())} Bitvavo items. Rows your uploaded "
                   "exports already cover are not counted twice.")

    st.subheader("Settings")
    fetch = st.toggle("Look up prices online", value=os.environ.get("MEERWAARDE_OFFLINE") != "1", help="Bitvavo for crypto, justETF for ETFs, Yahoo Finance for shares, ECB for exchange rates.")
    own_wallet = st.toggle("Crypto withdrawals go to my own wallet", value=True, help="Transfers to a hardware wallet such as a Ledger are not sales; the coins keep their purchase price.")
    conversions = st.toggle("Treat same-day ISIN swaps as conversions", value=True, help="For example Royal Dutch Shell A into Shell plc: not a sale.")
    profile = TaxProfile.load(PROFILE)
    year = int(st.number_input("Tax year", min_value=2026, max_value=2035, value=2026, step=1))
    exemption = st.number_input(f"Own exemption {year} (€)", min_value=0.0, max_value=50000.0,
                                value=float(profile.exemption_rules().base_for(year)), step=50.0, key=f"exemption-{year}",
                                help="€10,000 per person for 2026, indexed later (enter the published amount). Exemption "
                                     "carried forward from earlier years is added automatically (Tax years tab).")
    original = st.toggle("Use the original price when it's higher than 31-12-2025", value=True,
                         help="Allowed for sales until the end of 2030, and only to reduce a gain.")
    with st.expander("Reynders tax settings"):
        ry_cap = st.toggle("Limit the interest part to the real gain", value=True, key="ry-cap",
                           help="The taxed interest part is never more than what you really gained on the lot (sale price - "
                                "purchase price, without costs); a sale at a loss pays nothing. Check with your adviser.")
        ry_deduct = st.toggle("Don't tax the interest part again at 10%", value=True, key="ry-deduct",
                              help="The part taxed as Reynders income is taken out of the gain for the meerwaardebelasting.")
    if st.button("Refresh prices", width="stretch"):
        Fetcher(cache_dir=CACHE).clear_current()
        st.toast("Current prices will be fetched again")


# ---------------------------------------------------------------- manual positions
MANUAL_COLS = ["broker", "asset", "name", "asset_class", "date", "qty", "price", "costs", "price_now", "price_now_date",
               "price_31dec", "note"]


def manual_frame(book: ManualBook) -> pd.DataFrame:
    rows = [dict(broker=p.broker, asset=p.asset, name=p.name, asset_class=p.asset_class, date=t.date, qty=t.qty,
                 price=t.price, costs=t.costs, price_now=p.price_now, price_now_date=p.price_now_date,
                 price_31dec=p.price_31dec, note=p.note) for p in book.positions for t in sorted(p.trades, key=lambda t: t.date)]
    df = pd.DataFrame(rows, columns=MANUAL_COLS)
    for c in ("date", "price_now_date"):
        df[c] = pd.to_datetime(df[c]).dt.date
    return df


def manual_from_frame(df: pd.DataFrame, old: ManualBook) -> ManualBook:
    """Rows with the same broker and asset are one position; position fields come from its first row that has them."""
    ids = {(p.broker, p.asset): p.id for p in old.positions}
    book = ManualBook()
    df = df.dropna(how="all")
    for r in df.itertuples():
        name = "" if pd.isna(r.name) else str(r.name).strip()
        asset = "" if pd.isna(r.asset) else str(r.asset).strip().upper()
        asset = asset or asset_code(name)
        broker = "" if pd.isna(r.broker) else str(r.broker).strip()
        broker = broker or "Manual"
        num = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
        pos = next((p for p in book.positions if (p.broker, p.asset) == (broker, asset)), None)
        if pos is None:
            pos = ManualPosition(asset, name, r.asset_class if r.asset_class in ASSET_CLASSES else "Fund", broker,
                                 id=ids.get((broker, asset)) or book.new_id())
            book.positions.append(pos)
        pos.name = pos.name or name
        for k in ("price_now", "price_31dec"):
            if getattr(pos, k) is None:
                setattr(pos, k, num(getattr(r, k)))
        if pos.price_now_date is None and not pd.isna(r.price_now_date):
            pos.price_now_date = pd.Timestamp(r.price_now_date).date()
        pos.note = pos.note or ("" if pd.isna(r.note) else str(r.note))
        if not pd.isna(r.date) and num(r.qty):
            pos.trades.append(ManualTrade(pd.Timestamp(r.date).date(), num(r.qty), num(r.price), num(r.costs) or 0.0))
    return book


def manual_editor(book: ManualBook, key: str):
    st.caption("Funds at a bank, tak 21/23 policies, savings or investment gold that no export covers. One row per purchase "
               "or sale (negative quantity); rows with the same broker and asset form one position. Prices per unit in EUR "
               "before costs; a policy or account without units: quantity 1 and its value as the price. The 31-12-2025 "
               "value is needed for units you held on that date. Savings in euro never give a gain (class Cash/FX, asset "
               "EUR, price 1).")
    ed = st.data_editor(manual_frame(book), num_rows="dynamic", hide_index=True, width="stretch", key=key, column_config={
        "broker": st.column_config.TextColumn("Bank / insurer", help="Ownership shares per broker (Tax years tab) apply"),
        "asset": st.column_config.TextColumn("ISIN or code", help="Empty: a code is made from the name"),
        "name": "Name",
        "asset_class": st.column_config.SelectboxColumn("Class", options=list(ASSET_CLASSES), default="Fund",
                                                        help="Insurance = tak 21/23/26, Gold = investment gold"),
        "date": st.column_config.DateColumn("Date"),
        "qty": st.column_config.NumberColumn("Quantity (− = sold)", format="%.6g"),
        "price": st.column_config.NumberColumn("Price (€)", format="%.6g"),
        "costs": st.column_config.NumberColumn("Costs (€)", format="%.2f", help="Entry fee, commission: profit only, not the tax"),
        "price_now": st.column_config.NumberColumn("Price now (€)", format="%.6g"),
        "price_now_date": st.column_config.DateColumn("Price date"),
        "price_31dec": st.column_config.NumberColumn("Value 31-12-2025 (€)", format="%.6g"),
        "note": "Note"})
    if st.button("Save manual positions", key=f"{key}-save"):
        new = manual_from_frame(ed, book)
        new.save(MANUAL_JSON)
        for w in new.warnings():
            st.warning(w)
        st.toast("Manual positions saved")
        st.rerun()
    st.caption(f"Saved in {MANUAL_JSON}.")


manual = ManualBook.load(MANUAL_JSON)

# ---------------------------------------------------------------- empty state
if not parsed and not manual.positions and not SyncStore(DATA).load().transactions:
    st.header("Add your exports to start")
    st.markdown(
        """
Upload the PDF exports in the sidebar. They are kept on this computer, in `"""
        + str(UPLOADS) + """`.

| Broker | What to export |
|---|---|
| **Bitvavo** | The full transaction overview (*Transactieoverzicht*, all dates), as PDF. |
| **DEGIRO** | *Transacties* and the *Rekeningoverzicht* (account statement) for the full period since your first trade, as PDF or CSV. Later exports (for example every month, as CSV) can go straight into the inbox folder. |
| **LYNX** | The *Activity Statement* PDF for each year you had the account, plus the current year to date. |

After that, the app shows your profit per broker, the meerwaardebelasting if you sold everything today,
and a simulator for how much you can sell before going over the yearly exemption.

Positions no export covers (a fund at your bank, a tak 23 policy, investment gold) can be added below.
"""
    )
    with st.expander("Add manual positions"):
        manual_editor(manual, "manual-empty")
    st.stop()

# ---------------------------------------------------------------- build
results = [r for _, r in parsed]
txns, statements, parse_warnings = combine(results)
synced = SyncStore(DATA).load()
if synced.transactions:
    from meerwaarde.sync import merge_synced
    sync_added, sync_notes = merge_synced(txns, statements, synced.transactions)
    txns = txns + sync_added
    parse_warnings = parse_warnings + sync_notes + synced.warnings
opts = PortfolioOptions(own_wallet_withdrawals=own_wallet, detect_conversions=conversions)
pf = Portfolio(txns + manual.transactions(), statements, opts)
rules = TaxRules(year=int(year), exemption=float(exemption), use_original_if_higher=original)
pb = PriceBook(statements, fetch=fetch, cache_dir=CACHE, reference_date=rules.reference_date)
manual.apply_prices(pb)
if PRICES_CSV.exists():
    pb.load_overrides_csv(PRICES_CSV.read_text())
reynders_book = ReyndersBook.load(REYNDERS_JSON)
calc = TaxCalculator(pf, pb, rules, reynders=Reynders(reynders_book, ReyndersRules(cap_at_gain=ry_cap, deduct_from_cgt=ry_deduct),
                                                      pb.eur_per))
rep = Report(pf, pb, calc, parse_warnings + manual.warnings() + pf.warnings, profile, manual,
             [r.overview for r in results if getattr(r, "overview", None) is not None])
hh = profile.household

with st.spinner("Looking up prices…"):
    holdings_df = rep.holdings()
realized_df, income_df, summary_df = rep.realized(), rep.income(), rep.summary()
brokers = pf.brokers()


def money(v):
    return "—" if v is None or pd.isna(v) else f"€{v:,.0f}"


def reynders_panel():
    with st.expander("Reynders tax (bond funds)", expanded=bool(rep.reynders_flags())):
        st.caption("Funds with more than 10% in bonds (25% for units bought before 2018) pay 30% on the interest part of "
                   "the gain when sold, with no exemption. The best basis is the TIS (taxable income per share) that the "
                   "fund provider publishes, usually with the Belgian tax information or the distribution/tax documents on "
                   "the fund page (iShares, Vanguard, Amundi). Enter the TIS on or just before your purchase dates and the "
                   "latest one. Without TIS, the bond share of the fund (asset test) is used; without either, the whole "
                   "gain counts (worst case). Distributing funds are often still taxed in practice: mark a fund as not "
                   "subject only if its prospectus says so.")
        for f in rep.reynders_flags():
            st.warning(f)
        fund_like = {h.asset: h for h in pf.holdings() if h.asset_class in ("Bond ETF", "Fund", "ETF", "Bond")}
        assets = sorted(set(reynders_book.funds) | {a for a, h in fund_like.items() if h.asset_class == "Bond ETF"})
        options = sorted(set(fund_like) | set(assets))
        rows = []
        for a in assets:
            f = reynders_book.funds.get(a)
            h = fund_like.get(a)
            rows.append(dict(asset=a, name=h.name if h else "", subject=bool(f.subject) if f else bool(h and h.asset_class == "Bond ETF"),
                             currency=f.currency if f else "EUR",
                             debt_pct=(f.debt_pct * 100 if f and f.debt_pct is not None else None),
                             source=f.source if f else "", note=f.note if f else ""))
        fdf = pd.DataFrame(rows, columns=["asset", "name", "subject", "currency", "debt_pct", "source", "note"])
        st.markdown("**Funds**")
        fed = st.data_editor(fdf, num_rows="dynamic", hide_index=True, width="stretch", key="ry-funds", column_config={
            "asset": st.column_config.SelectboxColumn("Fund", options=options, required=True),
            "name": st.column_config.TextColumn("Name", disabled=True),
            "subject": st.column_config.CheckboxColumn("Subject to Reynders tax"),
            "currency": st.column_config.TextColumn("TIS currency", help="Currency the provider publishes the TIS in (EUR, USD...)"),
            "debt_pct": st.column_config.NumberColumn("Bond share (%)", min_value=0.0, max_value=100.0, format="%.1f",
                                                      help="For the asset test, when you have no TIS"),
            "source": st.column_config.LinkColumn("Where you found it"), "note": "Note"})
        tis_rows = [dict(asset=a, date=d, value=v) for a, f in sorted(reynders_book.funds.items()) for d, v in sorted(f.tis)]
        tdf = pd.DataFrame(tis_rows, columns=["asset", "date", "value"])
        tdf["date"] = pd.to_datetime(tdf["date"]).dt.date
        st.markdown("**TIS values** (per share, in the fund's TIS currency)")
        ted = st.data_editor(tdf, num_rows="dynamic", hide_index=True, width="stretch", key="ry-tis", column_config={
            "asset": st.column_config.SelectboxColumn("Fund", options=options, required=True),
            "date": st.column_config.DateColumn("Date"), "value": st.column_config.NumberColumn("TIS", format="%.6g")})
        if st.button("Save Reynders tax data"):
            book = ReyndersBook()
            for r in fed.dropna(subset=["asset"]).itertuples():
                cur = str(r.currency).strip().upper() if pd.notna(r.currency) and str(r.currency).strip() else "EUR"
                book.funds[r.asset] = ReyndersFund(r.asset, bool(r.subject), cur,
                                                   None if pd.isna(r.debt_pct) else float(r.debt_pct) / 100, [],
                                                   "" if pd.isna(r.source) else str(r.source),
                                                   "" if pd.isna(r.note) else str(r.note))
            for r in ted.dropna(subset=["asset", "date", "value"]).itertuples():
                book.funds.setdefault(r.asset, ReyndersFund(r.asset)).tis.append((pd.Timestamp(r.date).date(), float(r.value)))
            book.save(REYNDERS_JSON)
            st.toast("Saved")
            st.rerun()
        rt = rep.reynders_table()
        if not rt.empty:
            st.dataframe(rt, hide_index=True, width="stretch", column_config={
                "price_eur": st.column_config.NumberColumn("Price", format="%.6g"),
                "base_eur": st.column_config.NumberColumn("Interest part", format=EUR),
                "tax_eur": st.column_config.NumberColumn("Reynders tax (30%)", format=EUR)})
        st.caption(f"Saved in {REYNDERS_JSON}. Status Open = if you sold everything today.")


tabs = st.tabs(["Overview", "Positions", "Meerwaardebelasting", "Simulator", "Year-end", "Switch", "Tax report",
                "Tax years", "Prices", "Data", "Manual positions", "Alerts", "Performance", "Allocation"])

# ---------------------------------------------------------------- overview
with tabs[0]:
    pick = st.radio("Broker", ["All"] + brokers, horizontal=True, label_visibility="collapsed")
    row = summary_df[summary_df.broker == pick].iloc[0]
    c = st.columns(6)
    c[0].metric("Total profit", money(row.total), help="Realized + unrealized + dividends and interest")
    c[1].metric("Realized", money(row.realized), help="Sales, after costs, first in first out")
    c[2].metric("Unrealized", money(row.unrealized), help="Open positions at the latest price")
    c[3].metric("Dividends & interest", money(row.income))
    c[4].metric("Value now", money(row.value))
    c[5].metric("Still invested", money(row.cost_open), help="Purchase cost of what you still hold")
    if row.missing_prices:
        st.warning(f"{int(row.missing_prices)} position(s) have no current price. Add them on the Prices tab.")

    left, right = st.columns(2)
    by_year = rep.by_year()
    if pick != "All":
        by_year = by_year[by_year.broker == pick]
    with left:
        st.subheader("Realized profit per year")
        if by_year.empty:
            st.caption("No sales yet.")
        else:
            st.altair_chart(
                alt.Chart(by_year).mark_bar(cornerRadius=3).encode(
                    x=alt.X("year:O", title=None), y=alt.Y("sum(pnl_eur):Q", title="EUR"),
                    color=alt.Color("broker:N", legend=alt.Legend(orient="top", title=None)),
                    tooltip=["broker", "year", alt.Tooltip("sum(pnl_eur):Q", format=",.0f", title="Profit")]),
                width="stretch")
    with right:
        st.subheader("Where the profit sits")
        pos = []
        for (b, a), g in realized_df.groupby(["broker", "asset"]):
            pos.append(dict(broker=b, asset=a, name=g.name.iloc[-1], realized=g.pnl_eur.sum(), unrealized=0.0, income=0.0))
        idx = {(p["broker"], p["asset"]): p for p in pos}
        for h in holdings_df.itertuples():
            p = idx.setdefault((h.broker, h.asset), dict(broker=h.broker, asset=h.asset, name=h.name, realized=0.0, unrealized=0.0, income=0.0))
            if p not in pos:
                pos.append(p)
            p["unrealized"] += h.unrealized_eur if pd.notna(h.unrealized_eur) else 0.0
        for i in income_df.itertuples():
            p = idx.get((i.broker, i.asset))
            if p:
                p["income"] += i.amount_eur
        pos_df = pd.DataFrame(pos)
        if not pos_df.empty:
            pos_df["total"] = pos_df.realized + pos_df.unrealized + pos_df.income
            if pick != "All":
                pos_df = pos_df[pos_df.broker == pick]
            top = pos_df.reindex(pos_df.total.abs().sort_values(ascending=False).index).head(12)
            top["label"] = top.name.str.slice(0, 34) + " · " + top.broker
            st.altair_chart(
                alt.Chart(top).mark_bar(cornerRadius=3).encode(
                    y=alt.Y("label:N", sort="-x", title=None), x=alt.X("total:Q", title="EUR"),
                    color=alt.Color("broker:N", legend=None),
                    tooltip=["name", "broker", alt.Tooltip("realized:Q", format=",.0f"), alt.Tooltip("unrealized:Q", format=",.0f"),
                             alt.Tooltip("income:Q", format=",.0f", title="dividends"), alt.Tooltip("total:Q", format=",.0f")]),
                width="stretch")

    if rep.warnings:
        with st.expander(f"Notes about your data ({len(rep.warnings)})"):
            for w in rep.warnings:
                st.markdown(f"- {w}")

# ---------------------------------------------------------------- positions
with tabs[1]:
    st.subheader("Open positions")
    st.dataframe(holdings_df, hide_index=True, width="stretch", column_config={
        "qty": st.column_config.NumberColumn("Quantity", format="%.8g"),
        "cost_eur": st.column_config.NumberColumn("Cost", format=EUR),
        "price_eur": st.column_config.NumberColumn("Price", format="%.6g"),
        "value_eur": st.column_config.NumberColumn("Value", format=EUR),
        "unrealized_eur": st.column_config.NumberColumn("Unrealized", format=EUR),
        "price_date": st.column_config.DateColumn("Price date"), "price_source": "Price source"})
    st.download_button("Download open positions (CSV)", holdings_df.to_csv(index=False), "holdings.csv", "text/csv")
    st.subheader("Sales")
    st.dataframe(realized_df.sort_values("date", ascending=False), hide_index=True, width="stretch", column_config={
        "proceeds_eur": st.column_config.NumberColumn("Proceeds", format=EUR),
        "cost_eur": st.column_config.NumberColumn("Cost", format=EUR),
        "pnl_eur": st.column_config.NumberColumn("Profit", format=EUR)})
    st.download_button("Download sales (CSV)", realized_df.to_csv(index=False), "realized.csv", "text/csv")
    if not income_df.empty:
        st.subheader("Dividends and interest")
        st.dataframe(income_df.sort_values("date", ascending=False), hide_index=True, width="stretch",
                     column_config={"amount_eur": st.column_config.NumberColumn("Amount", format=EUR)})
        st.download_button("Download dividends and interest (CSV)", income_df.to_csv(index=False), "income.csv", "text/csv")

# ---------------------------------------------------------------- tax: sell everything
sold_lines = calc.sold_lines()
open_lines = calc.open_lines()
with tabs[2]:
    st.subheader(f"Meerwaardebelasting {rules.year} if you sold everything today")
    summ = rep.tax_summary(sold_lines + open_lines)
    sold_total = sum(v["sold"] for v in summ["per_type"].values())
    carried = sum(e[-1].carried for e in (rep.exemption_history(p) for p in hh.ids) if e)
    c = st.columns(4)
    c[0].metric("Already sold this year", money(sold_total), help="Taxable gain of this year's sales, including other sales from the Tax years tab")
    c[1].metric("Taxable gain if you sold the rest", money(summ["net"] - sold_total))
    c[2].metric("Exemption", money(summ["exemption"]),
                help=("Per person. " if not hh.single else "") + (f"Includes {money(carried)} carried forward from earlier years." if carried else "Nothing carried forward from earlier years."))
    c[3].metric("Meerwaardebelasting", money(summ["tax"]), help="10% of the net gain above the exemption, per person")
    ry_all = rep.reynders_summary(sold_lines + open_lines)
    if ry_all["base"]:
        st.info(f"Plus Reynders tax on bond funds: {money(ry_all['tax'])} (30% of an interest part of {money(ry_all['base'])}, "
                "no exemption). That part is " + ("not taxed again at 10%." if ry_deduct else "also counted in the 10% gain."))
    if not hh.single:
        st.dataframe(pd.DataFrame([dict(person=hh.name(pid), net=pp["net"], exemption=pp["exemption"], taxable=pp["taxable"], tax=pp["tax"])
                                   for pid, pp in summ["people"].items()]), hide_index=True, width="stretch", column_config={
            "person": "Person", "net": st.column_config.NumberColumn("Net taxable gain", format=EUR),
            "exemption": st.column_config.NumberColumn("Exemption", format=EUR),
            "taxable": st.column_config.NumberColumn("Taxable", format=EUR), "tax": st.column_config.NumberColumn("Meerwaardebelasting", format=EUR)})
    per = pd.DataFrame([dict(type=LAW_NAMES[k], sold=v["sold"], open=v["open"], net=v["sold"] + v["open"])
                        for k, v in summ["per_type"].items()])
    st.dataframe(per, hide_index=True, width="stretch", column_config={
        "type": "Type (as the law defines it)", "sold": st.column_config.NumberColumn("Sold this year", format=EUR),
        "open": st.column_config.NumberColumn("If you sold the rest", format=EUR), "net": st.column_config.NumberColumn("Net", format=EUR)})
    st.caption("All types fall under 'andere financiële activa': one 10% rate and one exemption, and losses offset gains across types within the year.")
    lines_df = rep.tax_lines()
    st.dataframe(lines_df.sort_values("taxable_gain_eur", ascending=False), hide_index=True, width="stretch", column_config={
        "price_eur": st.column_config.NumberColumn("Price", format="%.6g"),
        "price_31dec_eur": st.column_config.NumberColumn("Price 31-12-2025", format="%.6g"),
        "avg_purchase_eur": st.column_config.NumberColumn("Avg purchase price", format="%.6g"),
        "proceeds_eur": st.column_config.NumberColumn("Proceeds", format=EUR),
        "acquisition_eur": st.column_config.NumberColumn("Purchase value used", format=EUR),
        "taxable_gain_eur": st.column_config.NumberColumn("Taxable gain", format=EUR), "law_type": None})
    if (lines_df.missing != "").any():
        st.warning("Some lines are missing a price, so their gain is incomplete. Add the price on the Prices tab.")
    st.download_button("Download (CSV)", lines_df.to_csv(index=False), "meerwaardebelasting.csv", "text/csv")
    reynders_panel()
    with st.expander("How it's calculated"):
        st.markdown("\n".join(f"- {t}" for t in RULES_TEXT))

# ---------------------------------------------------------------- simulator
with tabs[3]:
    st.subheader("How much can you sell before paying tax?")
    hold = {(h.broker, h.asset): h for h in pf.holdings()}
    base = []
    for h in hold.values():
        p = pb.now(h)
        base.append(dict(key=f"{h.broker}|{h.asset}", broker=h.broker, name=h.name, type=LAW_NAMES[law_type(h.asset_class)],
                         held=h.qty, price=p.value if p else None))
    base_df = pd.DataFrame(base).sort_values(["broker", "name"]).reset_index(drop=True)
    state = st.session_state.setdefault("sim", {})
    base_df["sell"] = base_df.key.map(lambda k: state.get(k, {}).get("sell", 0.0))
    base_df["price"] = [state.get(k, {}).get("price") or p for k, p in zip(base_df.key, base_df.price)]

    with st.expander("Other sales this year that aren't in your exports (what-if; real ones go on the Tax years tab)"):
        extra = {}
        cols = st.columns(len(LAW_TYPES))
        for i, (k, (name, nl)) in enumerate(LAW_TYPES.items()):
            st.session_state.setdefault(f"extra-{k}", 0.0)
            extra[k] = cols[i].number_input(name, step=100.0, key=f"extra-{k}", help=f"{nl}: taxable gain (negative for a loss)")

    edited = st.data_editor(base_df, hide_index=True, width="stretch", key="sim-editor",
                            disabled=["key", "broker", "name", "type", "held"], column_config={
                                "key": None, "name": "Position", "held": st.column_config.NumberColumn("Held", format="%.8g"),
                                "price": st.column_config.NumberColumn("Price now (€)", format="%.6g", min_value=0.0),
                                "sell": st.column_config.NumberColumn("Sell", format="%.8g", min_value=0.0)})
    for r in edited.itertuples():
        state[r.key] = {"sell": float(r.sell or 0.0), "price": float(r.price) if pd.notna(r.price) else None}

    sell = {tuple(k.split("|", 1)): v["sell"] for k, v in state.items() if v.get("sell")}
    price_over = {tuple(k.split("|", 1)): v["price"] for k, v in state.items() if v.get("price")}
    sim = rep.simulate(sell=sell, price_override=price_over, extra=extra)
    sim_lines, summ = sim.lines, sim.summary
    room = summ["room"]
    c = st.columns(4)
    c[0].metric("Tax-free room left", money(max(0.0, room)) if room >= 0 else "€0",
                delta=None if room >= 0 else f"over by {money(-room)}", delta_color="inverse")
    c[1].metric("Net taxable gain", money(summ["net"]))
    c[2].metric("Cash from these sales", money(sum(l.proceeds for l in sim_lines)))
    c[3].metric("Meerwaardebelasting", money(summ["tax"]))
    st.progress(min(1.0, max(0.0, summ["net"] / summ["exemption"])) if summ["exemption"] else 1.0,
                text=f"{money(max(0.0, summ['net']))} of the {money(summ['exemption'])} exemption used")
    if not hh.single:
        st.caption(" · ".join(f"{hh.name(pid)}: room {money(max(0.0, pp['room']))}, tax {money(pp['tax'])}"
                              for pid, pp in summ["people"].items()))
    if sim.reynders.get("base"):
        st.warning(f"These sales also pay Reynders tax: {money(sim.reynders['tax'])} (30% of the interest part of the bond "
                   f"funds, {money(sim.reynders['base'])}). It has no exemption, so 'Max without tax' only covers the 10%.")

    out_df = sim.positions
    st.dataframe(out_df, hide_index=True, width="stretch", column_config={
        "key": None, "asset": None, "type": None, "held": None, "price": None,
        "name": "Position", "sell": st.column_config.NumberColumn("Selling", format="%.8g"),
        "gain": st.column_config.NumberColumn("Taxable gain", format=EUR),
        "proceeds": st.column_config.NumberColumn("Proceeds", format=EUR),
        "max_qty": st.column_config.NumberColumn("Max without tax", format="%.8g", help="Largest quantity you can sell from this position on top of everything else, without paying tax"),
        "max_value": st.column_config.NumberColumn("Max without tax (€)", format=EUR)})
    c1, c2, c3 = st.columns([3, 1, 1])
    choice = c1.selectbox("Fill the remaining exemption with", out_df.key, format_func=lambda k: f"{k.split('|')[0]} · {hold[tuple(k.split('|', 1))].name}")
    if c2.button("Fill", width="stretch"):
        mx = out_df.set_index("key").loc[choice, "max_qty"]
        state.setdefault(choice, {})["sell"] = float(mx)
        st.session_state.pop("sim-editor", None)
        st.rerun()
    if c3.button("Clear all", width="stretch"):
        st.session_state.sim = {}
        st.session_state.pop("sim-editor", None)
        st.rerun()
    st.caption("The Excel export (sidebar) includes this simulation.")

# ---------------------------------------------------------------- year-end harvesting
with tabs[4]:
    st.subheader(f"Use the rest of the {rules.year} exemption before 31 December")
    st.caption("Sell lots with a gain up to the exemption you have left and buy them back: their purchase value for the "
               "meerwaardebelasting goes up without tax. Unused exemption is gone at year end, except €1,000 that carries forward.")
    h1, h2, h3 = st.columns([1, 1, 2])
    keep = h1.toggle("Keep €1,000 to carry forward", value=True, key="h-keep",
                     help="The carry-forward is worth as much as harvesting €1,000, without trading costs.")
    spread = h2.number_input("Spread per trade (%)", min_value=0.0, max_value=2.0, value=0.05, step=0.01, key="h-spread",
                             help="Half the difference between bid and ask, paid on the sale and again on the purchase.") / 100
    hold_keys = {f"{h.broker}|{h.asset}": f"{h.broker} · {h.name}" for h in pf.holdings() if h.asset_class != "Cash/FX"}
    excl = h3.multiselect("Leave out", list(hold_keys), format_func=lambda k: hold_keys[k], key="h-excl")
    with st.expander("Commission per order"):
        cdf = pd.DataFrame([dict(broker=b, fixed=DEFAULT_COMMISSION.get(b, (5.0, 0.001))[0],
                                 pct=DEFAULT_COMMISSION.get(b, (5.0, 0.001))[1] * 100) for b in brokers])
        ced = st.data_editor(cdf, hide_index=True, key="h-comm", disabled=["broker"], column_config={
            "broker": "Broker", "fixed": st.column_config.NumberColumn("Fixed (€)", min_value=0.0),
            "pct": st.column_config.NumberColumn("Percentage (%)", min_value=0.0, format="%.3f")})
        st.caption("Defaults from your recent orders: DEGIRO about €1–3, LYNX about €5–8.50, Bitvavo 0.25%. The stock "
                   "exchange tax uses the rate each broker charged you on that product before.")
    hc = CostModel(spread=spread, commission={r.broker: (float(r.fixed or 0), float(r.pct or 0) / 100) for r in ced.itertuples()})
    plan = plan_harvest(rep, keep_carry=keep, costs=hc, exclude=set(excl))
    for pid, name in plan.persons:
        s = plan.status[pid]
        st.caption((f"**{name}** · " if not hh.single else "") +
                   f"exemption {money(s['exemption'])} · net gain so far {money(s['net'])} · left {money(s['room'])}"
                   + (f" · kept for the carry-forward {money(s['reserve'])}" if s["reserve"] else "")
                   + f" · to harvest {money(s['target'])}")
    c = st.columns(4)
    c[0].metric("Gain to harvest", money(plan.gain), help="Taxable gain realized now; the purchase value goes up by this much")
    c[1].metric("Tax saved later", money(plan.tax_saved), help="10% of the harvested gain, if it would otherwise be taxed when you sell later")
    c[2].metric("Costs now", money(plan.costs), help="Stock exchange tax on the sale and the purchase back, two commissions, spread")
    c[3].metric("Net benefit", money(plan.net))
    if plan.lines:
        st.dataframe(pd.DataFrame([dict(broker=l.broker, name=l.name, qty=l.qty, price=l.price, value=l.value, gain=l.gain,
                                        tob=f"{l.tob_rate:.2%} ({l.tob_source})", costs=l.costs, saved=l.tax_saved, net=l.net)
                                   for l in plan.lines]), hide_index=True, width="stretch", column_config={
            "broker": "Broker", "name": "Position", "qty": st.column_config.NumberColumn("Sell and buy back", format="%.8g"),
            "price": st.column_config.NumberColumn("Price (€)", format="%.6g"), "value": st.column_config.NumberColumn("Value", format=EUR),
            "gain": st.column_config.NumberColumn("Taxable gain", format=EUR), "tob": "Stock exchange tax",
            "costs": st.column_config.NumberColumn("Costs", format=EUR), "saved": st.column_config.NumberColumn("Tax saved later", format=EUR),
            "net": st.column_config.NumberColumn("Net", format=EUR)})
        if st.button("Put these sales in the Simulator"):
            st.session_state.sim = {f"{l.broker}|{l.asset}": {"sell": float(l.qty), "price": None} for l in plan.lines}
            st.session_state.pop("sim-editor", None)
            st.toast("Loaded in the Simulator tab")
            st.rerun()
    else:
        st.info("Nothing worth harvesting with these settings.")
    if plan.losses:
        with st.expander(f"Positions at a taxable loss ({len(plan.losses)})"):
            useful = sum(l.tax_saved_now for l in plan.losses)
            st.caption("Selling these would offset gains realized this year. " + (
                f"Up to {money(useful)} of this year's tax." if useful else
                "You're under the exemption this year, so that would only shrink the room you can still use."))
            st.dataframe(pd.DataFrame([dict(broker=l.broker, name=l.name, qty=l.qty, value=l.value, loss=l.loss, saved=l.tax_saved_now)
                                       for l in plan.losses]), hide_index=True, width="stretch", column_config={
                "broker": "Broker", "name": "Position", "qty": st.column_config.NumberColumn("Quantity", format="%.8g"),
                "value": st.column_config.NumberColumn("Value", format=EUR), "loss": st.column_config.NumberColumn("Taxable loss", format=EUR),
                "saved": st.column_config.NumberColumn("Tax saved this year", format=EUR)})
    for n in plan.notes:
        st.info(n)

# ---------------------------------------------------------------- switch planner
with tabs[5]:
    st.subheader("Switch to another fund")
    st.caption("Sell positions and buy a target fund: the cost now against the yearly effect, and a sw_plan that spreads the "
               "sales over the years so each year stays within the exemption.")
    sw_keys = {f"{h.broker}|{h.asset}": f"{h.broker} · {h.name} ({h.qty:g})" for h in pf.holdings() if h.asset_class != "Cash/FX"}
    sw_default = [f"{b}|{a}" for b, a in default_sources(rep) if f"{b}|{a}" in sw_keys]
    sw_pick = st.multiselect("Positions to switch", list(sw_keys), default=sw_default, format_func=lambda k: sw_keys[k],
                             key="sw-pick", help="Default: the distributing ETFs you hold.")
    s1, s2, s3 = st.columns(3)
    sw_horizon = s1.slider("Compare over (years)", 3, 30, 10, key="sw-horizon")
    sw_back = s2.toggle("Count 10% meerwaardebelasting later on reinvested dividends", value=True, key="sw-back",
                        help="Conservative: an accumulating fund reinvests the dividends, so they end up in the gain you "
                             "pay 10% on when you sell (if that year's gains exceed the exemption).")
    sw_spread = s3.number_input("Spread per trade (%)", 0.0, 2.0, 0.05, 0.01, key="sw-spread") / 100
    sw_costs = CostModel(spread=sw_spread)

    if "sw-targets" not in st.session_state:
        saved = []
        try:
            saved = funds_from_json(json.loads(FUNDS_JSON.read_text())) if FUNDS_JSON.exists() else []
        except (OSError, ValueError):
            saved = []
        st.session_state["sw-targets"] = saved or [CATALOG[i] for i in WORLD_TARGETS]
    sw_srcs = sources_for(rep, {tuple(k.split("|", 1)) for k in sw_pick}, sw_costs) if sw_pick else []

    with st.expander("Suggest cheaper funds with Gemini (optional, your own API key)"):
        st.caption("Sends only the names, ISINs, index and costs of the positions above, no amounts. Suggestions are "
                   "marked unverified: check them (KID, justETF) before buying. Key from the GEMINI_API_KEY environment "
                   "variable if set; it isn't stored.")
        g1, g2, g3 = st.columns([3, 2, 1])
        g_key = g1.text_input("Gemini API key", value=os.environ.get("GEMINI_API_KEY", ""), type="password", key="g-key")
        g_model = g2.text_input("Model", value=gemini.DEFAULT_MODEL, key="g-model")
        g_search = g3.toggle("Google Search", value=True, key="g-search", help="Let Gemini look up current facts")
        if st.button("Suggest", disabled=not (g_key and sw_srcs)):
            with st.spinner("Asking Gemini…"):
                res = gemini.suggest(g_key, [source_fund(x) for x in sw_srcs], model=g_model or None, search=g_search,
                                     exclude=tuple(f.isin for f in st.session_state["sw-targets"]))
            st.session_state["sw-gemini"] = res
            have = {f.isin for f in st.session_state["sw-targets"]}
            st.session_state["sw-targets"] = st.session_state["sw-targets"] + [f for f in res.funds if f.isin not in have]
            st.session_state.pop("sw-editor", None)
        res = st.session_state.get("sw-gemini")
        if res:
            if res.error:
                st.error(res.error)
            if res.funds:
                st.success(f"{len(res.funds)} suggestion(s) from {res.model} added to the sw_targets below.")
            for r in res.rejected:
                st.warning("Left out: " + r)
            if res.citations:
                st.markdown("Sources: " + " · ".join(f"[{t}]({u})" for t, u in res.citations[:8]))

    tdf = pd.DataFrame([dict(use=True, **f.as_row()) for f in st.session_state["sw-targets"]])
    ted = st.data_editor(tdf, hide_index=True, width="stretch", num_rows="dynamic", key="sw-editor", column_config={
        "use": st.column_config.CheckboxColumn("Compare"), "isin": "ISIN", "name": "Fund", "ticker": "Ticker", "index": "Index",
        "ter_pct": st.column_config.NumberColumn("TER (%)", format="%.3f"), "accumulating": st.column_config.CheckboxColumn("Acc"),
        "tob_pct": st.column_config.NumberColumn("Stock exchange tax (%)", help="Empty: unknown (1.32% assumed). Your broker's "
                                                  "rate on earlier trades of the fund wins."),
        "size_eur_m": st.column_config.NumberColumn("Size (€m)", format="%.0f"), "source": "Facts from",
        "verified": st.column_config.CheckboxColumn("Checked"), "reason": "Why suggested"})
    sw_targets = []
    for r in ted.itertuples():
        if not isinstance(r.isin, str) or not r.isin.strip():
            continue
        f = Fund(r.isin.strip().upper(), str(r.name or r.isin), str(r.ticker or ""), str(r.index or ""),
                 None if pd.isna(r.ter_pct) else float(r.ter_pct) / 100,
                 None if pd.isna(r.accumulating) else bool(r.accumulating),
                 None if pd.isna(r.tob_pct) else float(r.tob_pct) / 100, "", None if pd.isna(r.size_eur_m) else float(r.size_eur_m),
                 str(r.source or "you"), bool(r.verified) if pd.notna(r.verified) else False, str(r.reason or ""))
        if r.use:
            sw_targets.append(f)
    if st.button("Save this list of funds"):
        FUNDS_JSON.write_text(json.dumps(funds_to_json([f for f in sw_targets]), indent=2))
        st.toast("Saved")

    if sw_srcs and sw_targets:
        sw_cases = compare(rep, sw_srcs, sw_targets, sw_horizon, sw_costs, tax_reinvested=sw_back)
        sw_plans = {c.target.isin: spread_plan(rep, sw_srcs, c, sw_costs) for c in sw_cases}
        sw_cases.sort(key=lambda c: net_spread(c, sw_plans[c.target.isin]), reverse=True)
        c0 = sw_cases[0]
        st.caption(f"Value {money(c0.value)} · meerwaardebelasting if all sold now {money(c0.cgt_now)} · dividends over "
                   f"the last 12 months {money(sum(x.dividends_year for x in sw_srcs))}")
        st.dataframe(pd.DataFrame([dict(
            fund=f"{c.target.ticker} {c.target.name}".strip(), tob=f"{c.target_tob:.2%}", tob_from=c.target_tob_source,
            now=c.now, yearly=c.yearly, div=c.dividend_tax_saved - c.dividend_tax_back, ter=c.ter_saved,
            exit=c.exit_tob_extra, be=c.break_even, net_now=c.net, net_spread=net_spread(c, sw_plans[c.target.isin]),
            checked=c.target.verified) for c in sw_cases]), hide_index=True, width="stretch", column_config={
            "fund": "Target", "tob": "Stock exchange tax", "tob_from": "Rate from",
            "now": st.column_config.NumberColumn("Cost if all now", format=EUR, help="Meerwaardebelasting + stock exchange tax + commissions + spread"),
            "yearly": st.column_config.NumberColumn("Yearly effect", format=EUR),
            "div": st.column_config.NumberColumn("of which dividend tax", format=EUR),
            "ter": st.column_config.NumberColumn("of which running cost", format=EUR),
            "exit": st.column_config.NumberColumn("Extra stock exchange tax when you sell it later", format=EUR),
            "be": st.column_config.NumberColumn("Break-even (yrs)", format="%.1f"),
            "net_now": st.column_config.NumberColumn(f"Net {sw_horizon} yrs, all now", format=EUR),
            "net_spread": st.column_config.NumberColumn(f"Net {sw_horizon} yrs, spread", format=EUR),
            "checked": st.column_config.CheckboxColumn("Facts checked")})
        pick_t = st.selectbox("Plan for", [c.target.isin for c in sw_cases],
                              format_func=lambda i: next(f"{c.target.ticker} {c.target.name}" for c in sw_cases if c.target.isin == i))
        sw_case = next(c for c in sw_cases if c.target.isin == pick_t)
        sw_plan = sw_plans[pick_t]
        st.dataframe(pd.DataFrame([dict(year=st_.year, sell=", ".join(f"{q:g} × {k[0]}" for k, q in st_.units.items()),
                                        value=st_.value, gain=st_.gain, costs=st_.costs) for st_ in sw_plan.steps]),
                     hide_index=True, width="stretch", column_config={
            "year": st.column_config.NumberColumn("Year", format="%d"), "sell": "Sell (and buy the target)",
            "value": st.column_config.NumberColumn("Value", format=EUR), "gain": st.column_config.NumberColumn("Taxable gain", format=EUR),
            "costs": st.column_config.NumberColumn("Costs", format=EUR)})
        m = st.columns(3)
        m[0].metric("Meerwaardebelasting avoided", money(sw_case.cgt_now), help="Compared with selling everything now")
        m[1].metric("Yearly benefit you wait for", money(sw_plan.benefit_waited), help="While part of the position waits for a later year")
        m[2].metric("Spreading gains", money(sw_case.cgt_now - sw_plan.benefit_waited - (sw_plan.costs - sw_case.costs)))
        st.caption(("Done in " if sw_plan.done else "Not done after ") + f"{sw_plan.years} year(s), assuming today's prices, the "
                   "full exemption for this switch in later years and no other sales. Rising prices mean more years.")
        for n in sw_case.notes + ["Moving from an S&P 500 fund to a world fund changes what you own (more countries, about 60% "
                               "US instead of 100%): that's a choice, not a cost."]:
            st.info(n)
    elif not sw_srcs:
        st.info("Pick the positions to switch.")

# ---------------------------------------------------------------- tax report
with tabs[6]:
    first_year = min([d.year for d in [t.ts for t in pf.transactions]] + [rules.year])
    years = list(range(max(2024, first_year), max(rules.year, date.today().year) + 1))
    ry = st.selectbox("Income year", years, index=years.index(rules.year) if rules.year in years else len(years) - 1,
                      format_func=lambda y: f"{y} (tax return {y + 1})")
    ytr = tax_year_report(rep, ry)
    st.caption(ytr.subtitle)
    inc_tax = sum(i.to_pay for i in ytr.income.values())
    tob_total = float(ytr.tob["amount_eur"].sum()) if not ytr.tob.empty else 0.0
    c = st.columns(3)
    c[0].metric("Meerwaardebelasting", money(ytr.cgt["tax"]), help="On this year's sales (and other sales on the Tax years tab)")
    c[1].metric("To pay on dividends and interest", money(inc_tax), help="30% on what foreign brokers paid out, after the dividend exemption, minus Belgian tax already withheld (negative = refund)")
    c[2].metric("Stock exchange tax", money(tob_total), help="Withheld by the brokers; DEGIRO's is an estimate")
    d1, d2, _ = st.columns([1, 1, 3])
    d1.download_button("Excel", data=lambda: tax_report_xlsx(ytr), file_name=f"tax-report-{ry}.xlsx", mime=XLSX_MIME,
                       icon=":material/download:", on_click="ignore", width="stretch")
    d2.download_button("PDF", data=lambda: tax_report_pdf(ytr), file_name=f"tax-report-{ry}.pdf", mime="application/pdf",
                       icon=":material/download:", on_click="ignore", width="stretch")

    st.subheader("Meerwaardebelasting")
    for pid, name in ytr.persons:
        pp, e = ytr.cgt["people"][pid], ytr.exemption.get(pid)
        if not hh.single:
            st.markdown(f"**{name}**")
        st.dataframe(pd.DataFrame([dict(type=LAW_NAMES[k], gains=v["gains"], losses=v["losses"], net=v["gains"] + v["losses"])
                                   for k, v in pp["per_type"].items() if abs(v["gains"]) + abs(v["losses"]) > 0.005]),
                     hide_index=True, width="stretch", column_config={
                         "type": "Type", "gains": st.column_config.NumberColumn("Gains", format=EUR),
                         "losses": st.column_config.NumberColumn("Losses", format=EUR), "net": st.column_config.NumberColumn("Net", format=EUR)})
        txt = f"Net taxable gain {money(pp['net'])} · exemption {money(pp['exemption'])}"
        if e and e.carried:
            txt += f" (own {money(e.base)} + carried {money(e.carried)})"
        txt += f" · tax {money(pp['tax'])}"
        if e:
            txt += f" · carries forward {money(e.carry_out)}"
        st.caption(txt)
    if not ytr.sales.empty:
        with st.expander(f"Sales in {ry} ({len(ytr.sales)})"):
            st.dataframe(ytr.sales, hide_index=True, width="stretch")
    if ytr.reynders.get("base"):
        st.markdown(f"**Reynders tax** on bond funds sold: {money(ytr.reynders['tax'])} (30% of an interest part of "
                    f"{money(ytr.reynders['base'])}, no exemption)"
                    + ("" if hh.single else " · " + " · ".join(f"{name} {money(ytr.reynders['people'][pid]['tax'])}"
                                                              for pid, name in ytr.persons)))
        st.dataframe(ytr.reynders_detail, hide_index=True, width="stretch")

    if not ytr.checks.empty:
        st.markdown("**Check against the broker's annual overview**")
        st.dataframe(ytr.checks, hide_index=True, width="stretch", column_config={
            "broker_eur": st.column_config.NumberColumn("Broker's overview", format=EUR),
            "app_eur": st.column_config.NumberColumn("This app", format=EUR),
            "difference": st.column_config.NumberColumn("Difference", format=EUR)})

    st.subheader("Dividends and interest")
    for pid, name in ytr.persons:
        it = ytr.income[pid]
        if not hh.single:
            st.markdown(f"**{name}**")
        if it.lines:
            st.dataframe(pd.DataFrame([dict(income=CATEGORIES[l.category], broker=l.broker, gross=l.gross, foreign_tax=l.foreign_tax,
                                            net=l.net, be=l.be_withheld) for l in it.lines]), hide_index=True, width="stretch", column_config={
                "income": "Income", "broker": "Broker", "gross": st.column_config.NumberColumn("Gross", format=EUR),
                "foreign_tax": st.column_config.NumberColumn("Foreign tax", format=EUR),
                "net": st.column_config.NumberColumn("Net (taxable base)", format=EUR),
                "be": st.column_config.NumberColumn("Belgian tax withheld", format=EUR)})
        st.caption(f"Dividend exemption used {money(it.exemption_used)} of {money(it.exemption_available)} · "
                   f"taxable at 30% {money(it.taxable)} · tax {money(it.tax)}"
                   + (f" · already withheld in Belgium {money(it.be_withheld)}" if abs(it.be_withheld) > 0.005 else "")
                   + f" · to pay {money(it.to_pay)}")

    st.subheader("Stock exchange tax")
    st.dataframe(ytr.tob, hide_index=True, width="stretch", column_config={
        "broker": "Broker", "trades": "Trades", "amount_eur": st.column_config.NumberColumn("Amount", format=EUR), "basis": "Basis"})

    st.subheader("Checklist")
    for i, item in enumerate(ytr.checklist):
        st.checkbox(item, key=f"check-{ry}-{i}")
    st.dataframe(pd.DataFrame(ytr.accounts), hide_index=True, width="stretch",
                 column_config={"broker": "Broker", "institution": "Account held at", "country": "Country"})
    for n in ytr.notes:
        st.info(n)
    with st.expander("How dividends and interest are taxed"):
        st.markdown("\n".join(f"- {t}" for t in INCOME_RULES_TEXT))

# ---------------------------------------------------------------- tax years: people, exemption history, other income
with tabs[7]:
    st.subheader("People")
    has_partner = st.toggle("Include a partner (each person has their own exemptions)", value=not hh.single)
    partner_name = st.text_input("Partner's name", value=hh.name("partner") if not hh.single else "Partner",
                                 disabled=not has_partner)
    you_name = st.text_input("Your name", value=hh.persons[0].name)
    if has_partner:
        st.caption("Ownership per broker: your share in %. The rest belongs to your partner. For a joint account or "
                   "assets in the marital community use 50%.")
        cols = st.columns(max(1, len(brokers)))
        own = {}
        for i, b in enumerate(brokers):
            cur = hh.share(b).get("you", 1.0) if not hh.single else 1.0
            own[b] = cols[i].number_input(b, min_value=0, max_value=100, value=int(round(cur * 100)), step=5, key=f"own-{b}")
        pos_rows = [dict(position=f"{h.broker}|{h.asset}", name=h.name,
                         your_share=(hh.positions.get(f"{h.broker}|{h.asset}", {}).get("you", None)))
                    for h in pf.holdings()]
        pos_df = pd.DataFrame(pos_rows, columns=["position", "name", "your_share"])
        pos_df["your_share"] = [None if v is None else round(float(v) * 100) for v in pos_df["your_share"]]
        with st.expander("Exceptions per position"):
            pos_ed = st.data_editor(pos_df, hide_index=True, width="stretch", key="own-pos",
                                    disabled=["position", "name"], column_config={
                                        "position": None, "name": "Position",
                                        "your_share": st.column_config.NumberColumn("Your share (%)", min_value=0, max_value=100,
                                                                                    help="Empty: the broker's split")})

    st.subheader("Exemption per year")
    st.dataframe(rep.exemption_table(max(rules.year, date.today().year) + 1), hide_index=True, width="stretch", column_config={
        "person": "Person", "year": st.column_config.NumberColumn("Year", format="%d"),
        "base": st.column_config.NumberColumn("Own exemption", format=EUR),
        "carried_in": st.column_config.NumberColumn("Carried in", format=EUR),
        "available": st.column_config.NumberColumn("Available", format=EUR),
        "net_gain": st.column_config.NumberColumn("Net taxable gain", format=EUR),
        "used": st.column_config.NumberColumn("Used", format=EUR),
        "carry_out": st.column_config.NumberColumn("Carried forward", format=EUR),
        "expired": st.column_config.NumberColumn("Expired", format=EUR), "carried_from": "Carried in from"})
    st.caption("Unused exemption adds at most €1,000 a year to the following 5 years (so up to €15,000 in total); "
               "carried amounts are used first. A future year shows what is available if nothing else is sold.")
    carry_rule = st.radio("Carry-forward rule", ["partial", "none_used"], horizontal=True,
                          index=0 if profile.carry_rule == "partial" else 1,
                          format_func=lambda r: {"partial": "Unused part of the year's exemption, max €1,000 (default)",
                                                 "none_used": "€1,000 only in a year without a net gain"}[r],
                          help="Sources differ on this detail; see docs/domain/meerwaardebelasting.md.")

    st.subheader("Per year: amounts that aren't in your exports")
    yrs = list(range(2026, max(rules.year, date.today().year) + 1))
    ydf = pd.DataFrame([dict(year=y, exemption_base=profile.get(y).exemption_base,
                             dividend_exemption=profile.get(y).dividend_exemption) for y in yrs])
    yed = st.data_editor(ydf, hide_index=True, width="stretch", key="years-ed", disabled=["year"], column_config={
        "year": st.column_config.NumberColumn("Year", format="%d"),
        "exemption_base": st.column_config.NumberColumn("Own exemption meerwaardebelasting (€)", help="Empty: €10,000 or the latest amount entered"),
        "dividend_exemption": st.column_config.NumberColumn("Dividend exemption (€)", help="Empty: €833")})
    people_rows = []
    for y in yrs:
        yi = profile.get(y)
        for pid in hh.ids:
            inc = yi.income_for(pid)
            people_rows.append(dict(year=y, person=pid, name=hh.name(pid), other_gains=sum(yi.gains_for(pid).values()) or None,
                                    declared_net=yi.declared_for(pid), **{k: inc[k] or None for k in INCOME_FIELDS}))
    ped = st.data_editor(pd.DataFrame(people_rows), hide_index=True, width="stretch", key="people-ed",
                         disabled=["year", "name"], column_config={
                             "year": st.column_config.NumberColumn("Year", format="%d"), "person": None, "name": "Person",
                             "other_gains": st.column_config.NumberColumn("Other sales: taxable gain (€)", help="Sales at other banks or brokers; negative for a loss"),
                             "declared_net": st.column_config.NumberColumn("Declared net gain (€)", help="Once filed: the figure in your tax return; replaces the calculated one for the carry-forward"),
                             "dividends_shares": st.column_config.NumberColumn("Other dividends from shares (€)"),
                             "dividends_funds": st.column_config.NumberColumn("Other fund/ETF distributions (€)"),
                             "interest": st.column_config.NumberColumn("Other interest (€)"),
                             "foreign_tax": st.column_config.NumberColumn("Foreign tax on those (€)")})
    if st.button("Save", type="primary"):
        profile.set_partner(partner_name.strip() or "Partner" if has_partner else None)
        profile.household.persons[0].name = you_name.strip() or "You"
        if has_partner:
            profile.household.brokers = {b: {"you": v / 100, "partner": 1 - v / 100} for b, v in own.items()}
            profile.household.positions = {r.position: {"you": r.your_share / 100, "partner": 1 - r.your_share / 100}
                                           for r in pos_ed.itertuples() if pd.notna(r.your_share)}
        profile.carry_rule = carry_rule
        for r in yed.itertuples():
            yi = profile.year(int(r.year))
            yi.exemption_base = None if pd.isna(r.exemption_base) else float(r.exemption_base)
            yi.dividend_exemption = None if pd.isna(r.dividend_exemption) else float(r.dividend_exemption)
        for r in ped.itertuples():
            if r.person not in profile.household.ids:
                continue
            yi = profile.year(int(r.year))
            yi.other_gains[r.person] = {"fin": float(r.other_gains)} if pd.notna(r.other_gains) and r.other_gains else {}
            yi.declared_net[r.person] = None if pd.isna(r.declared_net) else float(r.declared_net)
            yi.other_income[r.person] = {k: float(getattr(r, k)) for k in INCOME_FIELDS if pd.notna(getattr(r, k)) and getattr(r, k)}
        profile.save(PROFILE)
        st.toast("Saved")
        st.rerun()
    st.caption(f"Saved in {PROFILE}. Other sales are counted as financial instruments.")

# ---------------------------------------------------------------- prices
with tabs[8]:
    st.subheader("Prices")
    st.caption("Fill in your own price where one is missing or wrong. Your prices are saved and win over every other source.")
    rows = []
    for h in pf.holdings():
        now, ref = pb.now(h), pb.reference(h) if any(l.date.date() <= rules.reference_date for l in h.lots) else None
        rows.append(dict(asset=h.asset, name=h.name, broker=h.broker, asset_class=h.asset_class,
                         price_now=now.value if now else None, now_date=now.on if now else None, now_source=now.source if now else "missing",
                         price_31dec=ref.value if ref else None, ref_source=ref.source if ref else ("not needed" if not any(l.date.date() <= rules.reference_date for l in h.lots) else "missing"),
                         your_now=pb.overrides_now.get(h.asset), your_31dec=pb.overrides_ref.get(h.asset)))
    pdf = pd.DataFrame(rows).drop_duplicates("asset")
    ed = st.data_editor(pdf, hide_index=True, width="stretch", key="prices-editor",
                        disabled=[c for c in pdf.columns if c not in ("your_now", "your_31dec")], column_config={
                            "price_now": st.column_config.NumberColumn("Price now (€)", format="%.6g"),
                            "now_date": st.column_config.DateColumn("Date"), "now_source": "Source",
                            "price_31dec": st.column_config.NumberColumn("31-12-2025 (€)", format="%.6g"), "ref_source": "Source",
                            "your_now": st.column_config.NumberColumn("Your price now (€)", format="%.6g"),
                            "your_31dec": st.column_config.NumberColumn("Your 31-12-2025 price (€)", format="%.6g")})
    if st.button("Save my prices"):
        lines = ["asset,price_now,price_31dec"]
        for r in ed.itertuples():
            if pd.notna(r.your_now) or pd.notna(r.your_31dec):
                lines.append(f"{r.asset},{'' if pd.isna(r.your_now) else r.your_now},{'' if pd.isna(r.your_31dec) else r.your_31dec}")
        PRICES_CSV.write_text("\n".join(lines) + "\n")
        st.toast("Prices saved")
        st.rerun()
    if pb.errors:
        with st.expander(f"Price lookups that failed ({len(pb.errors)})"):
            for e in pb.errors:
                st.markdown(f"- {e}")

# ---------------------------------------------------------------- data
with tabs[9]:
    st.subheader("Rows read from your files")
    tdf = pd.DataFrame([t.as_dict() for t in pf.transactions])
    kinds = sorted(tdf.kind.unique()) if not tdf.empty else []
    pick_k = st.multiselect("Kinds", kinds, default=[k for k in kinds if k != "reward"])
    st.dataframe(tdf[tdf.kind.isin(pick_k)] if pick_k else tdf, hide_index=True, width="stretch")
    if statements:
        st.subheader("LYNX statements")
        st.dataframe(pd.DataFrame([dict(file=s.source, start=s.period_start, end=s.period_end, positions=len(s.positions),
                                        dividends=s.nav.get("Dividends"), ending_value=s.nav.get("Ending Value")) for s in statements]),
                     hide_index=True, width="stretch")

# ---------------------------------------------------------------- CSV formats (Data tab)
with tabs[9]:
    st.subheader("CSV formats of other brokers")
    st.caption("DEGIRO and Bitvavo CSV exports are recognised by themselves. For another broker (Bolero, Saxo, Keytrade, "
               "…), upload one of its CSV exports here and say once which column holds what; files with the same header "
               "are then read automatically.")
    for f in list(mapped.FORMATS):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{f.name}** · broker *{f.broker}* · {len(f.header)} columns · types: "
                    + (", ".join(f"{k} → {v}" for k, v in f.types.items()) or "from the sign of the quantity"))
        if c2.button("Remove", key=f"fmt-rm-{f.name}"):
            mapped.save(CSV_FORMATS, [x for x in mapped.FORMATS if x.name != f.name])
            st.cache_data.clear()
            st.rerun()
    sample = st.file_uploader("CSV export to map", type=["csv"], key="fmt-sample")
    if sample is not None:
        from meerwaarde.parsers import decode
        text = decode(sample.getvalue())
        header, delim, preview = mapped.sniff(text)
        st.dataframe(pd.DataFrame(preview, columns=header[:max(len(r) for r in preview)] if preview else header),
                     hide_index=True, width="stretch")
        with st.form("fmt-form"):
            f1, f2, f3, f4 = st.columns(4)
            fname = f1.text_input("Name of this format", value=sample.name.rsplit(".", 1)[0])
            fbroker = f2.text_input("Broker", placeholder="Bolero")
            decimal = f3.selectbox("Decimal sign", [",", "."])
            datefmt = f4.selectbox("Date format", ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y"],
                                   format_func=lambda x: x.replace("%d", "dd").replace("%m", "mm").replace("%Y", "yyyy"))
            opts = [""] + header
            labels = dict(date="Date *", time="Time", type="Type of transaction", isin="ISIN *", name="Product name",
                          qty="Quantity", price="Price per unit", currency="Currency", fx="Exchange rate",
                          costs="Costs / commission", amount="Amount (dividends, fees, cash)", amount_eur="Amount in EUR")
            cols, grid = {}, st.columns(4)
            for i, (k, lab) in enumerate(labels.items()):
                cols[k] = grid[i % 4].selectbox(lab, opts, key=f"fmt-col-{k}")
            inverted = st.checkbox("The exchange rate is units per EUR (like EUR/USD 1.08), not EUR per unit")
            types_txt = st.text_area("Values in the type column, one per line as value = buy / sell / dividend / "
                                     "withholding / interest / fee / deposit / withdrawal",
                                     placeholder="Aankoop = buy\nVerkoop = sell\nDividend = dividend")
            if st.form_submit_button("Save this format"):
                types = {}
                for line in types_txt.splitlines():
                    if "=" in line:
                        a, b = (x.strip() for x in line.split("=", 1))
                        if b.lower() in mapped.KINDS:
                            types[a] = b.lower()
                if not (fname and fbroker and cols["date"] and (cols["isin"] or cols["amount"])):
                    st.error("A name, a broker, the date column and an ISIN or amount column are needed.")
                else:
                    fmt = mapped.CsvFormat(fname, fbroker.strip(), header, {k: v for k, v in cols.items() if v}, delim,
                                           decimal, datefmt, types, inverted)
                    try:
                        rows, warns = mapped.parse_text(text, fmt, sample.name)
                    except Exception as e:  # show the problem instead of saving a broken format
                        st.error(f"This mapping doesn't read the file: {e}")
                    else:
                        mapped.save(CSV_FORMATS, [x for x in mapped.FORMATS if x.name != fname] + [fmt])
                        st.cache_data.clear()
                        st.success(f"Saved: {len(rows)} rows read from the sample" + (f", {len(warns)} notes" if warns else "")
                                   + ". Upload the file in the sidebar (or put it in the inbox) to use it.")

# ---------------------------------------------------------------- manual positions
with tabs[10]:
    st.subheader("Manual positions")
    manual_editor(manual, "manual-editor")
    if manual.positions:
        st.dataframe(rep.manual_table(), hide_index=True, width="stretch")

# ---------------------------------------------------------------- alerts
with tabs[11]:
    st.subheader("Scheduled refresh and alerts")
    st.caption("`meerwaarde refresh` fetches fresh prices for this data folder and sends a message when the tax-free room "
               "drops below your threshold, a position moves more than your percentage since its last alert, or a price "
               "can't be found. Run it on a schedule (see below); this page only holds the settings.")
    cfg = alerting.AlertSettings.load(DATA / "alerts.json")
    with st.form("alerts-form"):
        a1, a2, a3 = st.columns(3)
        room_on = a1.checkbox("Tax-free room below", value=cfg.room_below is not None)
        room_v = a1.number_input("Threshold (€)", min_value=0.0, step=100.0, value=float(cfg.room_below or 1000.0))
        move_on = a2.checkbox("Position moves more than", value=cfg.move_pct is not None)
        move_v = a2.number_input("Percentage", min_value=0.5, step=0.5, value=float(cfg.move_pct or 10.0))
        missing_on = a3.checkbox("A price can't be found", value=cfg.missing_prices)
        stale_on = a1.checkbox("Remind me to export when data is older than", value=cfg.stale_days is not None)
        stale_v = a2.number_input("Days", min_value=7, max_value=365, step=1, value=int(cfg.stale_days or 35))
        amounts = a3.checkbox("Include amounts in messages", value=cfg.include_amounts,
                              help="Off by default: an ntfy.sh topic can be read by anyone who knows its name.")
        st.markdown("**ntfy** (push to your phone)")
        n1, n2 = st.columns([3, 2])
        ntfy_url = n1.text_input("Topic URL", value=cfg.ntfy.url, placeholder="https://ntfy.sh/a-long-random-topic-name",
                                 help="Use a long random topic on ntfy.sh, or your own ntfy server with an access token.")
        ntfy_env = n2.text_input("Token environment variable", value=cfg.ntfy.token_env,
                                 help="The token itself is never stored here; set this variable where refresh runs.")
        st.markdown("**E-mail** (SMTP)")
        e1, e2, e3, e4 = st.columns([3, 1, 2, 2])
        host = e1.text_input("Server", value=cfg.email.host, placeholder="smtp.example.org")
        port = e2.number_input("Port", min_value=1, max_value=65535, value=int(cfg.email.port))
        user = e3.text_input("User", value=cfg.email.user)
        pw_env = e4.text_input("Password environment variable", value=cfg.email.password_env)
        e5, e6, e7 = st.columns([3, 3, 1])
        sender = e5.text_input("From", value=cfg.email.sender)
        to = e6.text_input("To (comma-separated)", value=cfg.email.to)
        tls = e7.checkbox("STARTTLS", value=cfg.email.starttls)
        if st.form_submit_button("Save alert settings"):
            alerting.AlertSettings(room_v if room_on else None, move_v if move_on else None, missing_on,
                                   int(stale_v) if stale_on else None, amounts,
                                   alerting.NtfySettings(ntfy_url.strip(), ntfy_env.strip()),
                                   alerting.EmailSettings(host.strip(), int(port), tls, user.strip(), sender.strip(),
                                                          to.strip(), pw_env.strip())).save(DATA / "alerts.json")
            st.toast("Saved")
            st.rerun()
    b1, b2 = st.columns(2)
    if b1.button("Send a test message", disabled=not cfg.channels, width="stretch",
                 help="Uses the saved settings and the environment variables of this app's process."):
        errs = alerting.send_test(DATA)
        (st.error("; ".join(errs)) if errs else st.success("Test message sent."))
    if b2.button("Preview: what would be sent now", width="stretch",
                 help="Uses the prices already loaded; sends nothing and leaves the alert state unchanged."):
        prev = alerting.Snapshot.load(DATA / "alerts" / "state.json")
        found, _ = alerting.evaluate(prev, alerting.snapshot(rep, exports=alerting.freshness(rep, DATA)), cfg)
        if found:
            for al in found:
                st.markdown(f"- **{al.title}**: {al.message(True)}")
        else:
            st.info("Nothing to report compared with the last refresh.")
    log = alerting.read_log(DATA)
    if log:
        st.markdown("**Last runs**")
        st.dataframe(pd.DataFrame([dict(at=r["at"], positions=r["positions"], price_errors=r["price_errors"],
                                        alerts="; ".join(a["title"] for a in r["alerts"]) or "none", sent=r["sent"],
                                        errors="; ".join(r["errors"])) for r in log]), hide_index=True, width="stretch")
    else:
        st.caption("No scheduled refresh has run yet.")
    with st.expander("How to schedule it"):
        st.markdown(f"""
- **Synology NAS (Docker)**: Control Panel → Task Scheduler → Create → Scheduled task → User-defined script, daily:
  `docker exec meerwaarde meerwaarde refresh --data-dir /data`. Or run a second container with
  `meerwaarde refresh --data-dir /data --every 24` (see the README).
- **Linux cron**: `0 7 * * * meerwaarde refresh --data-dir {DATA}`
- **Windows Task Scheduler**: `schtasks /Create /SC DAILY /ST 07:00 /TN meerwaarde-refresh /TR "meerwaarde refresh --data-dir {DATA}"`
- Secrets: set `{cfg.ntfy.token_env}` and `{cfg.email.password_env}` in the environment of the scheduled task or container.
- Try it first with `meerwaarde refresh --dry-run`, then `meerwaarde refresh --test`.
""")

# ---------------------------------------------------------------- performance
perf = None
with tabs[12]:
    st.subheader("Performance against a benchmark")
    st.caption("Measured on the money you put into positions (purchases in, sales and income out), per broker and in total. "
               "Money-weighted (XIRR) is your own return including the timing of your purchases; time-weighted shows how "
               "the investments did regardless of when you added money. The benchmark gets exactly the same money on the "
               "same days.")
    options = [lookup(i) for i in WORLD_TARGETS if lookup(i)]
    labels = {f.isin: f"{f.ticker or f.isin} · {f.name}" for f in options}
    p1, p2, p3 = st.columns([3, 2, 2])
    default = "IE00B6R52259" if "IE00B6R52259" in labels else next(iter(labels), None)
    pick = p1.selectbox("Benchmark", list(labels) + ["other"], index=(list(labels).index(default) if default else 0),
                        format_func=lambda k: labels.get(k, "Other ISIN…"), key="perf-bench")
    other = p2.text_input("Other ISIN", key="perf-isin", disabled=pick != "other", placeholder="IE00BK5BQT80")
    bench_isin = other.strip().upper() if pick == "other" else pick
    who = p3.selectbox("Show", ["All"] + brokers, key="perf-broker")
    if pick == "other" and not bench_isin:
        st.info("Enter the ISIN of the fund to compare with.")
    else:
        bench = (bench_isin, labels.get(bench_isin, bench_isin))
        with st.spinner("Looking up price history (the first time can take a minute)…"):
            perf = rep.performance(bench)
        line = perf.line(who)
        if line is None:
            st.info("No trades to measure.")
        else:
            pct = lambda v: "—" if v is None else f"{v:+.1%}"  # noqa: E731
            c = st.columns(5)
            c[0].metric("Gain", money(line.gain), help="Value today + money out − money in")
            c[1].metric("Money-weighted (XIRR)", pct(line.xirr) + " / yr")
            c[2].metric("Time-weighted", pct(line.twr_annual) + " / yr" if line.twr_annual is not None else pct(line.twr),
                        help=f"Total {pct(line.twr)} since {perf.start:%d-%m-%Y}")
            c[3].metric("Benchmark XIRR", pct(line.benchmark_xirr) + " / yr" if line.benchmark_xirr is not None else "—",
                        delta=None if line.benchmark_xirr is None or line.xirr is None
                        else f"{(line.xirr - line.benchmark_xirr) * 100:+.1f} pts you vs benchmark")
            c[4].metric("Benchmark value today", money(line.benchmark_value))
            series = rep.performance_series(perf, who)
            long = series.melt("date", ["value", "invested", "benchmark"], var_name="series", value_name="eur").dropna()
            long["series"] = long["series"].map({"value": "Your value", "invested": "Net money in",
                                                 "benchmark": f"Same money in {bench[0]}"})
            st.altair_chart(alt.Chart(long).mark_line(interpolate="monotone").encode(
                x=alt.X("date:T", title=None), y=alt.Y("eur:Q", title="€", axis=alt.Axis(format=",.0f")),
                color=alt.Color("series:N", title=None, legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("date:T"), "series:N", alt.Tooltip("eur:Q", format=",.0f")]), width="stretch")
            st.dataframe(rep.performance_table(perf), hide_index=True, width="stretch", column_config={
                "broker": "Broker", "invested": st.column_config.NumberColumn("Money in", format=EUR),
                "returned": st.column_config.NumberColumn("Money out", format=EUR),
                "value": st.column_config.NumberColumn("Value today", format=EUR),
                "gain": st.column_config.NumberColumn("Gain", format=EUR),
                "xirr": st.column_config.NumberColumn("XIRR / yr", format="percent"),
                "twr": st.column_config.NumberColumn("Time-weighted total", format="percent"),
                "twr_annual": st.column_config.NumberColumn("Time-weighted / yr", format="percent"),
                "benchmark_value": st.column_config.NumberColumn("Benchmark value", format=EUR),
                "benchmark_xirr": st.column_config.NumberColumn("Benchmark XIRR / yr", format="percent")})
            for n in perf.notes:
                st.caption(n)
            with st.expander("Price history used per position"):
                st.dataframe(pd.DataFrame([dict(asset=a, source=v) for a, v in sorted(perf.sources.items())]),
                             hide_index=True, width="stretch")

# ---------------------------------------------------------------- allocation
alloc = None
EXPOSURE_JSON = DATA / "exposure.json"
with tabs[13]:
    from meerwaarde.engine.allocation import load_overrides
    st.subheader("Allocation, looking through your funds")
    st.caption("What you own underneath: funds are split by the countries and sectors they hold (justETF: the 4 "
               "largest, the rest in 'Other'), shares count for their own country. Overlap and the look-through holdings "
               "use each fund's top 10, so they are lower bounds.")
    try:
        overrides = load_overrides(EXPOSURE_JSON)
    except ValueError as e:
        overrides = {}
        st.error(f"exposure.json can't be read: {e}")
    with st.spinner("Looking up fund compositions…"):
        alloc = rep.allocation(overrides)
    if not alloc.positions:
        st.info("No positions with a price.")
    else:
        dims = {"asset_class": "Asset class", "region": "Region", "currency": "Currency (approximate)", "sector": "Sector"}
        cols = st.columns(2)
        for i, (dim, title) in enumerate(dims.items()):
            t = rep.allocation_table(alloc, dim)
            with cols[i % 2]:
                st.markdown(f"**{title}**")
                st.altair_chart(alt.Chart(t.head(10)).mark_bar(cornerRadius=3).encode(
                    x=alt.X("value_eur:Q", title="€", axis=alt.Axis(format=",.0f")),
                    y=alt.Y("name:N", sort="-x", title=None),
                    tooltip=["name:N", alt.Tooltip("value_eur:Q", format=",.0f", title="€"),
                             alt.Tooltip("share:Q", format=".1%")]).properties(height=34 * min(len(t), 10) + 20),
                    width="stretch")
        st.markdown("**Largest holdings through everything you own**")
        st.dataframe(rep.look_through_table(alloc), hide_index=True, width="stretch", column_config={
            "value_eur": st.column_config.NumberColumn("Value", format=EUR),
            "share": st.column_config.NumberColumn("Share", format="percent"), "via": "Through"})
        ov = rep.overlap_table(alloc)
        if not ov.empty:
            st.markdown("**Overlap between your funds** (shared top-10 holdings)")
            st.dataframe(ov, hide_index=True, width="stretch", column_config={
                "fund_a": "Fund", "fund_b": "Fund", "overlap": st.column_config.NumberColumn("Overlap", format="percent"),
                "shared": "Shared holdings"})
        with st.expander("Per position: where the breakdown comes from"):
            st.dataframe(pd.DataFrame(alloc.positions), hide_index=True, width="stretch",
                         column_config={"value": st.column_config.NumberColumn("Value", format=EUR)})
        for n in alloc.notes:
            st.caption(n)
        with st.expander("Your own breakdown for a fund (exposure.json)"):
            st.caption('For a fund justETF does not cover, or to correct it. Per ISIN, for example: {"XX0000000000": '
                       '{"asset_class": {"Equity": 60, "Bonds": 40}, "countries": {"United States": 55, "Germany": 45}, '
                       '"sectors": {"Technology": 30, "Other": 70}}}. Weights are scaled to 100%.')
            txt = st.text_area("exposure.json", value=EXPOSURE_JSON.read_text(encoding="utf-8") if EXPOSURE_JSON.exists() else "{}",
                               height=160, key="exposure-json")
            if st.button("Save breakdown"):
                try:
                    json.loads(txt or "{}")
                except ValueError as e:
                    st.error(f"Not valid JSON: {e}")
                else:
                    EXPOSURE_JSON.write_text(txt or "{}", encoding="utf-8")
                    st.toast("Saved")
                    st.rerun()

# ---------------------------------------------------------------- export (sidebar, after everything is computed)
with st.sidebar:
    st.subheader("Export")
    sw_result = (sw_srcs, sw_cases, sw_plans, sw_horizon) if sw_srcs and sw_targets else None
    st.download_button("Excel workbook", data=lambda: to_xlsx(rep, simulation=sim, harvest=plan, switch=sw_result, perf=perf, alloc=alloc),
                       file_name=default_name(rep),
                       mime=XLSX_MIME, icon=":material/download:", on_click="ignore", width="stretch",
                       help="Summary, positions, sales, income, meerwaardebelasting, the current simulation, prices "
                            "and how everything is calculated.")
