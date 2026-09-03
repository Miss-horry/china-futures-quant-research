from __future__ import annotations

import math
import uuid

from .models import ContractSpec, Fill, FuturesBar, Order, OrderSide, OrderStatus


class ExecutionSimulator:
    def __init__(
        self,
        max_volume_participation: float = 0.05,
        base_slippage_ticks: int = 1,
        impact_ticks_at_full_participation: int = 4,
        reject_open_on_limit_lock: bool = True,
    ):
        self.max_volume_participation = max_volume_participation
        self.base_slippage_ticks = base_slippage_ticks
        self.impact_ticks_at_full_participation = impact_ticks_at_full_participation
        self.reject_open_on_limit_lock = reject_open_on_limit_lock

    @staticmethod
    def _pinned(price: float, level: float | None, tick: float) -> bool:
        return level is not None and abs(price - level) <= tick / 2

    @staticmethod
    def _round_tick(price: float, tick: float) -> float:
        return round(round(price / tick) * tick, 10)

    def execute(self, order: Order, bar: FuturesBar, spec: ContractSpec) -> Fill | None:
        order.status = OrderStatus.SUBMITTED
        buy_blocked = order.side is OrderSide.BUY and all(
            self._pinned(p, bar.upper_limit, spec.tick_size) for p in (bar.open, bar.high, bar.low, bar.close)
        )
        sell_blocked = order.side is OrderSide.SELL and all(
            self._pinned(p, bar.lower_limit, spec.tick_size) for p in (bar.open, bar.high, bar.low, bar.close)
        )
        if buy_blocked or sell_blocked:
            order.status = OrderStatus.REJECTED
            order.reason = "limit-locked with no executable opposite liquidity"
            return None
        capacity = math.floor(max(bar.volume, 0) * self.max_volume_participation)
        quantity = min(order.quantity, capacity)
        if quantity <= 0:
            order.status = OrderStatus.REJECTED
            order.reason = "zero executable volume"
            return None
        participation = quantity / max(bar.volume, 1)
        impact = math.ceil(self.impact_ticks_at_full_participation * participation / self.max_volume_participation)
        ticks = self.base_slippage_ticks + max(impact, 0)
        raw = bar.open + order.side.sign * ticks * spec.tick_size
        if bar.upper_limit is not None:
            raw = min(raw, bar.upper_limit)
        if bar.lower_limit is not None:
            raw = max(raw, bar.lower_limit)
        price = self._round_tick(raw, spec.tick_size)
        order.filled_quantity = quantity
        order.status = OrderStatus.FILLED if quantity == order.quantity else OrderStatus.PARTIAL
        return Fill(
            fill_id=uuid.uuid4().hex,
            order_id=order.order_id,
            trading_day=bar.trading_day,
            symbol=bar.symbol,
            side=order.side,
            offset=order.offset,
            quantity=quantity,
            price=price,
            slippage_ticks=ticks,
        )

