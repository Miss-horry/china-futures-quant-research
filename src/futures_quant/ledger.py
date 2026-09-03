from __future__ import annotations

from datetime import date

from .contracts import ContractSpecStore
from .models import AccountSnapshot, ContractSpec, Fill, Offset, OrderSide, Position


class FuturesAccount:
    """Variation-margin account. Notional is never deducted from cash."""

    def __init__(self, initial_balance: float):
        if initial_balance <= 0:
            raise ValueError("initial balance must be positive")
        self.initial_balance = float(initial_balance)
        self.balance = float(initial_balance)
        self.positions: dict[str, Position] = {}
        self.fees_paid = 0.0
        self.realized_pnl = 0.0
        self.snapshots: list[AccountSnapshot] = []

    @staticmethod
    def fee(fill: Fill, spec: ContractSpec) -> float:
        rate, per_lot = {
            Offset.OPEN: (spec.open_fee_rate, spec.open_fee_per_lot),
            Offset.CLOSE: (spec.close_fee_rate, spec.close_fee_per_lot),
            Offset.CLOSE_TODAY: (spec.close_today_fee_rate, spec.close_today_fee_per_lot),
        }[fill.offset]
        return fill.price * spec.multiplier * fill.quantity * rate + fill.quantity * per_lot

    def apply_fill(self, fill: Fill, spec: ContractSpec) -> float:
        if fill.quantity <= 0:
            raise ValueError("fill quantity must be positive")
        charge = self.fee(fill, spec)
        current = self.positions.get(fill.symbol)
        if fill.offset is Offset.OPEN:
            signed = fill.side.sign * fill.quantity
            if current and current.quantity * signed < 0:
                raise ValueError("opposite position requires an explicit close")
            if current:
                total = abs(current.quantity) + fill.quantity
                current.reference_price = (
                    current.reference_price * abs(current.quantity) + fill.price * fill.quantity
                ) / total
                current.quantity += signed
                current.opened_today += fill.quantity
            else:
                self.positions[fill.symbol] = Position(
                    fill.symbol, spec.product, signed, fill.price, fill.quantity
                )
        else:
            if not current:
                raise ValueError("cannot close a missing position")
            required_side = OrderSide.SELL if current.quantity > 0 else OrderSide.BUY
            if fill.side is not required_side or fill.quantity > abs(current.quantity):
                raise ValueError("invalid close direction or quantity")
            if fill.offset is Offset.CLOSE_TODAY and fill.quantity > current.opened_today:
                raise ValueError("close-today quantity exceeds today's opens")
            direction = 1 if current.quantity > 0 else -1
            pnl = (fill.price - current.reference_price) * direction * fill.quantity * spec.multiplier
            self.balance += pnl
            self.realized_pnl += pnl
            current.quantity -= direction * fill.quantity
            if fill.offset is Offset.CLOSE_TODAY:
                current.opened_today -= fill.quantity
            if current.quantity == 0:
                del self.positions[fill.symbol]
        self.balance -= charge
        self.fees_paid += charge
        return charge

    def unrealized_pnl(self, prices: dict[str, float], specs: ContractSpecStore, trading_day: date) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            if symbol not in prices:
                raise KeyError(f"missing mark price for {symbol}")
            spec = specs.resolve(symbol, trading_day)
            total += (prices[symbol] - position.reference_price) * position.quantity * spec.multiplier
        return total

    def margin(self, prices: dict[str, float], specs: ContractSpecStore, trading_day: date) -> float:
        return sum(
            abs(position.quantity) * prices[symbol] * specs.resolve(symbol, trading_day).multiplier
            * specs.resolve(symbol, trading_day).margin_rate
            for symbol, position in self.positions.items()
        )

    def gross_notional(self, prices: dict[str, float], specs: ContractSpecStore, trading_day: date) -> float:
        return sum(
            abs(position.quantity) * prices[symbol] * specs.resolve(symbol, trading_day).multiplier
            for symbol, position in self.positions.items()
        )

    def equity(self, prices: dict[str, float], specs: ContractSpecStore, trading_day: date) -> float:
        return self.balance + self.unrealized_pnl(prices, specs, trading_day)

    def settle(
        self,
        trading_day: date,
        settlements: dict[str, float],
        specs: ContractSpecStore,
    ) -> AccountSnapshot:
        for symbol, position in list(self.positions.items()):
            if symbol not in settlements:
                raise RuntimeError(f"cannot settle without price for {symbol}")
            spec = specs.resolve(symbol, trading_day)
            pnl = (settlements[symbol] - position.reference_price) * position.quantity * spec.multiplier
            self.balance += pnl
            self.realized_pnl += pnl
            position.reference_price = settlements[symbol]
            position.opened_today = 0
        margin = self.margin(settlements, specs, trading_day)
        gross = self.gross_notional(settlements, specs, trading_day)
        available = self.balance - margin
        snapshot = AccountSnapshot(
            trading_day=trading_day,
            balance=self.balance,
            equity=self.balance,
            margin=margin,
            available=available,
            gross_notional=gross,
            fees_paid=self.fees_paid,
            margin_call=available < 0,
            positions={symbol: p.quantity for symbol, p in self.positions.items()},
        )
        self.snapshots.append(snapshot)
        return snapshot
