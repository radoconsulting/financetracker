# Backlog

Agreed on 14 October 2026. Ordered by suggested priority; items that depend on another say so.
Size: S = a day or less, M = a few days, L = a week or more.

## Now

Nothing open: items 1–9 are done. Next in line: 10.

## Next

### 10. Scheduled refresh + alerts · M
Refresh prices on a schedule (daily, on the NAS) and notify when something changes that matters: tax-free room
drops below a threshold, a position moves by more than a set percentage, or a price couldn't be found.
Notification by e-mail or a push service such as ntfy.

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

## Skipped for now

Sander asked on 2026-10-14 to skip this one for now; pick it up only when he asks.

### 11. Login + encrypted data · M
For running on the NAS: a login in front of the app, and uploads, prices and history stored encrypted at rest,
with the key outside the data folder. Document running it behind the NAS reverse proxy with HTTPS.

## Done

- 1. DEGIRO account statement · done 2026-10-14 · Rekeningoverzicht PDF: dividends, withholding, stock exchange tax (added to trade costs), interest, fees, deposits, bond payout; cash reconciles within a few euros (D18–D20).
- 2. Dividend & interest tax · done 2026-10-14 · per year, person and broker; 30% after foreign tax; exemption for shares only. DEGIRO dividends still need item 1 (or entering them as other income).
- 3. Excel export · done 2026-10-14 · `meerwaarde report --xlsx`, sidebar button in the app; totals are live formulas, engine results are values (docs/decisions.md D12).
- 4. Exemption over the years · done 2026-10-14 · carry-forward (+€1,000/yr, 5 yrs, max €15,000), partner with ownership shares per broker/position, Tax years tab, profile.json (D14, D15).
- 5. Yearly tax report · done 2026-10-14 · Tax report tab, `meerwaarde tax-report`, Excel and PDF, checklist with foreign accounts; DEGIRO stock exchange tax is an estimate until item 1.
- 6. Year-end gain harvesting · done 2026-10-14 · Year-end tab, `meerwaarde harvest`, Excel sheet; real stock exchange tax rates, keeps the €1,000 carry-forward by default, loss flags, anti-abuse note (D21).
- 7. Switch planner · done 2026-10-14 · Switch tab, `meerwaarde switch`, Excel sheet; real stock exchange tax rates, conservative yearly effect, spread plan over years; optional Gemini suggestions with your own key (D22, D23).
- 8. Manual positions · done 2026-10-14 · Manual positions tab and `--manual` JSON: funds, tak 21/23, savings, gold as ordinary FIFO transactions under their own broker label; price now and 31-12-2025 value per position; Excel sheet; year-end harvesting skips insurance (D24). Tak 21 roerende voorheffing on early surrender not handled.
- 9. Reynders tax · done 2026-10-15 · Reynders panel (Meerwaardebelasting tab) and `--reynders` JSON: TIS per lot, asset test or worst case; interest part out of the 10%; in the simulator, switch planner, tax report, Excel and PDF; harvesting skips subject funds; cap and deduction are settings (D25).
