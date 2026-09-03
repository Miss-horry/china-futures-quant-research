from __future__ import annotations

from dataclasses import replace
from datetime import date

from .models import ContractSpec, DailySettlementParameter, FuturesBar


class ContractSpecStore:
    def __init__(
        self,
        specs: list[ContractSpec],
        daily_parameters: list[DailySettlementParameter] | None = None,
        require_daily_parameters: bool = False,
    ):
        self._specs: dict[str, list[ContractSpec]] = {}
        self._daily: dict[tuple[str, date], DailySettlementParameter] = {}
        for parameter in daily_parameters or []:
            key = (parameter.symbol.upper(), parameter.trading_day)
            if key in self._daily:
                raise ValueError(f"duplicate daily settlement parameter for {key[0]} on {key[1]}")
            self._daily[key] = parameter
        self.require_daily_parameters = require_daily_parameters
        for spec in specs:
            self._specs.setdefault(spec.symbol.upper(), []).append(spec)
        for versions in self._specs.values():
            versions.sort(key=lambda item: item.effective_from)

    def resolve(self, symbol: str, trading_day: date) -> ContractSpec:
        normalized = symbol.upper()
        matches = [s for s in self._specs.get(normalized, []) if s.active_on(trading_day)]
        if len(matches) != 1:
            raise KeyError(f"expected one active spec for {symbol} on {trading_day}, got {len(matches)}")
        base = matches[0]
        daily = self._daily.get((normalized, trading_day))
        if daily is None:
            if self.require_daily_parameters:
                raise KeyError(f"missing daily settlement parameter for {symbol} on {trading_day}")
            return base
        return replace(
            base,
            margin_rate=max(base.margin_rate, daily.conservative_margin_rate),
            maintenance_margin_rate=min(
                max(base.maintenance_margin_rate, daily.conservative_margin_rate),
                max(base.margin_rate, daily.conservative_margin_rate),
            ),
            open_fee_rate=max(base.open_fee_rate, daily.trade_fee_rate),
            close_fee_rate=max(base.close_fee_rate, daily.trade_fee_rate),
            close_today_fee_rate=max(base.close_today_fee_rate, daily.close_today_fee_rate),
            open_fee_per_lot=max(base.open_fee_per_lot, daily.trade_fee_per_lot),
            close_fee_per_lot=max(base.close_fee_per_lot, daily.trade_fee_per_lot),
            close_today_fee_per_lot=max(base.close_today_fee_per_lot, daily.close_today_fee_per_lot),
        )

    def symbols(self) -> set[str]:
        return set(self._specs)


class ContractSelector:
    """Selects a tradable contract using only same-day observable liquidity."""

    def __init__(
        self,
        minimum_days_to_expiry: int = 20,
        liquidity_switch_ratio: float = 1.15,
        volume_weight: float = 0.6,
        open_interest_weight: float = 0.4,
    ):
        self.minimum_days_to_expiry = minimum_days_to_expiry
        self.liquidity_switch_ratio = liquidity_switch_ratio
        self.volume_weight = volume_weight
        self.open_interest_weight = open_interest_weight

    def _score(self, bar: FuturesBar) -> float:
        return self.volume_weight * max(bar.volume, 0) + self.open_interest_weight * max(bar.open_interest, 0)

    def select(
        self,
        trading_day: date,
        chain: list[FuturesBar],
        specs: ContractSpecStore,
        previous_symbol: str | None = None,
    ) -> str:
        eligible: list[FuturesBar] = []
        for bar in chain:
            spec = specs.resolve(bar.symbol, trading_day)
            if (spec.last_trade_date - trading_day).days >= self.minimum_days_to_expiry:
                eligible.append(bar)
        if not eligible:
            raise RuntimeError(f"no contract has sufficient time to expiry on {trading_day}")
        best = max(eligible, key=self._score)
        previous = next((b for b in eligible if b.symbol == previous_symbol), None)
        if previous and self._score(best) < self._score(previous) * self.liquidity_switch_ratio:
            return previous.symbol
        return best.symbol
