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
| Bund 2019/24 S.180 maturity (16-10-2024) | sell of the full nominal at price 0, total 0 | `bond_redemption_at_par`: proceeds = nominal (100%) with a warning. The real payout is on the account statement (backlog item 1). |
| Turbos (EUR/USD, ArcelorMittal, ING Shell) | ordinary buy/sell | normal FIFO |

## Not in this export

Dividends, coupons, dividend tax withheld abroad, deposits/withdrawals and cash interest: they are in the
account statement (*Rekeningoverzicht*), backlog item 1.
