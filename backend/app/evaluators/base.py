"""Engine-neutral evaluation types. Nothing outside `evaluators/` may depend on an engine's shapes."""
from __future__ import annotations

import abc
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

MetricStatus = Literal["passed", "failed", "not_applicable", "error"]


@dataclass(frozen=True)
class EvalInputs:
    question: str
    response: str | None = None
    reference: str | None = None
    context: list[str] | None = None
    criteria: str | None = None
    # Agent runs (D31): the tools the agent called, in order: [{"name", "args", "output"}], and the
    # scenario's expectations (must_call, must_not_call, order, max_tool_calls, answer checks).
    tools_called: list[dict] | None = None
    expectations: dict | None = None

    def present(self) -> set[str]:
        out = {"question"} if self.question.strip() else set()
        if self.response and self.response.strip():
            out.add("response")
        if self.reference and self.reference.strip():
            out.add("reference")
        if self.context and any(c.strip() for c in self.context):
            out.add("context")
        if self.criteria and self.criteria.strip():
            out.add("criteria")
        if self.tools_called is not None:  # an agent ran (an empty list = it called no tool)
            out.add("trace")
        if self.expectations:
            out.add("expectations")
            if self.expectations.get("must_call"):
                out.add("expected_tools")
        return out

    def clean_context(self) -> list[str]:
        return [c for c in (self.context or []) if c.strip()]


@dataclass(frozen=True)
class JudgeSettings:
    """Everything an adapter needs to call the judge. `api_key` never leaves the worker."""

    model: str
    api_key: str
    base_url: str
    temperature: float = 0.0
    embedding_model: str = "openai/text-embedding-3-small"
    price_in_per_token: float | None = None
    price_out_per_token: float | None = None
    judge_config_id: int | None = None
    timeout_s: float = 120.0

    def public_config(self) -> dict[str, Any]:
        return {"temperature": self.temperature, "embedding_model": self.embedding_model}


@dataclass
class EngineScore:
    """What an adapter returns for one successful metric evaluation."""

    score: float
    reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass
class NormalizedMetricResult:
    metric: str
    engine: str
    engine_version: str
    engine_metric_name: str
    higher_is_better: bool
    score: float | None
    threshold: float | None
    status: MetricStatus
    reason: str | None = None
    error_message: str | None = None
    judge_model: str | None = None
    judge_config: dict[str, Any] = field(default_factory=dict)
    repeat_index: int = 0
    tokens: dict[str, int | None] = field(default_factory=lambda: {"input": None, "output": None})
    judge_cost_usd: float | None = None
    cache_hit: bool = False
    execution_time_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def status_for(score: float, threshold: float | None, higher_is_better: bool) -> MetricStatus:
    if threshold is None:
        return "passed"
    return "passed" if (score >= threshold if higher_is_better else score <= threshold) else "failed"


class Evaluator(abc.ABC):
    """One adapter per engine. Instances are created per unit of work (never shared across loops)."""

    engine: str

    def __init__(self, judge: JudgeSettings):
        self.judge = judge

    @staticmethod
    @abc.abstractmethod
    def version() -> str: ...

    @abc.abstractmethod
    async def score(self, metric: str, inputs: EvalInputs, threshold: float | None) -> EngineScore:
        """Run one metric. Raise on any failure; the runner turns exceptions into `error`."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        """Release HTTP clients etc. Called once per unit of work."""
