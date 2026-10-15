# meerwaarde

Python package + Streamlit app that reads broker exports (Bitvavo, DEGIRO, LYNX), computes profit and loss
(FIFO, after costs), and the Belgian **meerwaardebelasting** (10% capital gains tax on financial assets from
1 January 2026), with a simulator for how much can be sold before the yearly exemption is used up.

Owner: Sander (Belgian tax resident). It runs locally or in Docker on his NAS. Not tax advice: the app says so.

## Commands

```bash
pip install -e ".[app,dev]"          # editable install with web app and tests
pytest                               # unit tests (synthetic data only)
python scripts/regression.py         # compare against the baseline from the real exports in private/
meerwaarde app                       # web app on http://localhost:8501 (data in ~/.local/share/meerwaarde)
meerwaarde report private/exports/*.pdf --offline --prices private/prices.csv --today 2026-10-14 -v --xlsx
meerwaarde tax-report private/exports/*.pdf --offline --prices private/prices.csv --today 2026-10-14 --year 2026
```

Run `pytest` and `python scripts/regression.py` before every commit. If the regression changes on purpose,
explain why in the commit message and regenerate the baseline with `python scripts/regression.py --update`.

## Layout

| Path | What |
|---|---|
| `src/meerwaarde/models.py` | Transaction, Lot, RealizedRow, Holding, IncomeRow; transaction kinds; `law_type()` |
| `src/meerwaarde/parsers/` | `degiro_csv.py` and Bitvavo CSV (`bitvavo.csv_rows`) feed the PDF parsers' row logic; `mapped.py` (column-mapped CSV of other brokers, `csv_formats.json`) (D29); `bitvavo.py` (pdfplumber tables), `degiro.py` (Transacties: layout text + regex), `degiro_account.py` (Rekeningoverzicht: column-aware words), `lynx.py` (IBKR statement, column-aware), `ibkr_history.py` (Transaction History PDF, a supplement: D31), `lynx_annual.py` (Jaaroverzicht: a check in the tax report, D32), `classify.py` (asset class guesses), `__init__.py` (detect, parse_file, combine) |
| `src/meerwaarde/engine/portfolio.py` | FIFO lots per (broker, asset); own-wallet transfers; ISIN conversions; bond redemption at par; foreign cash from LYNX statements |
| `src/meerwaarde/engine/tax.py` | TaxRules, unit_acquisition, gain_for, max_without_tax, TaxCalculator |
| `src/meerwaarde/engine/household.py` | Household (people, ownership shares), exemption carry-forward schedule, per-person tax |
| `src/meerwaarde/engine/switch.py` | Switch planner: sources, compare targets, spread plan over years (D22) |
| `src/meerwaarde/funds.py` | Fund catalog (TER, policy, stock exchange tax; checked 14-10-2026), ISIN check |
| `src/meerwaarde/suggest.py` | Optional Gemini suggestions (BYO key, REST, grounding; parse + validate) (D23) |
| `src/meerwaarde/engine/harvest.py` | Year-end harvesting: remaining room per person, cost model, plan, loss flags (D21) |
| `src/meerwaarde/engine/reynders.py` | Reynders tax on bond funds: TIS / asset test / worst case per lot, ReyndersBook JSON, deduction from the 10% (D25) |
| `src/meerwaarde/engine/performance.py` | XIRR, time-weighted return (Modified Dietz, monthly), value series, benchmark with the same flows (D27) |
| `src/meerwaarde/engine/allocation.py` | Look-through exposure (asset class, region, country, sector, currency), top holdings, fund overlap; justETF composition, `exposure.json` overrides (D30) |
| `src/meerwaarde/engine/networth.py` | Net worth per month: positions × price series, cash per broker rebuilt from the rows (D33) |
| `src/meerwaarde/engine/income_tax.py` | 30% on dividends and interest, dividend exemption (shares only), foreign tax |
| `src/meerwaarde/manual.py` | ManualBook JSON: positions no export covers (funds, tak 21/23, savings, gold) → BUY/SELL transactions, prices into the PriceBook (D24) |
| `src/meerwaarde/sync/` | Automatic sync: `flex.py` (IBKR Flex XML → rows, download), `bitvavo_api.py` (signed history, mapping, reconcile), `__init__.py` (secrets, SyncStore in `<data dir>/sync/`, merge rule) (D28) |
| `src/meerwaarde/alerts.py`, `datadir.py` | Scheduled refresh: data folder → report, snapshot, alert rules (room, move, missing), ntfy/SMTP, state + log (D26) |
| `src/meerwaarde/profile.py` | TaxProfile JSON: partner, ownership, per-year exemption amounts, other sales and income |
| `src/meerwaarde/taxreport.py` | `tax_year_report()`: everything for one income year, incl. checklist and foreign accounts |
| `src/meerwaarde/pdf_report.py` | PDF of the yearly report (reportlab) |
| `src/meerwaarde/prices/` | PriceBook (override > manual entry > statement > online; `history()` daily series) and `fetch.py` (Bitvavo, justETF, Yahoo, ECB; disk cache) |
| `src/meerwaarde/report.py` | `Report`: tables, per-person tax (`tax_summary`, `exemptions`), `simulate()`, income tax, stock exchange tax; `build_report()` |
| `src/meerwaarde/export.py` | Excel: main workbook (`to_xlsx`) and yearly report (`tax_report_xlsx`); engine results as values, totals as formulas (D12) |
| `src/meerwaarde/app/streamlit_app.py` | the web app (tabs: Overview, Positions, Meerwaardebelasting, Simulator, Year-end, Switch, Tax report, Tax years, Prices, Data, Manual positions, Alerts, Performance, Allocation); `<data dir>/profile.json`, `funds.json`, `manual.json`, `reynders.json` |
| `src/meerwaarde/cli.py` | `meerwaarde report`, `harvest`, `switch`, `tax-report`, `performance`, `allocation`, `sync`, `refresh`, `app`; `--profile`, `--manual`, `--reynders` |
| `tests/` | pytest, synthetic data only |
| `scripts/regression.py` | end-to-end check on `private/` |
| `docs/` | domain knowledge and decisions: read the relevant file before changing behaviour |
| `private/` | Sander's real exports, prices and old artifacts. Git-ignored. Never commit, never copy into tests. |
| `BACKLOG.md` | agreed features, ordered Now / Next / Later |

