# meerwaarde

Python package + Streamlit app that reads broker exports (Bitvavo, DEGIRO, LYNX), computes profit and loss
(FIFO, after costs), and the Belgian **meerwaardebelasting** (10% capital gains tax on financial assets from
1 January 2026), with a simulator for how much can be sold before the yearly exemption is used up.

Owner: Sander (Belgian tax resident). It runs locally or in Docker on his NAS. Not tax advice: the app says so.

## Commands

```bash
pip install -e ".[app,dev]"          # editable install with web app and tests
pytest                               # unit tests (synthetic data only)
python scripts/regression.py         # compare against the baseline from the real exports in private/
meerwaarde app                       # web app on http://localhost:8501 (data in ~/.local/share/meerwaarde)
meerwaarde report private/exports/*.pdf --offline --prices private/prices.csv --today 2026-10-14 -v
```

Run `pytest` and `python scripts/regression.py` before every commit. If the regression changes on purpose,
explain why in the commit message and regenerate the baseline with `python scripts/regression.py --update`.

## Layout

| Path | What |
|---|---|
| `src/meerwaarde/models.py` | Transaction, Lot, RealizedRow, Holding, IncomeRow; transaction kinds; `law_type()` |
| `src/meerwaarde/parsers/` | `bitvavo.py` (pdfplumber tables), `degiro.py` (layout text + regex), `lynx.py` (IBKR statement, column-aware), `classify.py` (asset class guesses), `__init__.py` (detect, parse_file, combine) |
| `src/meerwaarde/engine/portfolio.py` | FIFO lots per (broker, asset); own-wallet transfers; ISIN conversions; bond redemption at par; foreign cash from LYNX statements |
| `src/meerwaarde/engine/tax.py` | TaxRules, unit_acquisition, gain_for, max_without_tax, TaxCalculator |
| `src/meerwaarde/prices/` | PriceBook (override > statement > online) and `fetch.py` (Bitvavo, justETF, Yahoo, ECB; disk cache) |
| `src/meerwaarde/report.py` | pandas tables for CLI and app; `build_report()` |
| `src/meerwaarde/app/streamlit_app.py` | the web app (tabs: Overview, Positions, Meerwaardebelasting, Simulator, Prices, Data) |
| `src/meerwaarde/cli.py` | `meerwaarde report` and `meerwaarde app` |
| `tests/` | pytest, synthetic data only |
| `scripts/regression.py` | end-to-end check on `private/` |
| `docs/` | domain knowledge and decisions: read the relevant file before changing behaviour |
| `private/` | Sander's real exports, prices and old artifacts. Git-ignored. Never commit, never copy into tests. |
| `BACKLOG.md` | agreed features, ordered Now / Next / Later |

## Docs to read before changing things

- Tax rules and their sources: `docs/domain/meerwaardebelasting.md`
- Other Belgian taxes (stock exchange tax, dividend tax, Reynders, NBB reporting): `docs/domain/belgian-taxes.md`
- Broker export formats and quirks: `docs/brokers/bitvavo.md`, `docs/brokers/degiro.md`, `docs/brokers/lynx.md`
- Price sources and what was verified: `docs/prices.md`
- Why things work the way they do: `docs/decisions.md`
- Expected figures from the real exports: `private/notes/validation.md`
- What was done before this repo existed (analyses, dashboard, calculator, ETF switch, his positions):
  `private/notes/history.md`

## Rules that must hold

- Money is EUR. Purchase cost for P&L includes costs; the tax uses prices **without** costs (`gross_eur`).
- FIFO per (broker, asset). Same ISIN at two brokers = two pools.
- Crypto withdrawals are moves to Sander's own wallet (Ledger) by default, not sales. Coins coming back are
  matched to what went out. Option: `PortfolioOptions.own_wallet_withdrawals`.
- Tax: units held on or before 31-12-2025 use the 31-12-2025 price; a higher original price may be used until
  31-12-2030 but only to reduce a gain, never to create a loss. Units bought later use their purchase price.
- All law asset types (financial instruments, insurance, crypto, currency, gold) are one regime: losses offset
  gains in the same year; no carry-over of losses. Exemption €10,000 (2026), per person.
- Every price shown must carry its date and source. A user override always wins.
- A parser change needs a synthetic test in `tests/test_parsers.py` (text in, transactions out); parsers keep
  a text/lines entry point (`degiro.parse_text`, `lynx.parse_lines`) so tests don't need PDFs.
- Network failures must never break a report: price lookups return None and record an error.
- No personal data in the repo outside `private/`: no account numbers, quantities, amounts or lists of
  holdings in code, tests or `docs/` (an instrument name as the example of a format quirk is fine). The repo
  may be pushed to GitHub; `private/` never is.

## Conventions

- Python ≥ 3.10, type hints, dataclasses, pandas only in `report.py` and the app.
- User-facing text in plain English, with Dutch legal terms where the law uses them (meerwaardebelasting,
  andere financiële activa, beurstaks / stock exchange tax). Euro amounts formatted `€1,234`.
- Commit messages: imperative, explain the why; mention regression changes.
- Streamlit ≥ 1.50: use `width="stretch"`, not `use_container_width`.

## Working style

- Sander is a data engineer (dbt, SQL, Microsoft Fabric, Python notebooks): technical depth is welcome,
  skip basics. He writes in English or Dutch; answer in the language of his message.
- For backlog work: plan briefly, implement with tests, update `docs/` and move the item to `## Done` in
  `BACKLOG.md`.
- When a tax rule is uncertain, say so, cite the source, make it a setting where reasonable, and record the
  choice in `docs/decisions.md`.

Skills in this repo: `/validate` (tests + regression), `/backlog-next [n]` (pick up the next backlog item),
`/add-broker <name>` (steps for a new broker parser). Path rules in `.claude/rules/` load when you touch
parsers or the tax/price engines.

Not verified yet (no network or Docker in the environment where this was built): the live price fetchers
(Yahoo especially) and `docker build`. Test both early on a normal PC.
