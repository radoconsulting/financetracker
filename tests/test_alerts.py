"""Scheduled refresh and alerts (synthetic data, no network)."""
import json
from datetime import date

import pytest

from meerwaarde.alerts import (AlertSettings, EmailSettings, NtfySettings, Snapshot, evaluate, notify, read_log, refresh,
                               run_every, send_email, send_ntfy)
from meerwaarde.datadir import DataDir
from meerwaarde.manual import ManualBook, ManualPosition, ManualTrade

TODAY = date(2026, 10, 14)


def snap(prices=None, rooms=None, missing=(), anchors=None, names=None):
    prices = prices or {}
    return Snapshot("2026-10-14", dict(prices), names or {k: f"Fund {k}" for k in list(prices) + list(missing)},
                    dict(rooms or {}), list(missing), dict(anchors or {}))


def test_room_alert_only_when_it_crosses_the_threshold():
    s = AlertSettings(room_below=1000)
    alerts, _ = evaluate(snap(rooms={"You": 1500}), snap(rooms={"You": 800}), s)
    assert [a.kind for a in alerts] == ["room"]
    assert "800" not in alerts[0].text and "€800" in alerts[0].detail
    alerts, _ = evaluate(snap(rooms={"You": 800}), snap(rooms={"You": 700}), s)
    assert alerts == []                                       # already below: no daily repeat
    alerts, _ = evaluate(None, snap(rooms={"You": 500}), s)
    assert [a.kind for a in alerts] == ["room"]               # first run below the threshold


def test_move_alert_against_the_anchor_and_anchor_resets():
    s = AlertSettings(move_pct=10, room_below=None)
    first, now = evaluate(None, snap(prices={"B|X": 100.0}), s)
    assert first == [] and now.anchors == {"B|X": 100.0}
    alerts, now = evaluate(now, snap(prices={"B|X": 105.0}), s)
    assert alerts == [] and now.anchors["B|X"] == 100.0       # slow drift keeps the anchor
    alerts, now = evaluate(now, snap(prices={"B|X": 111.0}), s)
    assert [a.kind for a in alerts] == ["move"] and "+11" in alerts[0].title
    assert now.anchors["B|X"] == 111.0                         # alerted: new anchor
    alerts, now = evaluate(now, snap(prices={"B|X": 99.0}), s)
    assert [a.kind for a in alerts] == ["move"] and "-1" in alerts[0].title


def test_missing_price_alert_once():
    s = AlertSettings(room_below=None)
    alerts, now = evaluate(snap(prices={"B|X": 1.0}), snap(missing=["B|X"]), s)
    assert [a.kind for a in alerts] == ["missing"]
    alerts, _ = evaluate(now, snap(missing=["B|X"]), s)
    assert alerts == []


def test_messages_leave_amounts_out_unless_asked():
    alerts, _ = evaluate(None, snap(rooms={"You": 500}), AlertSettings())
    assert "€" not in alerts[0].message(False) and "€500" in alerts[0].message(True)


class Resp:
    def __init__(self, code):
        self.status_code = code


def test_ntfy_sends_title_body_and_token():
    calls = []

    def post(url, data, headers, timeout):
        calls.append((url, data, headers))
        return Resp(200)

    err = send_ntfy(NtfySettings("https://ntfy.example/topic"), "Fund moved +12%", "body €", post, {"MEERWAARDE_NTFY_TOKEN": "tk"})
    assert err is None
    url, data, headers = calls[0]
    assert url == "https://ntfy.example/topic" and data == "body €".encode() and headers["Authorization"] == "Bearer tk"
    assert send_ntfy(NtfySettings("https://x/t"), "t", "b", lambda *a, **k: Resp(403), {}) == "ntfy: HTTP 403"

    def boom(*a, **k):
        raise OSError("offline")
    assert "offline" in send_ntfy(NtfySettings("https://x/t"), "t", "b", boom, {})


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.logged = host, port, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, user, pw):
        self.logged = (user, pw)

    def send_message(self, msg):
        FakeSMTP.sent.append((msg["To"], msg["Subject"], msg.get_content(), self.logged))


