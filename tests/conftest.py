from datetime import date

from futures_quant.models import ContractSpec


def make_spec(symbol: str = "RB2605", product: str = "RB", **overrides):
    values = dict(
        symbol=symbol, product=product, exchange="SHFE", multiplier=10,
        tick_size=1, margin_rate=0.12, maintenance_margin_rate=0.10,
        price_limit_rate=0.10, last_trade_date=date(2026, 5, 15),
        effective_from=date(2026, 1, 1), open_fee_rate=0.0,
        close_fee_rate=0.0, close_today_fee_rate=0.0,
    )
    values.update(overrides)
    return ContractSpec(**values)

