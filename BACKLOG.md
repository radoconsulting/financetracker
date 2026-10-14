# Backlog

Agreed on 14 October 2026. Ordered by suggested priority; items that depend on another say so.
Size: S = a day or less, M = a few days, L = a week or more.

## Now

### 1. DEGIRO account statement · M
Read DEGIRO's *Rekeningoverzicht* export: dividends, coupons, dividend tax withheld abroad, AutoFX costs,
deposits and withdrawals.
- Dividends and coupons appear as income per position, like LYNX today.
- Cash movements reconcile with the *Transacties* export (deposits − withdrawals + trades = cash balance).
- The Bund redemption payout replaces the "redeemed at par" assumption.

### 2. Dividend & interest tax · M · needs 1 for DEGIRO
What you owe on income that foreign brokers don't withhold Belgian tax on.
- Per year: dividends and interest per broker, foreign tax withheld, and the 30% Belgian tax to declare.
- The €833-style dividend exemption (indexed) applied to dividends from shares only, not from funds or ETFs.
- Amounts grouped the way the tax return asks for them.

### 3. Excel export · S
One workbook with a sheet per table: summary, realized, open positions, income, meerwaardebelasting,
simulator result. Formatted euro columns, frozen headers, a "how calculated" sheet. Available from the app and
as `meerwaarde report --xlsx`.

### 4. Exemption over the years · M
- Keep a history of realized gains per tax year so unused exemption carries forward (+€1,000 a year, at most
  €15,000), and show the exemption available this year with how it was built up.
- Optional second person: each partner has their own exemption; positions can be owned 100/0, 50/50 or custom,
  and the tax is calculated per person.

### 5. Yearly tax report · M · needs 2 and 4
One report per year for your return or accountant: meerwaardebelasting (sales, losses, exemption used, tax),
dividends and interest to declare, stock exchange tax paid per broker, and a checklist item to report foreign
accounts (DEGIRO, LYNX, Bitvavo) to the NBB's central contact point. Export as Excel and PDF.

## Next

### 6. Year-end gain harvesting · M · needs 4
Before 31 December, suggest which lots to sell (and buy back) to use the remaining exemption, so the purchase
value moves up tax-free.
- Shows tax saved later against costs now (stock exchange tax twice, commissions, spread).
- Also flags losses that could offset gains already realized this year.
- Note in the app: check anti-abuse rules with an adviser before selling and buying back the same position.

### 7. Switch planner · M
The IUSA → global ETF analysis as a tool: pick positions to sell and a target fund, and see the one-time cost
(meerwaardebelasting, stock exchange tax at 0.12% or 1.32%, fees, spread) against the yearly effect (dividend
tax saved, fund cost difference), with a plan spread over tax years to stay within the exemption.

### 8. Manual positions · S
Add positions no export covers: funds, tak 21/23 policies, savings, investment gold. Purchase date, quantity,
price, and a 31-12-2025 value; they count in profit and in the meerwaardebelasting under the right asset type.

### 9. Reynders tax · M
For bond funds and bond ETFs (more than 10% in bonds), estimate the 30% on the interest part when selling.
Needs the taxable income per share (TIS) from the fund provider; entered by hand with a link to where to find
it, then taken into account in the simulator and the switch planner.

### 10. Scheduled refresh + alerts · M
Refresh prices on a schedule (daily, on the NAS) and notify when something changes that matters: tax-free room
drops below a threshold, a position moves by more than a set percentage, or a price couldn't be found.
Notification by e-mail or a push service such as ntfy.

### 11. Login + encrypted data · M
For running on the NAS: a login in front of the app, and uploads, prices and history stored encrypted at rest,
with the key outside the data folder. Document running it behind the NAS reverse proxy with HTTPS.

## Later

### 12. Automatic sync · L
Fetch new LYNX trades and statements through IBKR Flex Queries (token stored securely), and Bitvavo history
through a read-only API key. Uploads stay possible; synced and uploaded rows are matched so nothing counts twice.

### 13. CSV imports and more brokers · L
CSV exports of Bitvavo, DEGIRO and IBKR (sturdier than PDF layouts), then Bolero, Saxo, Trade Republic and
Keytrade. Each parser with tests on anonymised sample files.

### 14. Performance against a benchmark · M
Money-weighted (XIRR) and time-weighted return per broker and in total, a chart of portfolio value over time,
and the same deposits invested in a benchmark such as MSCI ACWI for comparison.

### 15. Allocation look-through · M
Exposure by region, sector, currency and asset class, looking through ETFs to their holdings, plus the overlap
between ETFs (for example S&P 500 inside MSCI World inside ACWI).
