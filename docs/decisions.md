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
Regression: DEGIRO realized and unrealized profit went down (stock exchange tax, connection fees, money market
fund) and income went up (dividends, net of withholding, and interest); meerwaardebelasting unchanged.

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

### D29 · CSV imports: shared row logic, an inbox folder, a column mapping instead of guessed formats (2026-10-15)
DEGIRO has no official API; the unofficial web endpoints need the full login with 2FA secret and may get an account
blocked, so DEGIRO stays export-based: CSV (sturdier than the PDF layout) read from uploads or `<data dir>/inbox/`, and
an alert (`stale_days`, default 35) when a broker's newest export or sync is older than that. The CSV readers feed the
existing row logic (`degiro_account.parse_rows`, `bitvavo.parse_rows`) instead of copying it. Brokers Sander doesn't
use (Bolero, Saxo, Keytrade, Trade Republic) get no guessed parser: `parsers/mapped.py` reads any CSV through a column
mapping made once on the Data tab and recognised again by its exact header; a mapped format wins over the built-in
detection. IBKR's CSV isn't added: the Flex sync (D28) covers it better. Freshness = the modification date of the newest
file a broker's rows came from (or its newest sync download), not the date of its last transaction, so a quiet month
doesn't trigger the reminder. Regression unchanged.

### D30 · Allocation: justETF composition, top-10 overlap as a lower bound, currency from countries (2026-10-15)
`engine/allocation.py`. Fund composition from the justETF profile page (`Fetcher.justetf_profile`, HTML parsed by
`parse_justetf_profile` on its `data-testid` markup; cached a week): the top 10 holdings with ISINs, the 4 largest
countries and sectors plus "Other" (the full lists need justETF's session-bound "Show more", not used). Provider
holdings files (iShares, Vanguard) were rejected: a different URL and format per provider and no ISIN → file mapping.
Your own breakdown in `exposure.json` wins. Shares count as 100% their company, country from the ISIN prefix (a
company's listing country, not always its business). Currency is approximated from the countries, which is wrong for
currency-hedged share classes (noted in the app). Overlap = Σ min(wA, wB) over shared top-10 ISINs, once per fund
(the same ISIN at two brokers is one fund): a lower bound. Tried live on 15 October 2026: every ETF had data, about
8 seconds for the first run. Regression unchanged (the workbook's Allocation sheet isn't in the baseline).

### D31 · LYNX Transaction History is a supplement, statements win where they cover (2026-10-15)
The Transaction History PDF has per-trade EUR amounts but no ISINs, no positions and no 31-12-2025 prices, so it
can't replace the Activity Statement (D6). `ParseResult.supplement` marks it; `combine` keeps only its rows after
the newest LYNX statement's end and maps symbols to ISINs first. Same rule as the Flex sync (D28), and the sync's
coverage includes these rows so the two never overlap. Regression unchanged (the file isn't in `private/exports`).

### D32 · Brokers' annual overviews are checks, not sources (2026-10-15)
The LYNX Jaaroverzicht repeats income that is already in the statements, so it gives no transactions;
`ParseResult.overview` carries it and `Report.overview_checks(year)` puts the broker's figures next to the app's:
gross dividends, dividend tax, interest after withholding, the code 1444-11 amount, and the value of the securities
on 31-12-2025 (units held that day × the reference prices: a direct check of the purchase values for the
meerwaardebelasting). Shown in the Tax report tab, its Excel (sheet Checks), PDF and `meerwaarde tax-report`; the
checklist quotes the broker's code 1444-11 amount. Regression unchanged.

### D33 · Net worth: positions from the value series, cash rebuilt from the rows, left out when deposits are missing (2026-10-15)
`engine/networth.py`. Positions: units held per month-end × that day's price (`PriceSeries`, as in D27), all classes
except Cash/FX lots. Cash per broker: deposits − withdrawals + trade totals + income + fees + bond payouts (the
`redemption` row; the bond's 'sale' row has total 0). A broker's cash is left out (with a note) when it ends below
−€250 or dips below 2% of its deposits: missing deposits pile up, while a purchase booked before its deposit only dips
for a while (shown as 0). Foreign cash stays at the euro amount of each trade (not revalued). Checked on the real
exports: DEGIRO's rebuilt cash ends within the known USD valuation gap of the account statement's balance. Regression
unchanged.

### D34 · Costs: trading costs from the rows, running costs estimated from TER × value (2026-10-15)
`engine/costs.py`. Stock exchange tax from the brokers' tax rows (the same totals as the tax report); commissions and
conversion costs = the trades' `fees_eur` minus that tax (DEGIRO's AutoFX is in there, the Transacties export has no
separate total); standalone fee rows; crypto network fees at their FIFO cost. Running costs: month-end value × TER/12
for funds whose TER is in the catalog; funds without a known TER are listed. "Per year at today's level" = trading
costs of the last 12 months + TER on today's value; × 10 for a decade, no growth or compounding (kept simple and
conservative). Regression unchanged.

### D35 · Fund red flags: facts from the broker or the catalog, like-for-like alternatives only (2026-10-15)
`engine/redflags.py`. Per fund held (across brokers): distributing (30% on the last 12 months' dividends, the switch
planner's per-unit method), stock exchange tax ≥ 1.32% (the rate the broker last charged, else the catalog), TER at
least 0.10 pp above the alternative (or unknown: said so), bond exposure (class Bond ETF or marked for Reynders). The
alternative is the cheapest accumulating 0.12% fund on the same index; a world fund only replaces a world fund; bond
funds, regional and emerging-market funds get none unless the catalog has the same index. CSPX was added to the
catalog as unverified (TER from iShares, 0.12% from docs/domain/belgian-taxes.md). Shown at the top of the Switch tab
and in the Excel sheet *Red flags*. Regression unchanged.

### D36 · Dividend calendar: last year's payments per unit, for the units held now (2026-10-15)
`engine/calendar.py`. Every dividend of the last 12 months is projected one year later at the same amount per unit
(units held then → units held now), with the foreign withholding booked within 10 days of it; positions sold since
drop out. Belgian tax per payment: 30% of the net amount, 0 for ISINs starting with BE (withheld at source). The
share dividend exemption is applied only in the yearly figures (income tax engine), not per payment. Taxes coming due:
the year's dividend/interest tax and meerwaardebelasting so far, declared in the next year's return and paid within
two months of the assessment (by 30 June two years later at the latest). Regression unchanged.

### D37 · Multi-year tax plan: greedy per year within the exemption, priced with one growth rate (2026-10-15)
`engine/taxplan.py`. For each year from now to 2030, per person, the room is the year's available exemption (incl.
carry-forward, via `exemption_schedule` on the planned gains) minus what's used, keeping €1,000 for the carry-forward
by default (as D21). Positions are picked cheapest cost per euro of benefit, where benefit = 10% × (gain harvested +
the original purchase price kept above the 31-12-2025 value, which is lost after 2030). Quantities come from
`max_without_tax`, trimmed to the last FIFO unit that still has a gain (selling units bought back earlier only costs
stock exchange tax). Sold units come back as a new lot at that year's price. Prices: today's × (1+g)^years, cash and
bonds flat; Reynders funds and insurance left out. Compared with selling everything in 2031 without the plan; the
saving is that difference minus the plan's costs. A plan, not advice: real prices on the trade day decide.
Regression unchanged.

