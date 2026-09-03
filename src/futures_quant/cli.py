from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import BacktestEngine
from .config import load_config
from .contracts import ContractSelector, ContractSpecStore
from .execution import ExecutionSimulator
from .io import load_bars_csv, load_contract_specs_csv, load_daily_parameters_csv
from .ingestion import ingest_shfe_product
from .models import ContractSpec
from .risk import RiskEngine, RiskLimits
from .product_rules import load_product_rule
from .reference_data import import_calendar, load_calendar
from .sources import ShfeOfficialSource
from .strategy import TimeSeriesTrendStrategy


def synthetic_demo() -> tuple[pd.DataFrame, list[ContractSpec]]:
    days = pd.bdate_range("2026-01-05", periods=80)
    specs = [
        ContractSpec("RB2605", "RB", "SHFE", 10, 1, 0.12, 0.10, 0.10, date(2026, 5, 15), date(2026, 1, 1)),
        ContractSpec("RB2609", "RB", "SHFE", 10, 1, 0.12, 0.10, 0.10, date(2026, 9, 15), date(2026, 1, 1)),
    ]
    rows: list[dict[str, object]] = []
    rng = np.random.default_rng(20260811)
    common = 3400 + np.concatenate([np.linspace(0, 180, 42), np.linspace(180, -80, 38)])
    for i, ts in enumerate(days):
        for symbol, basis in (("RB2605", 0.0), ("RB2609", 35.0)):
            close = float(common[i] + basis + rng.normal(0, 6))
            open_price = close + float(rng.normal(0, 4))
            high = max(open_price, close) + 12
            low = min(open_price, close) - 12
            switch = i < 38
            old_liquid = symbol == "RB2605"
            volume = 120_000 if switch == old_liquid else 55_000
            oi = 180_000 if switch == old_liquid else 75_000
            rows.append({
                "trading_day": ts.date(), "symbol": symbol, "product": "RB",
                "event_time": ts + pd.Timedelta(hours=15),
                "available_at": ts + pd.Timedelta(hours=15, minutes=2),
                "open": open_price, "high": high, "low": low, "close": close,
                "settlement": close + float(rng.normal(0, 2)), "volume": volume, "open_interest": oi,
                "upper_limit": close * 1.10, "lower_limit": close * 0.90,
                "source_version": "synthetic_demo_v1",
            })
    return pd.DataFrame(rows), specs


def run_demo(output: str = "artifacts/demo") -> dict[str, object]:
    frame, raw_specs = synthetic_demo()
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    # The demo is an idempotent verification run, not an accumulating shadow ledger.
    for name in ("prediction_ledger.jsonl", "strategy_ledger.jsonl", "execution_ledger.jsonl", "backtest_summary.json"):
        path = output_path / name
        if path.exists():
            path.unlink()
    engine = BacktestEngine(
        ContractSpecStore(raw_specs), ContractSelector(), TimeSeriesTrendStrategy(),
        RiskEngine(RiskLimits()), ExecutionSimulator(), 1_000_000, output_path,
    )
    result = engine.run(frame)
    result["synthetic_or_real"] = "synthetic"
    (output_path / "backtest_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def run_csv_backtest(
    bars: str, specs: str, output: str, config_path: str,
    daily_parameters: str | None = None,
) -> dict[str, object]:
    config = load_config(config_path)
    selector_cfg = config["contract_selection"]
    risk_cfg = config["risk"]
    execution_cfg = config["execution"]
    research_cfg = config["research"]
    engine = BacktestEngine(
        ContractSpecStore(
            load_contract_specs_csv(specs),
            load_daily_parameters_csv(daily_parameters) if daily_parameters else None,
            require_daily_parameters=bool(daily_parameters),
        ),
        ContractSelector(**selector_cfg),
        TimeSeriesTrendStrategy(
            fast_window=research_cfg["fast_window"], slow_window=research_cfg["slow_window"],
            atr_window=research_cfg["atr_window"], annual_risk_budget=research_cfg["annual_risk_budget"],
        ),
        RiskEngine(RiskLimits(**risk_cfg)), ExecutionSimulator(**execution_cfg),
        float(research_cfg["initial_capital_cny"]), output,
        strategy_version=str(research_cfg["strategy_version"]),
    )
    result = engine.run(load_bars_csv(bars))
    result["synthetic_or_real"] = "external_csv"
    result["daily_parameter_mode"] = "required_dynamic" if daily_parameters else "static_fallback"
    output_path = Path(output)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "backtest_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Chinese futures research system")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="run a deterministic synthetic end-to-end demo")
    demo.add_argument("--output", default="artifacts/demo")
    backtest = sub.add_parser("backtest", help="run a fail-closed backtest from external CSV files")
    backtest.add_argument("--bars", required=True)
    backtest.add_argument("--specs", required=True)
    backtest.add_argument("--output", default="artifacts/backtest")
    backtest.add_argument("--config", default="config/default.yaml")
    backtest.add_argument("--daily-parameters")
    calendar = sub.add_parser("import-calendar", help="import and hash an explicit trading-calendar artifact")
    calendar.add_argument("--input", required=True)
    calendar.add_argument("--output", default="data/reference/trading_calendar.json")
    calendar.add_argument("--source-label", required=True)
    ingest = sub.add_parser("ingest-shfe", help="ingest cached/official SHFE daily reports with coverage gates")
    ingest.add_argument("--product-rule", default="config/product_rules/shfe_rb.yaml")
    ingest.add_argument("--calendar", default="data/reference/trading_calendar.json")
    ingest.add_argument("--start", required=True)
    ingest.add_argument("--end", required=True)
    ingest.add_argument("--output", default="data/canonical/shfe_rb")
    ingest.add_argument("--cache", default="data/raw/shfe")
    ingest.add_argument("--allow-missing-limits", action="store_true")
    args = parser.parse_args()
    if args.command == "demo":
        print(json.dumps(run_demo(args.output), ensure_ascii=False, indent=2))
    elif args.command == "backtest":
        print(json.dumps(run_csv_backtest(
            args.bars, args.specs, args.output, args.config, args.daily_parameters
        ), ensure_ascii=False, indent=2))
    elif args.command == "import-calendar":
        artifact = import_calendar(args.input, args.output, args.source_label)
        summary = {key: value for key, value in artifact.items() if key != "dates"}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.command == "ingest-shfe":
        days, _ = load_calendar(args.calendar)
        report = ingest_shfe_product(
            ShfeOfficialSource(args.cache), load_product_rule(args.product_rule), days,
            date.fromisoformat(args.start), date.fromisoformat(args.end), args.output,
            require_limit_prices=not args.allow_missing_limits,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
