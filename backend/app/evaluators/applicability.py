"""Metric registry: which product metrics exist, which engines implement them, which inputs they need.

Engine metric names verified against ragas 0.4.4 and deepeval 4.3.5 (see docs/decisions.md D4).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.evaluators.base import EvalInputs

INPUT_LABELS = {
    "question": "question",
    "response": "model response",
    "reference": "reference answer",
    "context": "context",
    "criteria": "evaluation criteria",
    "trace": "an agent trace (agent runs only)",
    "expectations": "scenario expectations",
    "expected_tools": "must_call expectations (tools the agent should use)",
}


@dataclass(frozen=True)
class EngineImpl:
    engine: str
    engine_metric_name: str
    requires: frozenset[str]


@dataclass(frozen=True)
class MetricDef:
    name: str
    label: str
    description: str
    higher_is_better: bool
    default_threshold: float
    impls: tuple[EngineImpl, ...]

    def impl(self, engine: str) -> EngineImpl | None:
        return next((i for i in self.impls if i.engine == engine), None)


def _req(*names: str) -> frozenset[str]:
    return frozenset(names)


_DEFS = [
    MetricDef(
        "answer_relevancy", "Answer relevancy", "Does the response address the question?", True, 0.7,
        (
            EngineImpl("ragas", "answer_relevancy", _req("question", "response")),
            EngineImpl("deepeval", "AnswerRelevancyMetric", _req("question", "response")),
        ),
    ),
    MetricDef(
        "faithfulness", "Faithfulness", "Are the response's claims supported by the context?", True, 0.7,
        (
            EngineImpl("ragas", "faithfulness", _req("question", "response", "context")),
            EngineImpl("deepeval", "FaithfulnessMetric", _req("question", "response", "context")),
        ),
    ),
    # deepeval>=4.x: hallucination/bias/toxicity score 1 = clean (pass) and the threshold is a MINIMUM.
    # (Older deepeval scored the violation proportion, lower-is-better; see decisions.md D4.)
    MetricDef(
        "hallucination", "Hallucination",
        "Freedom from contradicting the context (1 = no hallucination).", True, 0.7,
        (EngineImpl("deepeval", "HallucinationMetric", _req("question", "response", "context")),),
    ),
    MetricDef(
        "answer_correctness", "Answer correctness", "Does the response match the reference answer?", True, 0.6,
        (
            EngineImpl("ragas", "factual_correctness", _req("response", "reference")),
            EngineImpl("deepeval", "GEval[Correctness]", _req("question", "response", "reference")),
        ),
    ),
    MetricDef(
        "bias", "Bias", "Freedom from biased opinions (1 = unbiased).", True, 0.7,
        (EngineImpl("deepeval", "BiasMetric", _req("question", "response")),),
    ),
    MetricDef(
        "toxicity", "Toxicity", "Freedom from toxic language (1 = non-toxic).", True, 0.7,
        (EngineImpl("deepeval", "ToxicityMetric", _req("question", "response")),),
    ),
    MetricDef(
        "context_precision", "Context precision", "Are the relevant context chunks ranked first?", True, 0.7,
        (
            EngineImpl("ragas", "context_precision", _req("question", "reference", "context")),
            EngineImpl(
                "deepeval", "ContextualPrecisionMetric", _req("question", "response", "reference", "context")
            ),
        ),
    ),
    MetricDef(
        "context_recall", "Context recall", "Does the context contain what the reference answer needs?", True, 0.7,
        (
            EngineImpl("ragas", "context_recall", _req("question", "reference", "context")),
            EngineImpl(
                "deepeval", "ContextualRecallMetric", _req("question", "response", "reference", "context")
            ),
        ),
    ),
    MetricDef(
        "custom_criteria", "Custom criteria", "Scores the response against your rubric text.", True, 0.7,
        (EngineImpl("deepeval", "GEval[Custom]", _req("question", "response", "criteria")),),
    ),
]

# ---- agent metrics (D31). `testmyllm` = deterministic checks of the trace, no judge call.
_DEFS += [
    MetricDef(
        "tool_correctness", "Tool correctness",
        "Did the agent call the expected tools (with the right arguments, in order) and avoid forbidden ones?",
        True, 1.0,
        (
            EngineImpl("testmyllm", "scenario_assertions", _req("question", "trace", "expectations")),
            EngineImpl("deepeval", "ToolCorrectnessMetric", _req("question", "response", "trace", "expected_tools")),
        ),
    ),
    MetricDef(
        "task_completion", "Task completion", "Did the agent accomplish what the user asked, judged from its trace?",
        True, 0.7, (EngineImpl("deepeval", "TaskCompletionMetric", _req("question", "response", "trace")),),
    ),
    MetricDef(
        "argument_correctness", "Argument correctness", "Were the tool arguments right for the request?",
        True, 0.7, (EngineImpl("deepeval", "ArgumentCorrectnessMetric", _req("question", "response", "trace")),),
    ),
]
AGENT_METRICS = ("tool_correctness", "task_completion", "argument_correctness")

METRICS: dict[str, MetricDef] = {m.name: m for m in _DEFS}
ENGINES = ("ragas", "deepeval", "testmyllm")


def check(metric: str, engine: str, inputs: EvalInputs | set[str]) -> tuple[bool, str | None]:
    """(applicable, human-readable reason if not). Accepts EvalInputs or a set of present input names."""
    mdef = METRICS.get(metric)
    if mdef is None:
        return False, f"Unknown metric '{metric}'"
    impl = mdef.impl(engine)
    if impl is None:
        return False, f"{mdef.label} is not available in {engine}"
    present = inputs.present() if isinstance(inputs, EvalInputs) else inputs
    missing = sorted(impl.requires - present)
    if missing:
        return False, "Needs " + ", ".join(INPUT_LABELS[m] for m in missing)
    return True, None
