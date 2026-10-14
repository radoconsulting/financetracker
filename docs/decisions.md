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
