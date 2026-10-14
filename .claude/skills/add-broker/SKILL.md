---
name: add-broker
description: Add a parser for a new broker or a new export format (PDF or CSV), with detection, tests and docs. Use when Sander provides an export from a broker or format the package doesn't read yet.
argument-hint: "<broker name> [path to sample export]"
disable-model-invocation: true
---

# Add a broker or export format

Broker / format: $ARGUMENTS

1. **Look at the export.** Sander's real files go in `private/exports/` (git-ignored). Open the sample and
   note: header row or layout, date and number formats (EU `1.234,56` vs US `1,234.56`), currency columns,
   how fees, FX costs, dividends, withholding, deposits and withdrawals appear, page breaks and continuation
   rows, and whether ISINs are present. Read `docs/brokers/*.md` for how the existing parsers solved similar
   problems.
2. **Write `src/meerwaarde/parsers/<broker>.py`:**
   - `BROKER = "<Name>"`;
   - a pure entry point that takes text, lines or rows (`parse_text(text, filename)` or
     `parse_rows(rows, filename)`) plus a thin `parse(source, filename)` that extracts from the file;
   - return `(transactions, warnings)` with `Transaction` objects from `models.py`: the right `kind`, amounts
     in EUR (`gross_eur` without costs, `fees_eur`, `total_eur`), `asset` = ISIN or ticker, `asset_class` via
     `classify.guess_class` unless the export says, `order` to keep same-timestamp rows stable, `source`
     = filename;
   - use `util.num_eu` / `num_us` / `parse_dt`; never guess a missing price; add a warning instead.
3. **Register it** in `parsers/__init__.py`: `BROKERS`, `detect_broker` (a phrase on page 1 or the CSV
   header), and `parse_file`. For CSV, also allow the extension in the app's `st.file_uploader(type=...)` in
   `app/streamlit_app.py`. Check that `combine()`'s dedupe key still makes sense for this format.
4. **Tests** in `tests/test_parsers.py`: synthetic text or rows written by hand (invented ISINs, amounts and
   dates; never Sander's data) covering a buy, a sell, fees, a foreign-currency trade, a dividend with
   withholding, a deposit, and any quirk found in step 1.
5. **Docs:** `docs/brokers/<broker>.md` with the format, quirks and how they're handled (no personal
   figures); a line in `README.md` under supported brokers; a `docs/decisions.md` entry if a choice was
   needed.
6. **Check on the real file:** `meerwaarde report private/exports/<file> --offline -v` and compare with the
   broker's own totals (realized result, cash balance, positions). Report the check to Sander.
7. Run `/validate`. If the new file is part of the regression set, the baseline changes: explain why and
   update it only when Sander agrees.
