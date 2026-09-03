from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .models import ContractSpec, DailySettlementParameter


SPEC_REQUIRED = {
    "symbol", "product", "exchange", "multiplier", "tick_size", "margin_rate",
    "maintenance_margin_rate", "price_limit_rate", "last_trade_date", "effective_from",
}


def load_contract_specs_csv(path: str | Path) -> list[ContractSpec]:
    frame = pd.read_csv(path)
    missing = SPEC_REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"contract spec file missing columns: {sorted(missing)}")
    specs: list[ContractSpec] = []
    for row in frame.to_dict("records"):
        effective_to = row.get("effective_to")
        if pd.isna(effective_to) or effective_to in (None, ""):
            effective_to = None
        else:
            effective_to = date.fromisoformat(str(effective_to))
        specs.append(ContractSpec(
            symbol=str(row["symbol"]), product=str(row["product"]), exchange=str(row["exchange"]),
            multiplier=float(row["multiplier"]), tick_size=float(row["tick_size"]),
            margin_rate=float(row["margin_rate"]),
            maintenance_margin_rate=float(row["maintenance_margin_rate"]),
            price_limit_rate=float(row["price_limit_rate"]),
            last_trade_date=date.fromisoformat(str(row["last_trade_date"])),
            effective_from=date.fromisoformat(str(row["effective_from"])), effective_to=effective_to,
            open_fee_rate=float(row.get("open_fee_rate", 0.00005)),
            close_fee_rate=float(row.get("close_fee_rate", 0.00005)),
            close_today_fee_rate=float(row.get("close_today_fee_rate", 0.0001)),
            open_fee_per_lot=float(row.get("open_fee_per_lot", 0.0)),
            close_fee_per_lot=float(row.get("close_fee_per_lot", 0.0)),
            close_today_fee_per_lot=float(row.get("close_today_fee_per_lot", 0.0)),
            settlement_type=str(row.get("settlement_type", "physical")),
            version=str(row.get("version", "imported_v1")),
        ))
    return specs


def load_daily_parameters_csv(path: str | Path) -> list[DailySettlementParameter]:
    frame = pd.read_csv(path)
    required = {
        "trading_day", "symbol", "settlement_price", "spec_long_margin_rate",
        "spec_short_margin_rate", "source_version",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"daily parameter file missing columns: {sorted(missing)}")
    result: list[DailySettlementParameter] = []
    for row in frame.to_dict("records"):
        result.append(DailySettlementParameter(
            trading_day=date.fromisoformat(str(row["trading_day"])), symbol=str(row["symbol"]).upper(),
            settlement_price=float(row["settlement_price"]),
            spec_long_margin_rate=float(row["spec_long_margin_rate"]),
            spec_short_margin_rate=float(row["spec_short_margin_rate"]),
            trade_fee_rate=float(row.get("trade_fee_rate", 0.0)),
            trade_fee_per_lot=float(row.get("trade_fee_per_lot", 0.0)),
            close_today_fee_rate=float(row.get("close_today_fee_rate", 0.0)),
            close_today_fee_per_lot=float(row.get("close_today_fee_per_lot", 0.0)),
            close_today_enabled=str(row.get("close_today_enabled", "True")).lower() not in {"0", "false"},
            source_version=str(row["source_version"]),
        ))
    return result


def load_bars_csv(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("trading_day", "event_time", "available_at"):
        if column not in frame:
            raise ValueError(f"bar file missing column: {column}")
    frame["trading_day"] = pd.to_datetime(frame["trading_day"]).dt.date
    frame["event_time"] = pd.to_datetime(frame["event_time"])
    frame["available_at"] = pd.to_datetime(frame["available_at"])
    return frame
