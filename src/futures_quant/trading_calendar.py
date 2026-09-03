from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class SessionWindow:
    start: time
    end: time
    name: str

    def contains(self, value: time) -> bool:
        if self.start <= self.end:
            return self.start <= value <= self.end
        return value >= self.start or value <= self.end


@dataclass(frozen=True)
class TradingSessionRule:
    exchange: str
    product: str
    effective_from: date
    effective_to: date | None
    day_sessions: tuple[SessionWindow, ...]
    night_sessions: tuple[SessionWindow, ...] = ()

    def active_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day <= self.effective_to)


class TradingDayMapper:
    """Map natural timestamps to exchange trading days.

    Night sessions need their own official calendar: a regular trading-day calendar
    cannot tell us that a holiday-eve night session was cancelled.
    """

    def __init__(
        self,
        trading_days: set[date],
        rules: list[TradingSessionRule],
        night_session_dates: set[date] | None = None,
    ):
        self.trading_days = set(trading_days)
        self.rules = rules
        self.night_session_dates = None if night_session_dates is None else set(night_session_dates)

    def _rule(self, exchange: str, product: str, candidate_day: date) -> TradingSessionRule:
        matches = [
            rule for rule in self.rules
            if rule.exchange == exchange and rule.product == product and rule.active_on(candidate_day)
        ]
        if len(matches) != 1:
            raise KeyError(f"expected one session rule for {exchange}/{product} on {candidate_day}")
        return matches[0]

    def next_trading_day(self, after: date) -> date:
        candidates = sorted(day for day in self.trading_days if day > after)
        if not candidates:
            raise KeyError(f"no trading day after {after}")
        return candidates[0]

    def map(self, timestamp: datetime, exchange: str, product: str) -> date:
        calendar_day = timestamp.date()
        clock = timestamp.time()
        if clock >= time(20, 0) or clock < time(8, 0):
            session_start_day = calendar_day if clock >= time(20, 0) else calendar_day - timedelta(days=1)
            if self.night_session_dates is None:
                raise ValueError("official night-session calendar is required")
            if session_start_day not in self.night_session_dates:
                raise ValueError("no night session is configured for this natural date")
            if session_start_day not in self.trading_days:
                raise ValueError("night session must start on an exchange trading day")
            trading_day = self.next_trading_day(session_start_day)
            rule = self._rule(exchange, product, trading_day)
            if not any(window.contains(clock) for window in rule.night_sessions):
                raise ValueError("timestamp is outside configured night sessions")
            return trading_day
        if calendar_day not in self.trading_days:
            raise ValueError("day session timestamp is not on a trading day")
        rule = self._rule(exchange, product, calendar_day)
        if not any(window.contains(clock) for window in rule.day_sessions):
            raise ValueError("timestamp is outside configured day sessions")
        return calendar_day
