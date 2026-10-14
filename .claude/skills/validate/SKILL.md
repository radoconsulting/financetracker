---
name: validate
description: Run the unit tests and the end-to-end regression on Sander's real exports, and explain any difference. Use before every commit and after any change to parsers, engines or prices.
allowed-tools: Bash(pytest*), Bash(python -m pytest*), Bash(python scripts/regression.py*), Bash(python3*), Read, Grep, Glob
---

# Validate

1. Run `python -m pytest -q`. Every test must pass.
2. Run `python scripts/regression.py`.
   - It runs offline on `private/exports/*.pdf` with `private/prices.csv` and value date 2026-10-14, and
     compares against `private/baseline.json` (tolerance €0.05).
   - If `private/exports/` is empty or missing, say so and stop after step 1: the regression needs
     Sander's own exports, which are not in git.
3. Report back in a few lines:
   - tests: passed / failed (name the failures);
   - regression: the `OK` line, or each difference.
4. For every regression difference, find the cause before proposing anything:
   - Which broker and which metric moved (summary, realized_by_year, tax, max_without_tax, rows).
   - Use `meerwaarde report private/exports/*.pdf --offline --prices private/prices.csv --today 2026-10-14 -v --out <scratch dir>`
     and compare the CSVs with `private/notes/validation.md` to locate the rows.
   - Say whether the change is intended (a fix or a new feature) or a regression.
5. Only if the change is intended **and** Sander agrees: `python scripts/regression.py --update`, update
   the tables in `private/notes/validation.md`, and explain the change in the commit message.

Never copy figures from `private/` into tests, docs or commit messages beyond the totals that moved.
