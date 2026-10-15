# DEGIRO

## Export

"Transacties" PDF (flatexDEGIRO Bank Dutch Branch). Newest first. Columns:

`Datum | Tijd | Product | ISIN | Beurs | Uitvoeringsplaats | Aantal | Koers (+cur) | Lokale waarde (+cur) | Waarde EUR | Wisselkoers | AutoFX Kosten | Transactiekosten en/of kosten van derden EUR | Totaal EUR`

- Read as layout text (`pdfplumber extract_text(layout=True)`) and parsed line by line with one regex
  (`degiro.LINE`). Dutch number format: `-10.671,36`.
- The cost column is empty on fee-free trades: the regex makes it optional.
- Product names wrap onto 1–2 following lines (`UCITS ETF USD`): appended to the name (`_CONT`).
- Product names are DEGIRO's *current* names, even for old trades (Cassava Sciences shows as Filana Therapeutics).
- `order = -line_index` keeps rows in the same minute in chronological order.
- The export period starts in 2015 but the first trade is 4 February 2019.

## Amounts

- `Waarde EUR` = gross value in EUR (negative for a buy) → `gross_eur`.
- AutoFX + transaction costs (negative) → `fees_eur`.
- `Totaal EUR` = cash effect → `total_eur`.
- Bonds are quoted in % of nominal: detected when `qty × price / 100 ≈ value` → asset class `Bond`.

## Corporate actions seen in the data

| Case | What the export shows | How it's handled |
|---|---|---|
| Royal Dutch Shell A → Shell plc (31-01-2022) | sell N RDSA and buy N SHELL PLC at the same price, no costs | `Portfolio._conversion_pairs`: same day, same qty, opposite cash, no costs, different ISIN → lots move to the new ISIN, no sale |
| Cassava warrants and exercise (Dec 2023 – Jan 2024) | warrant rows in/out at price 0; the exercise is a buy on a temporary ISIN (US14817C1154), then moved to the share's ISIN (US14817C1071) the same day | zero-price rows net out; the ISIN swap is a conversion |
| Bund 2019/24 S.180 maturity (16-10-2024) | sell of the full nominal at price 0, total 0 | the account statement's "Contante Verrekening Obligatie" (payout) becomes the proceeds; without it `bond_redemption_at_par` assumes 100% with a warning |
| Turbos (EUR/USD, ArcelorMittal, ING Shell) | ordinary buy/sell | normal FIFO |

## Not in the Transacties export

Dividends, coupons, dividend tax, **the Belgian stock exchange tax**, deposits/withdrawals, interest and
exchange connection fees. The Transacties cost column holds only DEGIRO's own transaction costs and AutoFX.

# DEGIRO account statement (Rekeningoverzicht) · `parsers/degiro_account.py`

## Export

Inbox → Rekeningoverzicht → period from before the first deposit until today → Exporteren (PDF; CSV also exists,
not read yet). Newest first. Columns: `Datum | Tijd | Valutadatum | Product | ISIN | Omschrijving | FX | Mutatie | Saldo`.
Detected by "Rekeningoverzicht" + "Omschrijving" + "Mutatie" on page 1 (the Transacties export has neither column).

## Reading the PDF

- pdfplumber words; column boundaries from the header words of each page (x positions), so wrapped product names
  and descriptions (next line, no date) are appended to the row above. The page footer (flatexDEGIRO address) ends
  a page. `statement_rows()` reads, `parse_rows()` (pure, tested) interprets.
- Amounts: `EUR -10.671,36` (Dutch notation). FX column = foreign currency per EUR on conversion rows.

## Row types

| Omschrijving | What it becomes |
|---|---|
| Dividend / Dividendbelasting | `dividend` / `withholding` per ISIN. USD amounts in EUR at the rate of the statement's next USD→EUR conversion ("Valuta Debitering" with FX), which is how DEGIRO converted them |
| Transactiebelasting België | `tob` with `ref="separate"`: added to the matching trade's costs (same ISIN and minute; partial fills matched by size), because the Transacties export leaves it out. Also kept for the stock exchange tax report |
| Contante Verrekening Obligatie | `redemption`: the bond payout, used as the proceeds of the bond "sold" at 0 |
| Flatex Interest (Income), Rente | `interest` (zero rows skipped; negative = interest paid, not deductible) |
| Inkomsten uit Securities Lending | `interest` named "Securities lending" → income category *other* (30%) |
| Storting, iDEAL storting, flatex Deposit / Terugstorting, flatex terugstorting | `cash_in` / `cash_out` |
| DEGIRO Aansluitingskosten / Exchange Connection Fee, Verrekening Welkomstactie | `fee` (P&L cost; not part of the meerwaardebelasting) |
| Conversie geldmarktfonds | `fee` "Money market fund result" (2019–2020 cash fund; only the price difference is booked) |
| Koop/Verkoop (also WIJZIGING ISIN, KAPITAALVERHOGING, ...), DEGIRO Transactiekosten | ignored: the Transacties export has them (the count is checked) |
| Degiro Cash Sweep Transfer, Overboeking ..., Processed Flatex Withdrawal, Valuta Creditering/Debitering | ignored: money moving between the DEGIRO account and the flatex bank account, or currency conversion |

Unknown descriptions are left out with a warning.

## Checks on the real export

- Sum of all bookings except sweep transfers = the last balance (cash on DEGIRO + flatex together); USD nets to 0.
- Number of trades equals the Transacties export.
- Cash identity (deposits − withdrawals + trades incl. stock exchange tax + income + fees + bond payout) matches
  within about €22: the EUR value given to USD dividends that were spent on USD trades instead of converted.

# CSV exports · `parsers/degiro_csv.py` (backlog item 13, D29)

Both exports also come as CSV (Inbox → Transacties / Rekeningoverzicht → Export → CSV); there is no API (D29).
- Columns are found by header name, Dutch or English (`Datum/Date`, `Aantal/Quantity`, `Waarde EUR/Value EUR`,
  `AutoFX Kosten/AutoFX Fee`, `Totaal EUR/Total EUR`, `Order ID`); a value column is followed by an unnamed currency
  column. Separator `,` or `;`; numbers in either notation (`number()`).
- Transacties rows become the same transactions as the PDF (`gross_eur = |Waarde EUR|`, costs = AutoFX + transaction
  costs, `total_eur = Totaal EUR`), plus the order id in `ref`. The same trade in a PDF and a CSV counts once
  (`combine()` compares the values, not the file type).
- Rekeningoverzicht rows are converted to the PDF reader's row dicts and go through `degiro_account.parse_rows`, so
  every rule above applies; English descriptions were added to `RULES` (Dividend Tax, Belgian Transaction Tax,
  Deposit, Withdrawal, Interest, FX Debit as the conversion rate) as best-known equivalents.
- Not checked against a real CSV export yet: written from DEGIRO's documented layout with invented test files.
  The first real export is the check (compare with the PDF figures, as was done for LYNX Flex).
