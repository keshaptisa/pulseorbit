from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor

from app.models import AssessmentRequest
from app.services.assessment import assess
from app.services import storage
from app import data


def test_saved_result_is_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RESULTS_DIR", tmp_path)
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    result = assess(AssessmentRequest(start=datetime(2024, 5, 10, tzinfo=UTC), duration_hours=2))
    result_id = storage.save_result(result)
    saved = storage.load_result(result_id)
    assert saved["result_id"] == result_id
    assert saved["result"]["result_id"] == result_id
    assert len(saved["content_sha256"]) == 64


def test_concurrent_result_writes_are_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "RESULTS_DIR", tmp_path)
    monkeypatch.setenv("EVA_LIVE_DATA", "0")
    result = assess(AssessmentRequest(start=datetime(2024, 5, 10, tzinfo=UTC), duration_hours=2))
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: storage.save_result(result), range(8)))
    assert len(set(ids)) == 1
    assert storage.load_result(ids[0])["result"]["result_id"] == ids[0]


def test_corrupted_cache_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    assert data._read_cache("broken") is None
