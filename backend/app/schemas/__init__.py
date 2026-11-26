"""Pydantic request/response models (the OpenAPI schema the frontend's types are generated from)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.evaluators.applicability import METRICS

Role = Literal["owner", "editor", "viewer"]
RunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
ResultStatus = Literal["pending", "running", "passed", "failed", "error", "cancelled"]
MetricStatus = Literal["passed", "failed", "not_applicable", "error"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth
class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(ORM):
    id: int
    email: str


class LoginOut(BaseModel):
    token: str
    user: UserOut


# ---------------------------------------------------------------- projects
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class ProjectSettings(BaseModel):
    """Data handling (spec §11, D30). retention_days: delete runs older than this (baseline runs are kept).
    no_persistence: new runs are deleted `no_persistence_hours` after creation - export what you need."""

    retention_days: int | None = Field(default=None, ge=1, le=3650)
    no_persistence: bool = False
    no_persistence_hours: int = Field(default=24, ge=1, le=168)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    settings: ProjectSettings | None = None


class LastRunSummary(BaseModel):
    id: int
    run_name: str
    status: RunStatus
    pass_rate: float | None
    created_at: datetime


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    created_at: datetime
    role: Role
    settings: ProjectSettings = ProjectSettings()
    last_run: LastRunSummary | None = None


# ---------------------------------------------------------------- providers
class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: Literal["openrouter"] = "openrouter"
    api_key: str = Field(min_length=8, max_length=500)


class ProviderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class ProviderOut(ORM):
    """Keys are write-only: only a masked hint is ever returned."""

    id: int
    project_id: int
    name: str
    type: str
    key_hint: str
    status: str
    status_message: str
    created_at: datetime


class ProviderTestOut(BaseModel):
    ok: bool
    message: str


class CatalogModel(BaseModel):
    id: str
    price_in_per_mtok: float | None
    price_out_per_mtok: float | None
    name: str = ""
    kind: Literal["chat", "embedding"] = "chat"
    supports_tools: bool = False  # OpenRouter "supported_parameters" includes "tools" (agent testing)
    context_length: int | None = None


# ---------------------------------------------------------------- judges
class JudgeConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_id: int
    model: str = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.0, ge=0, le=2)
    embedding_model: str = Field(default="openai/text-embedding-3-small", min_length=1, max_length=200)
    is_default: bool = False
    notes: str = Field(default="", max_length=2000)


class JudgeConfigPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    is_default: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)


class JudgeConfigOut(ORM):
    id: int
    project_id: int
    provider_id: int
    name: str
    model: str
    temperature: float
    embedding_model: str
    is_default: bool
    notes: str


# ---------------------------------------------------------------- metrics catalog
class EngineImplOut(BaseModel):
    engine: str
    engine_metric_name: str
    engine_version: str
    requires: list[str]


class MetricDefOut(BaseModel):
    name: str
    label: str
    description: str
    higher_is_better: bool
    default_threshold: float
    engines: list[EngineImplOut]


# ---------------------------------------------------------------- runs
class MetricSelection(BaseModel):
    metric: str
    engine: Literal["ragas", "deepeval", "testmyllm"]
    threshold: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _known(self) -> MetricSelection:
        mdef = METRICS.get(self.metric)
        if mdef is None:
            raise ValueError(f"Unknown metric '{self.metric}'")
        if mdef.impl(self.engine) is None:
            raise ValueError(f"{mdef.label} is not available in {self.engine}")
        if self.threshold is None:
            self.threshold = mdef.default_threshold
        return self


class StudioCase(BaseModel):
    question: str = Field(min_length=1, max_length=20000)
    reference: str | None = Field(default=None, max_length=20000)
    context: list[str] | None = Field(default=None, max_length=50)
    criteria: str | None = Field(default=None, max_length=5000)

    @field_validator("context")
    @classmethod
    def _ctx(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [c.strip() for c in v if c and c.strip()]
        if any(len(c) > 20000 for c in cleaned):
            raise ValueError("Each context chunk must be at most 20,000 characters")
        return cleaned or None

    @field_validator("reference", "criteria")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class OutputSpec(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # manual: pasted text (Studio only) · provider: generated per case · dataset: the case's recorded response
    # rag_api: answer + retrieved contexts from the user's RAG endpoint (live RAG, spec §4.2)
    source: Literal["manual", "provider", "dataset", "rag_api"]
    connector_id: int | None = None
    text: str | None = Field(default=None, max_length=50000)
    source_model: str | None = Field(default=None, max_length=200)  # manual: who produced it
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=200)
    max_tokens: int = Field(default=1000, ge=1, le=8000)
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode="after")
    def _shape(self) -> OutputSpec:
        if self.source == "manual" and not (self.text and self.text.strip()):
            raise ValueError(f"'{self.label}': a manual answer needs text")
        if self.source == "provider" and not (self.provider_id and self.model):
            raise ValueError(f"'{self.label}': generating an answer needs a provider and a model")
        if self.source == "rag_api" and not self.connector_id:
            raise ValueError(f"'{self.label}': live RAG answers need a connector")
        return self


class RunCreate(BaseModel):
    """Exactly one test source: `case` (Studio), `suite_id`, or `dataset_version_id`."""

    project_id: int
    run_name: str | None = Field(default=None, max_length=300)
    run_type: Literal["llm", "rag", "benchmark"] = "llm"
    judge_config_id: int
    repeats: int = Field(default=1, ge=1, le=10)
    budget_usd: float | None = Field(default=None, gt=0, le=10000)
    use_cache: bool = True
    metrics: list[MetricSelection] = Field(min_length=1, max_length=40)
    case: StudioCase | None = None
    suite_id: int | None = None
    dataset_version_id: int | None = None
    outputs: list[OutputSpec] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def _source(self) -> RunCreate:
        sources = [x for x in (self.case, self.suite_id, self.dataset_version_id) if x is not None]
        if len(sources) != 1:
            raise ValueError("Provide exactly one of: case, suite_id, dataset_version_id")
        studio = self.case is not None
        for o in self.outputs:
            if studio and o.source in ("dataset", "rag_api"):
                raise ValueError(f"'{o.label}': Studio cases need a pasted or generated answer")
            if not studio and o.source == "manual":
                raise ValueError(f"'{o.label}': dataset runs use recorded responses or a provider, not pasted text")
            if o.source == "rag_api" and self.run_type != "rag":
                raise ValueError("Live RAG answers require run_type 'rag'")
        if self.run_type == "rag" and studio:
            raise ValueError("RAG runs evaluate a dataset version or suite")
        if self.run_type == "benchmark":
            if studio:
                raise ValueError("Model comparisons run on a dataset version or suite")
            if len(self.outputs) < 2 or any(o.source != "provider" for o in self.outputs):
                raise ValueError("A model comparison needs 2 to 5 models, each generated by a provider")
            models = [(o.provider_id, o.model, o.temperature, o.max_tokens) for o in self.outputs]
            if len(models) != len(set(models)):
                raise ValueError("Each compared model configuration must be different")
        return self

    @model_validator(mode="after")
    def _unique(self) -> RunCreate:
        keys = [(m.metric, m.engine) for m in self.metrics]
        if len(keys) != len(set(keys)):
            raise ValueError("Each metric/engine pair may be selected once")
        labels = [o.label.strip().lower() for o in self.outputs]
        if len(labels) != len(set(labels)):
            raise ValueError("Answer labels must be unique")
        return self


class CostLine(BaseModel):
    usd: float | None
    input_tokens: int
    output_tokens: int


class RunWarning(BaseModel):
    type: Literal["judge_bias", "pricing_unknown", "not_applicable", "budget"]
    message: str


class EstimateOut(BaseModel):
    model_under_test: CostLine
    judge: CostLine
    total_usd: float | None
    judge_calls: int
    applicable_metric_runs: int
    not_applicable: list[str]
    warnings: list[RunWarning]
    note: str = "Heuristic estimate from input lengths and per-metric call counts; actual cost is measured."


class RunOut(ORM):
    id: int
    project_id: int
    run_name: str
    run_type: str
    status: RunStatus
    status_reason: str
    repeats: int
    use_cache: bool
    budget_usd: float | None
    estimated_cost_usd: float | None
    actual_cost_usd: float
    total_tests: int
    passed_tests: int
    failed_tests: int
    error_tests: int
    completed_tests: int = 0
    judge_config_id: int | None
    config_snapshot: dict
    engine_versions: dict
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime | None = None
    warnings: list[RunWarning] = []


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int
    page: int
    page_size: int


class ReviewIn(BaseModel):
    verdict: Literal["agree", "disagree"]
    override_status: Literal["passed", "failed"] | None = None
    comment: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _override_only_on_disagree(self) -> ReviewIn:
        if self.override_status and self.verdict != "disagree":
            raise ValueError("An override status only makes sense when you disagree")
        return self


class ReviewOut(ORM):
    id: int
    metric_result_id: int
    reviewer_id: int
    reviewer_email: str = ""
    verdict: Literal["agree", "disagree"]
    override_status: str | None
    comment: str
    created_at: datetime


class MetricResultOut(ORM):
    id: int
    repeat_index: int
    engine: str
    engine_version: str
    metric_name: str
    engine_metric_name: str
    higher_is_better: bool
    score: float | None
    threshold: float | None
    status: MetricStatus
    reason: str | None
    error_message: str | None
    judge_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    judge_cost_usd: float | None
    cache_hit: bool
    execution_time_ms: int | None
    reviews: list[ReviewOut] = []


class MetricAggregate(BaseModel):
    """One metric on one engine across repeats. Errors are counted, never averaged."""

    metric_name: str
    engine: str
    engine_version: str
    engine_metric_name: str
    higher_is_better: bool
    threshold: float | None
    status: MetricStatus
    mean: float | None
    std: float | None
    min: float | None
    max: float | None
    n: int
    errors: int
    reason: str | None
    judge_model: str | None


class CaseOut(ORM):
    id: int
    input: str
    expected_output: str | None
    provided_context: list[str] | None
    criteria: str | None
    tags: list[str] = []
    case_key: str = ""


class TestResultOut(ORM):
    __test__ = False
    id: int
    test_case_id: int
    output_label: str
    source_model: str | None
    output_source: str
    model_output: str | None
    retrieved_context: list[str] | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    status: ResultStatus
    error_message: str | None
    failure_origin: str | None
    trace: dict | None = None
    case: CaseOut
    metrics: list[MetricResultOut]
    aggregates: list[MetricAggregate]


class ResultsOut(BaseModel):
    items: list[TestResultOut]
    total: int
    page: int = 1
    page_size: int = 50
    labels: list[str] = []


class HistogramBin(BaseModel):
    start: float
    end: float
    count: int


class MetricSummaryRow(BaseModel):
    """Per metric × engine across the run's results. Per-result values are the mean over repeats."""

    metric_name: str
    engine: str
    engine_version: str
    higher_is_better: bool
    threshold: float | None
    n: int
    mean: float | None
    std: float | None
    pass_rate: float | None
    passed: int
    failed: int
    errors: int
    not_applicable: int
    histogram: list[HistogramBin]


