"""The yearly tax report (backlog item 5): everything for one income year, for the tax return or an accountant.

`tax_year_report(rep, year)` gathers it; `export.tax_report_xlsx` and `pdf_report.tax_report_pdf` render it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .engine.household import ExemptionYear
from .engine.income_tax import IncomeTax
from .models import LAW_TYPES
from .report import INCOME_RULES_TEXT, RULES_TEXT, Report

BE_RATE = 0.30


def return_lines(rep: Report, year: int, persons: list, income: dict, cgt: dict, reynders: dict, accounts: list) -> pd.DataFrame:
    """Which amount goes in which code of the tax return (D38). First person: 1xxx codes, partner: 2xxx.

    1444/2444 (vak VII): income without Belgian withholding = share dividends above the exemption + fund distributions
    + interest (after foreign tax) + the Reynders part of bond funds sold. 1437/2437: the Belgian withholding on share
    dividends covered by the exemption (refund), when the exemption isn't used up on dividends without withholding.
    1075 (vak XIII A): foreign accounts and their countries. The meerwaardebelasting has no code yet (2027 return)."""
    rows = []
    for n, (pid, name) in enumerate(persons):
        pre = "1" if n == 0 else "2"
        it = income.get(pid)
        if it is not None:
            be_gross = -sum(l.be_withheld for l in it.lines if l.category == "shares") / BE_RATE
            shares_no_be = max(0.0, it.total("shares") - be_gross)
            ex = it.exemption_available
            ex_foreign = min(ex, shares_no_be)
            ex_be = min(ex - ex_foreign, max(0.0, be_gross))
            amount = (shares_no_be - ex_foreign + max(0.0, it.total("funds")) + max(0.0, it.total("interest"))
                      + max(0.0, it.total("other")) + reynders.get("people", {}).get(pid, {}).get("base", 0.0))
            rows.append(dict(person=name, vak="VII A", code=f"{pre}444", amount_eur=round(amount, 2),
                             what="Dividends and interest without Belgian withholding (foreign brokers): fund "
                                  "distributions, interest, share dividends above the exemption, Reynders part of bond "
                                  "funds sold",
                             note=f"share dividends €{shares_no_be:,.2f}, of which €{ex_foreign:,.2f} exempt"))
            if ex_be > 0.005:
                rows.append(dict(person=name, vak="VII A", code=f"{pre}437", amount_eur=round(BE_RATE * ex_be, 2),
                                 what="Belgian withholding on share dividends you claim the exemption for (refund)",
                                 note=f"on €{ex_be:,.2f} of Belgian dividends; the refund is at most €{BE_RATE * ex:,.2f}"))
        p = cgt.get("people", {}).get(pid) if cgt else None
        if p is not None and year >= 2026:
            rows.append(dict(person=name, vak="(new)", code="not published yet",
                             amount_eur=round(p["net"], 2),
                             what="Meerwaardebelasting: net taxable gain of the year (before the exemption)",
                             note=f"exemption €{p['exemption']:,.2f}, tax €{p['tax']:,.2f}; the code comes with the "
                                  f"{year + 1} return"))
    if accounts:
        countries = sorted({a["country"] for a in accounts if a.get("country") and a["country"] != "?"})
        rows.append(dict(person="each person with an account", vak="XIII A", code="1075", amount_eur=None,
                         what="Foreign accounts: answer yes and give the countries", note=", ".join(countries)
                         + "; also registered once with the National Bank (CAP). Whether a crypto platform account must "
                           "be reported is not settled."))
    return pd.DataFrame(rows, columns=["person", "vak", "code", "amount_eur", "what", "note"])


# Where each broker's account is held, for the foreign-accounts checklist.
ACCOUNTS = {
    "DEGIRO": ("flatexDEGIRO Bank AG, Dutch branch", "Netherlands"),
    "LYNX": ("Interactive Brokers Ireland Ltd (introduced by LYNX)", "Ireland"),
    "Bitvavo": ("Bitvavo B.V. (crypto platform)", "Netherlands"),
}


@dataclass
class TaxYearReport:
    year: int
    value_date: date
    persons: list                                  # [(id, name)]
    cgt: dict                                      # summarize_people() of the year's sales (+ other gains)
    exemption: dict[str, ExemptionYear | None]     # the year's exemption per person
    history: dict[str, list[ExemptionYear]]
    sales: pd.DataFrame
    income: dict[str, IncomeTax]
    income_detail: pd.DataFrame
    tob: pd.DataFrame
    tob_detail: pd.DataFrame
    accounts: list[dict]
    checklist: list[str]
    notes: list[str] = field(default_factory=list)
    rules_text: list[str] = field(default_factory=list)
    reynders: dict = field(default_factory=dict)           # reynders_summary() of the year's sales
    reynders_detail: pd.DataFrame = field(default_factory=pd.DataFrame)
    checks: pd.DataFrame = field(default_factory=pd.DataFrame)   # the brokers' annual overviews next to the app's figures
    return_codes: pd.DataFrame = field(default_factory=pd.DataFrame)   # which amount goes in which code (D38)

    @property
    def complete(self) -> bool:
        return self.value_date > date(self.year, 12, 31)

    @property
    def title(self) -> str:
        return f"Tax report {self.year}"

    @property
    def subtitle(self) -> str:
        names = " and ".join(n for _, n in self.persons)
        upto = "" if self.complete else f" · year not finished: figures up to {self.value_date:%d-%m-%Y}"
        return f"Income year {self.year} (tax return {self.year + 1}) · {names}{upto}"

    def person_name(self, pid: str) -> str:
        return next((n for i, n in self.persons if i == pid), pid)


def tax_year_report(rep: Report, year: int | None = None) -> TaxYearReport:
    year = rep.tax.rules.year if year is None else int(year)
    hh = rep.household
    today = rep.tax.today
    sold = rep.sold_lines(year)
    cgt = rep.tax_summary(sold, year=year)
    first = rep.exemption_rules().first_year
    history = {pid: (rep.exemption_history(pid, year) if year >= first else []) for pid in hh.ids}
    exemption = {pid: (h[-1] if h else None) for pid, h in history.items()}

    rows = []
    for l in sold:
        shares = hh.share(l.broker, l.asset)
        row = dict(broker=l.broker, date=l.date, asset=l.asset, name=l.name, type=LAW_TYPES[l.law_type][0], qty=l.qty,
                   price_eur=l.price, price_31dec_eur=l.reference_price, proceeds_eur=round(l.proceeds, 2),
                   acquisition_eur=round(l.acquisition, 2), taxable_gain_eur=round(l.taxable_gain, 2),
                   reynders_base_eur=round(l.reynders_base, 2))
        if not hh.single:
            for pid in hh.ids:
                row[f"share_{pid}"] = shares.get(pid, 0.0)
        rows.append(row)
    sales = pd.DataFrame(rows)

    inc = rep.income_tax(year)
    detail = rep.income()
    if not detail.empty:
        detail = detail[pd.to_datetime(detail["date"]).dt.year == year].reset_index(drop=True)

    brokers = rep.portfolio.brokers()
    accounts = [dict(broker=b, institution=ACCOUNTS.get(b, ("", ""))[0], country=ACCOUNTS.get(b, ("", "?"))[1])
                for b in brokers]

    checklist = [
        "Foreign accounts: report each account below once to the Central Point of Contact (CAP) of the National Bank "
        "of Belgium (when opened; also changes and closing), and answer the question on foreign accounts in the tax "
        "return with the countries. Whether a crypto platform account (Bitvavo) must be reported is not settled: check "
        "with your adviser.",
        f"Dividends and interest: declare them as income received without Belgian withholding tax (taxed at 30%) and "
        f"claim the dividend exemption for dividends from shares (also those of Belgian companies, whose 30% was "
        f"already withheld: that gives a refund). Estimated to pay: €{sum(i.to_pay for i in inc.values()):,.2f}.",
    ]
    if year >= first:
        checklist.append(
            f"Meerwaardebelasting: declare the net gain of {year} per person and claim the exemption; foreign brokers "
            f"don't withhold it. Estimated tax: €{cgt['tax']:,.2f}. The tax administration publishes the codes with the "
            f"return for {year + 1}.")
        checklist.append("Keep proof of the 31-12-2025 values (broker statements of that date and the prices in this "
                         "report) and of original purchase prices above them, for sales until 2030.")
    reynders = rep.reynders_summary(sold)
    if reynders["base"] > 0.005:
        checklist.append(
            f"Reynders tax: the interest part of selling bond funds (€{reynders['base']:,.2f}) is movable income taxed at "
            f"30%, no exemption; foreign brokers don't withhold it. Estimated tax: €{reynders['tax']:,.2f}. Several "
            "guides name code 1444 (part VII, income without withholding); check the code in your return. Keep the TIS "
            "values (fund provider) you used.")
    checklist.append("Stock exchange tax: the brokers withhold it (amounts below). If a foreign broker hasn't, you file "
                     "the stock exchange tax return yourself, by the last working day of the second month after the trade.")

    notes = []
    checks = rep.overview_checks(year)
    if not checks.empty:
        off = checks[checks.difference.abs() > 1.0] if checks.difference.notna().any() else checks.iloc[0:0]
        notes.append(("The broker's annual overview matches the app's figures within €1." if off.empty else
                      f"The broker's annual overview differs from the app on: {', '.join(off.item)} (see Checks)."))
        open_ = checks[checks.app_eur.isna()]
        if not open_.empty:
            notes.append(f"Not comparable yet: {', '.join(open_.item)}.")
        code = checks[checks.item.str.startswith("Code 1444")]
        if not code.empty and code.broker_eur.iloc[0] is not None:
            checklist.append(f"{code.broker.iloc[0]} states €{code.broker_eur.iloc[0]:,.2f} for code 1444-11 in its "
                             f"annual overview (the app: €{code.app_eur.iloc[0]:,.2f}).")
    if rep.reynders_flags():
        notes.append(f"{len(rep.reynders_flags())} bond fund(s) without a Reynders entry: their Reynders tax isn't counted.")
    if not (pd.Timestamp(today) > pd.Timestamp(year, 12, 31)):
        notes.append(f"{year} isn't over: sales, dividends and interest are counted up to {today:%d-%m-%Y}.")
    if "DEGIRO" in brokers and not any(i.broker == "DEGIRO" for i in rep.portfolio.income) and not rep.portfolio.tob_separate:
        notes.append("DEGIRO's Transacties export has no dividends or interest. Add them under other income for the "
                     "year, or wait for the account statement import (backlog item 1).")
    if any(b == "Bitvavo" for b in brokers):
        notes.append("Staking rewards may be taxable as income when received; they aren't included here.")
    missing = [l for l in sold if l.missing]
    if missing:
        notes.append(f"{len(missing)} sale(s) miss a 31-12-2025 price, so their gain is incomplete.")
    if rep.warnings:
        notes.append(f"{len(rep.warnings)} notes about the data (see the app or the Excel workbook).")

    return TaxYearReport(year, today, [(p.id, p.name) for p in hh.persons], cgt, exemption, history, sales, inc, detail,
                         rep.tob_summary(year), rep.tob_detail(year), accounts, checklist, notes,
                         RULES_TEXT + INCOME_RULES_TEXT, reynders, rep.reynders_table(sold), checks,
                         return_lines(rep, year, [(p.id, p.name) for p in hh.persons], inc, cgt, reynders, accounts))
