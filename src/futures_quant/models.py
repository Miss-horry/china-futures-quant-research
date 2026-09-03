from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1


class Offset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    CLOSE_TODAY = "CLOSE_TODAY"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    ELIGIBLE = "ELIGIBLE"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    product: str
    exchange: str
    multiplier: float
    tick_size: float
    margin_rate: float
    maintenance_margin_rate: float
    price_limit_rate: float
    last_trade_date: date
    effective_from: date
    effective_to: date | None = None
    open_fee_rate: float = 0.00005
    close_fee_rate: float = 0.00005
    close_today_fee_rate: float = 0.0001
    open_fee_per_lot: float = 0.0
    close_fee_per_lot: float = 0.0
    close_today_fee_per_lot: float = 0.0
    settlement_type: str = "physical"
    version: str = "v1"

    def __post_init__(self) -> None:
        if self.multiplier <= 0 or self.tick_size <= 0:
            raise ValueError("multiplier and tick_size must be positive")
        if not 0 < self.maintenance_margin_rate <= self.margin_rate < 1:
            raise ValueError("invalid margin rates")
        if not 0 < self.price_limit_rate < 1:
            raise ValueError("invalid price limit rate")

    def active_on(self, trading_day: date) -> bool:
        return self.effective_from <= trading_day and (
            self.effective_to is None or trading_day <= self.effective_to
        )


@dataclass(frozen=True)
class DailySettlementParameter:
    trading_day: date
    symbol: str
    settlement_price: float
    spec_long_margin_rate: float
    spec_short_margin_rate: float
    trade_fee_rate: float = 0.0
    trade_fee_per_lot: float = 0.0
    close_today_fee_rate: float = 0.0
    close_today_fee_per_lot: float = 0.0
    close_today_enabled: bool = True
    source_version: str = "unknown"

    def __post_init__(self) -> None:
        if self.settlement_price <= 0:
            raise ValueError("settlement price must be positive")
        if min(self.spec_long_margin_rate, self.spec_short_margin_rate) <= 0:
            raise ValueError("daily margin rates must be positive")

    @property
    def conservative_margin_rate(self) -> float:
        return max(self.spec_long_margin_rate, self.spec_short_margin_rate)


@dataclass(frozen=True)
class FuturesBar:
    trading_day: date
    event_time: datetime
    available_at: datetime
    symbol: str
    product: str
    open: float
    high: float
    low: float
    close: float
    settlement: float
    volume: int
    open_interest: int
    upper_limit: float | None = None
    lower_limit: float | None = None
    source_version: str = "unknown"


@dataclass
class Order:
    order_id: str
    decision_id: str
    created_at: datetime
    eligible_day: date
    symbol: str
    product: str
    side: OrderSide
    offset: Offset
    quantity: int
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("created_at", "eligible_day"):
            data[key] = data[key].isoformat()
        data["side"] = self.side.value
        data["offset"] = self.offset.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    trading_day: date
    symbol: str
    side: OrderSide
    offset: Offset
    quantity: int
    price: float
    slippage_ticks: int


@dataclass
class Position:
    symbol: str
    product: str
    quantity: int
    reference_price: float
    opened_today: int = 0


@dataclass(frozen=True)
class AccountSnapshot:
    trading_day: date
    balance: float
    equity: float
    margin: float
    available: float
    gross_notional: float
    fees_paid: float
    margin_call: bool
    positions: dict[str, int] = field(default_factory=dict)
