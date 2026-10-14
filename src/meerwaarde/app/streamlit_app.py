"""Meerwaarde web app. Start it with `meerwaarde app`."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from meerwaarde.engine.portfolio import Portfolio, PortfolioOptions
from meerwaarde.engine.tax import TaxCalculator, TaxRules, gain_for
from meerwaarde.models import LAW_TYPES, law_type
from meerwaarde.parsers import combine, parse_file
from meerwaarde.prices import PriceBook
from meerwaarde.prices.fetch import Fetcher
from meerwaarde.report import Report

st.set_page_config(page_title="Meerwaarde", page_icon="📈", layout="wide")

DATA = Path(os.environ.get("MEERWAARDE_DATA") or Path.home() / ".local" / "share" / "meerwaarde")
UPLOADS = DATA / "uploads"
PRICES_CSV = DATA / "prices.csv"
CACHE = DATA / "cache"
UPLOADS.mkdir(parents=True, exist_ok=True)

EUR = "euro"
LAW_NAMES = {k: v[0] for k, v in LAW_TYPES.items()}


# ---------------------------------------------------------------- data loading
@st.cache_data(show_spinner=False)
def parse_cached(digest: str, data: bytes, name: str):
    return parse_file(data, filename=name)


def stored_files():
    return sorted(UPLOADS.glob("*.pdf"), key=lambda p: p.name.lower())


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
    up = st.file_uploader("Add exports (PDF)", type=["pdf"], accept_multiple_files=True, key=f"up{key}",
                          help="Bitvavo transaction history, DEGIRO Transacties, LYNX activity statements (one per year).")
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
                parsed.append((p, parse_cached(hashlib.sha1(data).hexdigest(), data, display_name(p))))
        except Exception as e:  # keep the app usable when one file can't be read
            failed.append((p, str(e)))

    if parsed or failed:
        st.subheader("Files")
        for p, r in parsed:
            c1, c2 = st.columns([5, 1])
            period = ""
            if r.statement is not None and r.statement.period_end:
                period = f" · {r.statement.period_start:%d-%m-%Y} – {r.statement.period_end:%d-%m-%Y}"
            c1.markdown(f"**{r.broker}** · {display_name(p)}  \n<small>{len(r.transactions)} rows{period}</small>", unsafe_allow_html=True)
            if c2.button("✕", key=f"rm-{p.name}", help="Remove this file"):
                p.unlink()
                st.rerun()
        for p, err in failed:
            c1, c2 = st.columns([5, 1])
            c1.error(f"{display_name(p)}: {err}")
            if c2.button("✕", key=f"rm-{p.name}", help="Remove this file"):
                p.unlink()
                st.rerun()

    st.subheader("Settings")
    fetch = st.toggle("Look up prices online", value=os.environ.get("MEERWAARDE_OFFLINE") != "1", help="Bitvavo for crypto, justETF for ETFs, Yahoo Finance for shares, ECB for exchange rates.")
    own_wallet = st.toggle("Crypto withdrawals go to my own wallet", value=True, help="Transfers to a hardware wallet such as a Ledger are not sales; the coins keep their purchase price.")
    conversions = st.toggle("Treat same-day ISIN swaps as conversions", value=True, help="For example Royal Dutch Shell A into Shell plc: not a sale.")
    year = st.number_input("Tax year", min_value=2026, max_value=2035, value=2026, step=1)
    exemption = st.number_input("Exemption (€)", min_value=0.0, max_value=50000.0, value=10000.0, step=500.0,
                                help="€10,000 per person for 2026. From 2027 unused exemption can add up to €1,000 a year, to at most €15,000.")
    original = st.toggle("Use the original price when it's higher than 31-12-2025", value=True,
                         help="Allowed for sales until the end of 2030, and only to reduce a gain.")
    if st.button("Refresh prices", width="stretch"):
        Fetcher(cache_dir=CACHE).clear_current()
        st.toast("Current prices will be fetched again")


# ---------------------------------------------------------------- empty state
if not parsed:
    st.header("Add your exports to start")
    st.markdown(
        """
Upload the PDF exports in the sidebar. They are kept on this computer, in `"""
        + str(UPLOADS) + """`.

| Broker | What to export |
|---|---|
| **Bitvavo** | The full transaction overview (*Transactieoverzicht*, all dates), as PDF. |
| **DEGIRO** | *Transacties* for the full period since your first trade, exported as PDF. |
| **LYNX** | The *Activity Statement* PDF for each year you had the account, plus the current year to date. |