def test_email_uses_the_password_from_the_environment():
    FakeSMTP.sent = []
    e = EmailSettings(host="smtp.example", user="me@example.org", to="me@example.org")
    assert send_email(e, "Subject", "Body", FakeSMTP, {"MEERWAARDE_SMTP_PASSWORD": "pw"}) is None
    to, subject, body, logged = FakeSMTP.sent[0]
    assert (to, subject, logged) == ("me@example.org", "Subject", ("me@example.org", "pw")) and "Body" in body


def test_notify_combines_alerts_per_channel():
    sent = []
    s = AlertSettings(ntfy=NtfySettings("https://x/t"))
    alerts, _ = evaluate(None, snap(rooms={"You": 1.0}, missing=["B|X"]), s)
    errs = notify(s, alerts, post=lambda url, data, headers, timeout: sent.append((headers["Title"], data)) or Resp(200))
    assert errs == [] and sent[0][0] == "meerwaarde: 2 alerts" and data_lines(sent[0][1]) == 2


def data_lines(b: bytes) -> int:
    return len(b.decode().splitlines())


def data_dir(tmp_path, price=110.0):
    dd = DataDir(tmp_path)
    ManualBook([ManualPosition("XX0000000009", "Fund", "Fund", "Bank", [ManualTrade(date(2026, 2, 1), 10, 100.0)],
                               price_now=price)]).save(dd.manual)
    return dd


def test_refresh_end_to_end_saves_state_and_log(tmp_path):
    dd = data_dir(tmp_path)
    AlertSettings(room_below=20000, move_pct=5, ntfy=NtfySettings("https://x/t")).save(dd.alerts)
    sent = []
    post = lambda url, data, headers, timeout: sent.append(data) or Resp(200)  # noqa: E731
    r = refresh(tmp_path, fetch=False, today=TODAY, post=post)
    assert [a.kind for a in r.alerts] == ["room"] and r.sent and len(sent) == 1
    state = json.loads((dd.alerts_dir / "state.json").read_text())
    assert state["prices"] == {"Bank|XX0000000009": 110.0}
    # price moves 10%: one move alert, the room alert isn't repeated
    data_dir(tmp_path, price=121.0)
    r = refresh(tmp_path, fetch=False, today=TODAY, post=post)
    assert [a.kind for a in r.alerts] == ["move"]
    log = read_log(tmp_path)
    assert len(log) == 2 and log[0]["alerts"][0]["kind"] == "move"


def test_refresh_without_channel_logs_and_says_so(tmp_path):
    data_dir(tmp_path)
    AlertSettings(room_below=20000).save(DataDir(tmp_path).alerts)
    r = refresh(tmp_path, fetch=False, today=TODAY)
    assert not r.sent and any("no channel" in e for e in r.errors)


def test_dry_run_sends_nothing(tmp_path):
    data_dir(tmp_path)
    AlertSettings(room_below=20000, ntfy=NtfySettings("https://x/t")).save(DataDir(tmp_path).alerts)
    r = refresh(tmp_path, send=False, fetch=False, today=TODAY, post=lambda *a, **k: pytest.fail("sent"))
    assert r.alerts and not r.sent


def test_settings_round_trip_and_no_secrets_in_json(tmp_path):
    s = AlertSettings(room_below=500, move_pct=None, include_amounts=True, ntfy=NtfySettings("https://x/t"),
                      email=EmailSettings(host="h", to="a@b.c"))
    s.save(tmp_path / "alerts.json")
    again = AlertSettings.load(tmp_path / "alerts.json")
    assert again == s and again.channels == ["ntfy", "email"]
    assert "password\"" not in (tmp_path / "alerts.json").read_text()


def test_run_every_survives_a_failing_run(tmp_path, capsys):
    (tmp_path / "alerts.json").write_text("{not json")
    run_every(tmp_path, 24, sleep=lambda s: None, runs=2)
    assert capsys.readouterr().out.count("refresh failed") == 2
