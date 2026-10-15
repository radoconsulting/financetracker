"""Smoke test: the web app runs every tab without an exception (synthetic data, offline)."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "src" / "meerwaarde" / "app" / "streamlit_app.py"


def _run(tmp_path, monkeypatch):
    monkeypatch.setenv("MEERWAARDE_DATA", str(tmp_path))
    monkeypatch.setenv("MEERWAARDE_OFFLINE", "1")
    monkeypatch.delenv("MEERWAARDE_SECRETS", raising=False)
    return AppTest.from_file(str(APP), default_timeout=180).run()


def test_app_starts_empty(tmp_path, monkeypatch):
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]


def test_app_runs_every_tab_with_data(tmp_path, monkeypatch):
    from test_parsers import BITVAVO_CSV, DEGIRO_ACC, DEGIRO_TX_NL
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "Transactions.csv").write_text(DEGIRO_TX_NL, encoding="utf-8")
    (inbox / "Account.csv").write_text(DEGIRO_ACC, encoding="utf-8")
    (inbox / "bitvavo.csv").write_text(BITVAVO_CSV, encoding="utf-8")
    (tmp_path / "prices.csv").write_text("asset,price_now,price_31dec\nXX0000000001,120,100\nXX0000000002,25,20\n"
                                         "XX0000000003,98,97\nBTC,50000,40000\nETH,3000,2500\n")
    at = _run(tmp_path, monkeypatch)
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.tabs) >= 14