class AgreementOut(BaseModel):
    reviewed: int
    agree: int
    disagree: int
    rate: float | None


class RunSummaryOut(BaseModel):
    metrics: list[MetricSummaryRow]
    agreement: AgreementOut
    status_counts: dict[str, int]
    # RAG runs: retrieval | generation | both | undetermined (heuristic, spec §4.2)
    failure_origins: dict[str, int] = {}


# ---------------------------------------------------------------- model comparison (M4)
class ModelConfigIn(BaseModel):
    provider_id: int
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int = Field(default=1000, ge=1, le=8000)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ModelConfigOut(BaseModel):
    id: int
    provider_id: int
    name: str
    model: str
    max_tokens: int
    temperature: float | None
    created_at: datetime


class ModelMetricSummary(BaseModel):
    metric_name: str
    engine: str
    mean: float | None
    std: float | None
    pass_rate: float | None
    n: int


class ModelSummary(BaseModel):
    label: str
    model: str | None
    results: int
    passed: int
    failed: int
    errors: int
    quality: float | None  # unweighted mean of this model's metric means (all metrics are 0-1, higher better)
    metrics: list[ModelMetricSummary]
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    input_tokens: int
    output_tokens: int
    avg_output_tokens: float | None
    model_cost_usd: float | None  # generation cost of this model (None if any call's cost is unknown)
    judge_cost_usd: float
    cost_per_case_usd: float | None


