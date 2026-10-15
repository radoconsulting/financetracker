# meerwaarde

Profit and loss across **Bitvavo**, **DEGIRO** and **LYNX** exports, plus the Belgian
**meerwaardebelasting** (the 10% capital gains tax on financial assets from 2026), with a simulator
that tells you how much you can sell before you go over the yearly exemption.

Everything runs on your own machine. Your exports never leave it; only price lookups go online
(and you can switch those off).

## What it does

- **Reads your exports** (PDF): the Bitvavo transaction overview, DEGIRO *Transacties*, and LYNX
  activity statements (one per year plus the current year to date). The broker is detected automatically.
- **Profit and loss** per broker and per position: realized (first in, first out, after costs),
  unrealized at the latest price, and dividends and interest.
- **Meerwaardebelasting** for the tax year: what you already sold this year plus what you would pay if
  you sold everything today, per asset type as the law defines them.
- **Simulator**: enter quantities to sell (and, if you like, a different price) and see the taxable gain,
  the tax-free room left, and for every position the largest quantity you can still sell without tax.
- **Excel export**: one workbook with a summary, open positions, sales, income, the meerwaardebelasting per
  line, the exemption per year, the tax on dividends and interest, your current simulation, prices and a "How
  calculated" sheet. Totals are formulas, so changing the exemption, the rate or a price in Excel updates them.
- **Exemption over the years and a partner**: unused exemption carries forward (at most €1,000 a year, for 5 years,
  up to €15,000). Add a partner and split each broker (or position) between you: each person gets their own
  exemption and tax. Sales and income elsewhere, and the figures you declared, go on the **Tax years** tab.
- **Dividends and interest**: per year, person and broker, with foreign tax, the dividend exemption (shares only)
  and the 30% Belgian tax to declare, because foreign brokers don't withhold it.
- **Year-end harvesting**: which lots to sell and buy back before 31 December to use the rest of the exemption,
  with the tax saved later against the costs now (stock exchange tax at the rate your broker charged before,
  commissions, spread), and which positions at a loss could offset gains when tax is due. One click puts the
  proposal in the simulator. Command line: `meerwaarde harvest *.pdf`.
- **Switch planner**: sell positions (by default your distributing ETFs) for a target fund and compare targets on
  the cost now (meerwaardebelasting, stock exchange tax at the rate your broker really charged, commissions, spread),
  the yearly effect (dividend tax no longer paid, running costs), break-even and the net over a horizon, plus a plan
  that spreads the sales over the years within the exemption. Optionally, with your own **Gemini API key**, it
  suggests cheaper funds with similar content (names and ISINs only are sent; suggestions are marked unverified).
  Command line: `meerwaarde switch *.pdf [--suggest]`.
- **Manual positions**: funds at a bank, tak 21/23 policies, savings and investment gold that no export covers.
  Enter purchases and sales, a current price and the 31-12-2025 value on the **Manual positions** tab (or in a JSON
  file for `--manual`); they count in profit and in the meerwaardebelasting under the right asset type.
