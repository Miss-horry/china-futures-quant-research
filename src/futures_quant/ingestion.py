from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .data_quality import validate_bars
from .models import DailySettlementParameter
from .product_rules import ProductRule, build_specs_from_observed
from .sources.shfe import ShfeOfficialSource, SourceUnavailable


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_json(payload: dict, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temporary, path)


def ingest_shfe_product(
    source: ShfeOfficialSource,
    rule: ProductRule,
    trading_days: set[date],
    start: date,
    end: date,
    output_dir: str | Path,
    minimum_day_coverage: float = 0.95,
    require_limit_prices: bool = True,
) -> dict:
    expected = sorted(day for day in trading_days if start <= day <= end)
    if not expected:
        raise ValueError("calendar has no expected trading days in requested range")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    bars_parts: list[pd.DataFrame] = []
    parameters: list[DailySettlementParameter] = []
    failed: list[dict[str, str]] = []
    for day in expected:
        try:
            daily = source.fetch_daily_bars(day)
            daily = daily[daily["product"].astype(str).str.upper() == rule.product.upper()]
            params = [
                item for item in source.fetch_settlement_parameters(day)
                if item.symbol.upper().startswith(rule.product.upper())
            ]
            if daily.empty or not params:
                raise SourceUnavailable("product is absent from daily or settlement report")
            bars_parts.append(daily)
            parameters.extend(params)
        except SourceUnavailable as exc:
            failed.append({"trading_day": day.isoformat(), "reason": str(exc)[:500]})
    coverage = len(bars_parts) / len(expected)
    report = {
        "schema": "shfe_ingestion_report_v1", "created_at": datetime.now().astimezone().isoformat(),
        "exchange": rule.exchange, "product": rule.product, "start": start.isoformat(), "end": end.isoformat(),
        "expected_days": len(expected), "successful_days": len(bars_parts), "day_coverage": coverage,
        "failed_days": failed, "minimum_day_coverage": minimum_day_coverage,
        "require_limit_prices": require_limit_prices, "status": "blocked",
    }
    if coverage < minimum_day_coverage:
        report["block_reason"] = "day coverage below threshold"
        _atomic_json(report, output / "ingestion_report.json")
        return report
    bars = pd.concat(bars_parts, ignore_index=True)
    limit_coverage = float(bars[["upper_limit", "lower_limit"]].notna().all(axis=1).mean())
    report["limit_price_coverage"] = limit_coverage
    if require_limit_prices and limit_coverage < minimum_day_coverage:
        report["block_reason"] = "daily limit-price coverage below threshold"
        _atomic_json(report, output / "ingestion_report.json")
        return report
    specs = build_specs_from_observed(bars, rule, trading_days)
    from .contracts import ContractSpecStore

    store = ContractSpecStore(specs, parameters, require_daily_parameters=True)
    quality = validate_bars(bars, store)
    spec_frame = pd.DataFrame([asdict(spec) for spec in specs])
    parameter_frame = pd.DataFrame([asdict(parameter) for parameter in parameters])
    _atomic_csv(bars, output / "bars.csv")
    _atomic_csv(spec_frame, output / "contract_specs.csv")
    _atomic_csv(parameter_frame, output / "daily_parameters.csv")
    report["status"] = "published"
    report["data_quality"] = asdict(quality)
    report["contracts"] = len(specs)
    report["parameter_rows"] = len(parameters)
    _atomic_json(report, output / "ingestion_report.json")
    return report

