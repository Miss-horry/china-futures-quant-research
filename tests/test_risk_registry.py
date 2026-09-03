from datetime import date, datetime

import pytest

from futures_quant.contracts import ContractSpecStore
from futures_quant.ledger import FuturesAccount
from futures_quant.models import Offset, Order, OrderSide
from futures_quant.registry import PromotionEvidence, StrategyRegistry
from futures_quant.risk import RiskEngine, RiskLimits
from conftest import make_spec


def test_risk_rejects_leveraged_open_but_never_blocks_reducing_close():
    day = date(2026, 1, 5)
    spec = make_spec()
    store = ContractSpecStore([spec])
    account = FuturesAccount(100_000)
    engine = RiskEngine(RiskLimits(max_gross_leverage=1.0))
    open_order = Order("o", "d", datetime.now(), day, "RB2605", "RB", OrderSide.BUY, Offset.OPEN, 200)
    decision = engine.check(account, open_order, 100, day, {"RB2605": 100}, store, 100_000)
    assert not decision.accepted
    close_order = Order("c", "d", datetime.now(), day, "RB2605", "RB", OrderSide.SELL, Offset.CLOSE, 1)
    assert engine.check(account, close_order, 100, day, {"RB2605": 100}, store, 100_000).accepted


def evidence(**overrides):
    values = dict(walk_forward_folds=3, positive_folds=2, net_return_positive=True,
                  stress_drawdown_passed=True, shadow_sessions=120, filled_orders=100,
                  multiple_regimes=True, reproducible=True)
    values.update(overrides)
    return PromotionEvidence(**values)


def test_strategy_promotion_requires_both_evidence_and_explicit_approval(tmp_path):
    registry = StrategyRegistry(tmp_path / "registry.json")
    registry.register("candidate", {"window": 20}, evidence())
    with pytest.raises(PermissionError):
        registry.promote("candidate")
    registry.promote("candidate", explicit_approval=True)
    assert registry._load()["champion"] == "candidate"


def test_future_shadow_time_cannot_be_fabricated(tmp_path):
    registry = StrategyRegistry(tmp_path / "registry.json")
    registry.register("too_early", {}, evidence(shadow_sessions=5))
    with pytest.raises(ValueError, match="shadow"):
        registry.promote("too_early", explicit_approval=True)

