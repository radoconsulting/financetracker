# Prices

`PriceBook.now(holding)` and `PriceBook.reference(holding)` return `Price(value_eur, on, source, local, currency)`.

## Order of preference

1. User override (`prices.csv`: `asset,price_now,price_31dec`; the app's Prices tab writes it)
2. Price entered with a manual position (`manual.json`, `PriceBook.set_manual`; date as entered, else today; the
   31-12-2025 value dated 31-12-2025), source "Your entry (manual position)" (D24)
3. Broker statement (LYNX open-position closes; Mark-to-Market prior prices for 31-12-2025)
4. Online (`prices/fetch.py`), newest date wins between statement and online

## Sources

| Source | Used for | Endpoint | Verified |
|---|---|---|---|
| Bitvavo API | crypto now and at 31-12-2025 midnight | `/v2/ticker/price`, `/v2/{coin}-EUR/candles` | Yes, from Claude's environment (Oct 2026) |
| justETF (unofficial JSON) | ETF quote in EUR; history for 31-12-2025 | `/api/etfs/{isin}/quote`, `/api/etfs/{isin}/performance-chart` | Yes. Quote dates can lag on thinly traded listings (iShares $ Corp Bond showed a 3-week-old quote) |
| Yahoo Finance (unofficial) | shares (ISIN search → symbol → chart) | `query2…/v1/finance/search`, `query1…/v8/finance/chart/{sym}` | **No**: blocked from Claude's environment; should work from a normal PC with a browser User-Agent. Test first. |
| ECB | EUR exchange rates for any date | `eurofxref-hist.zip` | Daily XML verified; the zip parser is tested offline |

Other sites that worked by hand during the analysis but aren't wired in: stockanalysis.com (US closes),
statmuse.com (historical US closes), beursduivel.be (Euronext quotes, sometimes blocked).

## Daily history (performance, D27)

`PriceBook.history(asset, class, start, end)` returns a daily EUR series and its source: Bitvavo daily candles for
crypto (1440 per call; delisted coins answer 400 and fall back), justETF `performance-chart` for ETFs and funds, Yahoo
Finance daily closes for the rest (converted with ECB rates per day). Cached for a day (the key holds the end date).
Positions without a series are valued at their last trade price on or before the day, labelled approximate. Tried live
on 15 October 2026: most positions had a series (from all three sources; delisted coins didn't); the first run took about a
minute, later runs come from the cache.

## Fund composition (allocation, D30)

`Fetcher.justetf_profile(isin)` reads the justETF profile page (`/en/etf-profile.html?isin=…`, HTML) and
`parse_justetf_profile` takes the top 10 holdings (name, ISIN from the stock-profile link, weight), the 4 largest
countries and sectors with "Other", the number of holdings and the "As of" date. Cached for 7 days. If justETF changes
the markup, the parser returns None and the fund counts as "Unknown" with a note.

## Cache

`<data dir>/cache/now/` (6 h) and `<data dir>/cache/history/` (1 year; the ECB zip is refreshed daily).
The app's *Refresh prices* button empties `now/`.

## Prices behind the regression baseline

`private/prices.csv` holds them; where each 31-12-2025 value came from is listed in
`private/notes/validation.md`.
