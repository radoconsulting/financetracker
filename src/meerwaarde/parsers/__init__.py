"""Read broker exports and turn them into normalized transactions."""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from . import bitvavo, degiro, degiro_account, degiro_csv, ibkr_history, lynx, lynx_annual
from ..util import pdf_text

BROKERS = ("Bitvavo", "DEGIRO", "LYNX")


@dataclass
class ParseResult:
    broker: str
    transactions: list
    statement: object = None          # LYNX StatementInfo
    warnings: list = field(default_factory=list)
    filename: str = ""
    account: object = None              # DEGIRO AccountInfo (account statement)
    supplement: bool = False            # only counts where the main exports don't reach (LYNX Transaction History)
    overview: object = None             # LYNX Jaaroverzicht: a check for the tax report, no transactions (D32)


def _open(source):
    if isinstance(source, (bytes, bytearray)):
        return io.BytesIO(source)
    return source


def detect_broker(source) -> str | None:
    with pdfplumber.open(_open(source)) as pdf:
        first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    low = first.lower()
    if "bitvavo" in low:
        return "Bitvavo"
    if "degiro" in low or "transacties van" in low:
        return "DEGIRO"
    if "activity statement" in low or "lynx" in low or "interactive brokers" in low:
        return "LYNX"
    return None


def decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_csv(text: str, filename: str = "") -> ParseResult:
    """A CSV export, recognised by its header row. A format you mapped yourself wins over the built-in ones."""
    from . import mapped
    m = mapped.match(text)
    if m is not None:
        txns, warns = mapped.parse_text(text, m, filename)
        return ParseResult(m.broker, txns, None, warns, filename)
    if degiro_csv.is_degiro_csv(text):
        txns, info, warns = degiro_csv.parse_text(text, filename)
        res = ParseResult("DEGIRO", txns, None, warns, filename)
        if info is not None:
            res.account = info
            res.warnings = warns + [f"DEGIRO account statement {info.period_start} – {info.period_end} (CSV): {info.trades} trades"]
        return res
    if bitvavo.is_bitvavo_csv(text):
        txns, warns = bitvavo.parse_csv_text(text, filename)
        return ParseResult("Bitvavo", txns, None, warns, filename)
    raise ValueError(f"{filename or 'file'}: CSV not recognised (DEGIRO Transacties or Rekeningoverzicht, Bitvavo, or a "
                     "format you mapped on the Data tab)")


def parse_file(source, filename: str = "", broker: str | None = None) -> ParseResult:
    """Parse one export (PDF or CSV; path, file object or bytes). The broker is detected when not given."""
    if isinstance(source, (str, Path)):
        filename = filename or Path(source).name
        data = Path(source).read_bytes()
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = source.read()
    if not data.lstrip()[:5].startswith(b"%PDF"):
        return parse_csv(decode(data), filename)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        first_page = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if not first_page.strip() and len(pdf.pages) > 1:     # a cover page with only a logo (LYNX Jaaroverzicht)
            first_page = pdf.pages[1].extract_text() or ""
    if lynx_annual.is_annual_overview(first_page):
        ov = lynx_annual.parse(io.BytesIO(data), filename)
        return ParseResult("LYNX", [], None, [f"{filename}: LYNX Jaaroverzicht {ov.year}, used as a check in the tax "
                                              "report (its income is already in the statements)"], filename, overview=ov)
    if ibkr_history.is_transaction_history(first_page):
        txns, warns = ibkr_history.parse(io.BytesIO(data), filename)
        return ParseResult("LYNX", txns, None, warns, filename, supplement=True)
    broker = broker or detect_broker(data)
    if broker == "Bitvavo":
        txns, warns = bitvavo.parse(io.BytesIO(data), filename)
        return ParseResult("Bitvavo", txns, None, warns, filename)
    if broker == "DEGIRO":
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
        if degiro_account.is_account_statement(first):
            txns, info, warns = degiro_account.parse(io.BytesIO(data), filename)
            warns = warns + [f"DEGIRO account statement {info.period_start} – {info.period_end}: deposits "
                             f"€{info.deposits:,.2f}, withdrawals €{info.withdrawals:,.2f}, cash now €{info.cash_eur:,.2f} "
                             f"(DEGIRO and flatex account together), {info.trades} trades"]
            res = ParseResult("DEGIRO", txns, None, warns, filename)
            res.account = info
            return res
        txns, warns = degiro.parse_text(pdf_text(io.BytesIO(data)), filename)
        return ParseResult("DEGIRO", txns, None, warns, filename)
    if broker == "LYNX":
        txns, info, warns = lynx.parse(io.BytesIO(data), filename)
        return ParseResult("LYNX", txns, info, warns, filename)
    raise ValueError(f"{filename or 'file'}: not recognized as a Bitvavo, DEGIRO or LYNX export")


def _key(t):
    return (t.broker, t.kind, t.ts, t.asset, round(t.qty or 0, 8), round(t.gross_eur or 0, 2),
            round(t.amount_eur or 0, 2), t.price)


def _supplements(results: list[ParseResult]) -> tuple[list[ParseResult], list[str]]:
    """LYNX Transaction History files: symbols -> ISINs, and only the days after the newest Activity Statement (D31)."""
    main = [r for r in results if not r.supplement]
    known = {t.note: t.asset for r in main if r.broker == "LYNX" for t in r.transactions
             if t.note and len(t.asset) == 12 and t.kind in ("buy", "sell")}
    ends = [r.statement.period_end for r in main if r.statement is not None and r.statement.period_end]
    end = max(ends) if ends else None
    out, notes = [], []
    for r in results:
        if not r.supplement:
            out.append(r)
            continue
        notes += ibkr_history.fill_isins(r.transactions, known)
        kept = [t for t in r.transactions if end is None or t.ts.date() > end]
        if end is not None:
            notes.append(f"{r.filename}: {len(kept)} of {len(r.transactions)} rows used (after your newest LYNX statement, "
                         f"which ends {end:%d-%m-%Y})")
        out.append(ParseResult(r.broker, kept, None, r.warnings, r.filename, supplement=True))
    return out, notes


def combine(results: list[ParseResult]):
    """Merge parsed files: drop duplicate rows (overlapping exports) and keep the newest LYNX statement info."""
    results, supplement_notes = _supplements(results)
    seen, txns = {}, []
    for r in results:
        for t in r.transactions:
            k = _key(t)
            if k in seen and seen[k] != r.filename:
                continue      # the same row in an overlapping export
            seen.setdefault(k, r.filename)
            txns.append(t)
    statements = [r.statement for r in results if r.statement is not None]
    statements.sort(key=lambda s: (s.period_end or s.period_start or 0))
    warnings = [w for r in results for w in r.warnings] + supplement_notes
    return txns, statements, warnings