After that, the app shows your profit per broker, the meerwaardebelasting if you sold everything today,
and a simulator for how much you can sell before going over the yearly exemption.
"""
    )
    st.stop()

# ---------------------------------------------------------------- build
results = [r for _, r in parsed]
txns, statements, parse_warnings = combine(results)
opts = PortfolioOptions(own_wallet_withdrawals=own_wallet, detect_conversions=conversions)
pf = Portfolio(txns, statements, opts)
rules = TaxRules(year=int(year), exemption=float(exemption), use_original_if_higher=original)
pb = PriceBook(statements, fetch=fetch, cache_dir=CACHE, reference_date=rules.reference_date)
if PRICES_CSV.exists():
    pb.load_overrides_csv(PRICES_CSV.read_text())
calc = TaxCalculator(pf, pb, rules)
rep = Report(pf, pb, calc, parse_warnings + pf.warnings)

with st.spinner("Looking up prices…"):
    holdings_df = rep.holdings()
realized_df, income_df, summary_df = rep.realized(), rep.income(), rep.summary()
brokers = pf.brokers()


def money(v):
    return "—" if v is None or pd.isna(v) else f"€{v:,.0f}"


tabs = st.tabs(["Overview", "Positions", "Meerwaardebelasting", "Simulator", "Prices", "Data"])

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
    summ = TaxCalculator.summarize(sold_lines + open_lines, rules)
    sold_total = sum(l.taxable_gain for l in sold_lines)
    c = st.columns(4)
    c[0].metric("Already sold this year", money(sold_total), help="Taxable gain of this year's sales")
    c[1].metric("Taxable gain if you sold the rest", money(summ["net"] - sold_total))
    c[2].metric("Exemption", money(rules.exemption))
    c[3].metric("Meerwaardebelasting", money(summ["tax"]), help="10% of the net gain above the exemption")
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
    with st.expander("How it's calculated"):
        st.markdown("""
- 10% on the net gain realized in the tax year above the exemption, per person. Partners each have their own exemption for assets they own.
- Units held before 2026 use the closing price of 31-12-2025 as purchase value. If you originally paid more, that price applies instead for sales until the end of 2030, but only to reduce a gain: a loss is always measured from the 31-12-2025 price.
- Units bought from 2026 use their purchase price. First in, first out per broker. Commissions and stock exchange tax are left out.
- Financial instruments, insurance contracts, crypto-assets, currency and investment gold form one regime. Losses offset gains within the year and don't carry over.
- Not included: the 33% regime for internal capital gains, holdings of 20% or more in a company, Reynders tax on bond funds, and income tax on staking rewards. Units received in the tax year without a purchase price count at zero cost.
- This is a calculation from your exports, not tax advice.
""")

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

    with st.expander("Other sales this year that aren't in your exports"):
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
    sim_lines = [l for l in calc.open_lines(sell=sell, price_override=price_over) if l.qty > 0]
    summ = TaxCalculator.summarize(sold_lines + sim_lines, rules, extra)
    room = rules.exemption - summ["net"]
    c = st.columns(4)
    c[0].metric("Tax-free room left", money(max(0.0, room)) if room >= 0 else "€0",
                delta=None if room >= 0 else f"over by {money(-room)}", delta_color="inverse")
    c[1].metric("Net taxable gain", money(summ["net"]))
    c[2].metric("Cash from these sales", money(sum(l.proceeds for l in sim_lines)))
    c[3].metric("Meerwaardebelasting", money(summ["tax"]))
    st.progress(min(1.0, max(0.0, summ["net"] / rules.exemption)) if rules.exemption else 1.0,
                text=f"{money(max(0.0, summ['net']))} of the {money(rules.exemption)} exemption used")

    sold_net = sum(l.taxable_gain for l in sold_lines) + sum(extra.values())
    out = []
    for h in hold.values():
        k = (h.broker, h.asset)
        line = next((l for l in sim_lines if l.key == k), None)
        others = sum(l.taxable_gain for l in sim_lines if l.key != k)
        r = max(0.0, rules.exemption - sold_net - others)
        pr = price_over.get(k) or (pb.now(h).value if pb.now(h) else None)
        m = calc.max_without_tax(h, r, pr) if pr else 0.0
        out.append(dict(key=f"{h.broker}|{h.asset}", broker=h.broker, name=h.name, sell=sell.get(k, 0.0),
                        gain=line.taxable_gain if line else 0.0, proceeds=line.proceeds if line else 0.0,
                        max_qty=m, max_value=m * pr if pr else None))
    out_df = pd.DataFrame(out).sort_values("max_value", ascending=False, na_position="last")
    st.dataframe(out_df, hide_index=True, width="stretch", column_config={
        "key": None, "name": "Position", "sell": st.column_config.NumberColumn("Selling", format="%.8g"),
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

# ---------------------------------------------------------------- prices
with tabs[4]:
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
with tabs[5]:
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
