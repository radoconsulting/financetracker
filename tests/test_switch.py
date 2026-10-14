"""Switch planner and the optional Gemini suggestions (synthetic data, no network)."""
from datetime import date, datetime

import pytest

from meerwaarde.engine.harvest import CostModel
from meerwaarde.engine.portfolio import Portfolio
from meerwaarde.engine.switch import (compare, default_sources, dividends_per_unit, net_spread, source_fund, sources_for,
                                      spread_plan)
from meerwaarde.engine.tax import TaxCalculator, TaxRules
from meerwaarde.funds import CATALOG, Fund, valid_isin
from meerwaarde.models import BUY, DIVIDEND, TOB, Transaction
from meerwaarde.prices import PriceBook
from meerwaarde.report import Report
from meerwaarde import suggest as gem

TODAY = date(2026, 10, 14)
DIST = "IE0031442068"            # a distributing S&P 500 ETF from the catalog (public fund facts)
WEBN, ISAC = "IE0003XJA0J9", "IE00B6R52259"


def tx(when, kind, asset, qty=0.0, gross=None, broker="DEGIRO", **kw):
    total = None if gross is None else -gross
    return Transaction(broker=broker, ts=when, kind=kind, asset=asset, name="Some S&P 500 (Dist)", asset_class="ETF",
                       qty=qty, gross_eur=gross, fees_eur=0.0, total_eur=total, source="x.pdf", **kw)


@pytest.fixture
def rep():
    txns = [
        tx(datetime(2024, 1, 10), BUY, DIST, 1000, 50000),
        tx(datetime(2024, 1, 10), TOB, DIST, amount_eur=-60.0, ref="separate"),
        tx(datetime(2026, 1, 10), BUY, DIST, 1000, 60000),
        tx(datetime(2025, 12, 1), DIVIDEND, DIST, amount_eur=200.0),   # 1,000 units then: 0.20 per unit
        tx(datetime(2026, 6, 1), DIVIDEND, DIST, amount_eur=400.0),    # 2,000 units then: 0.20 per unit
    ]
    pf = Portfolio(txns)
    pb = PriceBook(fetch=False, today=TODAY)
    pb.set_override(DIST, now=70.0, reference=60.0)
    return Report(pf, pb, TaxCalculator(pf, pb, TaxRules(), today=TODAY), pf.warnings)


def test_dividends_per_unit_follow_the_units_held(rep):
    assert dividends_per_unit(rep.portfolio, "DEGIRO", DIST, TODAY) == pytest.approx(0.40)


def test_compare_costs_and_yearly_effect(rep):
    assert default_sources(rep) == {("DEGIRO", DIST)}
    (src,) = sources_for(rep, {DIST})
    assert src.value == pytest.approx(140000) and src.dividends_year == pytest.approx(800) and src.tob_rate == 0.0012
    costs = CostModel(spread=0.0, commission={"DEGIRO": (2.0, 0.0)})
    webn, isac = compare(rep, [src], [CATALOG[WEBN], CATALOG[ISAC]], horizon=10, costs=costs)
    # all 2,000 units now: 1,000 x (70 - 60) + 1,000 x (70 - 60) = 20,000 gain -> 10% above 10,000 = 1,000
    assert webn.cgt_now == pytest.approx(1000)
    assert webn.tob_sell == pytest.approx(140000 * 0.0012) and webn.tob_buy == pytest.approx(140000 * 0.0012)
    assert webn.commission == 4.0
    assert webn.dividend_tax_saved == pytest.approx(240) and webn.dividend_tax_back == pytest.approx(80)
    assert webn.ter_saved == pytest.approx(0.0)                       # both 0.07%
    assert webn.exit_tob_extra == pytest.approx(0.0)
    assert isac.tob_buy == pytest.approx(min(140000 * 0.0132, 4000))  # 1.32%, capped at 4,000 per order
    assert isac.exit_tob_extra == pytest.approx(140000 * (0.0132 - 0.0012))
    assert isac.ter_saved == pytest.approx((0.0007 - 0.0020) * 140000)
    assert webn.net > isac.net and webn.break_even == pytest.approx(webn.now / webn.yearly)
    no_back = compare(rep, [src], [CATALOG[WEBN]], tax_reinvested=False)[0]
    assert no_back.dividend_tax_back == 0


