from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.models import AssessmentRequest
from app.services.assessment import _assess_window, assess


@pytest.fixture(autouse=True)
def disable_network(monkeypatch):
    monkeypatch.setenv("EVA_LIVE_DATA", "0")


def request(**overrides):
    values = dict(start=datetime(2024, 5, 10, 12, tzinfo=UTC), duration_hours=2, search_hours=6, step_minutes=30)
    values.update(overrides)
    return AssessmentRequest(**values)


def test_compares_equal_duration_windows():
    result = assess(request())
    durations = {(window.end - window.start).total_seconds() for window in result.windows}
    assert durations == {7200}
    assert len(result.windows) == 13


def test_lighting_constraint_can_distinguish_windows_with_complete_data():
    anchor = datetime(2024, 5, 10, 12, tzinfo=UTC)
    shadow = [(anchor, anchor.replace(hour=13))]
    affected = _assess_window(anchor, 2, anchor, shadow, [], True)
    clear = _assess_window(anchor.replace(hour=14), 2, anchor, shadow, [], True)
    assert clear.score < affected.score


def test_unknown_weather_blocks_false_recommendation():
    result = assess(request(continuous_lighting_required=True))
    assert all(window.score >= 300 for window in result.windows)
    assert result.recommendation == "Недостаточно данных для рекомендации"


def test_shadow_is_informational_without_work_constraint():
    result = assess(request())
    lighting = [factor for factor in result.windows[0].factors if factor.factor_id == "orbital-lighting"][0]
    assert lighting.severity.value == "unknown"
    assert not lighting.decision_relevant
    assert result.windows[0].completeness == "частичное покрытие"


def test_missing_sources_are_unknown_not_synthetic_safe():
    result = assess(request())
    assert all(window.severity.value == "unknown" for window in result.windows)
    assert result.recommendation == "Недостаточно данных для рекомендации"
    assert result.operational_status == "HOLD_DATA"
    assert "Не принимать решение" in result.operator_action
    assert {domain["id"] for domain in result.risk_domains} == {"radiation", "communications", "lighting", "data"}


def test_every_factor_has_traceable_evidence():
    result = assess(request())
    for window in result.windows:
        for factor in window.factors:
            assert factor.evidence
            assert all(item.record_id and item.source_name for item in factor.evidence)
            assert factor.applied_rule
            assert factor.confidence_basis


def test_live_orbit_propagation_has_valid_points(monkeypatch):
    fetched = datetime(2024, 5, 10, 12, tzinfo=UTC).isoformat()
    tle = "1 25544U 98067A   24130.50000000  .00016717  00000-0  30112-3 0  9991\n2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456\n"
    monkeypatch.setattr("app.services.assessment.load_live_sources", lambda force_refresh=False: {
        "noaa": {"source_id": "noaa", "url": "https://example.test/noaa", "fetched_at": fetched, "sha256": "a", "body": [], "cache_state": "fresh"},
        "orbit": {"source_id": "omm", "url": "https://example.test/omm", "fetched_at": fetched, "sha256": "b", "body": [], "cache_state": "fresh"},
        "orbit_tle": {"source_id": "tle", "url": "https://example.test/tle", "fetched_at": fetched, "sha256": "c", "body": tle, "cache_state": "fresh"},
    })
    result = assess(request(search_hours=2))
    assert result.trajectory["source"] == "CelesTrak OMM + TLE/SGP4"
    assert result.trajectory["propagator"] == "SGP4"
    assert result.trajectory["points"] >= 10
    assert result.operational_status == "HOLD_POLICY"


def test_current_dosimetry_closes_policy_gates_and_blocks_at_limit(monkeypatch):
    now = datetime.now(UTC)
    tle = "1 25544U 98067A   24130.50000000  .00016717  00000-0  30112-3 0  9991\n2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456\n"
    monkeypatch.setattr("app.services.assessment.load_live_sources", lambda force_refresh=False: {
        "noaa": {"source_id": "noaa", "url": "https://example.test/noaa", "fetched_at": now.isoformat(), "sha256": "a", "body": [], "cache_state": "fresh"},
        "noaa_forecast": {"source_id": "forecast", "url": "https://example.test/forecast", "fetched_at": now.isoformat(), "sha256": "b", "body": ":Product: invalid test fixture", "cache_state": "fresh"},
        "orbit": {"source_id": "omm", "url": "https://example.test/omm", "fetched_at": now.isoformat(), "sha256": "c", "body": [], "cache_state": "fresh"},
        "orbit_tle": {"source_id": "tle", "url": "https://example.test/tle", "fetched_at": now.isoformat(), "sha256": "d", "body": tle, "cache_state": "fresh"},
    })
    safe = assess(AssessmentRequest(start=now, duration_hours=2, operation_profile="EVA-OPS-01", policy_version="ORG-RAD-4.2", crew_dose_rate_usv_h=10, crew_dose_limit_usv_h=20, dosimetry_timestamp=now))
    assert safe.operational_status == "CONDITIONAL"
    assert safe.decision_confidence == "gates_closed"
    assert all(gate["passed"] for gate in safe.decision_gates)
    blocked = assess(AssessmentRequest(start=now, duration_hours=2, operation_profile="EVA-OPS-01", policy_version="ORG-RAD-4.2", crew_dose_rate_usv_h=20, crew_dose_limit_usv_h=20, dosimetry_timestamp=now))
    assert blocked.operational_status == "HOLD_DOSIMETRY"
    assert "20 мкЗв/ч" in blocked.operator_action


