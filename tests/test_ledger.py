from datetime import date

import pytest

from futures_quant.contracts import ContractSpecStore
from futures_quant.ledger import FuturesAccount
from futures_quant.models import Fill, Offset, OrderSide
from conftest import make_spec


def fill(fill_id, side, offset, quantity, price, day=date(2026, 1, 5)):
    return Fill(fill_id, fill_id, day, "RB2605", side, offset, quantity, price, 0)


def test_long_position_is_marked_to_settlement_without_notional_cash_deduction():
    spec = make_spec()
    store = ContractSpecStore([spec])
    account = FuturesAccount(100_000)
    account.apply_fill(fill("open", OrderSide.BUY, Offset.OPEN, 2, 100), spec)
    assert account.balance == 100_000
    snap = account.settle(date(2026, 1, 5), {"RB2605": 105}, store)
    assert snap.equity == 100_100
    assert snap.margin == pytest.approx(105 * 10 * 2 * 0.12)
    account.apply_fill(fill("close", OrderSide.SELL, Offset.CLOSE, 2, 106, date(2026, 1, 6)), spec)
    assert account.balance == 100_120
    assert account.positions == {}


def test_short_profit_and_explicit_close_direction():
    spec = make_spec()
    store = ContractSpecStore([spec])
    account = FuturesAccount(100_000)
    account.apply_fill(fill("open", OrderSide.SELL, Offset.OPEN, 3, 100), spec)
    account.settle(date(2026, 1, 5), {"RB2605": 95}, store)
    assert account.balance == 100_150
    with pytest.raises(ValueError):
        account.apply_fill(fill("bad", OrderSide.SELL, Offset.CLOSE, 1, 94), spec)


def test_close_today_cannot_consume_overnight_position():
    spec = make_spec()
    store = ContractSpecStore([spec])
    account = FuturesAccount(100_000)
    account.apply_fill(fill("open", OrderSide.BUY, Offset.OPEN, 1, 100), spec)
    account.settle(date(2026, 1, 5), {"RB2605": 100}, store)
    with pytest.raises(ValueError):
        account.apply_fill(fill("close", OrderSide.SELL, Offset.CLOSE_TODAY, 1, 101), spec)

