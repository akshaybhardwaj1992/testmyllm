"""SQLAlchemy models. One implicit organization in the MVP (see spec §7)."""
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

Json = dict[str, Any]
USD = Numeric(14, 6)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    settings: Mapped[Json] = mapped_column(JSONB, default=dict)
    members: Mapped[list["ProjectMember"]] = relationship(cascade="all, delete-orphan")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))  # owner | editor | viewer


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(30))  # openrouter (MVP); openai/anthropic/google later
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(20), default="")  # masked display value only
    status: Mapped[str] = mapped_column(String(20), default="untested")  # untested | ok | error
    status_message: Mapped[str] = mapped_column(Text, default="")


class JudgeConfig(TimestampMixin, Base):
    __tablename__ = "judge_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    model: Mapped[str] = mapped_column(String(200))
    temperature: Mapped[float] = mapped_column(Float, default=0.0)
    embedding_model: Mapped[str] = mapped_column(String(200), default="openai/text-embedding-3-small")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class ModelConfig(TimestampMixin, Base):
    """A saved model-under-test configuration (spec §4.3: save model configs, repeat benchmarks)."""

    __tablename__ = "model_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), default="")
    model: Mapped[str] = mapped_column(String(200))
    params: Mapped[Json] = mapped_column(JSONB, default=dict)  # {"max_tokens": int, "temperature": float|None}


class Prompt(TimestampMixin, Base):
    __tablename__ = "prompts"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    template: Mapped[str] = mapped_column(Text)


class Dataset(TimestampMixin, Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "dataset_versions.id", use_alter=True, ondelete="SET NULL", name="fk_datasets_current_version_id"
        ),
        nullable=True,
    )


