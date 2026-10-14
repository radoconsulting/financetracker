from datetime import date, datetime

import pytest

from meerwaarde.engine.tax import TaxRules, unit_acquisition, gain_for, max_without_tax, TaxCalculator, TaxLine
from meerwaarde.models import Lot

R = TaxRules()
OLD = datetime(2024, 5, 1)
NEW = datetime(2026, 3, 1)
SALE = date(2026, 10, 14)


def lot(when, qty, gross):
    return Lot(when, qty, gross or 0.0, gross)


def test_old_units_use_31dec_price():
    # bought at 50, worth 60 on 31-12-2025, sold at 70 -> gain counts from 60
    assert unit_acquisition(lot(OLD, 1, 50), 70, 60, SALE, R) == 60


def test_higher_original_price_reduces_gain():
    # bought at 80, worth 60 on 31-12-2025, sold at 70 -> original 80 caps the gain at zero
    assert unit_acquisition(lot(OLD, 1, 80), 70, 60, SALE, R) == 70
    # sold at 90 -> gain counts from the original 80
    assert unit_acquisition(lot(OLD, 1, 80), 90, 60, SALE, R) == 80


def test_loss_is_measured_from_31dec_price():
    # bought at 80, worth 60 on 31-12-2025, sold at 50 -> loss of 10, not 30
    assert unit_acquisition(lot(OLD, 1, 80), 50, 60, SALE, R) == 60


def test_original_price_option_ends_after_2030():
    assert unit_acquisition(lot(OLD, 1, 80), 90, 60, date(2031, 1, 2), R) == 60


def test_option_can_be_switched_off():
    rules = TaxRules(use_original_if_higher=False)
    assert unit_acquisition(lot(OLD, 1, 80), 90, 60, SALE, rules) == 60


def test_new_units_use_purchase_price_and_unknown_is_zero():
    assert unit_acquisition(lot(NEW, 1, 40), 50, 60, SALE, R) == 40
    assert unit_acquisition(lot(NEW, 1, None), 50, 60, SALE, R) == 0.0


def test_missing_reference_price_is_reported():
    gain, acq, missing = gain_for([lot(OLD, 10, 50)], 10, 70, None, SALE, R)
    assert missing


def test_fifo_gain():
    lots = [lot(OLD, 10, 50), lot(NEW, 10, 65)]
    # 15 units at 70: 10 old units from 60 (31-12) -> 100, 5 new units from 65 -> 25
    gain, acq, missing = gain_for(lots, 15, 70, 60, SALE, R)
    assert gain == pytest.approx(125)
    assert not missing


def test_max_without_tax_respects_room_and_fifo():
    lots = [lot(OLD, 10, 50), lot(NEW, 10, 65)]
    # room 105: all 10 old units (100) + 1 new unit (5)
    assert max_without_tax(lots, 70, 60, SALE, R, room=105) == 11
    # loss positions can always be sold in full
    assert max_without_tax([lot(OLD, 5, 80)], 50, 60, SALE, R, room=0) == 5


def test_summary_offsets_losses_across_types_and_applies_exemption():
    lines = [
        TaxLine("X", "A", "A", "fin", "Sold", SALE, 1, 1, 1, None, 0, 0, 15000.0),
        TaxLine("X", "B", "B", "crypto", "Open", None, 1, 1, 1, None, 0, 0, -2000.0),
    ]
    s = TaxCalculator.summarize(lines, R)
    assert s["net"] == pytest.approx(13000)
    assert s["tax"] == pytest.approx(300)
    s2 = TaxCalculator.summarize(lines, R, extra={"gold": -5000})
    assert s2["tax"] == 0
