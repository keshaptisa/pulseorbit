from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models import AssessmentRequest
from app.services.assessment import assess

ROOT = Path(__file__).resolve().parents[1]
DAYPRE = ROOT / "data/archive/noaa/daypre"
OBSERVATIONS = ROOT / "data/validation/proton_observations.json"
OUTPUT = ROOT / "data/validation/experiment_summary.json"


def proton_forecast(path: Path, target_index: int = 0) -> dict | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    issued_match = re.search(r":Issued:\s*(.+?) UTC", text)
    dates_match = re.search(r":Prediction_dates:\s*(.+)", text)
    proton_match = re.search(r"^Proton\s+(\d+)\s+(\d+)\s+(\d+)\s*$", text, re.M)
    if not issued_match or not dates_match or not proton_match:
        return None
    dates = re.findall(r"\d{4} \w{3} \d{2}", dates_match.group(1))
    probabilities = [int(value) for value in proton_match.groups()]
    if target_index >= min(len(dates), len(probabilities)):
        return None
    issued = datetime.strptime(issued_match.group(1), "%Y %b %d %H%M").replace(tzinfo=UTC)
    target = datetime.strptime(dates[target_index], "%Y %b %d").replace(tzinfo=UTC)
    return {"file": str(path.relative_to(ROOT)), "issued": issued, "target": target, "probability_percent": probabilities[target_index]}


def confusion(records: list[dict], threshold: int) -> dict:
    tp = sum(item["probability_percent"] >= threshold and item["observed_event"] for item in records)
    fp = sum(item["probability_percent"] >= threshold and not item["observed_event"] for item in records)
    fn = sum(item["probability_percent"] < threshold and item["observed_event"] for item in records)
    tn = sum(item["probability_percent"] < threshold and not item["observed_event"] for item in records)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return {"threshold_percent": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(precision, 3) if precision is not None else None, "recall": round(recall, 3) if recall is not None else None, "specificity": round(specificity, 3) if specificity is not None else None, "f1": round(f1, 3) if f1 is not None else None}


def main() -> None:
    observations = json.loads(OBSERVATIONS.read_text(encoding="utf-8"))
    events = [{**item, "start": datetime.fromisoformat(item["start"].replace("Z", "+00:00")), "end": datetime.fromisoformat(item["end"].replace("Z", "+00:00"))} for item in observations["events"]]
    coverage_start = datetime.fromisoformat(observations["coverage_start"].replace("Z", "+00:00"))
    coverage_end = datetime.fromisoformat(observations["coverage_end"].replace("Z", "+00:00"))
    records = []
    missing_or_unparseable = []
    for path in sorted(DAYPRE.glob("*daypre.txt")):
        record = proton_forecast(path)
        if record is None:
            missing_or_unparseable.append(str(path.relative_to(ROOT)))
            continue
        if not coverage_start <= record["target"] <= coverage_end:
            continue
        target_end = record["target"] + timedelta(days=1)
        matching = next((event for event in events if event["start"] < target_end and event["end"] >= record["target"]), None)
        record["observed_event"] = matching is not None
        record["observation_source"] = matching["source_report"] if matching else "NOAA weekly PRF reports: no proton event observed"
        records.append(record)

    metrics = confusion(records, settings.proton_probability_threshold_percent)
    brier = mean(((item["probability_percent"] / 100) - int(item["observed_event"])) ** 2 for item in records)
    leads = []
    for event in events:
        eligible = [item for item in records if item["issued"] <= event["start"] and item["target"] <= event["start"] < item["target"] + timedelta(days=1)]
        if eligible:
            forecast = max(eligible, key=lambda item: item["issued"])
            leads.append({"event_start": event["start"], "forecast_issued": forecast["issued"], "probability_percent": forecast["probability_percent"], "lead_hours": round((event["start"] - forecast["issued"]).total_seconds() / 3600, 2), "source_report": event["source_report"]})

    sensitivity = []
    for step in (15, 30, 60):
        result = assess(AssessmentRequest(mode="replay", start=datetime(2024, 5, 10, 0, tzinfo=UTC), cutoff=datetime(2024, 5, 9, 23, tzinfo=UTC), duration_hours=4, search_hours=6, step_minutes=step))
        sensitivity.append({"step_minutes": step, "candidate_count": len(result.windows), "best_score": min(item.score for item in result.windows), "recommendation": result.recommendation})

    positive = [item for item in records if item["observed_event"]]
    negative = [item for item in records if not item["observed_event"]]
    payload = {
        "scope": "NOAA daypre daily probability versus subsequent observed >10 MeV proton events at geosynchronous orbit; not astronaut dose or EVA safety",
        "dataset": {"forecast_records": len(records), "positive_days": len(positive), "negative_days": len(negative), "coverage_start": min(item["target"] for item in records), "coverage_end": max(item["target"] for item in records), "missing_or_unparseable": missing_or_unparseable, "observation_manifest": str(OBSERVATIONS.relative_to(ROOT))},
        "primary_threshold": metrics,
        "brier_score": round(brier, 4),
        "lead_time": leads,
        "threshold_sensitivity": [confusion(records, value) for value in range(10, 100, 10)],
        "event_probability_mean_percent": round(mean(item["probability_percent"] for item in positive), 1),
        "non_event_probability_mean_percent": round(mean(item["probability_percent"] for item in negative), 1),
        "window_step_sensitivity": sensitivity,
        "tle_age_sensitivity_days": [{"maximum_age_days": days, "interpretation": "stricter coverage, fewer usable historical epochs" if days < 3 else "broader coverage with increasing propagation uncertainty"} for days in (1, 2, 3, 5)],
        "records": [{**item, "issued": item["issued"].isoformat(), "target": item["target"].date().isoformat()} for item in records],
        "limitations": observations["limitations"] + ["The archive contains missing forecast files; no value is imputed.", "The sample covers two related active intervals and should not be treated as operational validation."],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
