from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromotionEvidence:
    walk_forward_folds: int
    positive_folds: int
    net_return_positive: bool
    stress_drawdown_passed: bool
    shadow_sessions: int
    filled_orders: int
    multiple_regimes: bool
    reproducible: bool


class StrategyRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"champion": None, "strategies": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def register(self, version: str, parameters: dict[str, Any], evidence: PromotionEvidence) -> None:
        data = self._load()
        data["strategies"][version] = {
            "version": version,
            "status": "challenger",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "parameters": parameters,
            "evidence": asdict(evidence),
        }
        self._save(data)

    @staticmethod
    def _eligible(e: dict[str, Any]) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if e["walk_forward_folds"] < 3 or e["positive_folds"] < 2:
            failures.append("insufficient positive walk-forward folds")
        if not e["net_return_positive"]:
            failures.append("cost-adjusted return is not positive")
        if not e["stress_drawdown_passed"]:
            failures.append("stress drawdown gate failed")
        if e["shadow_sessions"] < 120:
            failures.append("insufficient real-time shadow sessions")
        if e["filled_orders"] < 100:
            failures.append("insufficient filled orders")
        if not e["multiple_regimes"]:
            failures.append("market regime coverage is insufficient")
        if not e["reproducible"]:
            failures.append("research artifact is not reproducible")
        return not failures, failures

    def promote(self, version: str, explicit_approval: bool = False) -> None:
        if not explicit_approval:
            raise PermissionError("automatic promotion is disabled")
        data = self._load()
        candidate = data["strategies"].get(version)
        if not candidate:
            raise KeyError(version)
        eligible, failures = self._eligible(candidate["evidence"])
        if not eligible:
            raise ValueError("candidate is not promotable: " + "; ".join(failures))
        old = data.get("champion")
        if old and old in data["strategies"]:
            data["strategies"][old]["status"] = "archived"
        candidate["status"] = "champion"
        data["champion"] = version
        self._save(data)