class MetricColumn(BaseModel):
    metric: str
    engine: str


class ComparisonOut(BaseModel):
    models: list[ModelSummary]
    metric_columns: list[MetricColumn]
    quality_note: str = (
        "Quality = unweighted mean of the model's per-metric means across the selected metrics and engines. "
        "Use it for ranking at a glance; compare individual metrics before deciding."
    )


class CaseOutputOut(BaseModel):
    result_id: int
    status: ResultStatus
    model_output: str | None
    error_message: str | None
    latency_ms: int | None
    output_tokens: int | None
    cost_usd: float | None
    aggregates: list[MetricAggregate]


class CaseComparisonRow(BaseModel):
    case: CaseOut
    outputs: dict[str, CaseOutputOut]


class CaseComparisonOut(BaseModel):
    items: list[CaseComparisonRow]
    total: int
    page: int
    page_size: int
    labels: list[str]


# ---------------------------------------------------------------- regression, gates, tokens (M5)
class BaselineIn(BaseModel):
    project_id: int
    run_id: int
    name: str = Field(min_length=1, max_length=200)


class BaselineOut(BaseModel):
    id: int
    project_id: int
    name: str
    run_id: int
    run_name: str
    dataset_version_id: int
    dataset_name: str
    version_number: int
    created_at: datetime


