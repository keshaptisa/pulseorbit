from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class Mode(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    REPLAY = "replay"


class Severity(StrEnum):
    UNKNOWN = "unknown"
    INFO = "info"
    NOT_DETECTED = "not_detected"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class AssessmentRequest(BaseModel):
    mode: Mode = Mode.CURRENT
    start: datetime
    duration_hours: float = Field(ge=1, le=8)
    search_hours: float = Field(default=6, ge=0, le=24)
    step_minutes: int = Field(default=30, ge=15, le=120)
    cutoff: datetime | None = None
    disabled_sources: list[str] = Field(default_factory=list)
    continuous_lighting_required: bool = False
    force_refresh: bool = False
    operation_profile: str = ""
    policy_version: str = ""
    crew_dose_rate_usv_h: float | None = Field(default=None, ge=0)
    crew_dose_limit_usv_h: float | None = Field(default=None, gt=0)
    dosimetry_timestamp: datetime | None = None

    @model_validator(mode="after")
    def require_timezone(self):
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("start must include a timezone; UTC is recommended")
        start_utc = self.start.astimezone(UTC)
        if self.mode in {Mode.HISTORICAL, Mode.REPLAY}:
            lower = datetime(2024, 5, 1, tzinfo=UTC)
            upper = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
            if not lower <= start_utc <= upper:
                raise ValueError("historical and replay dates must be between 2024-05-01 and 2024-06-30 UTC")
        if self.mode is Mode.REPLAY:
            if self.cutoff is None or self.cutoff.tzinfo is None or self.cutoff.utcoffset() is None:
                raise ValueError("replay requires a timezone-aware cutoff")
            if self.cutoff.astimezone(UTC) > start_utc:
                raise ValueError("replay cutoff cannot be later than the requested historical time")
        if self.dosimetry_timestamp is not None and (self.dosimetry_timestamp.tzinfo is None or self.dosimetry_timestamp.utcoffset() is None):
            raise ValueError("dosimetry_timestamp must include a timezone")
        return self


class Evidence(BaseModel):
    kind: str
    title: str
    value: str
    unit: str | None = None
    observed_at: datetime | None
    published_at: datetime | None
    source_name: str
    source_url: str | None = None
    record_id: str
    record_sha256: str | None = None
    note: str


class FactorAssessment(BaseModel):
    factor_id: str
    name: str
    mechanism: str
    severity: Severity
    overlap_minutes: int
    confidence: str
    confidence_basis: str
    applied_rule: str
    decision_relevant: bool = True
    summary: str
    limitations: list[str]
    evidence: list[Evidence]


class WindowAssessment(BaseModel):
    start: datetime
    end: datetime
    severity: Severity
    completeness: str
    score: int
    factors: list[FactorAssessment]


class SourceStatus(BaseModel):
    source_id: str
    name: str
    status: str
    last_success: datetime | None
    age_minutes: int
    is_synthetic: bool
    url: str | None = None
    sha256: str | None = None
    product_timestamp: datetime | None = None
    product_age_minutes: int | None = None
    coverage: str = "unknown"


class AssessmentResponse(BaseModel):
    request: AssessmentRequest
    generated_at: datetime
    algorithm_version: str
    data_notice: str
    recommendation: str
    rationale: list[str]
    windows: list[WindowAssessment]
    source_status: list[SourceStatus]
    trajectory: dict
    historical_cutoff: datetime | None
    evaluation_mode: str
    verification: dict | None = None
    operational_status: str
    operator_action: str
    risk_domains: list[dict]
    decision_gates: list[dict]
    decision_confidence: str
    result_id: str | None = None
