import json
from datetime import date, datetime, time

import pytest

from futures_quant.contracts import ContractSpecStore
from futures_quant.ledger import FuturesAccount
from futures_quant.models import Fill, Offset, OrderSide
from futures_quant.product_rules import contract_month, shifted_last_trade_date, load_product_rule
from futures_quant.sources.shfe import ShfeOfficialSource, SourceUnavailable
from futures_quant.trading_calendar import SessionWindow, TradingDayMapper, TradingSessionRule
from conftest import make_spec


DAY = date(2024, 1, 2)


def transport_from(payloads):
    calls = []

    def transport(url, timeout):
        calls.append((url, timeout))
        kind = "settlement" if "/js" in url else "daily"
        return json.dumps(payloads[kind], ensure_ascii=False).encode("utf-8")

    transport.calls = calls
    return transport


def test_shfe_source_parses_and_caches_official_payloads(tmp_path):
    daily = {"o_curinstrument": [{
        "PRODUCTGROUPID": "rb_f", "DELIVERYMONTH": "2405", "OPENPRICE": "3900",
        "HIGHESTPRICE": "3950", "LOWESTPRICE": "3880", "CLOSEPRICE": "3930",
        "SETTLEMENTPRICE": "3920", "PRESETTLEMENTPRICE": "3890", "VOLUME": "1,000",
        "OPENINTEREST": "2,000", "TURNOVER": "39200000",
    }]}
    # Position-based fixture mirrors the order used by the official SHFE js report.
    settlement_values = [
        "rb2405", "0.0001", "0.0002", "0", "12", "8", "0", "rb", "螺纹钢",
        "3", "2", "8", "3920", "0", "13", "1",
    ]
    settlement = {"o_cursor": [dict(zip([f"raw_{i}" for i in range(16)], settlement_values))]}
    transport = transport_from({"daily": daily, "settlement": settlement})
    source = ShfeOfficialSource(tmp_path, transport=transport)
    bars = source.fetch_daily_bars(DAY)
    params = source.fetch_settlement_parameters(DAY)
    assert bars.iloc[0]["symbol"] == "RB2405"
    assert bars.iloc[0]["settlement"] == 3920
    assert params[0].spec_long_margin_rate == pytest.approx(0.12)
    assert params[0].spec_short_margin_rate == pytest.approx(0.13)
    assert params[0].trade_fee_per_lot == 2
    assert params[0].close_today_fee_per_lot == 3
    first_calls = len(transport.calls)
    source.fetch_daily_bars(DAY)
    source.fetch_settlement_parameters(DAY)
    assert len(transport.calls) == first_calls


def test_source_failure_is_bounded_and_audited(tmp_path):
    def broken(url, timeout):
        raise TimeoutError("bounded timeout")

    source = ShfeOfficialSource(tmp_path, retries=2, transport=broken)
    with pytest.raises(SourceUnavailable, match="unavailable"):
        source.fetch_daily_bars(DAY)
    records = source.failures.records()
    assert len(records) == 1 and records[0]["attempts"] == 2


def test_daily_parameters_override_upward_and_preserve_per_lot_fees(tmp_path):
    daily = {"o_curinstrument": []}
    settlement_values = [
        "rb2405", "0", "0", "0", "12", "8", "0", "rb", "螺纹钢",
        "3", "2", "8", "3920", "0", "13", "1",
    ]
    source = ShfeOfficialSource(
        tmp_path, transport=transport_from({
            "daily": daily,
            "settlement": {"o_cursor": [dict(zip([f"k{i}" for i in range(16)], settlement_values))]},
        })
    )
    parameter = source.fetch_settlement_parameters(DAY)[0]
    base = make_spec(
        "RB2405", margin_rate=0.10, maintenance_margin_rate=0.09,
        effective_from=date(2023, 1, 1), last_trade_date=date(2024, 5, 15),
    )
    store = ContractSpecStore([base], [parameter], require_daily_parameters=True)
    resolved = store.resolve("RB2405", DAY)
    assert resolved.margin_rate == pytest.approx(0.13)
    account = FuturesAccount(100_000)
    fill = Fill("f", "o", DAY, "RB2405", OrderSide.BUY, Offset.OPEN, 2, 3900, 0)
    assert account.apply_fill(fill, resolved) == 4


def test_daily_parameter_duplicates_are_rejected(tmp_path):
    settlement_values = [
        "rb2405", "0", "0", "0", "12", "8", "0", "rb", "rebar",
        "3", "2", "8", "3920", "0", "13", "1",
    ]
    source = ShfeOfficialSource(
        tmp_path, transport=transport_from({
            "daily": {"o_curinstrument": []},
            "settlement": {"o_cursor": [dict(zip([f"k{i}" for i in range(16)], settlement_values))]},
        })
    )
    parameter = source.fetch_settlement_parameters(DAY)[0]
    with pytest.raises(ValueError, match="duplicate daily"):
        ContractSpecStore([make_spec("RB2405")], [parameter, parameter])


def test_night_session_maps_friday_to_monday_with_explicit_night_calendar():
    trading_days = {
        date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)
    }
    rule = TradingSessionRule(
        "SHFE", "RB", date(2026, 1, 1), None,
        (SessionWindow(time(9), time(11, 30), "day_am"), SessionWindow(time(13, 30), time(15), "day_pm")),
        (SessionWindow(time(21), time(23), "night"),),
    )
    mapper = TradingDayMapper(
        trading_days, [rule], night_session_dates={date(2026, 8, 12), date(2026, 8, 14)}
    )
    assert mapper.map(datetime(2026, 8, 12, 21, 30), "SHFE", "RB") == date(2026, 8, 13)
    assert mapper.map(datetime(2026, 8, 14, 21, 30), "SHFE", "RB") == date(2026, 8, 17)


def test_night_session_fails_closed_on_exchange_holiday_eve():
    trading_days = {date(2026, 4, 3), date(2026, 4, 7)}
    rule = TradingSessionRule(
        "SHFE", "RB", date(2026, 1, 1), None,
        (SessionWindow(time(9), time(15), "day"),),
        (SessionWindow(time(21), time(23), "night"),),
    )
    mapper = TradingDayMapper(trading_days, [rule], night_session_dates=set())
    with pytest.raises(ValueError, match="no night session"):
        mapper.map(datetime(2026, 4, 3, 21, 30), "SHFE", "RB")


def test_product_rule_resolves_contract_month_and_holiday_shift():
    rule = load_product_rule("config/product_rules/shfe_rb.yaml")
    assert contract_month("RB2410") == (2024, 10)
    calendar = {date(2024, 9, 13), date(2024, 9, 18), date(2024, 9, 19)}
    assert shifted_last_trade_date("RB2409", rule, calendar) == date(2024, 9, 18)
