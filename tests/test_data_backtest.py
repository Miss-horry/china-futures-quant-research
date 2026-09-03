import json

import pandas as pd
import pytest

from futures_quant.backtest import BacktestEngine
from futures_quant.cli import synthetic_demo
from futures_quant.contracts import ContractSelector, ContractSpecStore
from futures_quant.data_quality import validate_bars
from futures_quant.execution import ExecutionSimulator
from futures_quant.io import load_bars_csv, load_contract_specs_csv, load_daily_parameters_csv
from futures_quant.risk import RiskEngine, RiskLimits
from futures_quant.strategy import TimeSeriesTrendStrategy


def test_data_quality_fails_closed_on_duplicates():
    frame, specs = synthetic_demo()
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        validate_bars(duplicate, ContractSpecStore(specs))


def test_data_quality_rejects_impossible_availability_time():
    frame, specs = synthetic_demo()
    frame.loc[0, "available_at"] = frame.loc[0, "event_time"] - pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="available before"):
        validate_bars(frame, ContractSpecStore(specs))


def test_end_to_end_uses_actual_contracts_and_three_ledgers(tmp_path):
    frame, specs = synthetic_demo()
    engine = BacktestEngine(
        ContractSpecStore(specs), ContractSelector(), TimeSeriesTrendStrategy(),
        RiskEngine(RiskLimits()), ExecutionSimulator(), 1_000_000, tmp_path,
    )
    result = engine.run(frame)
    assert result["fills"] > 0
    assert result["margin_calls"] == 0
    assert result["peak_margin_utilization"] <= 0.35
    for name in ("prediction_ledger.jsonl", "strategy_ledger.jsonl", "execution_ledger.jsonl"):
        path = tmp_path / name
        assert path.exists() and path.stat().st_size > 0
        for line in path.read_text(encoding="utf-8").splitlines():
            json.loads(line)
    traded = (tmp_path / "execution_ledger.jsonl").read_text(encoding="utf-8")
    assert "RB2605" in traded and "RB2609" in traded
    assert "continuous" not in traded.lower()
    predictions = [json.loads(line) for line in (tmp_path / "prediction_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    decisions = [json.loads(line) for line in (tmp_path / "strategy_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    executions = [json.loads(line) for line in (tmp_path / "execution_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    prediction_ids = {row["prediction_id"] for row in predictions}
    decision_ids = {row["decision_id"] for row in decisions}
    order_ids = {row["order_id"] for row in executions if row["type"] == "order"}
    assert all(row["prediction_id"] in prediction_ids for row in decisions)
    assert all(row["decision_id"] in decision_ids for row in executions if row["type"] == "order")
    assert all(row["order_id"] in order_ids for row in executions if row["type"] == "fill")


def test_csv_adapters_preserve_actual_contract_and_time_fields(tmp_path):
    frame, specs = synthetic_demo()
    bars_path = tmp_path / "bars.csv"
    specs_path = tmp_path / "specs.csv"
    frame.to_csv(bars_path, index=False)
    pd.DataFrame([{
        "symbol": s.symbol, "product": s.product, "exchange": s.exchange,
        "multiplier": s.multiplier, "tick_size": s.tick_size, "margin_rate": s.margin_rate,
        "maintenance_margin_rate": s.maintenance_margin_rate, "price_limit_rate": s.price_limit_rate,
        "last_trade_date": s.last_trade_date, "effective_from": s.effective_from,
        "version": s.version,
    } for s in specs]).to_csv(specs_path, index=False)
    loaded_bars = load_bars_csv(bars_path)
    loaded_specs = load_contract_specs_csv(specs_path)
    assert set(loaded_bars["symbol"]) == {"RB2605", "RB2609"}
    assert loaded_bars["available_at"].min() >= loaded_bars["event_time"].min()
    assert {s.symbol for s in loaded_specs} == {"RB2605", "RB2609"}
