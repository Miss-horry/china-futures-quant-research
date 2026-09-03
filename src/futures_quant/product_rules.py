from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

from .models import ContractSpec


@dataclass(frozen=True)
class ProductRule:
    product: str
    exchange: str
    multiplier: float
    tick_size: float
    conservative_margin_rate: float
    maintenance_margin_rate: float
    base_price_limit_rate: float
    last_trade_day_of_month: int
    settlement_type: str
    version: str
    source_url: str


def load_product_rule(path: str | Path) -> ProductRule:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    fields = set(ProductRule.__dataclass_fields__)
    return ProductRule(**{key: value for key, value in data.items() if key in fields})


def contract_month(symbol: str, pivot_year: int = 2030) -> tuple[int, int]:
    match = re.fullmatch(r"[A-Za-z]+(\d{4})", symbol)
    if not match:
        raise ValueError(f"unsupported contract symbol: {symbol}")
    digits = match.group(1)
    year_2, month = int(digits[:2]), int(digits[2:])
    pivot_2 = pivot_year % 100
    century = pivot_year - pivot_2
    year = century + year_2 if year_2 <= pivot_2 else century - 100 + year_2
    if not 1 <= month <= 12:
        raise ValueError(f"invalid contract month in {symbol}")
    return year, month


def shifted_last_trade_date(symbol: str, rule: ProductRule, trading_days: set[date]) -> date:
    year, month = contract_month(symbol)
    candidate = date(year, month, rule.last_trade_day_of_month)
    valid = sorted(day for day in trading_days if day >= candidate)
    if not valid:
        raise KeyError(f"calendar cannot resolve last trade date for {symbol}")
    return valid[0]


def build_specs_from_observed(
    bars: pd.DataFrame, rule: ProductRule, trading_days: set[date]
) -> list[ContractSpec]:
    work = bars[bars["product"].astype(str).str.upper() == rule.product.upper()].copy()
    if work.empty:
        raise ValueError(f"no observed bars for {rule.product}")
    work["trading_day"] = pd.to_datetime(work["trading_day"]).dt.date
    specs: list[ContractSpec] = []
    for symbol, group in work.groupby(work["symbol"].astype(str).str.upper()):
        specs.append(ContractSpec(
            symbol=symbol, product=rule.product.upper(), exchange=rule.exchange,
            multiplier=rule.multiplier, tick_size=rule.tick_size,
            margin_rate=rule.conservative_margin_rate,
            maintenance_margin_rate=rule.maintenance_margin_rate,
            price_limit_rate=rule.base_price_limit_rate,
            last_trade_date=shifted_last_trade_date(symbol, rule, trading_days),
            effective_from=min(group["trading_day"]), effective_to=None,
            settlement_type=rule.settlement_type, version=rule.version,
        ))
    return specs