class DatasetVersion(TimestampMixin, Base):
    """Immutable: edits create a new version."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    source_filename: Mapped[str] = mapped_column(String(300), default="")
    # which optional fields any case in this version has (drives metric applicability in the UI)
    fields_present: Mapped[list[str]] = mapped_column(JSONB, default=list)
    change_note: Mapped[str] = mapped_column(String(300), default="")
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_dataset_versions_created_by"), nullable=True
    )


class TestCase(TimestampMixin, Base):
    __tablename__ = "test_cases"
    __test__ = False  # not a pytest class
    id: Mapped[int] = mapped_column(primary_key=True)
    # Null = ad-hoc Test Studio case (decision D2).
    dataset_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # Stable identity of "the same case" across dataset versions (copied when a version is derived).
    case_key: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid.uuid4()), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pre-collected model response for offline evaluation (decision D15).
    recorded_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    provided_context: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_metadata: Mapped[Json] = mapped_column("metadata", JSONB, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)


class TestSuite(TimestampMixin, Base):
    __tablename__ = "test_suites"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"), nullable=True)
    case_ids: Mapped[list[int] | None] = mapped_column(JSONB, nullable=True)  # null = every case in the version
    selection: Mapped[str] = mapped_column(String(10), default="all")  # all | tags | manual
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)


class TestRun(TimestampMixin, Base):
    __tablename__ = "test_runs"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    suite_id: Mapped[int | None] = mapped_column(ForeignKey("test_suites.id"), nullable=True)
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"), nullable=True)
    run_name: Mapped[str] = mapped_column(String(300))
    run_type: Mapped[str] = mapped_column(String(20))  # llm | rag | benchmark | security | agent
    model_config_id: Mapped[int | None] = mapped_column(ForeignKey("model_configs.id"), nullable=True)
    prompt_id: Mapped[int | None] = mapped_column(ForeignKey("prompts.id"), nullable=True)
    judge_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("judge_configs.id", ondelete="SET NULL"), nullable=True
    )
    repeats: Mapped[int] = mapped_column(Integer, default=1)
    use_cache: Mapped[bool] = mapped_column(Boolean, default=True)
    budget_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)
    actual_cost_usd: Mapped[Decimal] = mapped_column(USD, default=Decimal(0))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    status_reason: Mapped[str] = mapped_column(String(200), default="")
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    error_tests: Mapped[int] = mapped_column(Integer, default=0)
    config_snapshot: Mapped[Json] = mapped_column(JSONB, default=dict)
    engine_versions: Mapped[Json] = mapped_column(JSONB, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # No-persistence mode (D30): the run and its results are deleted at this time. NULL = kept.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class TestResult(Base):
    __tablename__ = "test_results"
    __test__ = False
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    test_case_id: Mapped[int] = mapped_column(ForeignKey("test_cases.id", ondelete="CASCADE"))
    output_label: Mapped[str] = mapped_column(String(100), default="")  # decision D3
    source_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    output_source: Mapped[str] = mapped_column(String(20))  # manual | provider | rag_api | attack | agent
    model_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieved_context: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)
    # pending | running | passed | failed | error | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_origin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Agent runs (D31): {"steps": [...], "stopped": "answered|max_steps", "tool_calls": n}
    trace: Mapped[Json | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    test_case: Mapped[TestCase] = relationship()
    metrics: Mapped[list["MetricResult"]] = relationship(
        back_populates="test_result", cascade="all, delete-orphan", order_by="MetricResult.id"
    )


class MetricResult(TimestampMixin, Base):
    __tablename__ = "metric_results"
    # Idempotency key for a unit of work (spec §5.4): retries never duplicate rows.
    __table_args__ = (UniqueConstraint("test_result_id", "metric_name", "engine", "repeat_index"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    test_result_id: Mapped[int] = mapped_column(ForeignKey("test_results.id", ondelete="CASCADE"), index=True)
    repeat_index: Mapped[int] = mapped_column(Integer, default=0)
    engine: Mapped[str] = mapped_column(String(30))
    engine_version: Mapped[str] = mapped_column(String(30))
    metric_name: Mapped[str] = mapped_column(String(60))
    engine_metric_name: Mapped[str] = mapped_column(String(100))
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20))  # passed | failed | not_applicable | error
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    judge_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("judge_configs.id", ondelete="SET NULL"), nullable=True
    )
    judge_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    judge_config: Mapped[Json] = mapped_column(JSONB, default=dict)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_cost_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    test_result: Mapped[TestResult] = relationship(back_populates="metrics")


class HumanReview(TimestampMixin, Base):
    __tablename__ = "human_reviews"
    # one review per reviewer
    __table_args__ = (UniqueConstraint("metric_result_id", "reviewer_id", name="uq_human_reviews_metric_reviewer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    metric_result_id: Mapped[int] = mapped_column(
        ForeignKey("metric_results.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(String(20))  # agree | disagree
    override_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")


class RagConnector(TimestampMixin, Base):
    """A user's RAG HTTP endpoint (spec §4.2). Auth is encrypted at rest and never returned."""

    __tablename__ = "rag_connectors"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    endpoint_url: Mapped[str] = mapped_column(String(2000))
    method: Mapped[str] = mapped_column(String(10), default="POST")
    auth_header_name: Mapped[str] = mapped_column(String(100), default="")
    encrypted_auth: Mapped[str] = mapped_column(Text, default="")  # header value, Fernet-encrypted
    auth_hint: Mapped[str] = mapped_column(String(20), default="")
    request_template: Mapped[str] = mapped_column(Text, default='{"question": "{{question}}"}')
    # {"answer_path": "answer", "contexts_path": "sources[*]", "context_text_path": "text"}
    response_mapping: Mapped[Json] = mapped_column(JSONB, default=dict)
    timeout_s: Mapped[float] = mapped_column(Float, default=30.0)


class Baseline(TimestampMixin, Base):
    """A completed run saved as the reference for regression (spec §4.4). Pins its dataset version."""

    __tablename__ = "baselines"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class QualityGate(TimestampMixin, Base):
    """Per-metric rules: [{metric, engine, min_score?, max_regression?, min_pass_rate?}]."""

    __tablename__ = "quality_gates"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    rules: Mapped[list[Json]] = mapped_column(JSONB, default=list)


class GateEvaluation(TimestampMixin, Base):
    __tablename__ = "gate_evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    baseline_id: Mapped[int | None] = mapped_column(ForeignKey("baselines.id", ondelete="SET NULL"), nullable=True)
    gate_id: Mapped[int | None] = mapped_column(ForeignKey("quality_gates.id", ondelete="SET NULL"), nullable=True)
    security_gate_id: Mapped[int | None] = mapped_column(
        ForeignKey("security_gates.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(10))  # passed | failed
    details: Mapped[Json] = mapped_column(JSONB, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ApiToken(TimestampMixin, Base):
    """Project-scoped token for the CLI / CI / MCP. Only a SHA-256 hash is stored; shown once."""

    __tablename__ = "api_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(20), default="editor")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------------ AI security testing (decision D29)
class SecurityTarget(TimestampMixin, Base):
    """What a security scan attacks: a model + system prompt, or a RAG connector. Must be authorized."""

    __tablename__ = "target_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(10))  # model | rag | agent
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    params: Mapped[Json] = mapped_column(JSONB, default=dict)  # {"max_tokens", "temperature"}
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    connector_id: Mapped[int | None] = mapped_column(
        ForeignKey("rag_connectors.id", ondelete="CASCADE"), nullable=True
    )
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=True)
    # [{"name": "alice", "value": "user-123"}] -> {{user}} in the connector template (cross-user tests)
    identities: Mapped[list[Json]] = mapped_column(JSONB, default=list)
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, default=30)
    authorized_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    authorization_note: Mapped[str] = mapped_column(Text, default="")


class Canary(TimestampMixin, Base):
    """A unique secret planted in a target. Seeing it in an answer proves a leak."""

    __tablename__ = "canaries"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("target_configs.id", ondelete="CASCADE"), index=True)
    value: Mapped[str] = mapped_column(String(100))
    # system_prompt | document | identity:<name>
    location: Mapped[str] = mapped_column(String(120))


