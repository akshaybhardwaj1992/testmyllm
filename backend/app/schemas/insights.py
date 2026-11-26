"""Overview dashboard, quality trends and history (M6)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MetricMean(BaseModel):
    metric: str
    engine: str
    mean: float
    threshold: float | None
    higher_is_better: bool = True
    pass_rate: float | None = None


class LatestRun(BaseModel):
    id: int
    run_name: str
    run_type: str
    pass_rate: float | None
    completed_at: datetime | None
    metrics: list[MetricMean]


class LatestGate(BaseModel):
    run_id: int
    run_name: str
    gate_name: str
    status: str
    kind: str  # quality | security
    created_at: datetime
    baseline_id: int | None = None


class SecurityKpi(BaseModel):
    run_id: int
    run_name: str
    asr: float | None
    breached: int
    attacks: int
    critical: int
    created_at: datetime


class FailingCase(BaseModel):
    case_key: str
    input: str
    dataset: str | None = None
    failures: int
    appearances: int
    last_failed_run_id: int | None


class OverviewOut(BaseModel):
    latest_run: LatestRun | None
    spend_30d: float
    runs_30d: int
    open_gate_failures: int  # gate checks whose latest evaluation (per run and gate) failed, last 30 days
    latest_gate: LatestGate | None
    security: SecurityKpi | None
    top_failing: list[FailingCase]


class TrendPoint(MetricMean):
    run_id: int
    run_name: str
    run_type: str
    created_at: datetime
    judge_model: str | None = None


class TrendOut(BaseModel):
    points: list[TrendPoint]
    days: int
    max_runs: int