class GateRule(BaseModel):
    metric: str
    engine: Literal["ragas", "deepeval", "testmyllm"]
    min_score: float | None = Field(default=None, ge=0, le=1)
    max_regression: float | None = Field(default=0.05, ge=0, le=1)
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def _valid(self) -> GateRule:
        mdef = METRICS.get(self.metric)
        if mdef is None or mdef.impl(self.engine) is None:
            raise ValueError(f"{self.metric} is not available in {self.engine}")
        if self.min_score is None and self.max_regression is None and self.min_pass_rate is None:
            raise ValueError(f"Rule for {self.metric} ({self.engine}) needs at least one check")
        return self


class QualityGateIn(BaseModel):
    project_id: int
    name: str = Field(min_length=1, max_length=200)
    rules: list[GateRule] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _unique(self) -> QualityGateIn:
        keys = [(r.metric, r.engine) for r in self.rules]
        if len(keys) != len(set(keys)):
            raise ValueError("One rule per metric and engine")
        return self


class QualityGateOut(ORM):
    id: int
    project_id: int
    name: str
    rules: list[GateRule]
    created_at: datetime


class RegressionMetric(BaseModel):
    """baseline_mean / candidate_mean_paired / pass rates *_paired: over paired cases (what the delta uses).
    candidate_mean / candidate_pass_rate: over the whole candidate run (what min_score / min_pass_rate use)."""

    metric_name: str
    engine: str
    baseline_mean: float | None
    candidate_mean_paired: float | None = None
    candidate_mean: float | None
    candidate_pass_rate_paired: float | None = None
    delta: float | None  # direction-aware: positive = better
    tolerance: float
    standard_error: float | None  # judge-noise SE of the delta (both runs need repeats >= 3)
    paired: int
    baseline_pass_rate: float | None
    candidate_pass_rate: float | None
    status: Literal["regressed", "improved", "unchanged", "no_data", "no_baseline"]


