---
name: backlog-next
description: Pick up the next item from BACKLOG.md (or the item number given), plan it, build it with tests and docs, and move it to Done. Use when Sander says to work on the backlog.
argument-hint: "[item number]"
disable-model-invocation: true
---

# Next backlog item

Item requested: $ARGUMENTS (empty = the first open item under `## Now`, then `## Next`, then `## Later`,
skipping items whose "needs N" dependency is not done yet).

1. **Read** the item in `BACKLOG.md`, the docs it touches (`docs/domain/*`, `docs/brokers/*`,
   `docs/decisions.md`) and the code involved. Check `private/notes/history.md` for earlier analyses on the
   same subject (for example the switch analysis for item 7).
2. **Plan** in at most ten lines: what changes where, new settings, open questions. If a tax rule or an input
   format is uncertain, or the item needs a file only Sander has (for example a DEGIRO *Rekeningoverzicht*),
   ask before building. Otherwise go ahead.
3. **Build**:
   - engine/parser logic first, with pytest tests on synthetic data (never real figures from `private/`);
   - then `report.py`, the CLI and the Streamlit app;
   - keep every rule in `CLAUDE.md` ("Rules that must hold").
4. **Document**: update the relevant `docs/` file; add a `docs/decisions.md` entry for any choice that
   shapes behaviour; update `README.md` if usage changed.
5. **Validate** with `/validate`. A regression change must be explained; update the baseline only when
   Sander agrees.
6. **Close**: move the item from its section to a `## Done` section at the bottom of `BACKLOG.md`
   (create it if missing) as `- N. Title · done YYYY-MM-DD · short note`. Commit with an imperative
   message that says why.
7. Tell Sander in a few lines what changed, how to try it (command or app tab), and anything left open.
