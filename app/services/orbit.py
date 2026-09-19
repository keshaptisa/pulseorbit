from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import asin, atan2, cos, pi, sin, sqrt

from sgp4.api import Satrec, WGS72


def _satrec_from_omm(omm: dict) -> Satrec:
    epoch = datetime.fromisoformat(omm["EPOCH"].replace("Z", "+00:00")).astimezone(UTC)
    days = (epoch - datetime(1949, 12, 31, tzinfo=UTC)).total_seconds() / 86400
    sat = Satrec()
    sat.sgp4init(
        WGS72, "i", int(omm["NORAD_CAT_ID"]), days,
        float(omm.get("BSTAR", 0.0)), float(omm.get("ECCENTRICITY", 0.0)),
        float(omm.get("ARG_OF_PERICENTER", 0.0)) * pi / 180,
        float(omm.get("INCLINATION", 0.0)) * pi / 180,
        float(omm.get("MEAN_ANOMALY", 0.0)) * pi / 180,
        float(omm["MEAN_MOTION"]) * 2 * pi / 1440,
        float(omm.get("RA_OF_ASC_NODE", 0.0)) * pi / 180,
        0.0,
        0.0,
    )
    return sat


def trajectory(omm: dict, start: datetime, hours: float, step_minutes: int = 10) -> list[dict]:
    sat = _satrec_from_omm(omm)
    points = []
    count = int(hours * 60 // step_minutes) + 1
    for i in range(count):
        timestamp = start + timedelta(minutes=i * step_minutes)
        jd = timestamp.timestamp() / 86400 + 2440587.5
        error, position, velocity = sat.sgp4(jd, 0.0)
        if error:
            points.append({"time": timestamp, "valid": False, "error": error})
            continue
        radius = sqrt(sum(value * value for value in position))
        points.append({"time": timestamp, "valid": True, "x_km": position[0], "y_km": position[1], "z_km": position[2], "altitude_km": radius - 6378.137})
    return points


def trajectory_from_tle(tle: tuple[str, str], start: datetime, hours: float, step_minutes: int = 10) -> list[dict]:
    """Propagate UTC epochs with SGP4/WGS-72; returned Cartesian vectors are TEME kilometres."""
    sat = Satrec.twoline2rv(*tle)
    points = []
    count = int(hours * 60 // step_minutes) + 1
    for i in range(count):
        timestamp = start + timedelta(minutes=i * step_minutes)
        jd = timestamp.timestamp() / 86400 + 2440587.5
        error, position, _ = sat.sgp4(jd, 0.0)
        radius = sqrt(sum(value * value for value in position)) if not error else 0
        points.append({"time": timestamp, "valid": not error, "x_km": position[0], "y_km": position[1], "z_km": position[2], "altitude_km": radius - 6378.137, "error": error or None})
    return points


def shadow_intervals(points: list[dict]) -> list[tuple[datetime, datetime]]:
    """Cylindrical umbra approximation in the TEME-like inertial frame using an analytical Sun direction."""
    intervals = []
    opened = None
    for point in points:
        in_shadow = point.get("valid", False) and _in_earth_shadow(point)
        if in_shadow and opened is None:
            opened = point["time"]
        if not in_shadow and opened is not None:
            intervals.append((opened, point["time"]))
            opened = None
    if opened is not None and points:
        intervals.append((opened, points[-1]["time"]))
    return intervals


def shadow_intervals_gse(points: list[dict]) -> list[tuple[datetime, datetime]]:
    intervals, opened = [], None
    for point in points:
        in_shadow = point.get("valid", False) and point["x_km"] < 0 and sqrt(point["y_km"]**2 + point["z_km"]**2) < 6378.137
        if in_shadow and opened is None: opened = point["time"]
        if not in_shadow and opened is not None: intervals.append((opened, point["time"])); opened = None
    if opened is not None and points: intervals.append((opened, points[-1]["time"]))
    return intervals


def _sun_unit(timestamp: datetime) -> tuple[float, float, float]:
    jd = timestamp.timestamp() / 86400 + 2440587.5
    n = jd - 2451545.0
    mean_longitude = (280.460 + 0.9856474 * n) * pi / 180
    anomaly = (357.528 + 0.9856003 * n) * pi / 180
    ecliptic = mean_longitude + (1.915 * sin(anomaly) + 0.020 * sin(2 * anomaly)) * pi / 180
    obliquity = (23.439 - 0.0000004 * n) * pi / 180
    return cos(ecliptic), cos(obliquity) * sin(ecliptic), sin(obliquity) * sin(ecliptic)


def _in_earth_shadow(point: dict) -> bool:
    sun = _sun_unit(point["time"])
    position = (point["x_km"], point["y_km"], point["z_km"])
    projection = sum(position[i] * sun[i] for i in range(3))
    perpendicular = sqrt(max(0.0, sum(value * value for value in position) - projection * projection))
    return projection < 0 and perpendicular < 6378.137


def trajectory_summary(points: list[dict]) -> dict[str, float | None]:
    valid = [point for point in points if point.get("valid")]
    if not valid:
        return {"min_altitude_km": None, "max_altitude_km": None, "valid_points": 0}
    return {
        "min_altitude_km": round(min(point["altitude_km"] for point in valid), 1),
        "max_altitude_km": round(max(point["altitude_km"] for point in valid), 1),
        "valid_points": len(valid),
    }
