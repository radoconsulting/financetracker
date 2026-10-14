"""FIFO profit and loss across brokers."""
from __future__ import annotations

import copy
import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..models import (Transaction, Lot, RealizedRow, Holding, IncomeRow, TobRow, BUY, SELL, REWARD, DEPOSIT, WITHDRAWAL,
                      CASH_IN, CASH_OUT, DIVIDEND, INTEREST, WITHHOLDING, FX_RESULT, FEE, TOB, REDEMPTION)

EPS = 1e-10


@dataclass
class PortfolioOptions:
    own_wallet_withdrawals: bool = True    # crypto withdrawals go to your own wallet (e.g. a Ledger): not a sale
    detect_conversions: bool = True        # same-day swap of one ISIN into another at the same value is not a sale
    bond_redemption_at_par: bool = True    # a bond 'sold' at 0 on maturity was repaid at 100%
    record_network_fees: bool = True       # coins spent on network fees count as a (small) realized cost
    class_overrides: dict = field(default_factory=dict)   # asset -> asset class
    name_overrides: dict = field(default_factory=dict)    # asset -> display name


@dataclass
class CashFlow:
    broker: str
    date: datetime
    amount_eur: float
    note: str = ""


class Portfolio:
    def __init__(self, transactions: list[Transaction], statements=(), options: PortfolioOptions | None = None):
        self.options = options or PortfolioOptions()
        self.transactions = sorted(transactions, key=lambda t: (t.ts, t.broker, t.order))
        self.statements = list(statements)
        self.realized: list[RealizedRow] = []
        self.income: list[IncomeRow] = []
        self.cash: list[CashFlow] = []
        self.tob: list[TobRow] = []          # stock exchange tax per trade, for the yearly report only
        self.warnings: list[str] = []
        self.lots: dict[tuple, list[Lot]] = defaultdict(list)
        self.meta: dict[tuple, dict] = {}
        self.off_wallet: dict[tuple, float] = defaultdict(float)
        self.tob_separate = any(t.kind == TOB and t.ref == "separate" for t in self.transactions)
        self.transactions = self._attach_separate_tob(self.transactions)
        self._payouts = defaultdict(list)    # (broker, asset) -> [(ts, amount)] bond payouts from an account statement
        for t in self.transactions:
            if t.kind == REDEMPTION:
                self._payouts[(t.broker, t.asset)].append((t.ts, t.amount_eur or 0.0))
        self._build()

    # ---------- helpers ----------
    def _cls(self, t: Transaction) -> str:
        return self.options.class_overrides.get(t.asset, t.asset_class)

    def _name(self, t: Transaction) -> str:
        return self.options.name_overrides.get(t.asset, t.name or t.asset)

    def _remember(self, t: Transaction):
        self.meta[(t.broker, t.asset)] = dict(name=self._name(t), asset_class=self._cls(t))

    def _take(self, key, qty: float) -> tuple[list[Lot], float]:
        """Remove qty from the oldest lots; return copies of what was taken and any shortfall."""
        taken, left = [], qty
        pool = self.lots[key]
        while left > EPS and pool:
            lot = pool[0]
            t = min(lot.qty, left)
            part = copy.copy(lot)
            part.qty = t
            taken.append(part)
            lot.qty -= t
            left -= t
            if lot.qty <= EPS:
                pool.pop(0)
        return taken, max(0.0, left)

    # ---------- stock exchange tax booked apart from the trade (DEGIRO account statement) ----------
    def _attach_separate_tob(self, txns: list[Transaction]) -> list[Transaction]:
        """Add stock exchange tax that the trade export leaves out to the trade's costs (on copies of the rows)."""
        minute = lambda ts: ts.replace(second=0, microsecond=0)  # noqa: E731
        trades = defaultdict(list)
        for i, t in enumerate(txns):
            if t.kind in (BUY, SELL):
                trades[(t.broker, t.asset, minute(t.ts))].append(i)
        taxes = defaultdict(list)
        for t in txns:
            if t.kind == TOB and t.ref == "separate":
                taxes[(t.broker, t.asset, minute(t.ts))].append(t)
        out = list(txns)
        for key, rows in taxes.items():
            idx = sorted(trades.get(key, []), key=lambda i: abs(txns[i].gross_eur or 0))
            if not idx:                                   # no trade in the same minute: same day instead
                idx = sorted((i for (b, a, m), ii in trades.items() if b == key[0] and a == key[1]
                              and m.date() == key[2].date() for i in ii), key=lambda i: abs(txns[i].gross_eur or 0))
            if not idx:
                for t in rows:
                    out.append(dataclasses.replace(t, kind=FEE, name=f"Stock exchange tax {t.name}",
                                                   note="Stock exchange tax without a matching trade"))
                self.warnings.append(f"{key[0]}: stock exchange tax on {key[2]:%Y-%m-%d} for {rows[0].name} has no matching "
                                     "trade in the trade export; counted as a cost")
                continue
            for n, t in enumerate(sorted(rows, key=lambda t: abs(t.amount_eur or 0))):
                j = idx[min(n, len(idx) - 1)]
                tr = out[j]
                cost = -(t.amount_eur or 0.0)
                out[j] = dataclasses.replace(tr, fees_eur=(tr.fees_eur or 0.0) + cost, total_eur=(tr.total_eur or 0.0) - cost)
        return sorted(out, key=lambda t: (t.ts, t.broker, t.order))

    # ---------- conversions ----------
    def _conversion_pairs(self):
        """Same day, same quantity, one ISIN out and another in, cash effect zero, no costs."""
        pairs = {}
        by_day = defaultdict(list)
        for t in self.transactions:
            if t.kind in (BUY, SELL) and t.total_eur is not None:
                by_day[(t.broker, t.ts.date())].append(t)
        for rows in by_day.values():
            sells = [t for t in rows if t.kind == SELL and abs(t.fees_eur) < 0.005]
            buys = [t for t in rows if t.kind == BUY and abs(t.fees_eur) < 0.005]
            for s in sells:
                for b in buys:
                    if id(b) in pairs or id(s) in pairs or b.asset == s.asset:
                        continue
                    if abs(abs(s.qty) - b.qty) < 1e-9 and abs(s.total_eur + b.total_eur) < 0.02 and abs(s.total_eur) > 0:
                        pairs[id(s)] = b
                        pairs[id(b)] = None
                        break
        return pairs

    # ---------- build ----------
    def _build(self):
        o = self.options
        pairs = self._conversion_pairs() if o.detect_conversions else {}
        for t in self.transactions:
            key = (t.broker, t.asset)
            if t.kind in (BUY, SELL, REWARD, DEPOSIT, WITHDRAWAL):
                self._remember(t)
            if id(t) in pairs:
                target = pairs[id(t)]
                if target is None:
                    continue                      # the 'buy' half of a conversion
                taken, short = self._take(key, abs(t.qty))
                for lot in taken:
                    lot.origin = "conversion"
                    self.lots[(target.broker, target.asset)].append(lot)
                self._remember(target)
                self.lots[(target.broker, target.asset)].sort(key=lambda l: l.date)
                self.warnings.append(f"{t.broker}: {t.ts:%Y-%m-%d} {t.name} -> {target.name} treated as a conversion, not a sale")
                continue

            if t.kind == BUY:
                self.lots[key].append(Lot(t.ts, t.qty, (-t.total_eur) / t.qty, (t.gross_eur / t.qty) if t.gross_eur else None, "buy"))
            elif t.kind == SELL:
                q = abs(t.qty)
                proceeds, gross, note = t.total_eur, t.gross_eur, ""
                if self._cls(t) == "Bond" and abs(proceeds or 0) < 0.005 and abs(gross or 0) < 0.005:
                    paid = [a for ts, a in self._payouts.get(key, []) if abs((ts - t.ts).days) <= 31]
                    if paid:
                        proceeds = gross = sum(paid)
                        note = "Redeemed: payout from the account statement"
                    elif o.bond_redemption_at_par:
                        proceeds = gross = q            # nominal repaid at 100%
                        note = "Redeemed at par (payout is on the cash account, not in the export)"
                        self.warnings.append(f"{t.broker}: {t.name} redeemed on {t.ts:%Y-%m-%d}; counted at 100% of nominal")
                taken, short = self._take(key, q)
                if short > 1e-6:
                    self.warnings.append(f"{t.broker}: sold {short:g} more {t.name} than bought on {t.ts:%Y-%m-%d}; counted at zero cost")
                    taken.append(Lot(t.ts, short, 0.0, None, "unknown"))
                cost = sum(l.qty * l.cost_eur_unit for l in taken)
                self.realized.append(RealizedRow(t.broker, t.ts, t.asset, self._name(t), self._cls(t), q, proceeds, cost, gross, taken, note))
            elif t.kind == REWARD:
                self.lots[key].append(Lot(t.ts, t.qty, 0.0, None, "reward"))
            elif t.kind == DEPOSIT:
                q = t.qty
                if o.own_wallet_withdrawals and self.off_wallet[key] > EPS:
                    back = min(q, self.off_wallet[key])
                    self.off_wallet[key] -= back
                    q -= back
                if q > EPS:
                    self.lots[key].append(Lot(t.ts, q, 0.0, None, "deposit"))
                    if q * 1.0 > 1e-6 and not (o.own_wallet_withdrawals and t.qty - q > EPS):
                        self.warnings.append(f"{t.broker}: deposit of {q:g} {t.asset} on {t.ts:%Y-%m-%d} has no purchase price; counted at zero cost")
            elif t.kind == WITHDRAWAL:
                q = abs(t.qty)
                if t.fee_qty > EPS:
                    taken, _ = self._take(key, t.fee_qty)
                    if o.record_network_fees and taken:
                        cost = sum(l.qty * l.cost_eur_unit for l in taken)
                        self.realized.append(RealizedRow(t.broker, t.ts, t.asset, self._name(t), self._cls(t), t.fee_qty, 0.0, cost, 0.0, [], "Network fee"))
                    q -= t.fee_qty
                if o.own_wallet_withdrawals:
                    self.off_wallet[key] += abs(t.qty)
                else:
                    self._take(key, q)
            elif t.kind in (CASH_IN, CASH_OUT):
                self.cash.append(CashFlow(t.broker, t.ts, t.amount_eur if t.amount_eur is not None else t.qty, t.name))
            elif t.kind in (DIVIDEND, INTEREST, WITHHOLDING):
                cls = self.meta.get(key, {}).get("asset_class") or self._cls(t)
                self.income.append(IncomeRow(t.broker, t.ts, t.asset, self.meta.get(key, {}).get("name") or t.name, t.kind,
                                             t.amount_eur or 0.0, t.price, t.currency, t.note, cls))
            elif t.kind == TOB:
                self.tob.append(TobRow(t.broker, t.ts, t.asset, t.name, t.qty, t.price, t.amount_eur or 0.0, t.note))
            elif t.kind in (FX_RESULT, FEE):
                amt = t.amount_eur or 0.0
                self.realized.append(RealizedRow(t.broker, t.ts, t.asset, t.name, "Cash/FX", 0.0, amt, 0.0, None, [],
                                                 t.note or t.kind, t.kind))
        self._statement_holdings()

    def _statement_holdings(self):
        """Foreign cash at LYNX comes from the newest statement; quantities there also double-check the trades."""
        if not self.statements:
            return
        latest = self.statements[-1]
        for sym, p in latest.positions.items():
            key = (latest.broker, p.get("isin", sym))
            have = sum(l.qty for l in self.lots.get(key, []))
            if abs(have - p["qty"]) > 1e-6:
                self.warnings.append(f"{latest.broker}: {p.get('name', sym)}: trades add up to {have:g} units, the statement of "
                                     f"{latest.period_end} shows {p['qty']:g}")
        for cur, f in latest.forex.items():
            if abs(f["qty"]) < 0.005:
                continue
            key = (latest.broker, cur)
            start = latest.period_start
            prior_q = latest.prior_qty.get(cur, 0.0)
            lots = []
            avg_cost = f["cost_eur"] / f["qty"]          # the broker's own cost basis, for profit and loss
            if prior_q > 0 and start:
                lots.append(Lot(datetime.combine(start - timedelta(days=1), datetime.min.time()), prior_q,
                                avg_cost, None, "statement"))
            rest = f["qty"] - (prior_q if lots else 0.0)
            if rest > 0.005:
                inc = [i for i in self.income if i.currency == cur and i.broker == latest.broker and start and i.date.date() >= start and i.amount_local]
                rate = (sum(i.amount_eur for i in inc) / sum(i.amount_local for i in inc)) if inc and sum(i.amount_local for i in inc) else f["cost_eur"] / f["qty"]
                when = datetime.combine(start or latest.period_end, datetime.min.time())
                lots.append(Lot(when, rest, avg_cost, rate, "statement"))
            self.lots[key] = lots
            self.meta[key] = dict(name=f"{cur} cash", asset_class="Cash/FX")

    # ---------- views ----------
    def holdings(self) -> list[Holding]:
        out = []
        for key, lots in self.lots.items():
            lots = [l for l in lots if l.qty > EPS]
            if not lots:
                continue
            m = self.meta.get(key, {})
            off = self.off_wallet.get(key, 0.0)
            note = f"{off:g} in your own wallet" if off > EPS else ""
            out.append(Holding(key[0], key[1], m.get("name", key[1]), m.get("asset_class", ""), lots, note=note))
        return out

    def brokers(self) -> list[str]:
        return sorted({t.broker for t in self.transactions})
