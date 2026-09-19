from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings

CACHE_DIR = Path(os.getenv("EVA_CACHE_DIR", "data/cache"))
CACHE_TTL = settings.cache_ttl
NOAA_ALERTS_URL = settings.noaa_alerts_url
NOAA_FORECAST_URL = settings.noaa_forecast_url
CELESTRAK_ISS_URL = settings.celestrak_omm_url
CELESTRAK_ISS_TLE_URL = settings.celestrak_tle_url
DAYPRE_DIR = Path("data/archive/noaa/daypre")


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _cache_file(source_id: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{source_id}.json"


def _read_cache(source_id: str) -> dict[str, Any] | None:
    path = _cache_file(source_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload
    except (OSError, json.JSONDecodeError):
        return None


def _fetch_json(source_id: str, url: str, force_refresh: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC)
    cached = _read_cache(source_id)
    if cached and not force_refresh:
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if now - fetched_at < CACHE_TTL:
            cached["cache_state"] = "fresh"
            return cached
    try:
        response = httpx.get(url, timeout=settings.request_timeout_seconds, headers={"User-Agent": "eva-risk-planner/0.5"})
        response.raise_for_status()
        raw = response.content
        body = response.json()
        payload = {
            "source_id": source_id,
            "url": url,
            "fetched_at": now.isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "body": body,
            "cache_state": "fresh",
        }
        _atomic_write(_cache_file(source_id), json.dumps(payload, ensure_ascii=False))
        return payload
    except (httpx.HTTPError, ValueError, OSError) as exc:
        if cached:
            cached["cache_state"] = "stale"
            cached["error"] = str(exc)
            return cached
        return {
            "source_id": source_id,
            "url": url,
            "fetched_at": None,
            "sha256": None,
            "body": None,
            "cache_state": "unavailable",
            "error": str(exc),
        }


def _fetch_text(source_id: str, url: str, force_refresh: bool = False) -> dict[str, Any]:
    now = datetime.now(UTC)
    cached = _read_cache(source_id)
    if cached and not force_refresh and cached.get("body") and now - datetime.fromisoformat(cached["fetched_at"]) < CACHE_TTL:
        cached["cache_state"] = "fresh"
        return cached
    try:
        response = httpx.get(url, timeout=settings.request_timeout_seconds, headers={"User-Agent": "eva-risk-planner/0.5"})
        response.raise_for_status()
        raw = response.content
        payload = {"source_id": source_id, "url": url, "fetched_at": now.isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "body": response.text, "cache_state": "fresh"}
        _atomic_write(_cache_file(source_id), json.dumps(payload, ensure_ascii=False))
        return payload
    except (httpx.HTTPError, OSError) as exc:
        if cached:
            cached["cache_state"] = "stale"
            cached["error"] = str(exc)
            return cached
        return {"source_id": source_id, "url": url, "fetched_at": None, "sha256": None, "body": None, "cache_state": "unavailable", "error": str(exc)}


def load_live_sources(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    if os.getenv("EVA_LIVE_DATA", "1").lower() in {"0", "false", "no"}:
        return {}
    return {
        "noaa": _fetch_json("noaa-alerts", NOAA_ALERTS_URL, force_refresh),
        "noaa_forecast": _fetch_text("noaa-3-day-forecast", NOAA_FORECAST_URL, force_refresh),
        "orbit": _fetch_json("celestrak-iss", CELESTRAK_ISS_URL, force_refresh),
        "orbit_tle": _fetch_text("celestrak-iss-tle", CELESTRAK_ISS_TLE_URL, force_refresh),
    }


def _forecast_day(token: str, issued: datetime) -> datetime:
    """NOAA prints forecast days without a year, so resolve it against the issue date."""
    day = datetime.strptime(f"{issued.year} {token}", "%Y %b %d").replace(tzinfo=UTC)
    if (day - issued).days < -300:
        day = day.replace(year=issued.year + 1)
    return day


def _forecast_days(text: str, issued: datetime) -> list[datetime]:
    """Columns are dated by the table header; the 'Sep 19-Sep 21' line is a range, not three days."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "NOAA Kp index breakdown" not in line:
            continue
        for candidate in lines[index + 1:index + 5]:
            tokens = re.findall(r"[A-Z][a-z]{2} \d{1,2}", candidate)
            if len(tokens) >= 3:
                return [_forecast_day(token, issued) for token in tokens[:3]]
    return []


def parse_noaa_forecast(source: dict[str, Any], window_end: datetime) -> list[dict[str, Any]]:
    """Normalize NOAA's official three-day forecast into dated intervals."""
    text = source.get("body")
    if not isinstance(text, str):
        return []
    issued_match = re.search(r":Issued:\s*(\d{4} \w{3} \d{2} \d{4}) UTC", text)
    if not issued_match:
        return []
    issued = datetime.strptime(issued_match.group(1), "%Y %b %d %H%M").replace(tzinfo=UTC)
    dates = _forecast_days(text, issued)
    if not dates:
        return []
    record_hash = hashlib.sha256(text.encode()).hexdigest()
    events: list[dict[str, Any]] = []
    for start_hour, _, first, second, third in re.findall(r"^(\d{2})-(\d{2})UT\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", text, re.M):
        for day, raw_value in zip(dates, (first, second, third)):
            start = day + timedelta(hours=int(start_hour))
            kp = float(raw_value)
            level = 5 if kp >= 9 else 4 if kp >= 8 else 3 if kp >= 7 else 2 if kp >= 6 else 1 if kp >= 5 else 0
            events.append({"start": start, "end": start + timedelta(hours=3), "scale": f"G{level}" if level else None, "family": "G", "value": kp, "product_id": "noaa-3-day-forecast", "published_at": issued, "message": f"NOAA predicted Kp {kp:.2f}", "source_url": NOAA_FORECAST_URL, "record_sha256": record_hash, "forecast": True})
    for family, pattern in {"S": r"S1 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%", "R": r"R3 or greater\s+(\d+)%\s+(\d+)%\s+(\d+)%"}.items():
        match = re.search(pattern, text)
        if not match:
            continue
        for day, probability in zip(dates, map(int, match.groups())):
            if day <= window_end:
                events.append({"start": day, "end": day + timedelta(days=1), "scale": None, "family": family, "probability": probability, "product_id": "noaa-3-day-forecast", "published_at": issued, "message": f"NOAA probability {family}: {probability}%", "source_url": NOAA_FORECAST_URL, "record_sha256": record_hash, "forecast": True})
    return events


def source_status(source: dict[str, Any], name: str) -> dict[str, Any]:
    fetched_at = source.get("fetched_at")
    if fetched_at:
        parsed = datetime.fromisoformat(fetched_at)
        age = max(0, round((datetime.now(UTC) - parsed).total_seconds() / 60))
    else:
        age = 0
    state = source.get("cache_state", "unavailable")
    return {"source_id": source["source_id"], "name": name, "status": state, "last_success": fetched_at, "age_minutes": age, "is_synthetic": False, "url": source.get("url"), "sha256": source.get("sha256")}


def latest_iss_omm(source: dict[str, Any]) -> dict[str, Any] | None:
    body = source.get("body")
    if isinstance(body, list) and body:
        return body[0]
    return None


def latest_iss_tle(source: dict[str, Any]) -> tuple[str, str] | None:
    body = source.get("body")
    if not isinstance(body, str):
        return None
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.startswith("1 ") and i + 1 < len(lines) and lines[i + 1].startswith("2 "):
            return line, lines[i + 1]
    return None


def parse_noaa_intervals(source: dict[str, Any], published_before: datetime | None = None) -> list[dict[str, Any]]:
    """Extract dated event intervals from NOAA's human-readable alert messages."""
    body = source.get("body")
    if not isinstance(body, list):
        return []
    normalized = {}
    cancelled_products: dict[str, datetime] = {}
    date_re = re.compile(r"(?:Begin(?: Time)?|Start|Threshold Reached):\s*(\d{4} \w{3} \d{2} \d{4} UTC)")
    end_re = re.compile(r"End Time:\s*(\d{4} \w{3} \d{2} \d{4} UTC)")
    scale_re = re.compile(r"Noaa Scale:\s*([SGR]\d)", re.I)
    for record in body:
        published = None
        if record.get("issue_datetime"):
            try:
                published = datetime.fromisoformat(record["issue_datetime"].replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                published = None
        if published_before is not None and (published is None or published > published_before):
            continue
        message = record.get("message", "")
        product_id = str(record.get("product_id") or "")
        if re.search(r"CANCEL(?:ATION|LATION|LED)?", message, re.I):
            if product_id and published:
                cancelled_products[product_id] = max(published, cancelled_products.get(product_id, datetime.min.replace(tzinfo=UTC)))
            continue
        begin = date_re.search(message)
        if not begin:
            continue
        end = end_re.search(message)
        try:
            start = datetime.strptime(begin.group(1), "%Y %b %d %H%M UTC").replace(tzinfo=UTC)
            finish = datetime.strptime(end.group(1), "%Y %b %d %H%M UTC").replace(tzinfo=UTC) if end else start + timedelta(hours=1)
        except ValueError:
            continue
        scale = scale_re.search(message)
        scale_value = scale.group(1).upper() if scale else None
        raw_record = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        item = {"start": start, "end": finish, "scale": scale_value, "family": scale_value[:1] if scale_value else None, "product_id": record.get("product_id"), "published_at": published, "message": message[:240], "source_url": NOAA_ALERTS_URL, "record_sha256": hashlib.sha256(raw_record).hexdigest()}
        key = (item["product_id"], item["start"], item["end"], item["family"])
        previous = normalized.get(key)
        if previous is None or (published or datetime.min.replace(tzinfo=UTC)) > (previous.get("published_at") or datetime.min.replace(tzinfo=UTC)):
            normalized[key] = item
    active = [item for item in normalized.values() if not (item.get("product_id") and cancelled_products.get(str(item["product_id"])) and cancelled_products[str(item["product_id"])] >= (item.get("published_at") or datetime.min.replace(tzinfo=UTC)))]
    return sorted(active, key=lambda item: (item["start"], item.get("published_at") or item["start"]))


def load_historical_event_report(day: datetime) -> dict[str, Any]:
    """Load the official NCEI daily solar-event report for one UTC date."""
    stamp = day.astimezone(UTC).strftime("%Y%m%d")
    url = f"https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/solar_event_reports/{day.year:04d}/{day.month:02d}/{stamp}events.txt"
    return _fetch_text(f"noaa-events-{stamp}", url)


def parse_historical_event_report(source: dict[str, Any], day: datetime) -> list[dict[str, Any]]:
    body = source.get("body")
    if not isinstance(body, str):
        return []
    result = []
    for line in body.splitlines():
        fields = line.split()
        if len(fields) < 9 or not fields[0].isdigit() or fields[6] != "XRA":
            continue
        try:
            begin = datetime.strptime(f"{day:%Y %m %d} {fields[1]}", "%Y %m %d %H%M").replace(tzinfo=UTC)
            end = datetime.strptime(f"{day:%Y %m %d} {fields[3]}", "%Y %m %d %H%M").replace(tzinfo=UTC)
        except ValueError:
            continue
        result.append({"start": begin, "end": end, "scale": None, "family": "XRA", "product_id": fields[0], "published_at": None, "message": line.strip(), "source_url": source.get("url")})
    return result


def load_daypre_replay(cutoff: datetime, window_end: datetime) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Select the latest archived forecast issued by cutoff and normalize its daily proton probabilities."""
    candidates = []
    for path in DAYPRE_DIR.glob("*daypre.txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r":Issued:\s*(\d{4} \w{3} \d{2} \d{4}) UTC", text)
        if not match:
            continue
        issued = datetime.strptime(match.group(1), "%Y %b %d %H%M").replace(tzinfo=UTC)
        if issued <= cutoff:
            candidates.append((issued, path, text))
    if not candidates:
        return [], None
    issued, path, text = max(candidates, key=lambda item: item[0])
    dates_match = re.search(r":Prediction_dates:\s*(.+)", text)
    proton_match = re.search(r"^Proton\s+(\d+)\s+(\d+)\s+(\d+)\s*$", text, re.M)
    if not dates_match or not proton_match:
        return [], {"path": str(path), "issued": issued, "status": "unparseable"}
    date_tokens = re.findall(r"\d{4} \w{3} \d{2}", dates_match.group(1))
    probabilities = [int(value) for value in proton_match.groups()]
    events = []
    for token, probability in zip(date_tokens, probabilities):
        start = datetime.strptime(token, "%Y %b %d").replace(tzinfo=UTC)
        if start <= window_end and start + timedelta(days=1) >= cutoff:
            events.append({"start": start, "end": start + timedelta(days=1), "scale": None, "family": "PROTON", "probability": probability, "product_id": path.stem, "published_at": issued, "message": f"NOAA daypre proton-event probability: {probability}%", "source_url": "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/daypre/"})
    raw = path.read_bytes()
    return events, {"path": str(path), "issued": issued, "sha256": hashlib.sha256(raw).hexdigest(), "status": "archive"}
