from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot serialize {type(value)!r}")


class AtomicJsonlLedger:
    """Small-run audit ledger using replace semantics to avoid torn appends."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        existing = self.path.read_bytes() if self.path.exists() else b""
        line = (json.dumps(record, ensure_ascii=False, default=_json_default, sort_keys=True) + "\n").encode("utf-8")
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(existing)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

