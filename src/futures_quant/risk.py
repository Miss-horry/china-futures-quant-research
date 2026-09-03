from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .contracts import ContractSpecStore
from .ledger import FuturesAccount
from .models import Offset, Order


@dataclass(frozen=True)
class RiskLimits:
    max_margin_utilization: float = 0.35
    max_gross_leverage: float = 2.0
    max_product_notional_fraction: float = 0.75
    max_contracts_per_product: int = 100
    minimum_available_cash_fraction: float = 0.25
    locked_limit_stress_days: int = 2
    extra_stress_shock: float = 0.03
    hard_drawdown: float = 0.15


@dataclass(frozen=True)
class RiskDecision:
    accepted: bool
    reason: str
    projected_margin_ratio: float = 0.0
    projected_gross_leverage: float = 0.0
    stress_loss: float = 0.0


class RiskEngine:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def check(
        self,
        account: FuturesAccount,
        order: Order,
        price: float,
        trading_day: date,
        marks: dict[str, float],
        specs: ContractSpecStore,
        equity_peak: float,
    ) -> RiskDecision:
        if order.offset is not Offset.OPEN:
            return RiskDecision(True, "risk-reducing close")
        if price <= 0 or order.quantity <= 0:
            return RiskDecision(False, "invalid order price or quantity")
        equity = account.equity(marks, specs, trading_day)
        if equity <= 0:
            return RiskDecision(False, "non-positive account equity")
        drawdown = 1 - equity / max(equity_peak, equity)
        if drawdown >= self.limits.hard_drawdown:
            return RiskDecision(False, "hard drawdown gate")

        quantities = {s: p.quantity for s, p in account.positions.items()}
        quantities[order.symbol] = quantities.get(order.symbol, 0) + order.side.sign * order.quantity
        projected_marks = dict(marks)
        projected_marks[order.symbol] = price
        margin = gross = stress = 0.0
        product_notionals: dict[str, float] = {}
        product_contracts: dict[str, int] = {}
        for symbol, quantity in quantities.items():
            if quantity == 0:
                continue
            if symbol not in projected_marks:
                return RiskDecision(False, f"missing mark for projected position {symbol}")
            spec = specs.resolve(symbol, trading_day)
            notional = abs(quantity) * projected_marks[symbol] * spec.multiplier
            gross += notional
            margin += notional * spec.margin_rate
            stress += notional * (
                self.limits.extra_stress_shock
                + self.limits.locked_limit_stress_days * spec.price_limit_rate
            )
            product_notionals[spec.product] = product_notionals.get(spec.product, 0.0) + notional
            product_contracts[spec.product] = product_contracts.get(spec.product, 0) + abs(quantity)

        margin_ratio = margin / equity
        leverage = gross / equity
        if margin_ratio > self.limits.max_margin_utilization:
            return RiskDecision(False, "margin utilization limit", margin_ratio, leverage, stress)
        if leverage > self.limits.max_gross_leverage:
            return RiskDecision(False, "gross leverage limit", margin_ratio, leverage, stress)
        if any(v / equity > self.limits.max_product_notional_fraction for v in product_notionals.values()):
            return RiskDecision(False, "product concentration limit", margin_ratio, leverage, stress)
        if any(v > self.limits.max_contracts_per_product for v in product_contracts.values()):
            return RiskDecision(False, "contract count limit", margin_ratio, leverage, stress)
        if equity - margin < equity * self.limits.minimum_available_cash_fraction:
            return RiskDecision(False, "available cash buffer", margin_ratio, leverage, stress)
        if equity - margin - stress <= 0:
            return RiskDecision(False, "locked-limit stress insolvency", margin_ratio, leverage, stress)
        return RiskDecision(True, "accepted", margin_ratio, leverage, stress)