- **Reynders tax** on bond funds and bond ETFs: 30% on the interest part when you sell, from the TIS values the fund
  provider publishes (or the fund's bond share); that part is taken out of the 10%. Enter the funds on the
  Meerwaardebelasting tab (*Reynders tax* panel) or in a JSON file for `--reynders`; the simulator, switch planner and
  yearly report include it.
- **Performance against a benchmark**: money-weighted (XIRR) and time-weighted return per broker and in total, a
  chart of your value and net money in over time, and the same money invested on the same days in a benchmark fund
  (default iShares MSCI ACWI). **Performance** tab, or `meerwaarde performance *.pdf [--benchmark ISIN]`.
- **Scheduled refresh and alerts**: `meerwaarde refresh` fetches fresh prices for the app's data folder and sends a
  message (ntfy push or e-mail) when the tax-free room drops below your threshold, a position moves more than a set
  percentage, or a price can't be found. Settings on the **Alerts** tab; schedule it with cron, the NAS task
  scheduler or `--every 24` in a container.
- **Yearly tax report** (Excel and PDF): meerwaardebelasting per person, dividends and interest, stock exchange
  tax per broker, and a checklist including reporting your foreign accounts to the National Bank.

### Rules it applies

| Topic | Rule |
|---|---|
| Rate and exemption | 10% on the net gain of the year above €10,000 per person (2026). From 2027 unused exemption can add up to €1,000 a year, to at most €15,000; set the amount in the app. |
| Held before 2026 | Purchase value = closing price of 31-12-2025. If you originally paid more, that price applies instead for sales until the end of 2030, but only to reduce a gain; a loss is measured from the 31-12-2025 price. |
| Bought from 2026 | Purchase value = purchase price. First in, first out per broker. Commissions and stock exchange tax are left out. |
| Asset types | Financial instruments, insurance contracts, crypto-assets, currency and investment gold form one regime: losses offset gains within the year, and don't carry over. |
| Exemption over the years | Unused exemption adds at most €1,000 a year to the following years, each for 5 years (so at most €15,000); carried amounts are used first. Per person; partners split jointly owned assets. |
| Dividends and interest | 30% on the amount after foreign tax; dividends from shares are exempt up to €833 per person (2024–2026); fund and ETF distributions and interest are not. |
| Not included | The 33% regime for internal capital gains, holdings of 20% or more in a company, Reynders tax on bond funds, and income tax on staking rewards (units received in the tax year without a price count at zero cost). |

### How the exports are interpreted

- **Bitvavo**: crypto withdrawals are moves to your own wallet (for example a Ledger), not sales; the coins
  keep their purchase price and coins coming back are matched to what went out. Staking and other credits are
  free units. Coins deposited from elsewhere have no purchase price and count at zero cost (you get a note).
- **DEGIRO**: a same-day swap of one ISIN into another at the same value (such as Royal Dutch Shell A into
  Shell plc) is a conversion, not a sale. A bond "sold" at 0 on its maturity date is counted as repaid at 100%.
  The *Transacties* export has no dividends and leaves out the stock exchange tax; the account statement
  (*Rekeningoverzicht*) supplies both, and the real payout of a bond at maturity.
- **LYNX**: trades, dividends, interest, withholding tax, deposits, currency results and open positions are
  read from the activity statements. Amounts in other currencies are converted with the statement's own EUR
  totals. The statement that starts on 1 January 2026 also supplies the 31-12-2025 prices and exchange rate.

### Where prices come from

For every position the app shows the price, its date and its source. In order of preference:

1. **Your own price** (Prices tab, or a CSV with `asset,price_now,price_31dec`)
2. **The price entered with a manual position** (with the date you give it)
3. **Broker statements** (LYNX closing prices and 31-12-2025 prices)
4. **Online**: Bitvavo for crypto (current price, and the price at midnight on 31-12-2025), justETF for ETFs,
   Yahoo Finance for shares (unofficial), the ECB for exchange rates

Online prices are cached in `<data folder>/cache` (current prices for 6 hours). Use **Refresh prices** in
the app to fetch them again.

## Install

Python 3.10 or newer.

```bash
pip install ".[app]"          # from this folder; adds the web app
meerwaarde app                # opens http://localhost:8501
```

Uploads and your own prices are kept in `~/.local/share/meerwaarde` (change it with `--data-dir`).

### Command line

```bash
meerwaarde report Volledige_geschiedenis.pdf Transactions.pdf LYNX_2023.pdf LYNX_2024.pdf LYNX_2025.pdf LYNX_2026.pdf \
    --out results/            # writes realized.csv, holdings.csv, income.csv, cash.csv, meerwaardebelasting.csv, summary.csv
```

Options: `--xlsx [FILE]` for the Excel workbook (default `meerwaarde-<date>.xlsx`, in the `--out` folder when
given), `--prices my_prices.csv`, `--reynders reynders.json` (bond funds subject to the Reynders tax, format in `src/meerwaarde/engine/reynders.py`),
`--manual manual.json` (positions no export covers; the app's `manual.json`, format
in `src/meerwaarde/manual.py`; it can be used without any PDF), `--offline`, `--year 2027`, `--exemption 11000`, `--no-original-price`,
`--withdrawals-are-sales`, `-v` for all notes.

### Yearly tax report

```bash
meerwaarde tax-report *.pdf --year 2026 --profile profile.json     # prints it and writes tax-report-2026.xlsx and .pdf
```

`--xlsx [FILE]` or `--pdf [FILE]` writes only one of the two. In the app: the **Tax report** tab. The profile
(partner, ownership, other sales and income, exemption amounts per year) is the app's `profile.json`; without it,
everything belongs to one person.

### Excel workbook

In the app: **Export → Excel workbook** in the sidebar (it includes the simulation on the Simulator tab).
On the command line: `--xlsx`. In a notebook: `from meerwaarde.export import to_xlsx; to_xlsx(rep, "out.xlsx")`.

| Sheet | What |
|---|---|
| Summary | Profit per broker and per year, meerwaardebelasting if you sold everything; exemption and rate can be changed |
| Positions, Sales, Income | The app's tables, with filters and frozen headers |
| Meerwaardebelasting | Every sale this year and every open position: purchase value used and taxable gain |
| Simulator | The simulated sales, other sales (editable) and the largest quantity per position without tax |
| Prices | Current and 31-12-2025 prices with date and source |
| How calculated | Rules, settings, files read and notes about your data |

Results of the calculation (first-in-first-out costs, taxable gains) are values; arithmetic on them and every
total is a formula. Excel recalculates when the file opens.

### In a notebook

```python
from meerwaarde.report import build_report

rep = build_report(["Volledige_geschiedenis.pdf", "Transactions.pdf", "LYNX_2026.pdf"])
rep.summary()          # profit per broker (pandas DataFrame)
rep.holdings()         # open positions with price, date and source
rep.tax_lines()        # meerwaardebelasting per position
```

### Docker (for example on a NAS)

```bash
docker build -t meerwaarde .
docker run -d --name meerwaarde -p 8501:8501 -v /volume1/docker/meerwaarde:/data meerwaarde
```

The app has no login. Only expose it on your own network.

Scheduled refresh next to the app, with docker compose (the token and password stay in the environment, never in
`alerts.json`):

```yaml
services:
  meerwaarde:
    image: meerwaarde
    ports: ["8501:8501"]
    volumes: ["/volume1/docker/meerwaarde:/data"]
  meerwaarde-refresh:
    image: meerwaarde
    command: ["meerwaarde", "refresh", "--data-dir", "/data", "--every", "24"]
    volumes: ["/volume1/docker/meerwaarde:/data"]
    environment:
      MEERWAARDE_NTFY_TOKEN: ${MEERWAARDE_NTFY_TOKEN:-}
      MEERWAARDE_SMTP_PASSWORD: ${MEERWAARDE_SMTP_PASSWORD:-}
    healthcheck: {disable: true}
```

Or keep one container and schedule `docker exec meerwaarde meerwaarde refresh --data-dir /data` daily in the NAS
task scheduler.

### Alerts

```bash
meerwaarde refresh --dry-run     # what would be sent now; sends nothing, changes nothing
meerwaarde refresh --test        # a test message on the channels set up on the Alerts tab
meerwaarde refresh               # refresh, send, remember the state (run it daily)
```

Messages leave amounts out unless you switch *Include amounts* on: a topic on the public ntfy.sh server can be read
by anyone who knows its name, so use a long random topic or your own ntfy server with a token.

## Exporting from the brokers

| Broker | Export |
|---|---|
| Bitvavo | The full transaction overview (*Transactieoverzicht*, all dates), as PDF. |
| DEGIRO | *Transacties* **and** the *Rekeningoverzicht* (account statement), both for the whole period since your first deposit, as PDF. The account statement adds dividends, the tax withheld on them, the Belgian stock exchange tax, interest and deposits. |
| LYNX | The *Activity Statement* PDF for every year you had the account, plus the current year to date. |

Uploading an overlapping export again is safe: rows that appear in two files are counted once.

## Tests

```bash
pip install ".[dev]"
pytest
```

The tests use made-up data only.

## Disclaimer

This is a calculation tool, not tax advice. Check the results before you rely on them in a tax return.
