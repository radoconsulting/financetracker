# Decisions

Short records of choices that shape behaviour. Add a new entry instead of rewriting an old one.

### D1 · FIFO for profit and loss and for the tax (2026-10-14)
FIFO per (broker, asset), both for P&L and for the meerwaardebelasting. The law uses FIFO. An average-cost view
was computed once for comparison: realized profit differed by about 0.5%.

### D2 · Crypto withdrawals are transfers to the owner's wallet (2026-10-14)
Sander's withdrawals went to his Ledger and came back later. Treating them as sales would be wrong; dropping
the coins would lose their purchase price. Coins stay in the pool; returns are matched. Setting:
`own_wallet_withdrawals` (on by default).

### D3 · Zero cost when the purchase price is unknown (2026-10-14)
Staking rewards, airdrops and coins deposited from elsewhere have no price in the export. They count at zero
cost (conservative), with a warning. For the tax, pre-2026 units of these use the 31-12-2025 price anyway.

### D4 · Conversions detected from the data (2026-10-14)
A same-day pair (sell ISIN A, buy ISIN B, same quantity, opposite cash, no costs) is a conversion, not a sale.
Found for Royal Dutch Shell → Shell plc and the Cassava warrant exercise. Setting: `detect_conversions`.

### D5 · Bonds that go to zero on maturity are redeemed at par (2026-10-14)
DEGIRO books a maturity as a sale at price 0. Without the account statement the payout is unknown; 100% of the
nominal is the normal case. Replace with the real payout once the account statement is parsed (backlog 1).

### D6 · LYNX amounts in EUR come from the statement itself (2026-10-14)
Per currency block, scale = the statement's `Total in EUR` / local total. This matches IBKR's EUR figures and
needs no exchange-rate lookups. ECB rates are only a fallback (prices, reference values).

### D7 · Purchase value for the tax: per lot, without costs (2026-10-14)
`unit_acquisition` applies max(31-12-2025 price, original price), capped at the sale price, per FIFO lot; the
original price excludes costs. See docs/domain/meerwaardebelasting.md for the open FIFO-vs-average question.

### D8 · One price per ISIN across brokers (2026-10-14)
The same ETF held at two brokers uses the same price (the newest of statement and online). Earlier dashboards
showed slightly different prices for the same ISIN, which looked wrong.

### D9 · Foreign cash as a currency position (2026-10-14)
LYNX's USD cash is a position of law type "currency". P&L uses IBKR's cost basis; the tax uses the
31-12-2025 rate for what was held then and the receipt rate for 2026 inflows.

### D10 · Streamlit for the app, PDF parsing without poppler (2026-10-14)
Streamlit fits a local, single-user tool and a Python owner. pdfplumber alone (no poppler/pdftotext) keeps
installation and the Docker image simple.

### D11 · Price sources: override > statement > online (2026-10-14)
Every price carries date and source. Online sources are unofficial where no official API exists (justETF,
Yahoo); the user can always override.

