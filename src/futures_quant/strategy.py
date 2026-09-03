from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .models import ContractSpec


@dataclass(frozen=True)
class Forecast:
    direction: int
    score: float
    uncertainty: float
    target_contracts: int
    reason: str


class TimeSeriesTrendStrategy:
    """Simple, deterministic infrastructure probe; not a validated alpha claim."""

    def __init__(self, fast_window: int = 5, slow_window: int = 20, atr_window: int = 14, annual_risk_budget: float = 0.12):
        if not 1 < fast_window < slow_window:
            raise ValueError("require 1 < fast_window < slow_window")
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.atr_window = atr_window
        self.annual_risk_budget = annual_risk_budget

    def forecast(self, history: pd.DataFrame, spec: ContractSpec, equity: float) -> Forecast:
        if len(history) < max(self.slow_window, self.atr_window + 1):
            return Forecast(0, 0.0, 1.0, 0, "insufficient warmup history")
        close = history["close"].astype(float)
        fast = close.iloc[-self.fast_window:].mean()
        slow = close.iloc[-self.slow_window:].mean()
        previous = close.shift(1)
        true_range = pd.concat([
            history["high"].astype(float) - history["low"].astype(float),
            (history["high"].astype(float) - previous).abs(),
            (history["low"].astype(float) - previous).abs(),
        ], axis=1).max(axis=1)
        atr = float(true_range.iloc[-self.atr_window:].mean())
        if not math.isfinite(atr) or atr <= spec.tick_size:
            return Forecast(0, 0.0, 1.0, 0, "invalid or negligible ATR")
        direction = 1 if fast > slow else -1 if fast < slow else 0
        score = float((fast - slow) / max(atr, spec.tick_size))
        daily_budget = equity * self.annual_risk_budget / math.sqrt(252)
        contracts = math.floor(daily_budget / (atr * spec.multiplier))
        target = direction * max(contracts, 0)
        return Forecast(direction, score, atr / close.iloc[-1], target, "SMA spread scaled by ATR")

