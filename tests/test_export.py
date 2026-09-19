import io
import zipfile
from datetime import UTC, datetime

from app.models import AssessmentRequest
from app.services.assessment import assess
from app.services.experiment import compare_with_baseline
from app.services.export import build_export, build_windows_csv
from app.main import app
from fastapi.testclient import TestClient


def test_export_contains_report_result_and_manifest(monkeypatch):
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    result = assess(AssessmentRequest(start=datetime(2024, 5, 10, tzinfo=UTC), duration_hours=2))
    envelope = {"result_id": "abc", "content_sha256": "0" * 64, "result": result.model_dump(mode="json")}
    with zipfile.ZipFile(io.BytesIO(build_export(envelope))) as archive:
        assert set(archive.namelist()) == {"result.json", "windows.csv", "report.html", "manifest.json"}


def test_csv_contains_all_candidate_windows(monkeypatch):
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    result = assess(AssessmentRequest(start=datetime(2024, 5, 10, tzinfo=UTC), duration_hours=2))
    csv_text = build_windows_csv(result.model_dump(mode="json")).decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("start_utc,end_utc,severity,score")
    assert len(csv_text.splitlines()) == len(result.windows) + 1


def test_baseline_comparison_uses_same_candidates(monkeypatch):
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    result = assess(AssessmentRequest(start=datetime(2024, 5, 10, tzinfo=UTC), duration_hours=2))
    comparison = compare_with_baseline(result)
    assert comparison["candidate_count"] == len(result.windows)
    assert comparison["score_improvement"] >= 0


def test_saved_json_download_matches_server_envelope(monkeypatch):
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    client = TestClient(app)
    created = client.post("/api/assess", json={"start": "2024-05-10T12:00:00Z", "duration_hours": 2, "search_hours": 2}).json()
    response = client.get(f"/api/results/{created['result_id']}/result.json")
    assert response.status_code == 200
    assert response.json()["result"] == created
    assert "attachment" in response.headers["content-disposition"]
