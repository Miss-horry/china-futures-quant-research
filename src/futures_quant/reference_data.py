from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path


def import_calendar(
    input_path: str | Path, output_path: str | Path, source_label: str
) -> dict:
    source = Path(input_path)
    raw = source.read_bytes()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError("calendar source must be a JSON list")
    days = sorted({date.fromisoformat(str(value)).isoformat() for value in parsed})
    if not days:
        raise ValueError("calendar is empty")
    if any(date.fromisoformat(day).weekday() >= 5 for day in days):
        raise ValueError("calendar contains weekend dates")
    artifact = {
        "schema": "trading_calendar_v1", "source_label": source_label,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "imported_at": datetime.now().astimezone().isoformat(),
        "count": len(days), "first": days[0], "last": days[-1], "dates": days,
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, destination)
    return artifact


def load_calendar(path: str | Path) -> tuple[set[date], dict]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    if artifact.get("schema") != "trading_calendar_v1":
        raise ValueError("unsupported calendar artifact")
    days = {date.fromisoformat(value) for value in artifact.get("dates", [])}
    if len(days) != artifact.get("count"):
        raise ValueError("calendar count mismatch")
    return days, artifact

