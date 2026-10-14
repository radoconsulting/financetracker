---
paths:
  - "src/meerwaarde/engine/**"
  - "src/meerwaarde/prices/**"
  - "tests/test_tax.py"
  - "tests/test_portfolio.py"
---

# Tax, portfolio and price rules

- Read `docs/domain/meerwaardebelasting.md` before changing `engine/tax.py`. Every rule there has a source
  and a code location; keep both in sync.
- `unit_acquisition` is the single place where the purchase value for the tax is decided:
  - held on or before 31-12-2025 → the 31-12-2025 price; the original price (without costs) may replace it
    until 31-12-2030 only when it is higher **and** only up to the sale price (it reduces a gain, never
    creates a loss);
  - bought later → purchase price without costs;
  - unknown purchase price (rewards, deposits from elsewhere) → zero, with a warning.
- FIFO per (broker, asset) for both P&L and tax. Do not switch to average cost without a decision entry.
- All law types share one regime and one exemption: losses offset gains within the same year; no loss
  carry-over. The exemption is per person; carry-forward is backlog item 4.
- New tax parameters go into `TaxRules` with the 2026 value as default, never hard-coded in the engine.
- `max_without_tax` must stay exact for piecewise-linear FIFO gains; test edge cases (a lot at a loss, a lot
  with an unknown cost, a sale price between the original and the reference price).
- Prices: override > statement > online; every `Price` has a date and a source. Fetchers must catch network
  errors, return None and add to `PriceBook.errors`. Parse functions stay separate from the HTTP call so they
  can be tested offline with stored samples.
- One price per ISIN across brokers (D8).
- Uncertain rule? Say so, cite the source, make it a setting, add a `docs/decisions.md` entry.
