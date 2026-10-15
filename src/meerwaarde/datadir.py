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
    inbox = property(lambda self: self.root / "inbox")
    csv_formats = property(lambda self: self.root / "csv_formats.json")

    def exports(self) -> list[Path]:
        """Uploaded exports and the files in the inbox folder (PDF and CSV)."""
        found = [p for folder in (self.uploads, self.inbox) if folder.exists() for ext in ("*.pdf", "*.csv")
                 for p in folder.glob(ext)]
        return sorted(found, key=lambda p: (p.parent != self.uploads, p.name.lower()))

    def pdfs(self) -> list[Path]:          # kept for callers from before CSV support
        return self.exports()

    def build(self, fetch: bool = True, year: int | None = None, today: date | None = None):
        """The report the app shows with its default settings."""
        from .engine.reynders import ReyndersBook
        from .engine.tax import TaxRules
        from .manual import ManualBook
        from .parsers import parse_file
        from .profile import TaxProfile
        from .report import build_report
        from .parsers import mapped
        from .sync import SyncStore
        mapped.load(self.csv_formats)
        parsed, warnings = [], []
        for p in self.exports():
            try:
                parsed.append(parse_file(p, filename=p.name.split("__", 1)[-1]))
            except Exception as e:  # one unreadable file must not stop the refresh
                warnings.append(f"{p.name}: {e}")
        synced = SyncStore(self.root).load()
        warnings += synced.warnings
        profile = TaxProfile.load(self.profile)
        rules = TaxRules(year=year or (today or date.today()).year)
        rules.exemption = profile.exemption_rules().base_for(rules.year)
        rep = build_report([], fetch_prices=fetch, cache_dir=self.cache, rules=rules,
                           overrides_csv=self.prices_csv.read_text() if self.prices_csv.exists() else None,
                           today=today, parsed=parsed, profile=profile, manual=ManualBook.load(self.manual),
                           reynders=ReyndersBook.load(self.reynders), synced=synced.transactions)
        rep.warnings = warnings + rep.warnings
        return rep
