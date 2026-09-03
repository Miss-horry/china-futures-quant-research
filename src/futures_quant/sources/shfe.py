from __future__ import annotations

import hashlib
import json
import os
import re
import time as time_module
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

from ..audit import AtomicJsonlLedger
from ..models import DailySettlementParameter


class SourceUnavailable(RuntimeError):
    pass


Transport = Callable[[str, tuple[float, float]], bytes]


def _number(value, default: float = 0.0) -> float:
    if value in (None, "", "-") or pd.isna(value):
        return default
    return float(str(value).replace(",", "").strip())


def _ratio(value) -> float:
    number = _number(value)
    return number / 100 if number > 1 else number


def _row_value(row: dict, aliases: tuple[str, ...], position: int):
    for alias in aliases:
        if alias in row:
            return row[alias]
    values = list(row.values())
    return values[position] if position < len(values) else None


def _default_transport(url: str, timeout: tuple[float, float]) -> bytes:
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; FuturesQuant/0.2)"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.content


class ShfeOfficialSource:
    """Bounded, cached adapter for SHFE official daily `.dat` reports."""

    DAILY_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/kx{day}.dat"
    SETTLEMENT_URL = "https://www.shfe.com.cn/data/tradedata/future/dailydata/js{day}.dat"

    def __init__(
        self,
        cache_dir: str | Path,
        timeout: tuple[float, float] = (5.0, 20.0),
        retries: int = 2,
        transport: Transport | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.retries = retries
        self.transport = transport or _default_transport
        self.failures = AtomicJsonlLedger(self.cache_dir / "source_failures.jsonl")

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)

    def _get(self, kind: str, trading_day: date) -> tuple[dict, str, datetime]:
        day = trading_day.strftime("%Y%m%d")
        raw_path = self.cache_dir / f"{kind}_{day}.json"
        meta_path = self.cache_dir / f"{kind}_{day}.meta.json"
        if raw_path.exists() and meta_path.exists():
            raw = raw_path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return json.loads(raw), meta["sha256"], datetime.fromisoformat(meta["retrieved_at"])
        template = self.DAILY_URL if kind == "daily" else self.SETTLEMENT_URL
        url = template.format(day=day)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                raw = self.transport(url, self.timeout)
                payload = json.loads(raw)
                sha = hashlib.sha256(raw).hexdigest()
                retrieved = datetime.now().astimezone()
                meta = {
                    "source": "SHFE_OFFICIAL", "kind": kind, "trading_day": trading_day.isoformat(),
                    "url": url, "sha256": sha, "retrieved_at": retrieved.isoformat(),
                }
                self._atomic_write(raw_path, raw)
                self._atomic_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
                return payload, sha, retrieved
            except Exception as exc:  # bounded retry with attributable failure
                last_error = exc
                if attempt < self.retries:
                    time_module.sleep(0.2 * attempt)
        self.failures.append({
            "at": datetime.now().astimezone(), "source": "SHFE_OFFICIAL", "kind": kind,
            "trading_day": trading_day, "url": url, "error_type": type(last_error).__name__,
            "error": str(last_error)[:500], "attempts": self.retries,
        })
        raise SourceUnavailable(f"SHFE {kind} unavailable for {trading_day}: {last_error}")

    def fetch_daily_bars(self, trading_day: date) -> pd.DataFrame:
        payload, sha, retrieved = self._get("daily", trading_day)
        rows = payload.get("o_curinstrument", [])
        result: list[dict[str, object]] = []
        for row in rows:
            delivery = str(row.get("DELIVERYMONTH", "")).strip()
            if not delivery or delivery in {"小计", "合计"}:
                continue
            product_raw = str(row.get("PRODUCTGROUPID") or row.get("PRODUCTID") or "")
            product = product_raw.split("_")[0].strip().upper()
            if not product:
                continue
            result.append({
                "trading_day": trading_day,
                "event_time": datetime.combine(trading_day, time(15, 0)),
                "available_at": datetime.combine(trading_day, time(16, 0)),
                "retrieved_at": retrieved,
                "symbol": f"{product}{delivery}", "product": product,
                "open": _number(row.get("OPENPRICE")), "high": _number(row.get("HIGHESTPRICE")),
                "low": _number(row.get("LOWESTPRICE")), "close": _number(row.get("CLOSEPRICE")),
                "settlement": _number(row.get("SETTLEMENTPRICE")),
                "pre_settlement": _number(row.get("PRESETTLEMENTPRICE")),
                "volume": int(_number(row.get("VOLUME"))),
                "open_interest": int(_number(row.get("OPENINTEREST"))),
                "turnover": _number(row.get("TURNOVER")),
                "upper_limit": _number(row.get("UPPERLIMITPRICE"), default=float("nan")),
                "lower_limit": _number(row.get("LOWERLIMITPRICE"), default=float("nan")),
                "source_version": f"shfe_official_daily:{sha[:16]}",
            })
        if not result:
            raise SourceUnavailable(f"SHFE official daily payload contains no contracts for {trading_day}")
        return pd.DataFrame(result)

    def fetch_settlement_parameters(self, trading_day: date) -> list[DailySettlementParameter]:
        payload, sha, _ = self._get("settlement", trading_day)
        result: list[DailySettlementParameter] = []
        for row in payload.get("o_cursor", []):
            symbol = str(_row_value(row, ("INSTRUMENTID", "symbol"), 0) or "").strip().upper()
            if not re.fullmatch(r"[A-Z]+\d{3,4}", symbol):
                continue
            settle_price = _row_value(row, ("SETTLEMENTPRICE", "settle_price"), 12)
            long_margin = _row_value(row, ("SPECLONGMARGINRATIO", "spec_long_margin_ratio"), 4)
            short_margin = _row_value(row, ("SPECSHORTMARGINRATIO", "spec_short_margin_ratio"), 14)
            if _number(settle_price) <= 0 or _ratio(long_margin) <= 0 or _ratio(short_margin) <= 0:
                continue
            close_flag = _row_value(row, ("ISCLOSETODAY", "is_close_today"), 15)
            result.append(DailySettlementParameter(
                trading_day=trading_day, symbol=symbol,
                settlement_price=_number(settle_price),
                spec_long_margin_rate=_ratio(long_margin),
                spec_short_margin_rate=_ratio(short_margin),
                trade_fee_rate=_number(_row_value(row, ("TRADINGFEERATE", "trade_fee_ratio"), 1)),
                trade_fee_per_lot=_number(_row_value(row, ("TRADINGFEEUNIT", "trade_fee_unit"), 10)),
                close_today_fee_rate=_number(_row_value(row, ("CLOSETODAYFEERATE", "close_today_fee_ratio"), 2)),
                close_today_fee_per_lot=_number(_row_value(row, ("CLOSETODAYFEEUNIT", "close_today_fee_unit"), 9)),
                close_today_enabled=str(close_flag if close_flag is not None else "1") not in {"0", "False", "false"},
                source_version=f"shfe_official_settlement:{sha[:16]}",
            ))
        if not result:
            raise SourceUnavailable(f"SHFE official settlement payload contains no contracts for {trading_day}")
        return result
