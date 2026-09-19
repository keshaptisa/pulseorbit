from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from app.data import latest_iss_omm, latest_iss_tle, load_daypre_replay, load_historical_event_report, load_live_sources, parse_historical_event_report, parse_noaa_forecast, parse_noaa_intervals, source_status
from app.services.orbit import shadow_intervals, shadow_intervals_gse, trajectory_from_tle, trajectory_summary
from app.services.historical_orbit import load_historical_tle, load_sscweb_points, tle_epoch
from app.config import settings

from app.models import (
    AssessmentRequest,
    AssessmentResponse,
    Evidence,
    FactorAssessment,
    Severity,
    SourceStatus,
    WindowAssessment,
    Mode,
)

ALGORITHM_VERSION = "1.0.1-nasa-historical-geometry"
SEVERITY_RANK = {Severity.INFO: 0, Severity.NOT_DETECTED: 0, Severity.LOW: 0, Severity.MODERATE: 1, Severity.HIGH: 2, Severity.UNKNOWN: 3}
FAMILY_NAMES = {"S": "Радиационная буря NOAA S", "G": "Геомагнитная буря NOAA G", "R": "Радионарушения NOAA R", "PROTON": "Прогноз протонного события", "XRA": "Рентгеновские вспышки XRA"}
OBSERVATIONS_PATH = Path("data/validation/proton_observations.json")


def _overlap_minutes(start: datetime, end: datetime, event_start: datetime, event_end: datetime) -> int:
    overlap = max(timedelta(0), min(end, event_end) - max(start, event_start))
    seconds = overlap.total_seconds()
    # A real intersection shorter than half a minute must not round down to "no overlap".
    return max(1, round(seconds / 60)) if seconds > 0 else 0


def _intersects(window_start: datetime, window_end: datetime, item: dict) -> bool:
    if item["start"] == item["end"]:
        return window_start <= item["start"] <= window_end
    return item["start"] < window_end and item["end"] > window_start


def _record_id(prefix: str, timestamp: datetime) -> str:
    digest = sha256(f"{prefix}:{timestamp.isoformat()}".encode()).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _replay_verification(anchor: datetime, weather_intervals: list[dict] | None) -> dict:
    forecast = max((item for item in (weather_intervals or []) if item.get("probability") is not None), key=lambda item: item["probability"], default=None)
    if not OBSERVATIONS_PATH.exists():
        return {"status": "observation_dataset_unavailable", "forecast": f"{forecast['probability']}%" if forecast else "нет прогноза", "observation": None, "note": "Архив последующих наблюдений не подключён."}
    payload = json.loads(OBSERVATIONS_PATH.read_text(encoding="utf-8"))
    coverage_start = datetime.fromisoformat(payload["coverage_start"].replace("Z", "+00:00"))
    coverage_end = datetime.fromisoformat(payload["coverage_end"].replace("Z", "+00:00"))
    if not coverage_start <= anchor <= coverage_end:
        return {"status": "outside_observation_coverage", "forecast": f"{forecast['probability']}%" if forecast else "нет прогноза", "observation": None, "note": "Дата вне сохранённого наблюдательного периода NOAA SWPC."}
    event = next((item for item in payload["events"] if datetime.fromisoformat(item["start"].replace("Z", "+00:00")) <= anchor <= datetime.fromisoformat(item["end"].replace("Z", "+00:00"))), None)
    return {
        "status": "verified_against_later_observation",
        "forecast": f"Вероятность NOAA daypre: {forecast['probability']}%" if forecast else "Датированный прогноз для окна не найден",
        "observation": f"Наблюдалось событие >10 MeV; пик {event['peak_pfu']} pfu" if event else "В недельных отчётах NOAA протонное событие не зафиксировано",
        "observation_source": event["source"] if event else payload["negative_coverage_reports"],
        "note": "Наблюдение используется только после расчёта для проверки и не влияет на replay-рекомендацию.",
    }


