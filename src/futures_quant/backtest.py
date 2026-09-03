from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from .audit import AtomicJsonlLedger
from .contracts import ContractSelector, ContractSpecStore
from .data_quality import validate_bars
from .execution import ExecutionSimulator
from .ledger import FuturesAccount
from .models import FuturesBar, Offset, Order, OrderSide, OrderStatus
from .risk import RiskEngine
from .strategy import TimeSeriesTrendStrategy


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class BacktestEngine:
    def __init__(
        self,
        specs: ContractSpecStore,
        selector: ContractSelector,
        strategy: TimeSeriesTrendStrategy,
        risk: RiskEngine,
        execution: ExecutionSimulator,
        initial_capital: float,
        artifact_dir: str | Path,
        strategy_version: str = "ts_trend_v1",
    ):
        self.specs = specs
        self.selector = selector
        self.strategy = strategy
        self.risk = risk
        self.execution = execution
        self.account = FuturesAccount(initial_capital)
        self.strategy_version = strategy_version
        base = Path(artifact_dir)
        self.predictions = AtomicJsonlLedger(base / "prediction_ledger.jsonl")
        self.decisions = AtomicJsonlLedger(base / "strategy_ledger.jsonl")
        self.executions = AtomicJsonlLedger(base / "execution_ledger.jsonl")
        self._queued: list[Order] = []
        self._active: dict[str, str] = {}
        self._equity_peak = initial_capital
        self._risk_locked = False

    @staticmethod
    def _bar(row: pd.Series) -> FuturesBar:
        def optional(name: str) -> float | None:
            value = row.get(name)
            return None if value is None or pd.isna(value) else float(value)
        return FuturesBar(
            trading_day=row["trading_day"], event_time=pd.Timestamp(row["event_time"]).to_pydatetime(),
            available_at=pd.Timestamp(row["available_at"]).to_pydatetime(),
            symbol=str(row["symbol"]), product=str(row["product"]),
            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]), close=float(row["close"]),
            settlement=float(row["settlement"]), volume=int(row["volume"]), open_interest=int(row["open_interest"]),
            upper_limit=optional("upper_limit"), lower_limit=optional("lower_limit"),
            source_version=str(row["source_version"]),
        )

    def _approved_quantity(self, order: Order, bar: FuturesBar, marks: dict[str, float], day: date) -> tuple[int, str]:
        for quantity in range(order.quantity, 0, -1):
            trial = replace(order, quantity=quantity)
            decision = self.risk.check(
                self.account, trial, bar.open, day, marks, self.specs, self._equity_peak
            )
            if decision.accepted:
                return quantity, decision.reason
        decision = self.risk.check(self.account, order, bar.open, day, marks, self.specs, self._equity_peak)
        return 0, decision.reason

    def _execute_queued(self, day: date, bars: dict[str, FuturesBar]) -> int:
        fills = 0
        marks = {symbol: bar.open for symbol, bar in bars.items()}
        due = [o for o in self._queued if o.eligible_day == day]
        self._queued = [o for o in self._queued if o.eligible_day > day]
        due.sort(key=lambda o: 0 if o.offset is not Offset.OPEN else 1)
        for order in due:
            order.status = OrderStatus.ELIGIBLE
            bar = bars.get(order.symbol)
            if not bar:
                order.status = OrderStatus.REJECTED
                order.reason = "missing executable bar"
                self.executions.append({"type": "order", **order.to_dict()})
                continue
            if self._risk_locked and order.offset is Offset.OPEN:
                order.status = OrderStatus.REJECTED
                order.reason = "account risk lock permits reductions only"
                self.executions.append({"type": "order", **order.to_dict()})
                continue
            quantity, risk_reason = self._approved_quantity(order, bar, marks, day)
            if quantity == 0:
                order.status = OrderStatus.REJECTED
                order.reason = risk_reason
                self.executions.append({"type": "order", **order.to_dict()})
                continue
            if quantity != order.quantity:
                order.reason = f"risk-clipped {order.quantity} -> {quantity}"
                order.quantity = quantity
            spec = self.specs.resolve(order.symbol, day)
            fill = self.execution.execute(order, bar, spec)
            self.executions.append({"type": "order", **order.to_dict()})
            if fill:
                fee = self.account.apply_fill(fill, spec)
                self.executions.append({"type": "fill", **asdict(fill), "fee": fee})
                fills += 1
        return fills

    @staticmethod
    def _close_order(
        position_qty: int, symbol: str, product: str, as_of: date, eligible_day: date, decision_id: str
    ) -> Order:
        return Order(
            uuid.uuid4().hex, decision_id, datetime.combine(as_of, time(15, 1)), eligible_day,
            symbol, product, OrderSide.SELL if position_qty > 0 else OrderSide.BUY,
            Offset.CLOSE, abs(position_qty),
        )

    def _plan_target(self, product: str, active: str, target: int, eligible_day: date, as_of: date, decision_id: str) -> list[Order]:
        orders: list[Order] = []
        active_current = 0
        for symbol, position in list(self.account.positions.items()):
            if position.product != product:
                continue
            if symbol != active:
                orders.append(self._close_order(position.quantity, symbol, product, as_of, eligible_day, decision_id))
            else:
                active_current = position.quantity
        if active_current and active_current * target < 0:
            orders.append(self._close_order(active_current, active, product, as_of, eligible_day, decision_id))
            active_current = 0
        delta = target - active_current
        if delta:
            reducing = active_current != 0 and abs(target) < abs(active_current) and target * active_current >= 0
            orders.append(Order(
                uuid.uuid4().hex, decision_id, datetime.combine(as_of, time(15, 1)), eligible_day,
                active, product, OrderSide.BUY if delta > 0 else OrderSide.SELL,
                Offset.CLOSE if reducing else Offset.OPEN, abs(delta),
            ))
        return orders

    def run(self, frame: pd.DataFrame) -> dict[str, Any]:
        work = frame.copy()
        work["trading_day"] = pd.to_datetime(work["trading_day"]).dt.date
        quality = validate_bars(work, self.specs)
        days = sorted(work["trading_day"].unique())
        if len(days) < 2:
            raise ValueError("at least two sessions are required")
        products = sorted(work["product"].unique())
        fill_count = order_count = 0
        for index, day in enumerate(days):
            today = work[work["trading_day"] == day]
            bars = {str(row["symbol"]): self._bar(row) for _, row in today.iterrows()}
            fill_count += self._execute_queued(day, bars)
            settlements = {s: b.settlement for s, b in bars.items() if s in self.account.positions}
            snapshot = self.account.settle(day, settlements, self.specs)
            self._equity_peak = max(self._equity_peak, snapshot.equity)
            if snapshot.margin_call:
                self._risk_locked = True
            if index == len(days) - 1:
                continue
            next_day = days[index + 1]
            for product in products:
                chain = [bar for bar in bars.values() if bar.product == product]
                if not chain:
                    continue
                active = self.selector.select(day, chain, self.specs, self._active.get(product))
                self._active[product] = active
                history = work[(work["symbol"] == active) & (work["trading_day"] <= day)].sort_values("trading_day")
                spec = self.specs.resolve(active, day)
                forecast = self.strategy.forecast(history, spec, snapshot.equity)
                target = 0 if self._risk_locked else forecast.target_contracts
                prediction_id = uuid.uuid4().hex
                decision_id = uuid.uuid4().hex
                self.predictions.append({
                    "prediction_id": prediction_id, "as_of": day, "eligible_day": next_day,
                    "product": product, "contract": active, "strategy_version": self.strategy_version,
                    "score": forecast.score, "uncertainty": forecast.uncertainty,
                    "raw_target_contracts": forecast.target_contracts, "reason": forecast.reason,
                    "data_hash": _stable_hash(history.tail(self.strategy.slow_window).to_dict("records")),
                })
                orders = self._plan_target(product, active, target, next_day, day, decision_id)
                self.decisions.append({
                    "decision_id": decision_id, "prediction_id": prediction_id, "as_of": day,
                    "eligible_day": next_day, "product": product, "contract": active,
                    "target_contracts": target, "risk_locked": self._risk_locked,
                    "order_ids": [order.order_id for order in orders],
                })
                self._queued.extend(orders)
                order_count += len(orders)
        equities = [s.equity for s in self.account.snapshots]
        peaks = pd.Series(equities).cummax()
        drawdowns = pd.Series(equities) / peaks - 1
        return {
            "schema": "futures_backtest_v1",
            "strategy_version": self.strategy_version,
            "data_quality": asdict(quality),
            "sessions": len(days),
            "initial_capital": self.account.initial_balance,
            "final_equity": equities[-1],
            "total_return_pct": (equities[-1] / equities[0] - 1) * 100,
            "max_drawdown_pct": float(drawdowns.min() * 100),
            "fees_paid": self.account.fees_paid,
            "orders": order_count,
            "fills": fill_count,
            "peak_margin_utilization": max(
                (s.margin / s.equity if s.equity > 0 else 1.0 for s in self.account.snapshots), default=0.0
            ),
            "margin_calls": sum(s.margin_call for s in self.account.snapshots),
            "synthetic_or_real": "determined_by_input_provenance",
        }
