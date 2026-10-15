"""Scheduled refresh and alerts (backlog item 10, docs/decisions.md D26).

`meerwaarde refresh` (cron, Synology Task Scheduler, Windows Task Scheduler, or `--every` in a container) rebuilds the
report from the data folder with fresh prices, compares it with the previous run and notifies when:
- the tax-free room of a person drops below a threshold (once, when it crosses);
- a position's price moved more than a set percentage since the last alert for it (or since it was first seen);
- a price can't be found any more.

Settings in `<data dir>/alerts.json`; state and a log in `<data dir>/alerts/`. Secrets never go in the JSON: the SMTP
password and the ntfy token are read from environment variables. By default messages carry no amounts (an ntfy.sh
topic can be read by anyone who knows its name); `include_amounts` turns them on.
"""
from __future__ import annotations

import json
import os
import smtplib
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from .datadir import DataDir


@dataclass
class NtfySettings:
    url: str = ""                                   # https://ntfy.sh/<long-random-topic> or your own ntfy server
    token_env: str = "MEERWAARDE_NTFY_TOKEN"        # access token (optional), read from this environment variable


@dataclass
class EmailSettings:
    host: str = ""
    port: int = 587
    starttls: bool = True
    user: str = ""
    sender: str = ""
    to: str = ""                                    # comma-separated
    password_env: str = "MEERWAARDE_SMTP_PASSWORD"


@dataclass
class AlertSettings:
    room_below: float | None = 1000.0               # € of tax-free room left (per person); None = off
    move_pct: float | None = 10.0                   # % price move of a position; None = off
    missing_prices: bool = True
    include_amounts: bool = False
    ntfy: NtfySettings = field(default_factory=NtfySettings)
    email: EmailSettings = field(default_factory=EmailSettings)

    @property
    def channels(self) -> list[str]:
        return [c for c, on in (("ntfy", bool(self.ntfy.url)), ("email", bool(self.email.host and self.email.to))) if on]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "AlertSettings":
        d = dict(d or {})
        ntfy = NtfySettings(**{k: v for k, v in (d.pop("ntfy", None) or {}).items() if k in NtfySettings.__dataclass_fields__})
        email = EmailSettings(**{k: v for k, v in (d.pop("email", None) or {}).items() if k in EmailSettings.__dataclass_fields__})
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__}, ntfy=ntfy, email=email)

    @classmethod
    def load(cls, path) -> "AlertSettings":
        p = Path(path)
        return cls.from_dict(json.loads(p.read_text(encoding="utf-8") or "{}")) if p.exists() else cls()

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------- state
@dataclass
class Snapshot:
    """What the alerts compare against. `anchors`: price per position at its last alert (or first sighting)."""
    on: str = ""
    prices: dict = field(default_factory=dict)      # "broker|asset" -> EUR per unit
    names: dict = field(default_factory=dict)       # "broker|asset" -> name
    rooms: dict = field(default_factory=dict)       # person name -> tax-free room left (€)
    missing: list = field(default_factory=list)     # "broker|asset" without a current price
    anchors: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path) -> "Snapshot | None":
        p = Path(path)
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")


def snapshot(rep, on: date | None = None) -> Snapshot:
    s = Snapshot(on=(on or rep.tax.today).isoformat())
    for h in rep.portfolio.holdings():
        k = f"{h.broker}|{h.asset}"
        s.names[k] = h.name
        p = rep.prices.now(h)
        if p is None:
            s.missing.append(k)
        else:
            s.prices[k] = p.value
    people = rep.tax_summary(rep.sold_lines())["people"]
    s.rooms = {rep.household.name(pid): round(max(0.0, pp["room"]), 2) for pid, pp in people.items()}
    return s


@dataclass
class Alert:
    kind: str                  # room | move | missing
    key: str
    title: str
    text: str                  # without amounts
    detail: str = ""           # with amounts (only sent when include_amounts)

    def message(self, include_amounts: bool) -> str:
        return f"{self.text} {self.detail}".strip() if include_amounts and self.detail else self.text


def evaluate(prev: Snapshot | None, now: Snapshot, settings: AlertSettings) -> tuple[list[Alert], Snapshot]:
    """Alerts for `now` against `prev`; returns them and `now` with its anchors set."""
    alerts = []
    prev_anchors = dict(prev.anchors) if prev else {}
    now.anchors = {}
    if settings.room_below is not None:
        for person, room in now.rooms.items():
            before = prev.rooms.get(person) if prev else None
            if room < settings.room_below and (before is None or before >= settings.room_below):
                alerts.append(Alert("room", person, "Tax-free room is running low",
                                    f"{person}: the tax-free room for this year dropped below the threshold you set.",
                                    f"Left: €{room:,.0f} (threshold €{settings.room_below:,.0f})."))
    for k, price in now.prices.items():
        anchor = prev_anchors.get(k)
        if anchor is None:
            anchor = (prev.prices.get(k) if prev else None) or price
        if settings.move_pct is not None and anchor:
            change = (price / anchor - 1) * 100
            if abs(change) >= settings.move_pct:
                name = now.names.get(k, k)
                alerts.append(Alert("move", k, f"{name} moved {change:+.0f}%",
                                    f"{name} ({k.split('|')[0]}) moved {change:+.1f}% since the last alert.",
                                    f"Price €{anchor:,.4g} → €{price:,.4g}."))
                anchor = price
        now.anchors[k] = anchor
    if settings.missing_prices:
        before = set(prev.missing) if prev else set()
        for k in now.missing:
            if k not in before:
                name = now.names.get(k, k)
                alerts.append(Alert("missing", k, "Price not found",
                                    f"No current price for {name} ({k.split('|')[0]}): add one on the Prices tab."))
    return alerts, now


