"""meerwaarde: P&L across Bitvavo, DEGIRO and LYNX, and the Belgian meerwaardebelasting."""

from .models import Transaction, Lot, RealizedRow, Holding, IncomeRow
from .engine.portfolio import Portfolio, PortfolioOptions
from .engine.tax import TaxRules, TaxCalculator
from .parsers import parse_file, detect_broker

__all__ = [
    "Transaction", "Lot", "RealizedRow", "Holding", "IncomeRow",
    "Portfolio", "PortfolioOptions", "TaxRules", "TaxCalculator",
    "parse_file", "detect_broker",
]
__version__ = "0.1.0"
