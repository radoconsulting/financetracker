# Bitvavo

## Export

"Transactieoverzicht" PDF (full history, all dates) from Bitvavo B.V. First page has a cover letter, then a
table per page with columns:

`Date (Europe/Brussels) | Type | Currency | Amount | Quote Price | Received / Paid Amount | Fee amount | Status | Transaction ID | Address`

- Read with `pdfplumber` `extract_tables()`; header row detected by name, so column order may change.
- A row split over a page break comes back as a row with an empty date: merged into the previous row.
- Cells contain line breaks (type `Manually_assi\ngned`, IDs, addresses): newlines are removed.
- Numbers carry their unit in the cell: `22719 EUR`, `-753.85 EUR`, `0.00009BTC`, `0.002 ETH`. Units are
  read from the end of the cell (`_unit()`); a bug that stripped the `E` of `EUR` was fixed, keep the regex.
- The 97-page export takes ~25 s to parse; the app caches parse results per file hash.

## Row types

| Type | Meaning | Kind |
|---|---|---|
| Deposit / Withdrawal, currency EUR | bank transfer | `cash_in` / `cash_out` |
| Buy / Sell | trade against EUR; *paid* includes the fee, *received* is net of the fee | `buy` / `sell` |
| Deposit (crypto) | coins arriving from elsewhere | `deposit` |
| Withdrawal (crypto) | coins leaving, fee in coin units | `withdrawal` (`fee_qty`) |
| Staking, Fixed_staking, Manually_assigned (airdrops, e.g. LUNA2) | free coins | `reward` |

Status `Completed` / `Distributed` only. Non-EUR markets are skipped with a warning.

## Interpretation (decided with Sander)

- **Withdrawals go to his Ledger** (hardware wallet) and are **not sales**. The coins stay in the FIFO pool
  with their purchase price. Coins deposited back are matched against what went out (`off_wallet`); a small
  surplus becomes a zero-cost lot. The network fee (in coins) is consumed from the pool and recorded as a
  realized cost row "Network fee". Coins still on the Ledger show a note on the holding.
- Deposits that don't match an earlier withdrawal (coins bought elsewhere) have no purchase price: zero-cost
  lots with a warning.
- Staking rewards: zero-cost lots. For the tax, pre-2026 rewards use the 31-12-2025 price like any other
  pre-2026 unit; 2026 rewards count at zero cost.
- A sale that exceeds the known lots (rewards missing from the export) counts the shortfall at zero cost, with
  a warning.

## Prices

Current: `https://api.bitvavo.com/v2/ticker/price?market=BTC-EUR` → `{"market":"BTC-EUR","price":"74655"}`.
31-12-2025: hourly candles `https://api.bitvavo.com/v2/BTC-EUR/candles?interval=1h&start=..&end=..`
(`[[ts_ms, open, high, low, close, volume], ...]`, timestamp = candle start). The candle starting
2025-12-31 22:00 UTC (23:00 Brussels) closes at midnight: that close is the reference price.

## API (automatic sync, backlog item 12, D28)

Read-only key (Settings → API, permission *View* only, no Trade or Withdraw; IP whitelist recommended). `GET
/v2/account/history` (signed: HMAC-SHA256 of timestamp + method + path with query + body; headers
`Bitvavo-Access-Key/-Timestamp/-Signature/-Window`), 100 items per page, all types. The transaction ids are the same
UUIDs as the PDF's *Transaction ID* column (every row of a real export has one), so overlap is removed by id.
Not verified yet against the live API: whether a buy's `sentAmount` includes the fee and the PDF's time zone (assumed
Europe/Brussels). The first sync compares the API rows with the PDF rows of the same id and stores the fee rule it
finds (`<data dir>/sync/bitvavo/settings.json`).

## CSV export (backlog item 13)

Header: `Timezone, Date, Time, Type, Currency, Amount, Quote Currency, Quote Price, Received / Paid Currency,
Received / Paid Amount, Fee currency, Fee amount, Status, Transaction ID, Address`. `bitvavo.csv_rows()` turns it into
the PDF reader's row dicts (amount and unit joined, as printed in the PDF), so `parse_rows` is shared. Not checked
against a real CSV export yet (layout from Bitvavo's export as known; invented test file).
