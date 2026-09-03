from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .contracts import ContractSpecStore


REQUIRED_COLUMNS = {
    "trading_day", "event_time", "available_at", "symbol", "product", "open", "high", "low", "close",
    "settlement", "volume", "open_interest", "source_version",
}


@dataclass(frozen=True)
class QualityReport:
    rows: int
    sessions: int
    symbols: int
    coverage: float
    source_versions: tuple[str, ...]


def validate_bars(
    frame: pd.DataFrame,
    specs: ContractSpecStore,
    expected_symbols: set[str] | None = None,
    minimum_coverage: float = 0.8,
) -> QualityReport:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("market data is empty")
    work = frame.copy()
    work["trading_day"] = pd.to_datetime(work["trading_day"]).dt.date
    work["event_time"] = pd.to_datetime(work["event_time"])
    work["available_at"] = pd.to_datetime(work["available_at"])
    if work[["event_time", "available_at"]].isna().any().any():
        raise ValueError("event and availability timestamps are required")
    if (work["available_at"] < work["event_time"]).any():
        raise ValueError("data cannot be available before its event time")
    if work.duplicated(["trading_day", "symbol"]).any():
        raise ValueError("duplicate trading_day/symbol rows")
    numeric = ["open", "high", "low", "close", "settlement", "volume", "open_interest"]
    if work[numeric].isna().any().any():
        raise ValueError("required numeric market data contains nulls")
    if (work[["open", "high", "low", "close", "settlement"]] <= 0).any().any():
        raise ValueError("prices must be positive")
    if (work[["volume", "open_interest"]] < 0).any().any():
        raise ValueError("volume and open interest must be non-negative")
    if ((work["high"] < work[["open", "close", "low"]].max(axis=1)) |
            (work["low"] > work[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("invalid OHLC relationship")
    for row in work.itertuples(index=False):
        spec = specs.resolve(row.symbol, row.trading_day)
        if spec.product != row.product:
            raise ValueError(f"product mismatch for {row.symbol}")
    sessions = work["trading_day"].nunique()
    expected = expected_symbols or set(work["symbol"].unique())
    observed = set(work["symbol"].unique())
    coverage = len(observed & expected) / max(len(expected), 1)
    if coverage < minimum_coverage:
        raise RuntimeError(f"market data coverage too low: {coverage:.1%}")
    versions = tuple(sorted(str(v) for v in work["source_version"].dropna().unique()))
    if not versions:
        raise ValueError("source version is required")
    return QualityReport(len(work), sessions, len(observed), coverage, versions)
