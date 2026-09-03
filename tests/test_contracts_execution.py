from datetime import date, datetime

from futures_quant.contracts import ContractSelector, ContractSpecStore
from futures_quant.execution import ExecutionSimulator
from futures_quant.models import FuturesBar, Offset, Order, OrderSide, OrderStatus
from conftest import make_spec


DAY = date(2026, 1, 5)


def bar(symbol, volume, oi, price=100, upper=None, lower=None):
    stamp = datetime(2026, 1, 5, 15, 0)
    return FuturesBar(DAY, stamp, stamp, symbol, "RB", price, price, price, price, price, volume, oi, upper, lower)


def test_contract_selector_uses_hysteresis_and_then_switches():
    specs = ContractSpecStore([
        make_spec("RB2605"),
        make_spec("RB2609", last_trade_date=date(2026, 9, 15)),
    ])
    selector = ContractSelector(liquidity_switch_ratio=1.15)
    mild = [bar("RB2605", 100, 100), bar("RB2609", 110, 110)]
    assert selector.select(DAY, mild, specs, "RB2605") == "RB2605"
    strong = [bar("RB2605", 100, 100), bar("RB2609", 140, 140)]
    assert selector.select(DAY, strong, specs, "RB2605") == "RB2609"


def test_limit_locked_order_is_rejected_not_filled_at_fantasy_price():
    spec = make_spec()
    order = Order("o", "d", datetime.now(), DAY, "RB2605", "RB", OrderSide.BUY, Offset.OPEN, 2)
    pinned = bar("RB2605", 10_000, 10_000, price=110, upper=110, lower=90)
    assert ExecutionSimulator().execute(order, pinned, spec) is None
    assert order.status is OrderStatus.REJECTED


def test_volume_participation_creates_partial_fill():
    spec = make_spec()
    order = Order("o", "d", datetime.now(), DAY, "RB2605", "RB", OrderSide.BUY, Offset.OPEN, 10)
    stamp = datetime(2026, 1, 5, 15, 0)
    liquid = FuturesBar(DAY, stamp, stamp, "RB2605", "RB", 100, 102, 99, 101, 101, 100, 1000, 110, 90)
    fill = ExecutionSimulator(max_volume_participation=0.05).execute(order, liquid, spec)
    assert fill is not None and fill.quantity == 5
    assert order.status is OrderStatus.PARTIAL