### D12 · Excel export: engine results as values, arithmetic and totals as formulas (2026-10-14)
`export.py` writes the workbook with openpyxl (now a core dependency). FIFO costs, purchase values for the tax,
taxable gains and "max without tax" are values (formulas can't do FIFO); value = qty × price, profit =
proceeds − cost and every total are formulas (SUMIFS over the data sheets), so editing a price, the exemption or
the rate in Excel stays consistent. Inputs are blue on yellow. `fullCalcOnLoad` makes Excel compute on open.
Checked once with LibreOffice on the real exports: 332 formulas, no errors, totals equal the regression baseline.
The simulator logic moved from the app to `Report.simulate()` so the app and the export share it. The app's
download button builds the file only when clicked (callable `data`), which needs Streamlit ≥ 1.65.

### D13 · Stock exchange tax rows are information only (2026-10-14)
LYNX's per-trade TOB is already part of the trade commission. Counting it again would double the cost, so the parser
emits `TOB` transactions that the portfolio stores in `Portfolio.tob` and nothing else uses for P&L or the tax.
Regression: new row kind `LYNX|tob`, no other figure changed.

### D14 · Exemption carry-forward: unused part, max €1,000, carried amounts first (2026-10-14)
Default rule `partial` (Belfius' wording); `none_used` as a setting because other sources read "if you don't use
it" more strictly. Carried amounts are used before the year's own amount, oldest first, and expire after 5 years.
The own amount for years after 2026 is entered by the user once published.

### D15 · Partners: ownership shares per broker, exceptions per position, everything per person (2026-10-14)
A profile (`profile.json`) holds the people and shares. Every tax line, income row and other sale is split by share;
each person gets their own exemption, carry-forward and dividend exemption. One person = exactly the old figures.

### D16 · Dividend tax on the net amount, exemption for shares only, classification by asset class (2026-10-14)
30% on dividends and interest after foreign withholding; the exemption (€833 for 2024–2026, overridable per year)
only for assets of class `Stock`. DEGIRO's dividends aren't in its Transacties export: until backlog item 1 they're
entered as other income in the profile.

### D17 · Yearly tax report as one object, rendered to Excel and PDF (2026-10-14)
`taxreport.tax_year_report()` collects everything for one income year; `export.tax_report_xlsx` (formulas for the
arithmetic, rates and exemptions as inputs) and `pdf_report.tax_report_pdf` (reportlab, no system dependencies)
render it. No tax-return codes are printed for the meerwaardebelasting: they're published with the 2027 return.

### D18 · DEGIRO account statement: trades stay from Transacties, the statement adds the rest (2026-10-14)
Both exports list the trades; Transacties has the EUR value, AutoFX and costs per trade, so it stays the trade
source and the statement's Koop/Verkoop rows are only counted. The statement adds dividends, withholding, the stock
exchange tax, interest, fees, deposits/withdrawals and the bond payout.

### D19 · DEGIRO's stock exchange tax is a purchase/sale cost (2026-10-14)
The Transacties export leaves it out, so P&L understated costs. It is now added to the matching trade's costs
(copies of the rows; the parsed data isn't mutated). The meerwaardebelasting is unaffected (costs are excluded).
Regression: DEGIRO realized and unrealized profit went down (stock exchange tax, connection fees, money market fund),
income went up (dividends, net of withholding and interest); meerwaardebelasting unchanged.

### D20 · Dividends of Belgian companies: Belgian withholding is a credit (2026-10-14)
Withholding on an ISIN starting with BE is Belgian roerende voorheffing, not foreign tax: the base stays the gross
amount and the tax counts as paid (`to_pay` = 30% × taxable − withheld). With the exemption this produces the refund
the return would give. Fees (exchange connection, money market fund) never enter the meerwaardebelasting.

### D21 · Year-end harvesting: cheapest gain first, keep the €1,000 carry-forward by default (2026-10-14)
`plan_harvest` fills each person's remaining exemption with whole positions' FIFO lots, picking the position with the
lowest cost per euro of gain (stock exchange tax on sale and purchase back at the rate the broker charged before,
two commissions, spread), and drops lines whose 10% saving doesn't beat their costs. €1,000 of room is kept by
default because the carry-forward gives the same saving without costs (under the `none_used` rule any gain loses it).
Commission defaults per broker come from Sander's recent orders and can be changed in the app. Cash/FX is left out.
Joint positions use min(room_p / share_p), as in the simulator.

### D22 · Switch planner: conservative yearly effect, rates from real trades, spread plan at today's prices (2026-10-14)
`engine/switch.py`. Yearly effect = 30% dividend tax no longer paid − 10% meerwaardebelasting that may come back on
the reinvested dividends (setting; assumes later gains above the exemption) + TER difference. Dividends are the last
12 months per unit held at each payment. Stock exchange tax: the most recent rate the broker charged on that fund
(VWCE went from 0.12% to 1.32% between 2024 and 2026), else the catalog (`funds.py`), else 1.32% (worst case). The
extra stock exchange tax when the target is sold later is shown separately. The spread plan sells per year what
fits in that year's exemption (this year: what's left; later years: the full own amount, no other sales) at today's
prices, and counts the yearly benefit you wait for. `observed_tob_rate` now uses the latest trade (was the median).

### D23 · Gemini suggestions: optional, your own key, no personal amounts, everything unverified (2026-10-14)
`suggest.py` calls the generateContent REST endpoint (model `gemini-3.8-flash`, falls back to `gemini-2.5-flash`
on 404) with Google Search grounding and asks for a JSON array; the answer is parsed leniently. Only fund names,
ISINs, index, TER and policy of the positions to replace are sent. ISINs are checked (check digit); catalog facts
win; an unknown Belgian registration means 1.32% is assumed. The key comes from the field or `GEMINI_API_KEY` and is
not stored. Verified against the live endpoint only up to key validation (no key here).

### D24 · Manual positions are ordinary transactions under their own "broker" (2026-10-14)
`manual.py` turns each entry into BUY/SELL `Transaction`s (source "manual entry", `ref` = the entry's id), added after
`combine()` so they are never deduplicated against exports. So FIFO, profit, `unit_acquisition`, ownership shares (per
broker label, e.g. the bank or insurer), the simulator and the exports need no special case. Prices are per unit in
EUR before costs; a policy without units is quantity 1. The current price and the 31-12-2025 value entered with a
position sit between an override and the statement/online sources (`PriceBook.set_manual`), so an ISIN entered by hand
still gets an online price when no price is entered. A missing 31-12-2025 value is flagged ("31-12-2025 price"),
never guessed. Invalid entries (no asset, no purchase, selling more than held) are left out with a warning. Year-end
harvesting skips class `Insurance`: a policy can't be sold and bought back at market cost (entry fees, new contract);
losses on one are still flagged. Not handled: tak 21 roerende voorheffing on early surrender (docs/domain).
Stored in `<data dir>/manual.json` (git-ignored, like `profile.json`). Regression unchanged (no manual positions in it).

### D25 · Reynders tax: TIS per lot, then asset test, then worst case; settings for the open points (2026-10-15)
`engine/reynders.py`. The base per FIFO lot is TIS(sale) − TIS(purchase) (latest published TIS on or before each date,
in EUR at that date via `PriceBook.eur_per`); without TIS the asset test (real gain × bond share); without either the
whole real gain, labelled "worst case". Open points made settings: `cap_at_gain` (base ≤ the lot's real gain, default
on) and `deduct_from_cgt` (the interest part is taken out of the 10% gain, default on; the deduction never exceeds the
lot's 10% gain, so pre-2026 units with a high 31-12-2025 value don't create a 10% loss). The deduction goes through
`gain_for(deduct=)` / `max_without_tax(deduct=)`, so the FIFO maximum stays exact (each lot stays linear in the
quantity). Which funds are subject is the user's call (`reynders.json`, `--reynders`); without an entry nothing is
taxed and bond ETFs are flagged in `Report.reynders_flags()`, kept apart from `rep.warnings` so the regression's note
count doesn't change. In the app a flagged bond ETF defaults to subject, so saving the panel never silences a flag
without a decision. Year-end harvesting skips subject funds (30% without exemption makes harvesting cost real tax);
the switch planner counts the Reynders tax in the cost now and in each year of the spread plan. "Max without tax"
covers the 10% only; the simulator warns when the sales also pay Reynders tax. Regression unchanged (no Reynders file
in it).

### D26 · Scheduled refresh: one-shot command, alerts on crossings and anchors, no amounts by default (2026-10-15)
`alerts.py` + `datadir.py`. `meerwaarde refresh` is a one-shot command (cron, NAS task scheduler, Windows Task
Scheduler) with `--every HOURS` for a container; the app doesn't schedule anything itself, so a Streamlit restart never
loses a run. `DataDir.build()` builds the report from the data folder with the app's default settings (sidebar
toggles aren't stored). Alerts avoid daily repeats: the room alert fires when a person's room crosses below the
threshold (or on the first run already below it); a move alert compares with an anchor price per position (its price
at the last alert or first sighting), so slow drifts add up and the anchor resets after an alert; a missing price
alerts once. State in `<data dir>/alerts/state.json`, a JSON-lines log next to it. `--dry-run` and the app's preview
send nothing and leave state and log untouched. Channels: ntfy (HTTP POST, optional bearer token) and SMTP e-mail;
secrets only from environment variables named in `alerts.json`. Messages carry no amounts unless `include_amounts`
(public ntfy.sh topics are readable by anyone who knows the name); position names are still in them. Send errors are
returned and logged, never raised. Not verified against a live ntfy server or SMTP server here (both tested with fakes).

### D27 · Performance from trade flows; benchmark with the same flows, capped at its value (2026-10-15)
`engine/performance.py`. Flows come from the trades (purchase = money in, sale and income = money out, standalone fee =
money in), not from deposits into the account, because not every export has those and cash at the broker would dilute
the return. Cash/FX positions are left out on both sides (their EUR is already in the sale flows). Units: own-wallet
withdrawals keep the units and coins coming back are matched, as in `Portfolio`. Bond redemptions use the payout from
the realized row. XIRR by Newton with a bisection fallback. Time-weighted: Modified Dietz per month between month-end
values, chained; the first point is the first trade day (value after its purchases) so no period starts empty, and
periods with less than €100 at work are skipped (a €1 test deposit would otherwise swing the chain). The benchmark buys
with every euro in and sells for every euro out on the same day, but never pays out more than it is worth (otherwise a
broker where you took out more than you put in ends negative); its XIRR uses what it really paid out. The "All" line pools
the brokers, so its benchmark isn't the sum of the per-broker benchmarks. Checked on the real exports: gain per broker
equals the profit and loss total within the currency results that are left out. Regression unchanged (the workbook's
new Performance sheet isn't in the baseline).

### D28 · Automatic sync: raw downloads in the data folder, exports win where they cover, secrets only from outside (2026-10-15)
`sync/`. LYNX through the IBKR Flex Web Service (`sync/flex.py`), Bitvavo through `GET /v2/account/history` with a
read-only key (`sync/bitvavo_api.py`). Downloads are stored raw in `<data dir>/sync/` and parsed on every build, like
the uploaded PDFs, so a parser fix applies to old downloads too. Merge rule (`merge_synced`): where an uploaded export
covers a period, it wins: for LYNX everything up to the newest statement's end (the statement's EUR totals, D6), for
Bitvavo every transaction id in the PDF and everything up to its last row. Row-by-row matching was rejected: Flex lists
executions where the PDF merges fills, and USD amounts convert at a different rate. Within the synced data, ids
remove overlaps between downloads. The Bitvavo fee rule (is the fee inside `sentAmount`?) is decided by comparing with
the PDF rows of the same id on the first sync, when at least 5 match, and stored. Credentials come from environment
variables or a KEY=VALUE file named by `MEERWAARDE_SECRETS` / `--secrets`, outside the data folder; the app shows
only whether they're set. Encryption at rest is item 11 (skipped for now). Checked on a real Flex file: identical
trades, costs and stock exchange tax per day as the statement PDF; positions equal. Regression unchanged.