def _weather_level(item: dict | None, missing: bool) -> Severity:
    if missing:
        return Severity.UNKNOWN
    if item is None:
        return Severity.NOT_DETECTED
    probability = item.get("probability")
    if probability is not None:
        if probability >= settings.proton_probability_threshold_percent:
            return Severity.HIGH
        if probability >= settings.forecast_watch_probability_percent:
            return Severity.MODERATE
        return Severity.LOW
    if item.get("family") == "G" and item.get("value") is not None:
        return Severity.HIGH if item["value"] >= 7 else Severity.MODERATE if item["value"] >= 5 else Severity.LOW
    scale = item.get("scale") or ""
    level = int(scale[1:]) if len(scale) == 2 and scale[1].isdigit() else 0
    # NOAA families remain distinct; category thresholds are mapped separately.
    if scale.startswith("S"):
        return Severity.HIGH if level >= 3 else Severity.MODERATE
    if scale.startswith(("G", "R")):
        return Severity.HIGH if level >= 3 else Severity.MODERATE
    # XRA is a retrospective flare proxy, not a proton-dose estimate.
    return Severity.MODERATE if item.get("family") == "XRA" else Severity.LOW


def _factor_assessments(window_start: datetime, window_end: datetime, anchor: datetime, orbit_intervals: list[tuple[datetime, datetime]] | None = None, weather_intervals: list[dict] | None = None, continuous_lighting_required: bool = False, expected_families: tuple[str, ...] = ("S", "G", "R")) -> list[FactorAssessment]:
    factors = []
    for family in expected_families:
        matches = [item for item in (weather_intervals or []) if item.get("family") == family and _intersects(window_start, window_end, item)]
        weather = max(
            matches,
            key=lambda item: (
                SEVERITY_RANK[_weather_level(item, False)],
                1 if not item.get("forecast") else 0,
                item.get("probability", 0),
            ),
            default=None,
        )
        overlap = _overlap_minutes(window_start, window_end, weather["start"], weather["end"]) if weather else 0
        missing = weather_intervals is None
        severity = _weather_level(weather, missing)
        value = weather.get("scale") if weather else None
        if weather and weather.get("probability") is not None:
            value = f"{weather['probability']}%"
        elif weather and weather.get("value") is not None:
            value = f"Kp {weather['value']:.2f}"
        factors.append(FactorAssessment(
            factor_id=f"space-weather-{family.lower()}", name=FAMILY_NAMES[family], mechanism=f"Космическая погода: {family}", severity=severity,
            overlap_minutes=overlap, confidence="низкая" if missing or family == "XRA" else "средняя", decision_relevant=True,
            confidence_basis=("Источник недоступен: численная оценка невозможна." if missing else "Датированный внешний прогноз NOAA; локальная доза и состояние конкретного канала не измеряются." if weather and weather.get("forecast") else "Оперативное или архивное сообщение NOAA с идентификатором записи."),
            applied_rule=(f"Вероятность >= {settings.proton_probability_threshold_percent}%: высокий уровень; >= {settings.forecast_watch_probability_percent}%: требует внимания." if weather and weather.get("probability") is not None else "NOAA S/G/R: уровни 1-2 требуют внимания, 3-5 имеют высокий уровень." if family in {"S", "G", "R"} else "XRA используется только как ретроспективный индикатор вспышки."),
            summary=(f"Прогноз {family} покрывает окно: {value}; пересечение {overlap} мин." if weather and weather.get("forecast") else f"Окно пересекает интервал {family} на {overlap} мин." if overlap else f"Событие {family} не выявлено в пределах подключённого продукта." if not missing else f"Источник для {family} недоступен."),
            limitations=["Отсутствие записи не доказывает отсутствие воздействия вне охвата продукта.", "Показатель внешней среды не является оценкой индивидуальной дозы или допуска к ВКД."],
            evidence=[Evidence(
                kind="external_forecast" if family == "PROTON" or weather and weather.get("forecast") else "observation",
                title=f"NOAA {family}" if weather else f"Проверка NOAA {family}", value=value or ("источник недоступен" if missing else "событие не найдено"),
                observed_at=weather.get("start") if weather else None, published_at=weather.get("published_at") if weather else None,
                source_name="NOAA NCEI daypre" if family == "PROTON" else "NOAA SWPC 3-day forecast" if weather and weather.get("forecast") else "NOAA NCEI event report" if family == "XRA" else "NOAA SWPC alerts",
                source_url=weather.get("source_url") if weather else "https://www.swpc.noaa.gov/products/alerts-watches-and-warnings",
                record_id=str(weather.get("product_id")) if weather and weather.get("product_id") else _record_id(f"coverage-{family.lower()}", anchor),
                record_sha256=weather.get("record_sha256") if weather else None,
                note=weather.get("message", "") if weather else "Проверен доступный продукт; искусственное время события не создаётся." if not missing else "Нельзя отличить отсутствие события от отсутствия данных.",
            )],
        ))
    shadow_overlap = sum(_overlap_minutes(window_start, window_end, start, end) for start, end in (orbit_intervals or []))
    shadow_severity = Severity.UNKNOWN if orbit_intervals is None else Severity.MODERATE if continuous_lighting_required and shadow_overlap else Severity.LOW if continuous_lighting_required else Severity.INFO
    factors.append(FactorAssessment(
            factor_id="orbital-lighting",
            name="Переход в орбитальную тень",
            mechanism="Орбитальные условия",
            severity=shadow_severity,
            overlap_minutes=shadow_overlap,
            confidence="средняя" if orbit_intervals is not None else "низкая",
            confidence_basis="SGP4 по датированному TLE с шагом 10 минут; модель не учитывает полутень и геометрию рабочего места." if orbit_intervals is not None else "Подходящие орбитальные элементы отсутствуют.",
            applied_rule="Тень влияет на выбор только при включённом требовании непрерывного освещения; любое пересечение тогда требует внимания.",
            decision_relevant=continuous_lighting_required,
            summary=(
                (f"Ограничение непрерывного освещения нарушается: {shadow_overlap} мин модельной тени." if shadow_overlap else "Ограничение непрерывного освещения выполнено.")
                if continuous_lighting_required
                else f"Ожидается {shadow_overlap} мин модельной тени; это информационное условие и оно не повышает риск без ограничения работ."
            ),
            limitations=[
                "Это ограничение работ, а не самостоятельное доказательство опасности.",
                "Цилиндрическая модель тени не учитывает полутень и конкретную геометрию места работ.",
            ],
            evidence=[
                Evidence(
                    kind="derived_signal",
                    title="Расчёт освещённости",
                    value=str(shadow_overlap),
                    unit="мин пересечения",
                    observed_at=window_start,
                    published_at=None,
                    source_name="SGP4 + analytical Sun model" if orbit_intervals is not None else "Орбитальные данные недоступны",
                    source_url="https://celestrak.org/NORAD/elements/gp.php?CATNR=25544",
                    record_id=_record_id("shadow", anchor),
                    note="Расчёт команды по орбитальной траектории и направлению на Солнце." if orbit_intervals is not None else "Расчёт не выполнен; нулевое пересечение не предполагается.",
                )
            ],
        ))
    return factors


