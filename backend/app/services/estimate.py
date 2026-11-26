"""Pre-run cost estimate (spec §2 cost control). Heuristic, labelled as such in the API and UI.

Per (metric, engine): number of judge calls, fixed template tokens per call, which inputs are sent
per call, and output tokens per call. Calibrated loosely from the engines' prompt templates.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.evaluators.applicability import METRICS, check
from app.evaluators.base import EvalInputs


@dataclass(frozen=True)
class Profile:
    calls: int
    template_tokens: int  # per call
    inputs: tuple[str, ...]  # sent on every call
    output_tokens: int  # per call
    per_context_chunk: bool = False  # one call per context chunk (Ragas context precision)


PROFILES: dict[tuple[str, str], Profile] = {
    ("answer_relevancy", "ragas"): Profile(3, 450, ("response",), 60),
    ("answer_relevancy", "deepeval"): Profile(3, 700, ("question", "response"), 250),
    ("faithfulness", "ragas"): Profile(2, 900, ("question", "response", "context"), 350),
    ("faithfulness", "deepeval"): Profile(4, 900, ("response", "context"), 350),
    ("hallucination", "deepeval"): Profile(2, 700, ("response", "context"), 250),
    ("answer_correctness", "ragas"): Profile(4, 900, ("response", "reference"), 350),
    ("answer_correctness", "deepeval"): Profile(2, 600, ("response", "reference"), 200),
    ("bias", "deepeval"): Profile(3, 800, ("response",), 250),
    ("toxicity", "deepeval"): Profile(3, 800, ("response",), 250),
    ("context_precision", "ragas"): Profile(1, 700, ("question", "reference"), 120, per_context_chunk=True),
    ("context_precision", "deepeval"): Profile(2, 800, ("question", "reference", "context"), 300),
    ("context_recall", "ragas"): Profile(1, 900, ("question", "reference", "context"), 400),
    ("context_recall", "deepeval"): Profile(2, 800, ("reference", "context"), 300),
    ("custom_criteria", "deepeval"): Profile(2, 600, ("question", "response"), 200),
    # agent metrics (D31)
    ("tool_correctness", "testmyllm"): Profile(0, 0, (), 0),  # deterministic, no judge call
    ("tool_correctness", "deepeval"): Profile(1, 500, ("question", "trace"), 150),
    ("task_completion", "deepeval"): Profile(2, 900, ("question", "response", "trace"), 250),
    ("argument_correctness", "deepeval"): Profile(2, 800, ("question", "trace"), 250),
}


def tokens_of(text: str | None) -> int:
    return 0 if not text else max(1, len(text) // 4)


def _input_tokens(inputs: EvalInputs, name: str) -> int:
    if name == "trace":
        import json

        return tokens_of(json.dumps(inputs.tools_called or []))
    if name == "context":
        return sum(tokens_of(c) for c in inputs.clean_context())
    return tokens_of(getattr(inputs, name))


@dataclass
class JudgeEstimate:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    applicable: int = 0


def estimate_judge(
    metrics: list[tuple[str, str]], inputs_per_output: list[EvalInputs], repeats: int
) -> tuple[JudgeEstimate, list[str]]:
    """Returns the judge estimate and human-readable 'not applicable' notes."""
    est = JudgeEstimate()
    notes: list[str] = []
    for metric, engine in metrics:
        for inputs in inputs_per_output:
            ok, why = check(metric, engine, inputs)
            if not ok:
                note = f"{METRICS[metric].label} ({engine}): {why}"
                if note not in notes:
                    notes.append(note)
                continue
            p = PROFILES[(metric, engine)]
            calls = p.calls * (max(1, len(inputs.clean_context())) if p.per_context_chunk else 1)
            payload = sum(_input_tokens(inputs, n) for n in p.inputs)
            if p.per_context_chunk:
                payload += sum(tokens_of(c) for c in inputs.clean_context()) // max(1, calls)
            est.applicable += repeats
            est.calls += calls * repeats
            est.input_tokens += (p.template_tokens + payload) * calls * repeats
            est.output_tokens += p.output_tokens * calls * repeats
    return est, notes


def usd(in_tok: int, out_tok: int, price: tuple[float, float] | None) -> float | None:
    if price is None:
        return None
    return in_tok * price[0] + out_tok * price[1]