class RegressionCase(BaseModel):
    case_key: str
    input: str
    label: str
    baseline_result_id: int
    candidate_result_id: int
    baseline_status: str
    candidate_status: str
    baseline_output: str | None
    candidate_output: str | None
    metric_deltas: dict[str, float]


class RegressionReport(BaseModel):
    baseline_run_id: int | None
    candidate_run_id: int
    dataset_version_match: bool | None
    paired_cases: int
    metrics: list[RegressionMetric]
    regressed: list[RegressionCase]
    improved: list[RegressionCase]
    unchanged_count: int
    warnings: list[str]
    note: str = ("Regression = drop beyond the tolerance and, with 3+ repeats in both runs, beyond one standard "
                 "error of judge noise. Tunable defaults, not statistical guarantees.")


class GateCheck(BaseModel):
    check: str
    passed: bool
    detail: str
    skipped: bool = False


class GateRuleResult(BaseModel):
    metric: str
    engine: str
    passed: bool
    checks: list[GateCheck]


class GateResult(BaseModel):
    gate_id: int
    gate_name: str
    passed: bool
    rules: list[GateRuleResult]


class GateEvaluateIn(BaseModel):
    run_id: int
    baseline_id: int | None = None
    allow_dataset_mismatch: bool = False
    record: bool = True


class GateEvaluationOut(BaseModel):
    id: int | None
    run_id: int
    baseline_id: int | None
    status: Literal["passed", "failed"]
    gate: GateResult
    report: RegressionReport
    created_at: datetime | None


class ApiTokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["editor", "viewer"] = "editor"


class ApiTokenOut(ORM):
    id: int
    project_id: int
    name: str
    prefix: str
    role: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreated(ApiTokenOut):
    token: str  # shown once


# ---------------------------------------------------------------- RAG connectors
class ResponseMapping(BaseModel):
    answer_path: str = Field(default="answer", min_length=1, max_length=300)
    contexts_path: str = Field(default="", max_length=300)
    context_text_path: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _paths(self) -> ResponseMapping:
        from app.rag.connector import validate_path

        for p in (self.answer_path, self.contexts_path, self.context_text_path):
            validate_path(p)
        return self


class RagConnectorBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    endpoint_url: str = Field(min_length=8, max_length=2000)
    method: Literal["POST", "GET"] = "POST"
    auth_header_name: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9\-_]*$")
    request_template: str = Field(default='{"question": "{{question}}"}', max_length=20000)
    response_mapping: ResponseMapping = Field(default_factory=ResponseMapping)
    timeout_s: float = Field(default=30.0, ge=1, le=300)

    @model_validator(mode="after")
    def _template(self):
        from app.rag.connector import validate_template

        validate_template(self.request_template, self.method)
        if not self.endpoint_url.startswith(("http://", "https://")):
            raise ValueError("Endpoint URL must start with http:// or https://")
        return self


class RagConnectorIn(RagConnectorBase):
    auth_value: str | None = Field(default=None, max_length=4000)


class RagConnectorPatch(RagConnectorIn):
    """Full replacement of the config; omit auth_value to keep the stored secret."""

    clear_auth: bool = False


class RagConnectorOut(ORM):
    id: int
    project_id: int
    name: str
    endpoint_url: str
    method: str
    auth_header_name: str
    auth_hint: str
    request_template: str
    response_mapping: dict
    timeout_s: float
    created_at: datetime


class ConnectorTestIn(RagConnectorBase):
    """Test a draft or saved config. With connector_id and no auth_value, the stored secret is used."""

    project_id: int
    connector_id: int | None = None
    auth_value: str | None = Field(default=None, max_length=4000)
    question: str = Field(min_length=1, max_length=5000)


