"""Fund facts the exports don't contain: running cost (TER), distribution policy and the Belgian stock exchange tax.

The catalog holds funds that came up for Sander, checked on justETF and curvo.eu on 14 October 2026. Everything here
can be overridden in the app; Gemini suggestions (optional) are added as unverified. The rate a broker actually
charged on earlier trades wins over the catalog (`engine.harvest.observed_tob_rate`): ISAC turned out 1.32%.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CHECKED = "2026-10-14"


@dataclass
class Fund:
    isin: str
    name: str
    ticker: str = ""
    index: str = ""
    ter: float | None = None              # yearly running cost, fraction (0.0007 = 0.07%)
    accumulating: bool | None = None
    tob_rate: float | None = None         # Belgian stock exchange tax per trade, fraction
    tob_note: str = ""
    size_eur_m: float | None = None
    source: str = ""                      # where the facts come from
    verified: bool = True                 # False for suggestions nobody checked yet
    reason: str = ""                      # why it was suggested
    links: list = field(default_factory=list)

    def as_row(self) -> dict:
        return dict(isin=self.isin, name=self.name, ticker=self.ticker, index=self.index,
                    ter_pct=None if self.ter is None else round(self.ter * 100, 3),
                    accumulating=self.accumulating, tob_pct=None if self.tob_rate is None else round(self.tob_rate * 100, 2),
                    size_eur_m=self.size_eur_m, source=self.source, verified=self.verified, reason=self.reason)


_J = "justETF, " + CHECKED
CATALOG: dict[str, Fund] = {f.isin: f for f in [
    # common distributing ETFs: natural candidates to switch from
    Fund("IE0031442068", "iShares Core S&P 500 UCITS ETF USD (Dist)", "IUSA", "S&P 500", 0.0007, False, 0.0012,
         "distributing: 0.12%", 20694, _J),
    # world / all-country accumulating candidates
    Fund("IE00B44Z5B48", "SPDR MSCI All Country World UCITS ETF (Acc)", "SPYY", "MSCI ACWI", 0.0012, True, 0.0012,
         "not registered in Belgium (earlier research): 0.12%, verify", 18242, _J, verified=False),
    Fund("IE0003XJA0J9", "Amundi Prime All Country World UCITS ETF Acc", "WEBN", "Solactive GBS Global Markets L&M Cap",
         0.0007, True, 0.0012, "0.12% per earlier research, verify", 3288, _J, verified=False),
    Fund("IE00B3YLTY66", "SPDR MSCI ACWI IMI UCITS ETF (Acc)", "IMIE", "MSCI ACWI IMI", 0.0017, True, 0.0012,
         "0.12% (curvo.eu, Aug 2026)", 8306, _J),
    Fund("IE00B6R52259", "iShares MSCI ACWI UCITS ETF USD (Acc)", "ISAC", "MSCI ACWI", 0.0020, True, 0.0132,
         "1.32%: charged by DEGIRO on 12-10-2026", 32131, _J),
    Fund("IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF (USD) Acc", "VWCE", "FTSE All-World", 0.0014, True, 0.0132,
         "registered in Belgium: 1.32% (curvo.eu)", 55439, _J),
    Fund("IE00B4L5Y983", "iShares Core MSCI World UCITS ETF USD (Acc)", "IWDA", "MSCI World", 0.0020, True, 0.0012,
         "0.12% (curvo.eu)", 127000, "curvo.eu, Aug 2026"),
    Fund("IE00BFY0GT14", "SPDR MSCI World UCITS ETF (Acc)", "SWRD", "MSCI World", 0.0012, True, 0.0012,
         "0.12% (curvo.eu; also charged by DEGIRO)", 17700, "curvo.eu, Aug 2026"),
    Fund("IE00B5BMR087", "iShares Core S&P 500 UCITS ETF USD (Acc)", "CSPX", "S&P 500", 0.0007, True, 0.0012,
         "0.12% (docs/domain/belgian-taxes.md)", None, "iShares (TER); not re-checked", verified=False,
         reason="accumulating twin of IUSA"),
]}
WORLD_TARGETS = ("IE00B44Z5B48", "IE0003XJA0J9", "IE00B3YLTY66", "IE00B6R52259", "IE00BK5BQT80")

_ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def valid_isin(isin: str) -> bool:
    """Format and check digit (Luhn on the letters-as-numbers string)."""
    s = (isin or "").strip().upper()
    if not _ISIN.match(s):
        return False
    digits = "".join(str(int(c, 36)) for c in s[:-1])
    total, double = 0, True
    for ch in reversed(digits):
        d = int(ch) * (2 if double else 1)
        total += d - 9 if d > 9 else d
        double = not double
    return (10 - total % 10) % 10 == int(s[-1])


def lookup(isin: str) -> Fund | None:
    return CATALOG.get((isin or "").upper())
