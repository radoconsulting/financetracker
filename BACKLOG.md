# Backlog

Agreed on 14 October 2026. Ordered by suggested priority; items that depend on another say so.
Size: S = a day or less, M = a few days, L = a week or more.

## Now

Items 1–10 and 12–15 are done (11 skipped for now). Now: 16–24 below, in order. To confirm with real files: the first Bitvavo sync, and a DEGIRO and Bitvavo CSV export.

## Next

Agreed on 15 October 2026 (from the feature review), in this order.

### 22. Savings and pension savings · M
Regulated savings accounts (interest exemption) and pension savings (pensioensparen: tax reduction on contributions,
the advance tax at 60) as positions with their own rules.

### 23. Corporate actions · M
Splits, mergers and spin-offs from the Flex Corporate Actions section (and statements) applied to the lots, instead
of only flagged.

### 24. More brokers built in · M
Bolero, Saxo and Trade Republic as built-in formats. Without sample files: presets of the CSV mapping marked
unverified until checked against a real export.

## Later

Empty.

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
- 10. Scheduled refresh + alerts · done 2026-10-15 · `meerwaarde refresh` (daily via cron/NAS task scheduler, or `--every` in a container), Alerts tab; room below threshold, price move since last alert, missing price; ntfy and SMTP with secrets from the environment; no amounts by default (D26). Not tried against a live ntfy or SMTP server.
- 14. Performance against a benchmark · done 2026-10-15 · Performance tab, `meerwaarde performance`, Excel sheet with a chart; XIRR and time-weighted return per broker and in total from trade flows; benchmark (default iShares MSCI ACWI) with the same flows, capped at its value; daily history from Bitvavo, justETF, Yahoo, else the last trade price (D27).
- 12. Automatic sync · done 2026-10-15 · `meerwaarde sync`, *Sync now* in the app, `refresh --sync`; LYNX via the Flex Web Service (checked against the statement PDF: identical trades, costs, positions), Bitvavo via `/account/history` with a read-only key (fee rule and time zone checked against the PDF on the first live sync); exports win where they cover; secrets only from the environment or a secrets file (D28).
- 13. CSV imports and more brokers · done 2026-10-15 · DEGIRO Transacties/Rekeningoverzicht and Bitvavo as CSV (through the PDF parsers' row logic), inbox folder, reminder when an export is older than 35 days; other brokers through a column mapping on the Data tab instead of guessed formats; IBKR CSV not needed (Flex). Not yet checked against real CSV exports (D29).
- 15. Allocation look-through · done 2026-10-15 · Allocation tab, `meerwaarde allocation`, Excel sheet; asset class, region, currency (approximate), sector; look-through top holdings and fund overlap from the top 10 (lower bounds); justETF composition, own breakdown in exposure.json (D30).
- 16. Net worth over time · done 2026-10-15 · Overview tab chart (by broker or asset class), Excel sheet with a chart; positions from the price series, cash per broker rebuilt from the rows and left out where deposits are missing (D33).
- 17. Fees and costs dashboard · done 2026-10-15 · Costs tab, Excel sheet; stock exchange tax, commissions and conversion, other fees, network fees and TER drag per year and broker; yearly level today and over 10 years (D34).
- 18. Fund red flags · done 2026-10-15 · top of the Switch tab, Excel sheet; distributing, 1.32% stock exchange tax, TER above the alternative, bond funds; like-for-like alternatives only; CSPX added to the catalog as unverified (D35).
- 19. Dividend and cash-flow calendar · done 2026-10-15 · Calendar tab, Excel sheet; last year's payments per unit for the units held now, foreign and Belgian tax per payment, taxes coming due and when (D36).
- 20. Multi-year tax plan 2026–2030 · done 2026-10-15 · Year-end tab section, `meerwaarde plan`, Excel sheet; yearly sell-and-buy-back within each person's exemption to 2030, original-price window counted, compared with selling everything in 2031 (D37).
- 21. Tax return helper · done 2026-10-15 · Tax report tab, its Excel sheet Tax return, PDF and CLI; 1444/2444, 1437/2437, 1075, the meerwaardebelasting per person until its code is published; sources in docs/domain/belgian-taxes.md (D38).
