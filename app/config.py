from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Settings:
    cache_ttl: timedelta = timedelta(hours=float(os.getenv("EVA_CACHE_TTL_HOURS", "2")))
    request_timeout_seconds: float = float(os.getenv("EVA_REQUEST_TIMEOUT_SECONDS", "12"))
    historical_tle_max_age_days: float = float(os.getenv("EVA_HISTORICAL_TLE_MAX_AGE_DAYS", "3"))
    proton_probability_threshold_percent: int = int(os.getenv("EVA_PROTON_THRESHOLD_PERCENT", "50"))
    forecast_watch_probability_percent: int = int(os.getenv("EVA_FORECAST_WATCH_PERCENT", "25"))
    noaa_alerts_url: str = os.getenv("EVA_NOAA_ALERTS_URL", "https://services.swpc.noaa.gov/products/alerts.json")
    noaa_forecast_url: str = os.getenv("EVA_NOAA_FORECAST_URL", "https://services.swpc.noaa.gov/text/3-day-forecast.txt")
    celestrak_omm_url: str = os.getenv("EVA_CELESTRAK_OMM_URL", "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=JSON")
    celestrak_tle_url: str = os.getenv("EVA_CELESTRAK_TLE_URL", "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE")


settings = Settings()

if not 0 <= settings.forecast_watch_probability_percent <= settings.proton_probability_threshold_percent <= 100:
    raise ValueError("Forecast probability thresholds must satisfy 0 <= watch <= action <= 100")