def test_api_rejects_duration_outside_contract():
    client = TestClient(app)
    response = client.post("/api/assess", json={"start": "2024-05-10T12:00:00Z", "duration_hours": 9, "search_hours": 2})
    assert response.status_code == 422


def test_historical_date_contract_and_replay_cutoff():
    historical = AssessmentRequest(mode="historical", start=datetime(2024, 5, 10, 12, tzinfo=UTC), duration_hours=2)
    assert historical.cutoff is None
    replay = AssessmentRequest(mode="replay", start=datetime(2024, 5, 10, 12, tzinfo=UTC), cutoff=datetime(2024, 5, 10, 11, tzinfo=UTC), duration_hours=2)
    assert replay.cutoff.hour == 11


def test_historical_never_uses_current_feeds():
    result = assess(AssessmentRequest(mode="historical", start=datetime(2024, 5, 10, 12, tzinfo=UTC), duration_hours=2))
    assert result.trajectory["source"] == "NASA GSFC SSCWeb historical ISS coordinates"
    assert {item.status for item in result.source_status} <= {"fresh", "stale", "archive", "unavailable"}
    assert any(item.source_id == "historical-orbit" and item.status in {"unavailable", "archive"} for item in result.source_status)


@pytest.mark.parametrize("day", [1, 15, 30])
def test_historical_uses_real_nasa_sscweb_geometry_across_period(day):
    month = 5 if day == 1 else 6
    result = assess(AssessmentRequest(mode="historical", start=datetime(2024, month, day, 12, tzinfo=UTC), duration_hours=2))
    assert result.trajectory["source"] == "NASA GSFC SSCWeb historical ISS coordinates"
    assert result.trajectory["coordinate_frame"] == "GSE"
    assert result.trajectory["points"] >= 12
    assert 350 < result.trajectory["min_altitude_km"] < 500


def test_replay_never_uses_retrospective_nasa_sscweb_geometry():
    result = assess(AssessmentRequest(mode="replay", start=datetime(2024, 6, 15, 12, tzinfo=UTC), cutoff=datetime(2024, 6, 14, 23, tzinfo=UTC), duration_hours=2))
    assert result.trajectory["source"] != "NASA GSFC SSCWeb historical ISS coordinates"


def test_replay_separates_forecast_from_later_observation():
    result = assess(AssessmentRequest(mode="replay", start=datetime(2024, 5, 10, 14, tzinfo=UTC), cutoff=datetime(2024, 5, 9, 23, tzinfo=UTC), duration_hours=2))
    assert result.verification["status"] == "verified_against_later_observation"
    assert "50%" in result.verification["forecast"]
    assert "207 pfu" in result.verification["observation"]
    assert "не влияет" in result.verification["note"]


def test_trajectory_exposes_shadow_intervals_for_visualization(monkeypatch):
    fetched = datetime(2024, 5, 10, 12, tzinfo=UTC).isoformat()
    tle = "1 25544U 98067A   24130.50000000  .00016717  00000-0  30112-3 0  9991\n2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456\n"
    monkeypatch.setattr("app.services.assessment.load_live_sources", lambda force_refresh=False: {
        "noaa": {"source_id": "noaa", "url": "https://example.test/noaa", "fetched_at": fetched, "sha256": "a", "body": [], "cache_state": "fresh"},
        "orbit": {"source_id": "omm", "url": "https://example.test/omm", "fetched_at": fetched, "sha256": "b", "body": [], "cache_state": "fresh"},
        "orbit_tle": {"source_id": "tle", "url": "https://example.test/tle", "fetched_at": fetched, "sha256": "c", "body": tle, "cache_state": "fresh"},
    })
    result = assess(request(search_hours=2))
    assert isinstance(result.trajectory["shadow_intervals"], list)
    assert all({"start", "end"} <= set(item) for item in result.trajectory["shadow_intervals"])


def test_touching_forecast_block_is_not_reported_as_detected_event():
    anchor = datetime(2024, 5, 10, 12, tzinfo=UTC)
    touching = {"family": "G", "start": anchor - timedelta(hours=3), "end": anchor, "value": 1.67, "forecast": True, "product_id": "touch", "published_at": anchor, "message": "", "source_url": "https://example.test"}
    window = _assess_window(anchor, 2, anchor, [], [touching])
    factor = [item for item in window.factors if item.factor_id == "space-weather-g"][0]
    assert factor.severity.value == "not_detected"
    assert factor.overlap_minutes == 0


def test_overlapping_forecast_block_is_graded_and_never_rounds_to_zero():
    anchor = datetime(2024, 5, 10, 12, tzinfo=UTC)
    overlapping = {"family": "G", "start": anchor - timedelta(hours=3), "end": anchor + timedelta(seconds=20), "value": 1.67, "forecast": True, "product_id": "ov", "published_at": anchor, "message": "", "source_url": "https://example.test"}
    window = _assess_window(anchor, 2, anchor, [], [overlapping])
    factor = [item for item in window.factors if item.factor_id == "space-weather-g"][0]
    assert factor.severity.value == "low"
    assert factor.overlap_minutes == 1
