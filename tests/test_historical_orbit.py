from datetime import UTC, datetime

from app.services.historical_orbit import load_historical_tle
from app.services.orbit import shadow_intervals, trajectory_from_tle


def test_selects_latest_tle_before_target(tmp_path):
    path = tmp_path / "iss.tle"
    path.write_text("\n".join([
        "ISS (ZARYA)",
        "1 25544U 98067A   24130.50000000  .00000000  00000-0  00000-0 0  9991",
        "2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456",
        "ISS (ZARYA)",
        "1 25544U 98067A   24135.50000000  .00000000  00000-0  00000-0 0  9992",
        "2 25544  51.6400 110.0000 0005000 120.0000 240.0000 15.50000000123457",
    ]), encoding="utf-8")
    selected = load_historical_tle(datetime(2024, 5, 12, tzinfo=UTC), path)
    assert selected is not None
    assert "24130.50000000" in selected[0]


def test_rejects_future_and_stale_historical_tle(tmp_path):
    path = tmp_path / "iss.tle"
    path.write_text("ISS (ZARYA)\n1 25544U 98067A   24130.50000000  .00000000  00000-0  00000-0 0  9991\n2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456\n", encoding="utf-8")
    assert load_historical_tle(datetime(2024, 5, 5, tzinfo=UTC), path) is None
    assert load_historical_tle(datetime(2024, 5, 20, tzinfo=UTC), path) is None


def test_sgp4_points_have_physical_iss_altitude(tmp_path):
    path = tmp_path / "iss.tle"
    path.write_text("1 25544U 98067A   24130.50000000  .00016717  00000-0  30112-3 0  9991\n2 25544  51.6400 100.0000 0005000 120.0000 240.0000 15.50000000123456\n", encoding="utf-8")
    tle = load_historical_tle(datetime(2024, 5, 10, 13, tzinfo=UTC), path)
    points = trajectory_from_tle(tle, datetime(2024, 5, 10, 13, tzinfo=UTC), 2)
    assert all(point["valid"] for point in points)
    assert all(300 < point["altitude_km"] < 550 for point in points)
    assert isinstance(shadow_intervals(points), list)