# ---------------------------------------------------------------- notifiers
def send_ntfy(settings: NtfySettings, title: str, message: str, post=None, env=os.environ) -> str | None:
    """Returns an error text, or None when sent."""
    if not settings.url:
        return "ntfy: no URL"
    if post is None:
        import requests
        post = requests.post
    headers = {"Title": title.encode("utf-8"), "Tags": "chart_with_upwards_trend"}
    token = env.get(settings.token_env or "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = post(settings.url, data=message.encode("utf-8"), headers=headers, timeout=15)
        code = getattr(r, "status_code", 200)
        return None if 200 <= code < 300 else f"ntfy: HTTP {code}"
    except Exception as e:  # network trouble must never break the refresh
        return f"ntfy: {e}"


def send_email(settings: EmailSettings, subject: str, body: str, smtp_factory=None, env=os.environ) -> str | None:
    if not (settings.host and settings.to):
        return "e-mail: no server or recipient"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.sender or settings.user
    msg["To"] = settings.to
    msg.set_content(body)
    factory = smtp_factory or smtplib.SMTP
    try:
        with factory(settings.host, int(settings.port), timeout=20) as s:
            if settings.starttls:
                s.starttls()
            password = env.get(settings.password_env or "")
            if settings.user and password:
                s.login(settings.user, password)
            s.send_message(msg)
        return None
    except Exception as e:
        return f"e-mail: {e}"


def notify(settings: AlertSettings, alerts: list[Alert], post=None, smtp_factory=None, env=os.environ) -> list[str]:
    """Send one message per channel with all alerts. Returns errors."""
    if not alerts:
        return []
    title = alerts[0].title if len(alerts) == 1 else f"meerwaarde: {len(alerts)} alerts"
    body = "\n".join(f"• {a.message(settings.include_amounts)}" for a in alerts)
    errors = []
    if "ntfy" in settings.channels:
        err = send_ntfy(settings.ntfy, title, body, post, env)
        errors += [err] if err else []
    if "email" in settings.channels:
        err = send_email(settings.email, title, body + "\n\nSent by meerwaarde refresh.", smtp_factory, env)
        errors += [err] if err else []
    return errors


# ---------------------------------------------------------------- one run
@dataclass
class RefreshResult:
    on: str
    alerts: list
    sent: bool
    errors: list
    price_errors: int
    positions: int


def refresh(data_dir, send: bool = True, fetch: bool = True, today: date | None = None, post=None, smtp_factory=None,
            env=os.environ) -> RefreshResult:
    """Rebuild with fresh prices, evaluate, notify, save the state and append to the log."""
    from .prices.fetch import Fetcher
    dd = DataDir(data_dir)
    settings = AlertSettings.load(dd.alerts)
    if fetch:
        Fetcher(cache_dir=dd.cache).clear_current()
    rep = dd.build(fetch=fetch, today=today)
    state = dd.alerts_dir / "state.json"
    alerts, snap = evaluate(Snapshot.load(state), snapshot(rep, today), settings)
    errors = notify(settings, alerts, post, smtp_factory, env) if send and settings.channels else []
    if send and alerts and not settings.channels:
        errors.append("no channel set up (ntfy URL or e-mail server): alerts only written to the log")
    snap.save(state)
    result = RefreshResult(snap.on, alerts, bool(send and alerts and settings.channels and not errors), errors,
                           len(rep.prices.errors), len(snap.prices) + len(snap.missing))
    log_entry = dict(at=datetime.now().isoformat(timespec="seconds"), on=snap.on, positions=result.positions,
                     price_errors=result.price_errors, sent=result.sent, errors=errors,
                     alerts=[dict(kind=a.kind, key=a.key, title=a.title, text=a.text) for a in alerts])
    dd.alerts_dir.mkdir(parents=True, exist_ok=True)
    with open(dd.alerts_dir / "log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    return result


def read_log(data_dir, last: int = 30) -> list[dict]:
    p = DataDir(data_dir).alerts_dir / "log.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-last:]
    return [json.loads(x) for x in reversed(lines) if x.strip()]


def run_every(data_dir, hours: float, send: bool = True, sleep=time.sleep, runs: int | None = None):
    """Keep refreshing every `hours` (for a container). `runs` limits the loop (tests)."""
    n = 0
    while runs is None or n < runs:
        try:
            r = refresh(data_dir, send=send)
            print(f"{datetime.now():%Y-%m-%d %H:%M} refresh: {r.positions} positions, {len(r.alerts)} alerts"
                  + (f", errors: {'; '.join(r.errors)}" if r.errors else ""), flush=True)
        except Exception as e:  # keep the loop alive; the next run may work
            print(f"{datetime.now():%Y-%m-%d %H:%M} refresh failed: {e}", flush=True)
        n += 1
        if runs is None or n < runs:
            sleep(hours * 3600)
