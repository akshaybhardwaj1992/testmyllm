"""Runs one metric on one engine and always returns a NormalizedMetricResult (never raises).

Errors become `status="error"` with a redacted message; they are never stored as zero.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict

from app.core.redact import redact
from app.evaluators.applicability import METRICS, check
from app.evaluators.base import EvalInputs, Evaluator, JudgeSettings, NormalizedMetricResult, status_for
from app.evaluators.deepeval_evaluator import DeepEvalEvaluator
from app.evaluators.agent_evaluator import TestMyLLMEvaluator
from app.evaluators.ragas_evaluator import RagasEvaluator

EVALUATORS: dict[str, type[Evaluator]] = {"ragas": RagasEvaluator, "deepeval": DeepEvalEvaluator, "testmyllm": TestMyLLMEvaluator}


_INNER_EXC = re.compile(r"<exception>\s*(.*?)\s*</exception>", re.S)


def friendly_error(e: BaseException) -> str:
    """Short, redacted, human-readable error. Unwraps Instructor's XML retry dumps (used by Ragas)."""
    inner = _INNER_EXC.findall(str(e))
    if inner:
        text = f"{type(e).__name__} after {len(inner)} attempt(s): {inner[-1]}"
    else:
        text = f"{type(e).__name__}: {e}"
    return redact(" ".join(text.split()))[:1000]


def engine_versions() -> dict[str, str]:
    out = {}
    for name, cls in EVALUATORS.items():
        try:
            out[name] = cls.version()
        except Exception:  # noqa: BLE001 - engine not installed
            out[name] = "unavailable"
    return out


def cache_key(
    engine: str, engine_version: str, metric: str, judge: JudgeSettings, inputs: EvalInputs, repeat_index: int
) -> str:
    """Spec §2 cache key plus repeat index (decision D5)."""
    payload = {
        "engine": engine,
        "engine_version": engine_version,
        "metric": metric,
        "judge_model": judge.model,
        "judge_config": judge.public_config(),
        "inputs": asdict(inputs),
        "repeat_index": repeat_index,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _base(metric: str, engine: str, version: str, threshold: float | None, repeat_index: int) -> dict:
    mdef = METRICS[metric]
    impl = mdef.impl(engine)
    return {
        "metric": metric,
        "engine": engine,
        "engine_version": version,
        "engine_metric_name": impl.engine_metric_name if impl else metric,
        "higher_is_better": mdef.higher_is_better,
        "threshold": threshold,
        "repeat_index": repeat_index,
    }


def not_applicable(
    metric: str, engine: str, version: str, threshold: float | None, repeat_index: int, reason: str
) -> NormalizedMetricResult:
    return NormalizedMetricResult(
        **_base(metric, engine, version, threshold, repeat_index), score=None, status="not_applicable", reason=reason
    )


async def run_metric(
    evaluator: Evaluator,
    metric: str,
    inputs: EvalInputs,
    threshold: float | None,
    repeat_index: int,
    timeout_s: float,
) -> NormalizedMetricResult:
    engine = evaluator.engine
    version = evaluator.version()
    ok, why = check(metric, engine, inputs)
    if not ok:
        return not_applicable(metric, engine, version, threshold, repeat_index, why or "Not applicable")
    base = _base(metric, engine, version, threshold, repeat_index)
    judge = evaluator.judge
    attribution = ({"judge_model": judge.model, "judge_config": judge.public_config()}
                   if getattr(evaluator, "uses_judge", True) else {})
    t0 = time.perf_counter()
    try:
        es = await asyncio.wait_for(evaluator.score(metric, inputs, threshold), timeout=timeout_s)
    except TimeoutError:
        return NormalizedMetricResult(
            **base, **attribution, score=None, status="error",
            error_message=f"Judge timed out after {timeout_s:.0f}s",
            execution_time_ms=int((time.perf_counter() - t0) * 1000),
        )
    except Exception as e:  # noqa: BLE001 - any engine/provider failure is an `error` result
        return NormalizedMetricResult(
            **base, **attribution, score=None, status="error",
            error_message=friendly_error(e),
            execution_time_ms=int((time.perf_counter() - t0) * 1000),
        )
    return NormalizedMetricResult(
        **base,
        **attribution,
        score=es.score,
        status=status_for(es.score, threshold, METRICS[metric].higher_is_better),
        reason=es.reason,
        tokens={"input": es.input_tokens, "output": es.output_tokens},
        judge_cost_usd=es.cost_usd,
        execution_time_ms=int((time.perf_counter() - t0) * 1000),
    )
