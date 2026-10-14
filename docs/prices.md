# Prices

`PriceBook.now(holding)` and `PriceBook.reference(holding)` return `Price(value_eur, on, source, local, currency)`.

## Order of preference

1. User override (`prices.csv`: `asset,price_now,price_31dec`; the app's Prices tab writes it)
2. Broker statement (LYNX open-position closes; Mark-to-Market prior prices for 31-12-2025)
3. Online (`prices/fetch.py`), newest date wins between statement and online

## Sources

| Source | Used for | Endpoint | Verified |
|---|---|---|---|
| Bitvavo API | crypto now and at 31-12-2025 midnight | `/v2/ticker/price`, `/v2/{coin}-EUR/candles` | Yes, from Claude's environment (Oct 2026) |
| justETF (unofficial JSON) | ETF quote in EUR; history for 31-12-2025 | `/api/etfs/{isin}/quote`, `/api/etfs/{isin}/performance-chart` | Yes. Quote dates can lag on thinly traded listings (iShares $ Corp Bond showed a 3-week-old quote) |
| Yahoo Finance (unofficial) | shares (ISIN search → symbol → chart) | `query2…/v1/finance/search`, `query1…/v8/finance/chart/{sym}` | **No**: blocked from Claude's environment; should work from a normal PC with a browser User-Agent. Test first. |
| ECB | EUR exchange rates for any date | `eurofxref-hist.zip` | Daily XML verified; the zip parser is tested offline |

Other sites that worked by hand during the analysis but aren't wired in: stockanalysis.com (US closes),
statmuse.com (historical US closes), beursduivel.be (Euronext quotes, sometimes blocked).

## Cache

`<data dir>/cache/now/` (6 h) and `<data dir>/cache/history/` (1 year; the ECB zip is refreshed daily).
The app's *Refresh prices* button empties `now/`.

## Prices behind the regression baseline

`private/prices.csv` holds them; where each 31-12-2025 value came from is listed in
`private/notes/validation.md`.
