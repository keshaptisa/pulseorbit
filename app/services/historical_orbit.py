from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from app.config import settings

MAX_TLE_AGE = timedelta(days=settings.historical_tle_max_age_days)
SSC_PATH = Path("data/archive/orbit/iss_sscweb_gse_20240501_20240701.json")


def load_sscweb_points(start: datetime, hours: float) -> list[dict]:
    if not SSC_PATH.exists():
        return []
    payload = json.loads(SSC_PATH.read_text(encoding="utf-8"))
    end = start.astimezone(UTC) + timedelta(hours=hours)
    result = []
    for item in payload.get("points", []):
        timestamp = datetime.fromisoformat(item["time"])
        if start.astimezone(UTC) <= timestamp <= end:
            result.append({"time": timestamp, "valid": True, "x_km": item["x_km"], "y_km": item["y_km"], "z_km": item["z_km"], "altitude_km": (item["x_km"]**2 + item["y_km"]**2 + item["z_km"]**2)**0.5 - 6378.137})
    return result


def tle_epoch(line1: str) -> datetime:
    year = int(line1[18:20])
    year += 2000 if year < 57 else 1900
    day = float(line1[20:32])
    return datetime(year, 1, 1, tzinfo=UTC) + timedelta(days=day - 1)


def load_historical_tle(day: datetime, path: Path = Path("data/archive/orbit/iss.tle"), available_before: datetime | None = None) -> tuple[str, str] | None:
    metadata_path = path.with_name("iss_history.json")
    if metadata_path.exists():
        records = json.loads(metadata_path.read_text(encoding="utf-8"))
        candidates = []
        for record in records:
            captured = datetime.strptime(record["captured_at"], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if available_before is not None and captured > available_before.astimezone(UTC):
                continue
            candidates.append((tle_epoch(record["line1"]), record["line1"], record["line2"]))
        if candidates:
            target = day.astimezone(UTC)
            before = [item for item in candidates if item[0] <= target]
            if not before:
                return None
            selected = max(before, key=lambda item: item[0])
            return (selected[1], selected[2]) if target - selected[0] <= MAX_TLE_AGE else None
    if not path.exists():
        return None
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = []
    for index, line in enumerate(lines):
        if line.startswith("1 ") and index + 1 < len(lines) and lines[index + 1].startswith("2 "):
            try:
                candidates.append((tle_epoch(line), line, lines[index + 1]))
            except (ValueError, IndexError):
                continue
    if not candidates:
        return None
    target = day.astimezone(UTC)
    before = [item for item in candidates if item[0] <= target]
    if not before:
        return None
    selected = max(before, key=lambda item: item[0])
    return (selected[1], selected[2]) if target - selected[0] <= MAX_TLE_AGE else None
