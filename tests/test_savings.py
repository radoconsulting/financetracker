"""Savings accounts and pension savings (invented amounts)."""
import pytest

from meerwaarde.savings import Account, Pension, SavingsBook, advance_levy, pension_reduction, savings_tax


def test_regulated_interest_exemption_per_person_and_joint_accounts():
    book = SavingsBook([Account("Own", regulated=True, owner="you", interest={"2026": 900.0}),
                        Account("Joint", regulated=True, owner="joint", interest={"2026": 600.0}),
                        Account("Online", regulated=False, owner="partner", interest={"2026": 50.0})])
    t = savings_tax(book, ["you", "partner"], 2026)
    assert (t["you"].regulated, t["you"].exempt, t["you"].taxable_15) == (1200.0, 1020.0, 180.0)
    assert t["you"].tax == pytest.approx(0.15 * 180)
    assert (t["partner"].regulated, t["partner"].taxable_15, t["partner"].other) == (300.0, 0.0, 50.0)
    assert t["partner"].tax == pytest.approx(0.30 * 50)


def test_pension_reduction_options_and_the_dip():
    assert pension_reduction(Pension("P", option=1050, contributions={"2026": 1050}), 2026).reduction == 315.0
    assert pension_reduction(Pension("P", option=1350, contributions={"2026": 1350}), 2026).reduction == 337.5
    dip = pension_reduction(Pension("P", option=1350, contributions={"2026": 1200}), 2026)
    assert dip.reduction == 300.0 and "less" in dip.note
    over = pension_reduction(Pension("P", option=1050, contributions={"2026": 1200}), 2026)
    assert over.reduction == 315.0 and "no reduction" in over.note
    late = pension_reduction(Pension("P", birth_year=1960, contributions={"2026": 1050}), 2026)
    assert late.reduction == 0.0


def test_advance_levy_at_60():
    p = Pension("P", birth_year=1980, start_year=2010, contributions={"2030": 1000.0, "2040": 1000.0, "2045": 1000.0})
    due, levy = advance_levy(p)
    assert due == 2040
    assert levy == pytest.approx(0.08 * (1000 * 1.0475 ** 10 + 1000))           # 2045 is after the levy: not taxed
    late = Pension("P", birth_year=1970, start_year=2027, contributions={"2027": 1000.0})
    assert advance_levy(late)[0] == 2037                                         # started after 55: 10th anniversary


def test_round_trip(tmp_path):
    book = SavingsBook([Account("A", "Bank", True, "joint", 5000.0, {"2026": 10.0})],
                       [Pension("P", "Bank", "you", 1350, 1985, 2015, 20000.0, {"2026": 1350.0})])
    book.save(tmp_path / "savings.json")
    assert SavingsBook.load(tmp_path / "savings.json").to_dict() == book.to_dict()
    assert SavingsBook.load(tmp_path / "none.json").accounts == []