def test_spread_plan_stays_within_the_exemption(rep):
    (src,) = sources_for(rep, {DIST})
    case = compare(rep, [src], [CATALOG[WEBN]])[0]
    plan = spread_plan(rep, [src], case)
    assert plan.done and plan.years == 2
    assert all(s.gain <= 10000 + 1e-6 for s in plan.steps)
    assert sum(sum(s.units.values()) for s in plan.steps) == pytest.approx(2000)
    assert plan.benefit_waited == pytest.approx(case.yearly * 0.5, rel=0.01)  # half the position waited one year
    assert net_spread(case, plan) > case.net                          # no meerwaardebelasting at all


def test_valid_isin():
    assert valid_isin("IE00B4L5Y983") and valid_isin("US0378331005")
    assert not valid_isin("IE00B4L5Y984") and not valid_isin("XX123")


GEMINI_PAYLOAD = {"candidates": [{
    "content": {"parts": [{"text": "Here you go:\n```json\n[\n"
                           '{"name": "Cheap World Acc", "isin": "IE00B4L5Y983", "ticker": "X", "index": "MSCI World",'
                           ' "ter_pct": 0.05, "accumulating": true, "registered_in_belgium": false, "similarity": "s",'
                           ' "reason": "r", "source_urls": ["https://example.org/a"]},\n'
                           '{"name": "Made Up", "isin": "IE00B4L5Y984", "ter_pct": 0.01},\n'
                           '{"name": "Unknown Reg", "isin": "US0378331005", "ter_pct": 0.1, "accumulating": true,'
                           ' "registered_in_belgium": null}\n]\n```'}]},
    "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.org/x", "title": "example.org"}}]}}]}


def test_gemini_response_is_parsed_and_checked():
    text, cites = gem.parse_response(GEMINI_PAYLOAD)
    assert cites == [("example.org", "https://example.org/x")]
    items = gem.extract_json_array(text)
    funds, rejected = gem.to_funds(items, "m", TODAY)
    assert [f.isin for f in funds] == ["IE00B4L5Y983", "US0378331005"]
    assert "IE00B4L5Y984" in rejected[0]                                # bad check digit: left out
    known, unknown = funds
    assert known.ter == CATALOG["IE00B4L5Y983"].ter and known.verified    # the catalog's facts win
    assert unknown.verified is False and unknown.tob_rate is None and unknown.ter == pytest.approx(0.001)


def test_gemini_prompt_has_no_amounts_and_the_call_falls_back_on_404(rep):
    (src,) = sources_for(rep, {DIST})
    prompt = gem.build_prompt([source_fund(src)])
    assert DIST in prompt and "140" not in prompt and "2000" not in prompt and "€" not in prompt

    class Resp:
        def __init__(self, code, payload):
            self.status_code, self._p, self.text = code, payload, ""

        def json(self):
            return self._p

    class Session:
        def __init__(self):
            self.urls = []

        def post(self, url, headers, json, timeout):
            self.urls.append(url)
            assert headers["x-goog-api-key"] == "k" and json["tools"] == [{"google_search": {}}]
            return Resp(404, {"error": {"message": "not found"}}) if len(self.urls) == 1 else Resp(200, GEMINI_PAYLOAD)

    sess = Session()
    res = gem.suggest("k", [source_fund(src)], session=sess)
    assert not res.error and len(res.funds) == 2 and res.model == gem.FALLBACK_MODELS[0]
    assert gem.DEFAULT_MODEL in sess.urls[0] and gem.FALLBACK_MODELS[0] in sess.urls[1]
    assert gem.suggest("", [source_fund(src)]).error


def test_unknown_registration_assumes_the_worst(rep):
    (src,) = sources_for(rep, {DIST})
    f = Fund("US0378331005", "Unknown", ter=0.001, accumulating=True, tob_rate=None, verified=False)
    case = compare(rep, [src], [f])[0]
    assert case.target_tob == 0.0132 and any("not all facts are verified" in n for n in case.notes)