def _assess_window(start: datetime, duration_hours: float, anchor: datetime, orbit_intervals: list[tuple[datetime, datetime]] | None = None, weather_intervals: list[dict] | None = None, continuous_lighting_required: bool = False, expected_families: tuple[str, ...] = ("S", "G", "R")) -> WindowAssessment:
    end = start + timedelta(hours=duration_hours)
    factors = _factor_assessments(start, end, anchor, orbit_intervals, weather_intervals, continuous_lighting_required, expected_families)
    relevant = [item for item in factors if item.decision_relevant]
    worst = max(relevant, key=lambda item: SEVERITY_RANK[item.severity]).severity
    relevant_overlaps = [item.overlap_minutes for item in relevant if item.severity in {Severity.MODERATE, Severity.HIGH}]
    max_overlap_share = round(100 * max(relevant_overlaps, default=0) / max(1, duration_hours * 60))
    score = SEVERITY_RANK[worst] * 100 + min(max_overlap_share, 100)
    return WindowAssessment(
        start=start,
        end=end,
        severity=worst,
        completeness="частичное покрытие" if any(item.severity is Severity.UNKNOWN for item in factors) else "подключённые источники обработаны",
        score=score,
        factors=factors,
    )


def assess(request: AssessmentRequest) -> AssessmentResponse:
    anchor = request.start.astimezone(UTC)
    # Current feeds are not valid historical evidence. Until dated archives are
    # connected, historical modes deliberately expose missing data.
    live = load_live_sources(force_refresh=request.force_refresh) if request.mode is Mode.CURRENT else {}
    if "noaa" in request.disabled_sources:
        live.pop("noaa", None)
        live.pop("noaa_forecast", None)
    if "orbit" in request.disabled_sources:
        live.pop("orbit", None)
        live.pop("orbit_tle", None)
    cutoff = request.cutoff.astimezone(UTC) if request.cutoff else (anchor if request.mode is Mode.REPLAY else None)
    archive = None
    replay_meta = None
    if request.mode is Mode.HISTORICAL:
        expected_families = ("XRA",)
        archive = load_historical_event_report(anchor)
        weather_intervals = parse_historical_event_report(archive, anchor) if isinstance(archive.get("body"), str) else None
    elif request.mode is Mode.REPLAY:
        expected_families = ("PROTON",)
        weather_intervals, replay_meta = load_daypre_replay(cutoff, anchor + timedelta(hours=request.search_hours + request.duration_hours))
        if replay_meta is None or replay_meta.get("status") == "unparseable":
            weather_intervals = None
    else:
        expected_families = ("S", "G", "R")
        noaa_source = live.get("noaa", {})
        alerts = parse_noaa_intervals(noaa_source, cutoff) if isinstance(noaa_source.get("body"), list) else None
        forecast_source = live.get("noaa_forecast", {})
        forecast = parse_noaa_forecast(forecast_source, anchor + timedelta(hours=request.search_hours + request.duration_hours)) if isinstance(forecast_source.get("body"), str) else None
        weather_intervals = (alerts or []) + (forecast or []) if alerts is not None or forecast is not None else None
    omm = latest_iss_omm(live.get("orbit", {})) if live else None
    tle = latest_iss_tle(live.get("orbit_tle", {})) if live else load_historical_tle(anchor, available_before=cutoff if request.mode is Mode.REPLAY else None)
    ssc_historical = request.mode is Mode.HISTORICAL
    points = load_sscweb_points(anchor, request.search_hours + request.duration_hours) if ssc_historical else trajectory_from_tle(tle, anchor, request.search_hours + request.duration_hours, 10) if tle else []
    orbit_intervals = shadow_intervals_gse(points) if ssc_historical and points else shadow_intervals(points) if points else None
    step = timedelta(minutes=request.step_minutes)
    count = int(request.search_hours * 60 // request.step_minutes) + 1
    windows = [_assess_window(anchor + step * index, request.duration_hours, anchor, orbit_intervals, weather_intervals, request.continuous_lighting_required, expected_families) for index in range(count)]
    ranked = sorted(windows, key=lambda item: (item.score, item.start))
    best = ranked[0]
    ties = [window for window in ranked if window.score == best.score]

    if all(window.severity is Severity.UNKNOWN for window in windows):
        recommendation = "Недостаточно данных для рекомендации"
        rationale = ["Критически важный фактор имеет статус unknown.", "Отсутствие данных не интерпретируется как благоприятная обстановка."]
    elif len(ties) > 1:
        recommendation = "Несколько окон равноценны по доступным данным"
        rationale = [
            f"Минимальную оценку {best.score} получили {len(ties)} окон.",
            "Для окончательного выбора нужны реальные оперативные данные и ограничения конкретных работ.",
        ]
    else:
        recommendation = f"Предпочтительное начало: {best.start.isoformat()}"
        rationale = [
            f"Индекс выбранного окна {best.score}, исходного окна {windows[0].score}.",
            "Выбор минимизирует категорию худшего критичного фактора, затем максимальную долю пересечения одного фактора.",
        ]

    now = datetime.now(UTC)
    statuses = []
    if live:
        statuses = []
        if "noaa" in live:
            noaa_status = source_status(live["noaa"], "NOAA SWPC alerts")
            issued_values = [item.get("issue_datetime") for item in (live["noaa"].get("body") or []) if item.get("issue_datetime")]
            if issued_values:
                try:
                    product_time = max(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC) for value in issued_values)
                    noaa_status.update(product_timestamp=product_time, product_age_minutes=max(0, round((now - product_time).total_seconds() / 60)), coverage="S/G/R alerts")
                except ValueError:
                    pass
            statuses.append(noaa_status)
            if "noaa_forecast" in live:
                forecast_status = source_status(live["noaa_forecast"], "NOAA SWPC 3-day forecast")
                forecast_status["coverage"] = "Kp, S1+ и R3+ на три суток"
                statuses.append(forecast_status)
        else:
            statuses.append(SourceStatus(source_id="noaa-alerts", name="NOAA SWPC alerts", status="disabled", last_success=None, age_minutes=0, is_synthetic=False).model_dump())
        if "orbit" in live:
            omm_status = source_status(live["orbit"], "CelesTrak ISS OMM")
            if omm and omm.get("EPOCH"):
                product_time = datetime.fromisoformat(omm["EPOCH"].replace("Z", "+00:00")).astimezone(UTC)
                omm_status.update(product_timestamp=product_time, product_age_minutes=max(0, round((now - product_time).total_seconds() / 60)), coverage="ISS NORAD 25544")
            tle_status = source_status(live["orbit_tle"], "CelesTrak ISS TLE")
            if tle:
                product_time = tle_epoch(tle[0])
                tle_status.update(product_timestamp=product_time, product_age_minutes=max(0, round((now - product_time).total_seconds() / 60)), coverage="ISS NORAD 25544")
            statuses.extend([omm_status, tle_status])
        else:
            statuses.append(SourceStatus(source_id="celestrak-iss", name="CelesTrak ISS orbit", status="disabled", last_success=None, age_minutes=0, is_synthetic=False).model_dump())
    else:
        historical = request.mode in {Mode.HISTORICAL, Mode.REPLAY}
        statuses = [
            (source_status(archive, "NOAA NCEI daily event archive") if archive else SourceStatus(source_id="historical-noaa" if historical else "live-noaa", name="NOAA historical archive" if historical else "NOAA SWPC live sources", status="unavailable", last_success=None, age_minutes=0, is_synthetic=False, coverage="источник не получен").model_dump()),
            SourceStatus(source_id="historical-orbit", name="NASA SSCWeb historical ISS coordinates" if request.mode is Mode.HISTORICAL and points else "Historical ISS orbit archive", status="archive" if historical and points else "unavailable", last_success=anchor if historical and points else None, age_minutes=0, is_synthetic=False, url="https://sscweb.gsfc.nasa.gov/" if request.mode is Mode.HISTORICAL and points else None, product_timestamp=anchor if request.mode is Mode.HISTORICAL and points else tle_epoch(tle[0]) if tle else None, product_age_minutes=0 if request.mode is Mode.HISTORICAL and points else round((anchor - tle_epoch(tle[0])).total_seconds()/60) if tle else None, coverage="ISS, GSE, шаг 10 минут" if request.mode is Mode.HISTORICAL and points else "ISS NORAD 25544" if tle else "нет подходящих орбитальных данных").model_dump(),
        ]
        if request.mode is Mode.REPLAY and replay_meta:
            statuses[0] = SourceStatus(source_id="noaa-daypre", name="NOAA NCEI archived daypre", status=replay_meta["status"], last_success=replay_meta["issued"], age_minutes=0, is_synthetic=False, url="https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports/daypre/", sha256=replay_meta.get("sha256"), product_timestamp=replay_meta["issued"], product_age_minutes=round((anchor-replay_meta["issued"]).total_seconds()/60), coverage="суточная вероятность протонного события").model_dump()
    orbit_summary = trajectory_summary(points)
    orbit_epoch = omm.get("EPOCH") if omm else tle_epoch(tle[0]).isoformat() if tle else None
    trajectory_info = {
        "source": "NASA GSFC SSCWeb historical ISS coordinates" if ssc_historical and points else "CelesTrak OMM + TLE/SGP4" if live and tle else ("Local historical ISS TLE + SGP4" if tle else "Орбитальная траектория недоступна"),
        "norad_id": 25544,
        "epoch": orbit_epoch,
        "propagator": "SGP4" if points else "unavailable",
        "coordinate_frame": "GSE" if ssc_historical and points else "TEME" if points else None,
        "time_scale": "UTC",
        "gravity_model": "WGS-72" if points else None,
        "points": len(points) if points else None,
        "shadow_intervals": [
            {"start": start.isoformat(), "end": end.isoformat()}
            for start, end in (orbit_intervals or [])
        ],
        **orbit_summary,
    }
    active_statuses = [item for item in statuses if item.get("status") != "disabled"]
    has_gaps = any(item.get("status") in {"unavailable", "unparseable"} for item in active_statuses)
    disabled_names = [item["name"] for item in statuses if item.get("status") == "disabled"]
    all_factors = [factor for window in windows for factor in window.factors]
    missing_critical = any(f.severity is Severity.UNKNOWN and f.decision_relevant for f in all_factors)
    radiation_factors = [f for f in all_factors if f.factor_id in {"space-weather-s", "space-weather-proton"}]
    max_s = max((int(f.evidence[0].value[1:]) for f in radiation_factors if f.evidence[0].value.startswith("S")), default=0)
    radiation_attention = any(f.severity is Severity.HIGH for f in radiation_factors)
    dose_age_minutes = None if request.dosimetry_timestamp is None else (now - request.dosimetry_timestamp.astimezone(UTC)).total_seconds() / 60
    dosimetry_current = request.crew_dose_rate_usv_h is not None and request.crew_dose_limit_usv_h is not None and dose_age_minutes is not None and -5 <= dose_age_minutes <= 30
    gates = [
        {"id": "sources", "label": "Критичные источники доступны", "passed": not missing_critical, "blocking": True},
        {"id": "policy", "label": f"Применена политика {request.policy_version}", "passed": bool(request.policy_version.strip()), "blocking": True},
        {"id": "dosimetry", "label": "Дозиметрия и порог политики актуальны (не старше 30 мин)", "passed": dosimetry_current, "blocking": True},
        {"id": "operation", "label": f"Профиль операции: {request.operation_profile}", "passed": bool(request.operation_profile.strip()), "blocking": True},
    ]
    if request.crew_dose_rate_usv_h is not None and request.crew_dose_limit_usv_h is not None:
        gates.append({"id": "dose_limit", "label": "Дозиметрическая скорость ниже порога политики", "passed": request.crew_dose_rate_usv_h < request.crew_dose_limit_usv_h, "blocking": True})
    blocking_gate_failed = any(gate["blocking"] and not gate["passed"] for gate in gates)
    failed_gate_labels = [gate["label"] for gate in gates if gate["blocking"] and not gate["passed"]]
    dose_exceeded = request.crew_dose_rate_usv_h is not None and request.crew_dose_limit_usv_h is not None and request.crew_dose_rate_usv_h >= request.crew_dose_limit_usv_h
    if missing_critical:
        operational_status, operator_action = "HOLD_DATA", "Не принимать решение по окну. Восстановить критичные источники и повторить расчёт."
    elif max_s >= 3:
        operational_status, operator_action = "NO_GO_REVIEW", "Не планировать ВКД по данным прототипа. Требуются проверка радиационного специалиста и бортовая дозиметрия."
    elif max_s >= 1 or radiation_attention:
        operational_status, operator_action = "HOLD_RADIATION", "Приостановить выбор окна до оценки радиационного специалиста и проверки бортовой дозиметрии."
    elif dose_exceeded:
        operational_status, operator_action = "HOLD_DOSIMETRY", f"Измеренная скорость дозы {request.crew_dose_rate_usv_h:g} мкЗв/ч достигла порога политики {request.crew_dose_limit_usv_h:g} мкЗв/ч. Окно заблокировано."
    elif blocking_gate_failed:
        operational_status, operator_action = "HOLD_POLICY", "Ожидают заполнения: " + "; ".join(failed_gate_labels) + ". Оценка окон уже выполнена и показана ниже; статус обновится после ввода."
    elif any(w.severity in {Severity.HIGH, Severity.MODERATE} for w in windows):
        operational_status, operator_action = "REVIEW", "Проверить ограничения связи, навигации и освещения у ответственного специалиста."
    else:
        operational_status, operator_action = "CONDITIONAL", "Окно можно рассматривать дальше. Для допуска нужны бортовая дозиметрия, профиль конкретной ВКД и решение ответственного специалиста."
    risk_domains = [
        {"id": "radiation", "name": "Радиационная обстановка", "basis": "NOAA S-scale, прогноз S1+ и введённая дозиметрия", "status": "critical" if max_s >= 3 or dose_exceeded else "review" if max_s >= 1 or radiation_attention else "monitor", "limitation": "Открытые данные не являются индивидуальной дозой; решение использует отдельно введённую телеметрию."},
        {"id": "communications", "name": "Связь и навигация", "basis": "NOAA G/R", "status": "review" if any(f.factor_id in {"space-weather-g", "space-weather-r"} and f.severity in {Severity.MODERATE, Severity.HIGH} for f in all_factors) else "monitor", "limitation": "Не является прогнозом отказа конкретного канала."},
        {"id": "lighting", "name": "Освещённость", "basis": "SGP4 и модель тени", "status": "review" if request.continuous_lighting_required and any(f.factor_id == "orbital-lighting" and f.overlap_minutes for f in all_factors) else "monitor", "limitation": "Не включает полутень, локальную геометрию и тепловой режим."},
        {"id": "data", "name": "Полнота данных", "basis": "Актуальность и provenance", "status": "critical" if missing_critical else "verified", "limitation": "Публичные источники не заменяют сертифицированные контуры управления."},
    ]
    # Describes only whether the checklist is closed, never the quality of the underlying data.
    decision_confidence = "gates_closed" if not blocking_gate_failed and not missing_critical else "gates_open"
    return AssessmentResponse(
        request=request,
        generated_at=now,
        algorithm_version=ALGORITHM_VERSION,
        data_notice=(
            ("REPLAY: использован последний NOAA daypre с Issued <= cutoff; орбита допускается только из снимка CelesTrak, доступного до cutoff. " if request.mode is Mode.REPLAY and replay_meta else "REPLAY: датированный прогноз до cutoff не найден; показана неполнота данных. " if request.mode is Mode.REPLAY else "HISTORICAL ANALYSIS: использованы итоговые события NOAA NCEI и доступная историческая реконструкция орбиты. " if request.mode is Mode.HISTORICAL else "")
            + ("Подключённые источники обработаны. " if not has_gaps else "Есть пробелы данных; они не считаются благоприятной обстановкой. ")
            + (f"Отключены: {', '.join(disabled_names)}. " if disabled_names else "")
            + "Это исследовательский прототип, а не допуск к ВКД."
        ),
        recommendation=recommendation,
        rationale=rationale,
        windows=windows,
        source_status=[SourceStatus(**status) if isinstance(status, dict) else status for status in statuses],
        trajectory=trajectory_info,
        historical_cutoff=cutoff,
        evaluation_mode=("replay_strict" if request.mode is Mode.REPLAY else "historical_analysis" if request.mode is Mode.HISTORICAL else "current_monitoring"),
        verification=(_replay_verification(anchor, weather_intervals) if request.mode is Mode.REPLAY else None),
        operational_status=operational_status,
        operator_action=operator_action,
        risk_domains=risk_domains,
        decision_gates=gates,
        decision_confidence=decision_confidence,
    )
