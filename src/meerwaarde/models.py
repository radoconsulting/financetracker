"""Normalized data model shared by parsers, engines, the CLI and the app."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional

# Transaction kinds
BUY = "buy"                  # units in, paid in cash
SELL = "sell"                # units out, received in cash
REWARD = "reward"            # units in for free (staking, airdrop, manual credit)
DEPOSIT = "deposit"          # units transferred in from elsewhere
WITHDRAWAL = "withdrawal"    # units transferred out (e.g. to a hardware wallet)
CASH_IN = "cash_in"          # euro deposit
CASH_OUT = "cash_out"        # euro withdrawal
DIVIDEND = "dividend"
INTEREST = "interest"
WITHHOLDING = "withholding"
FX_RESULT = "fx_result"      # realized currency result reported by the broker
FEE = "fee"                  # standalone cost not tied to a trade
TOB = "tob"                  # stock exchange tax reported per trade; information only (already in the trade's costs)

# Asset classes (display) -> law types (meerwaardebelasting)
ASSET_CLASSES = ("Crypto", "ETF", "Bond ETF", "Fund", "Stock", "Bond", "Turbo", "Cash/FX", "Insurance", "Gold")


def law_type(asset_class: str) -> str:
    """Map an asset class to the asset type the meerwaardebelasting law uses."""
    if asset_class == "Crypto":
        return "crypto"
    if asset_class == "Cash/FX":
        return "fx"
    if asset_class == "Insurance":
        return "ins"
    if asset_class == "Gold":
        return "gold"
    return "fin"


LAW_TYPES = {
    "fin": ("Financial instruments", "Financiële instrumenten"),
    "crypto": ("Crypto-assets", "Crypto-activa"),
    "fx": ("Currency", "Valuta"),
    "ins": ("Insurance contracts", "Verzekeringscontracten"),
    "gold": ("Investment gold", "Beleggingsgoud"),
}


@dataclass
class Transaction:
    broker: str
    ts: datetime
    kind: str
    asset: str                       # ISIN, coin ticker or currency code
    name: str = ""
    asset_class: str = "ETF"
    qty: float = 0.0                 # units (positive in, negative out); cash amount for cash kinds
    price: Optional[float] = None    # per unit, in price_cur, before costs
    price_cur: Optional[str] = None
    gross_eur: Optional[float] = None  # |qty| * price in EUR, before costs
    fees_eur: float = 0.0            # all costs in EUR (commission, FX cost, stock exchange tax)
    total_eur: Optional[float] = None  # cash effect in EUR after costs (negative for a buy)
    currency: str = "EUR"            # currency of the amount for income/cash kinds
    amount_eur: Optional[float] = None  # income/cash kinds: amount in EUR
    fee_qty: float = 0.0             # costs paid in units of the asset itself (e.g. a network fee in BTC)
    order: int = 0                   # tie-breaker for rows with the same timestamp (chronological)
    note: str = ""
    ref: str = ""
    source: str = ""                 # file the row came from

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat(sep=" ")
        return d


@dataclass
class Lot:
    date: datetime
    qty: float
    cost_eur_unit: float             # purchase cost per unit incl. costs (for P&L)
    gross_eur_unit: Optional[float]  # purchase price per unit excl. costs (for the tax); None = unknown
    origin: str = "buy"              # buy | reward | deposit | conversion


@dataclass
class RealizedRow:
    broker: str
    date: datetime
    asset: str
    name: str
    asset_class: str
    qty: float
    proceeds_eur: float              # after costs
    cost_eur: float                  # FIFO cost incl. purchase costs
    gross_proceeds_eur: Optional[float] = None
    lots: list = field(default_factory=list)   # consumed Lot copies (for the tax)
    note: str = ""

    @property
    def pnl_eur(self) -> float:
        return self.proceeds_eur - self.cost_eur


@dataclass
class Holding:
    broker: str
    asset: str
    name: str
    asset_class: str
    lots: list
    price: Optional[float] = None         # current price per unit in EUR
    price_date: Optional[date] = None
    price_source: str = ""
    note: str = ""

    @property
    def qty(self) -> float:
        return sum(l.qty for l in self.lots)

    @property
    def cost_eur(self) -> float:
        return sum(l.qty * l.cost_eur_unit for l in self.lots)

    @property
    def value_eur(self) -> Optional[float]:
        return None if self.price is None else self.qty * self.price

    @property
    def unrealized_eur(self) -> Optional[float]:
        v = self.value_eur
        return None if v is None else v - self.cost_eur


@dataclass
class IncomeRow:
    broker: str
    date: datetime
    asset: str
    name: str
    kind: str                         # dividend | interest | withholding
    amount_eur: float
    amount_local: Optional[float] = None
    currency: str = "EUR"
    note: str = ""
    asset_class: str = ""             # of the paying asset: decides whether the dividend exemption applies


@dataclass
class TobRow:
    """Stock exchange tax (beurstaks / TOB) on one trade, as reported by the broker. Already part of the trade's costs."""
    broker: str
    date: datetime
    asset: str
    name: str
    qty: float
    price: Optional[float]
    amount_eur: float                 # negative = paid
    note: str = ""