class AttackSet(TimestampMixin, Base):
    """Versioned attack collection. Built-in sets have project_id NULL and are never edited in place."""

    __tablename__ = "attack_sets"
    __table_args__ = (UniqueConstraint("project_id", "key", "version", name="uq_attack_sets_key_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(20), default="1")
    description: Mapped[str] = mapped_column(Text, default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ready", server_default="ready")  # ready | generating | failed
    # DeepTeam generation: {"engine", "source_set_id", "enhancers", "judge_model", "cost_usd", "error", ...}
    generation: Mapped[Json] = mapped_column(JSONB, default=dict, server_default="{}")
    attacks: Mapped[list["Attack"]] = relationship(cascade="all, delete-orphan", order_by="Attack.id")


class Attack(Base):
    __tablename__ = "attacks"
    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("attack_sets.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    technique: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # critical | high | medium | low
    owasp: Mapped[str] = mapped_column(String(10))  # e.g. LLM01 (OWASP Top 10 for LLM Apps, 2025)
    turns: Mapped[list[str]] = mapped_column(JSONB, default=list)  # user messages, in order
    document: Mapped[str | None] = mapped_column(Text, nullable=True)  # poisoned context (indirect)
    detector: Mapped[str] = mapped_column(String(20))  # marker | canary | pattern | cross_user
    marker: Mapped[str | None] = mapped_column(String(60), nullable=True)  # code word the attack tries to elicit
    remediation: Mapped[str] = mapped_column(Text, default="")


class SecurityFinding(TimestampMixin, Base):
    __tablename__ = "security_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    test_result_id: Mapped[int] = mapped_column(ForeignKey("test_results.id", ondelete="CASCADE"), unique=True)
    attack_id: Mapped[int | None] = mapped_column(ForeignKey("attacks.id", ondelete="SET NULL"), nullable=True)
    attack_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), index=True)
    technique: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), index=True)
    owasp: Mapped[str] = mapped_column(String(10))
    detector: Mapped[str] = mapped_column(String(40))
    evidence: Mapped[str] = mapped_column(Text)
    successes: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    remediation: Mapped[str] = mapped_column(Text, default="")


class SecurityGate(TimestampMixin, Base):
    """rules: [{"category": "prompt_injection" | "*", "max_asr": 0.1}]; max_critical: usually 0."""

    __tablename__ = "security_gates"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    rules: Mapped[list[Json]] = mapped_column(JSONB, default=list)
    max_critical: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    max_high: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ------------------------------------------------------------------ agent testing (decision D31)
class AgentConfig(TimestampMixin, Base):
    """A sandboxed agent: model + system prompt + MOCK tools. Tools never execute real actions."""

    __tablename__ = "agent_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(200))
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    # [{"name", "description", "parameters", "side_effect", "response", "rules"}]
    tools: Mapped[list[Json]] = mapped_column(JSONB, default=list)
    max_steps: Mapped[int] = mapped_column(Integer, default=6)
    params: Mapped[Json] = mapped_column(JSONB, default=dict)  # {"max_tokens", "temperature"}


class AgentScenarioSet(TimestampMixin, Base):
    """Versioned scenarios: [{"key", "name", "turns", "reference"?, "criteria"?, "expectations"}]."""

    __tablename__ = "agent_scenario_sets"
    __table_args__ = (UniqueConstraint("project_id", "key", "version", name="uq_agent_scenario_sets_key_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(Text, default="")
    scenarios: Mapped[list[Json]] = mapped_column(JSONB, default=list)


class Export(TimestampMixin, Base):
    __tablename__ = "exports"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class JudgeCache(TimestampMixin, Base):
    """Judge result cache (spec §2 cost control; key includes repeat index, decision D5)."""

    __tablename__ = "judge_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    judge_cost_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)
