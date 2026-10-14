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
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.engine.income_tax import CATEGORIES
from meerwaarde.export import XLSX_MIME, default_name, tax_report_xlsx, to_xlsx
from meerwaarde.models import LAW_TYPES, law_type
from meerwaarde.parsers import combine, parse_file
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
                          help="Bitvavo transaction history, DEGIRO Transacties and account statement (Rekeningoverzicht), LYNX activity statements (one per year).")
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
    profile = TaxProfile.load(PROFILE)
    year = int(st.number_input("Tax year", min_value=2026, max_value=2035, value=2026, step=1))
    exemption = st.number_input(f"Own exemption {year} (€)", min_value=0.0, max_value=50000.0,
                                value=float(profile.exemption_rules().base_for(year)), step=50.0, key=f"exemption-{year}",
                                help="€10,000 per person for 2026, indexed later (enter the published amount). Exemption "
                                     "carried forward from earlier years is added automatically (Tax years tab).")
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
| **DEGIRO** | *Transacties* and the *Rekeningoverzicht* (account statement) for the full period since your first trade, as PDF. |
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
rep = Report(pf, pb, calc, parse_warnings + pf.warnings, profile)
hh = profile.household

with st.spinner("Looking up prices…"):
    holdings_df = rep.holdings()
realized_df, income_df, summary_df = rep.realized(), rep.income(), rep.summary()
brokers = pf.brokers()


def money(v):
    return "—" if v is None or pd.isna(v) else f"€{v:,.0f}"


tabs = st.tabs(["Overview", "Positions", "Meerwaardebelasting", "Simulator", "Tax report", "Tax years", "Prices", "Data"])

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

# ---------------------------------------------------------------- tax report
with tabs[4]:
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
with tabs[5]:
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
with tabs[6]:
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
with tabs[7]:
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

# ---------------------------------------------------------------- export (sidebar, after everything is computed)
with st.sidebar:
    st.subheader("Export")
    st.download_button("Excel workbook", data=lambda: to_xlsx(rep, simulation=sim), file_name=default_name(rep),
                       mime=XLSX_MIME, icon=":material/download:", on_click="ignore", width="stretch",
                       help="Summary, positions, sales, income, meerwaardebelasting, the current simulation, prices "
                            "and how everything is calculated.")