## Docs to read before changing things

- Tax rules and their sources: `docs/domain/meerwaardebelasting.md`
- Other Belgian taxes (stock exchange tax, dividend tax, Reynders, NBB reporting): `docs/domain/belgian-taxes.md`
- Broker export formats and quirks: `docs/brokers/bitvavo.md`, `docs/brokers/degiro.md`, `docs/brokers/lynx.md`
- Price sources and what was verified: `docs/prices.md`
- Why things work the way they do: `docs/decisions.md`
- Expected figures from the real exports: `private/notes/validation.md`
- What was done before this repo existed (analyses, dashboard, calculator, ETF switch, his positions):
  `private/notes/history.md`

## Rules that must hold

- Money is EUR. Purchase cost for P&L includes costs; the tax uses prices **without** costs (`gross_eur`).
- FIFO per (broker, asset). Same ISIN at two brokers = two pools.
- Crypto withdrawals are moves to Sander's own wallet (Ledger) by default, not sales. Coins coming back are
  matched to what went out. Option: `PortfolioOptions.own_wallet_withdrawals`.
- Tax: units held on or before 31-12-2025 use the 31-12-2025 price; a higher original price may be used until
  31-12-2030 but only to reduce a gain, never to create a loss. Units bought later use their purchase price.
- All law asset types (financial instruments, insurance, crypto, currency, gold) are one regime: losses offset
  gains in the same year; no carry-over of losses. Exemption €10,000 (2026), per person, plus carry-forward
  (D14). Everything tax-related is per person by ownership share; one person must give the old figures (D15).
- Dividends/interest: 30% after foreign tax; the exemption only for class `Stock` (D16).
- Stock exchange tax rows (`TOB`): LYNX's are information only (already in the commission, D13); DEGIRO's
  (`ref="separate"`) are added to the trade's costs because Transacties leaves them out (D19). Never in the tax.
- Every price shown must carry its date and source. A user override always wins.
- A parser change needs a synthetic test in `tests/test_parsers.py` (text in, transactions out); parsers keep
  a text/lines entry point (`degiro.parse_text`, `lynx.parse_lines`) so tests don't need PDFs.
- Network failures must never break a report: price lookups return None and record an error; Gemini errors come
  back in `Suggestion.error`. Never send amounts or quantities to an external AI service (D23).
- Credentials (IBKR token, Bitvavo key/secret, SMTP password, ntfy token) never in the repo, the data folder or a log:
  only environment variables or a secrets file outside both (D26, D28).
- No personal data in the repo outside `private/`: no account numbers, quantities, amounts or lists of
  holdings in code, tests or `docs/` (an instrument name as the example of a format quirk is fine). The repo
  may be pushed to GitHub; `private/` never is.

## Conventions

- Python ≥ 3.10, type hints, dataclasses, pandas only in `report.py` and the app.
- User-facing text in plain English, with Dutch legal terms where the law uses them (meerwaardebelasting,
  andere financiële activa, beurstaks / stock exchange tax). Euro amounts formatted `€1,234`.
- Commit messages: imperative, explain the why; mention regression changes.
- Streamlit ≥ 1.65: use `width="stretch"`, not `use_container_width`.
- A new table or figure in the app also goes into the Excel export (`export.py`) and its test.

## Working style

- Sander is a data engineer (dbt, SQL, Microsoft Fabric, Python notebooks): technical depth is welcome,
  skip basics. He writes in English or Dutch; answer in the language of his message.
- For backlog work: plan briefly, implement with tests, update `docs/` and move the item to `## Done` in
  `BACKLOG.md`.
- When a tax rule is uncertain, say so, cite the source, make it a setting where reasonable, and record the
  choice in `docs/decisions.md`.

Skills in this repo: `/validate` (tests + regression), `/backlog-next [n]` (pick up the next backlog item),
`/add-broker <name>` (steps for a new broker parser). Path rules in `.claude/rules/` load when you touch
parsers or the tax/price engines.

Not verified yet: `docker build`. The live price fetchers (current, 31-12-2025 and daily history) were tried on
15 October 2026 on Sander's PC: they work, except delisted coins at Bitvavo (400) and some funds without a series.
