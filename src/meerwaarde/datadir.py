"""The app's data folder as one object, so the scheduled refresh (alerts.py) builds the same report as the app.

<data dir>/uploads/*.pdf, prices.csv, profile.json, manual.json, reynders.json, cache/, alerts.json, alerts/
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def default_data_dir() -> Path:
    return Path(os.environ.get("MEERWAARDE_DATA") or Path.home() / ".local" / "share" / "meerwaarde")


@dataclass
class DataDir:
    root: Path

    def __post_init__(self):
        self.root = Path(self.root).expanduser()

    uploads = property(lambda self: self.root / "uploads")
    prices_csv = property(lambda self: self.root / "prices.csv")
    profile = property(lambda self: self.root / "profile.json")
    manual = property(lambda self: self.root / "manual.json")
    reynders = property(lambda self: self.root / "reynders.json")
    cache = property(lambda self: self.root / "cache")
    alerts = property(lambda self: self.root / "alerts.json")
    alerts_dir = property(lambda self: self.root / "alerts")

    def pdfs(self) -> list[Path]:
        return sorted(self.uploads.glob("*.pdf"), key=lambda p: p.name.lower()) if self.uploads.exists() else []

    def build(self, fetch: bool = True, year: int | None = None, today: date | None = None):
        """The report the app shows with its default settings."""
        from .engine.reynders import ReyndersBook
        from .engine.tax import TaxRules
        from .manual import ManualBook
        from .parsers import parse_file
        from .profile import TaxProfile
        from .report import build_report
        parsed, warnings = [], []
        for p in self.pdfs():
            try:
                parsed.append(parse_file(p, filename=p.name.split("__", 1)[-1]))
            except Exception as e:  # one unreadable file must not stop the refresh
                warnings.append(f"{p.name}: {e}")
        profile = TaxProfile.load(self.profile)
        rules = TaxRules(year=year or (today or date.today()).year)
        rules.exemption = profile.exemption_rules().base_for(rules.year)
        rep = build_report([], fetch_prices=fetch, cache_dir=self.cache, rules=rules,
                           overrides_csv=self.prices_csv.read_text() if self.prices_csv.exists() else None,
                           today=today, parsed=parsed, profile=profile, manual=ManualBook.load(self.manual),
                           reynders=ReyndersBook.load(self.reynders))
        rep.warnings = warnings + rep.warnings
        return rep
