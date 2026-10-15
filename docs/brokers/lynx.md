# LYNX (Interactive Brokers Ireland)

## Export

"Activity Statement" PDF per period (Lynx b.v. as introducing broker, IBIE as executing broker), one per
calendar year plus year-to-date. Base currency EUR. All statements are needed: the FIFO lots come from the
trades of every year. When the trades don't add up to the newest statement's positions, a warning says so.

## Reading the PDF (`parsers/lynx.py`)

- `statement_lines()` builds text lines from words with `pdfplumber.extract_words(extra_attrs=["size"])`.
  Section titles are 10 pt (body text 7 pt, page title 14 pt): lines whose words are all 9.5–12 pt get a
  `## ` prefix.
- **Side-by-side sections** (e.g. *Dividends* next to *Deposits & Withdrawals*, or *Interest Accruals* and
  *Dividends* on the right): when a 10 pt title starts right of 45% of the page width, the page is split from
  that title's height down into a left and a right column, each read separately.
- A section title repeated on the next page is a **continuation**: category (Stocks/Forex) and currency are kept.
  (Bug found on the 2024 statement: resetting them made USD trades after a page break count as EUR.)
- `parse_lines()` is the testable entry point.

## Sections used

| Section | Used for |
|---|---|
| Account Information | base currency |
| Net Asset Value / Change in NAV | totals per period (dividends, fees, deposits) for checks |
| Mark-to-Market Performance Summary | prior and current prices and quantities per symbol and currency. In the statement that starts on 1 January 2026, *prior* = 31-12-2025 → reference prices and EUR/USD (0.85135) |
| Realized & Unrealized Performance Summary | Forex realized total → `fx_result` (currency result on USD cash); stock realized totals kept for checks |
| Open Positions | quantity, close price, value; `Total in EUR` for foreign currency |
| Forex Balances | foreign cash (USD) with IBKR's EUR cost basis |
| Trades | per trade: date line above the row, qty, trade price, proceeds, comm/fee (includes Belgian TOB), basis. Forex rows (`EUR.USD`): only the commission is kept (`fee`) |
| Dividends | description line `SYM(ISIN) Cash Dividend USD x per Share`, then `date amount`; reversals are negative rows. Asset class from the instrument type (`COMMON` → Stock), which decides the dividend exemption |
| Withholding Tax, Interest, Deposits & Withdrawals | dated rows. Withholding on a dividend is linked to the security when the description has `(ISIN)`; withholding on credit interest (20% Irish) stays on the currency |
| Transaction Fees | `date, time SYM BE Securities Transaction Tax qty price amount` per trade, in currency blocks with `Total in EUR` → `TOB` rows (information only: the same amount is inside the trade's Comm/Fee) |
| Financial Instrument Information | symbol → ISIN, description, type |

## Currency conversion

Trades and dividends in USD are converted with the statement's own `Total in EUR` line for that block
(section + category + currency): scale = EUR total / local total. This reproduces IBKR's EUR figures without
needing exchange rates. Dividends get the same pro-rata treatment.

## Foreign cash as a position

USD cash comes from the newest statement's *Forex Balances*: two lots, the quantity held at the start of the
period (prior quantity from Mark-to-Market) dated 31-12-2025, and the rest dated at the period start. P&L cost
uses IBKR's cost basis; for the tax the first lot uses the 31-12-2025 rate and the rest the EUR/USD rate of the
2026 dividends that created it.

## Known differences with IBKR's own figures

- CSPX unrealized: IBKR converts the USD cost basis at today's rate; the app uses the EUR paid at the time
  (rate of each trade block). The app's number is the real EUR result; IBKR's differs by the currency move.
- Total profit matches *ending NAV − deposits* within about €3.

## Flex Web Service (automatic sync, backlog item 12, D28)

LYNX accounts are IBKR accounts: in the LYNX portal, *Performance & Reports → Flex Queries*. Create an Activity Flex
Query (XML, period *Last 365 Calendar Days*) with Trades, Transaction Fees, Cash Transactions, Transfers, Corporate
Actions and Open Positions; the Flex Web Service is switched on with the gear icon of *Flex Web Service Configuration*
at the bottom of that page (token valid up to 1 year; a new token invalidates the old one).

Compared with the statement PDF on a real export (15 October 2026): identical quantities, costs and
totals per day. Differences that matter for the code (`sync/flex.py`):
- Flex lists **executions**; the PDF merges partial fills of one order into one row.
- Costs: the PDF's commission includes the stock exchange tax; Flex splits it into `ibCommission` and `taxes`, so the
  sync adds them up. `netCash` equals the PDF's total.
- EUR: Flex gives `fxRateToBase` per row (the rate of that day); the PDF statement's EUR totals per block differ by a
  few euros on USD dividends. The statement wins for the period it covers (D6, D28).
- Only 365 days per request; each download is kept, ids (`tradeID`, `transactionID`) make overlaps harmless.

## Transaction History PDF · `parsers/ibkr_history.py` (D31)

*Performance & Reports → Transaction History* gives one table for any period: `Date | Account | Description |
Transaction Type | Symbol | Quantity | Price | Price Currency | Gross Amount | Commission | Net Amount`, amounts in EUR.
Recognised by "Transaction History" + "Transaction Type" + "Net Amount" on page 1 (the IBKR name is only a logo).
Checked against real statements covering the same years: every day equal in quantity; euro amounts equal
except USD trades and USD dividends (per-trade conversion here, the statement's block totals there: a few euros).
- Trades have the symbol, not the ISIN: matched to ISINs from the statement/Flex rows of the same symbol, the
  dividend descriptions in the file ("IUSA(IE0031442068) …", the ISIN often broken over two lines) and the fund
  catalog; an unmatched symbol stays as is, with a note.
- Costs = Net − Gross (IBKR commission + stock exchange tax; the Commission column has only the commission).
- "FX Translations P&L" (revaluation of USD cash) is left out; Forex Trade Component rows only add their commission.
- It supplements the Activity Statements: its rows count only after the newest statement's end (`combine`), and
  the Flex sync counts from the end of whichever reaches further.

## Jaaroverzicht (annual tax overview) · `parsers/lynx_annual.py` (D32)

LYNX's yearly summary for the tax return: dividends and interest in and outside Belgium, tax withheld, the amount
for code 1444-11, the account value on 1 January and 31 December (cash, securities), and the rows behind the totals.
Page 1 is only the LYNX logo, so detection also reads page 2. It is used as a **check** in the tax report, not as
transactions (the statements already have the same income). On a real 2025 overview everything matched to the cent:
gross dividends, interest after withholding, the code 1444-11 amount and the securities on 31-12-2025 (the app's
units × 31-12-2025 prices). The per-payment EUR amounts in the overview use the rate of the
payment day and differ from the statement's (D6), but the yearly totals are the same.