class ConnectorTestOut(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    answer: str | None = None
    contexts: list[str] | None = None
    warnings: list[str] = []
    error: str | None = None
    raw_preview: str = ""


# ---------------------------------------------------------------- datasets
class DatasetVersionOut(ORM):
    id: int
    dataset_id: int
    version_number: int
    row_count: int
    source_filename: str
    fields_present: list[str]
    change_note: str
    created_at: datetime


class DatasetOut(ORM):
    id: int
    project_id: int
    name: str
    created_at: datetime
    current_version: DatasetVersionOut | None = None
    version_count: int = 0


class DatasetPatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UploadIssue(BaseModel):
    row: int
    column: str | None
    message: str


class UploadReport(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: list[UploadIssue]
    warnings: list[UploadIssue]
    truncated: bool
    fields_present: list[str]
    preview: list[dict]


class UploadOut(BaseModel):
    columns: list[str]
    suggested_mapping: dict[str, str | None]
    mapping_used: dict[str, str | None]
    metadata_columns: list[str]
    mapping_problems: list[str]
    report: UploadReport | None
    imported: bool
    dataset: DatasetOut | None = None
    version: DatasetVersionOut | None = None


class CaseIn(BaseModel):
    input: str = Field(min_length=1, max_length=20000)
    expected_output: str | None = Field(default=None, max_length=20000)
    recorded_output: str | None = Field(default=None, max_length=50000)
    provided_context: list[str] | None = Field(default=None, max_length=50)
    criteria: str | None = Field(default=None, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    metadata: dict = Field(default_factory=dict)

    @field_validator("expected_output", "recorded_output", "criteria")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None

    @field_validator("provided_context")
    @classmethod
    def _ctx(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [c.strip() for c in v if c and c.strip()]
        return cleaned or None

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = t.strip()
            if len(t) > 50:
                raise ValueError("Tags are at most 50 characters")
            if t and t not in out:
                out.append(t)
        return out


class CaseCreate(CaseIn):
    dataset_id: int
    base_version_id: int


class CasePatch(CaseIn):
    pass


class CaseFullOut(ORM):
    id: int
    case_key: str
    dataset_version_id: int | None
    position: int
    input: str
    expected_output: str | None
    recorded_output: str | None
    provided_context: list[str] | None
    criteria: str | None
    tags: list[str]
    metadata: dict = Field(validation_alias="case_metadata")
    created_at: datetime


class CasesPage(BaseModel):
    items: list[CaseFullOut]
    total: int
    page: int
    page_size: int
    tags: list[str]


class CaseEditOut(BaseModel):
    """Every edit creates a new dataset version (spec §10.2)."""

    version: DatasetVersionOut
    case: CaseFullOut | None


# ---------------------------------------------------------------- suites
class SuiteIn(BaseModel):
    project_id: int
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    dataset_version_id: int
    selection: Literal["all", "tags", "manual"] = "all"
    tags: list[str] = Field(default_factory=list, max_length=20)
    case_ids: list[int] = Field(default_factory=list, max_length=5000)

    @model_validator(mode="after")
    def _sel(self) -> SuiteIn:
        if self.selection == "tags" and not self.tags:
            raise ValueError("Choose at least one tag")
        if self.selection == "manual" and not self.case_ids:
            raise ValueError("Select at least one case")
        return self


class SuiteOut(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    dataset_id: int
    dataset_name: str
    dataset_version_id: int
    version_number: int
    is_latest_version: bool
    case_count: int
    selection: str
    tags: list[str] = []
    created_at: datetime


# ---------------------------------------------------------------- members
class MemberIn(BaseModel):
    email: EmailStr
    role: Role
    initial_password: str | None = Field(default=None, min_length=10, max_length=200)


class MemberPatch(BaseModel):
    role: Role


class MemberOut(BaseModel):
    user_id: int
    email: str
    role: Role
    created_user: bool = False
