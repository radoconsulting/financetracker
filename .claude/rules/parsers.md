---
paths:
  - "src/meerwaarde/parsers/**"
  - "tests/test_parsers.py"
---

# Parser rules

- Every parser keeps a pure entry point (text, lines or rows in, transactions out) so tests never need a PDF.
  The file-reading wrapper stays thin.
- Each behaviour change gets a synthetic test in `tests/test_parsers.py`. Test data is invented: no real
  quantities, amounts, account numbers or dates from `private/`.
- Amounts go into `Transaction` in EUR. `gross_eur` excludes costs (the tax uses it); `fees_eur` holds costs;
  `total_eur` is what left or entered the account.
- LYNX/IBKR: EUR comes from the statement's own `Total in EUR` per (section, category, currency) block, not
  from exchange rates (docs/decisions.md D6). A repeated section header after a page break must not reset the
  current currency. Two-column pages are split when a 10pt header starts past 45% of the page width.
- Bitvavo: page-break continuation rows are merged; the currency unit is taken with a regex at the end of the
  cell (stripping digits once removed the "E" from "EUR").
- DEGIRO: bonds are quoted in %, detected by `qty × price / 100 ≈ gross`; name continuation lines are joined.
- `combine()` drops duplicates only across different files; identical rows inside one file (a reversal and a
  repost) are real.
- Read `docs/brokers/<broker>.md` before changing a parser and update it after.
- After any parser change run `python scripts/regression.py`: row counts per (broker, kind) are in the baseline.
