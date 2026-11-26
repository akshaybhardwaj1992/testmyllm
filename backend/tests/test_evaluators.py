"""Applicability, runner normalization and adapter contract tests (mocked judge, no network).

The adapter contract tests construct the REAL pinned Ragas/DeepEval metric classes with the exact
arguments the adapters use and mock only the judge call. Run them on every engine upgrade.
"""
import dataclasses
import asyncio
import inspect
import math

import httpx
import pytest

from app.evaluators import applicability, runner
from app.evaluators.base import EngineScore, EvalInputs, Evaluator, JudgeSettings
from app.evaluators.deepeval_evaluator import DeepEvalEvaluator
from app.evaluators.ragas_evaluator import RagasEvaluator
from app.evaluators.usage import Usage, current_usage, tracking_http_client
from app.services.estimate import PROFILES

JUDGE = JudgeSettings(model="openai/gpt-4o-mini", api_key="sk-or-v1-test", base_url="https://openrouter.ai/api/v1")
FULL = EvalInputs(
    question="What is the capital of France?", response="Paris is the capital.", reference="Paris",
    context=["Paris is the capital of France."], criteria="Answer must be one sentence.",
)


# ------------------------------------------------------------------ applicability
def test_every_engine_impl_has_a_cost_profile():
    for m in applicability.METRICS.values():
        for impl in m.impls:
            assert (m.name, impl.engine) in PROFILES


def test_missing_inputs_give_readable_reason():
    ok, why = applicability.check("faithfulness", "ragas", EvalInputs(question="q", response="a"))
    assert not ok and why == "Needs context"
    ok, why = applicability.check("context_recall", "deepeval", EvalInputs(question="q"))
    assert not ok and why == "Needs context, reference answer, model response"


def test_engine_without_metric():
    ok, why = applicability.check("hallucination", "ragas", FULL)
    assert not ok and "not available in ragas" in why


def test_whitespace_counts_as_missing():
    ok, _ = applicability.check("answer_correctness", "ragas", EvalInputs(question="q", response="a", reference="  "))
    assert not ok


# ------------------------------------------------------------------ runner normalization
class _Stub(Evaluator):
    engine = "deepeval"

    def __init__(self, judge, result=None, exc=None, delay=0.0):
        super().__init__(judge)
        self.result, self.exc, self.delay = result, exc, delay

    @staticmethod
    def version():
        return "9.9"

    async def score(self, metric, inputs, threshold):
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


def _run(ev, metric="answer_relevancy", threshold=0.7, timeout=5):
    return asyncio.run(runner.run_metric(ev, metric, FULL, threshold, 0, timeout))


def test_error_is_error_not_zero_and_secret_is_redacted():
    r = _run(_Stub(JUDGE, exc=RuntimeError("401 bad key sk-or-v1-abcdefghijklmnopqrst")))
    assert r.status == "error" and r.score is None
    assert "abcdefghijklmnop" not in r.error_message and "[REDACTED]" in r.error_message
    assert r.judge_model == JUDGE.model


def test_instructor_retry_dump_is_summarized():
    class InstructorRetryException(Exception):
        pass

    dump = (
        "<failed_attempts>\n<generation number=\"1\">\n<exception>\n    Error code: 401 - "
        "{'error': {'message': 'User not found.'}}\n</exception>\n</generation>\n</failed_attempts>"
    )
    msg = runner.friendly_error(InstructorRetryException(dump))
    assert msg == "InstructorRetryException after 1 attempt(s): Error code: 401 - {'error': {'message': 'User not found.'}}"


def test_timeout_is_error():
    r = _run(_Stub(JUDGE, result=EngineScore(0.9), delay=1), timeout=0.05)
    assert r.status == "error" and "timed out" in r.error_message


def test_direction_aware_status():
    from app.evaluators.base import status_for

    assert status_for(0.2, 0.5, higher_is_better=False) == "passed"
    assert status_for(0.8, 0.5, higher_is_better=False) == "failed"
    assert status_for(0.5, 0.5, higher_is_better=True) == "passed"


def test_deepeval_safety_metrics_are_higher_is_better_in_pinned_version():
    """deepeval 4.x flipped hallucination/bias/toxicity to 1 = pass. Guard against silently regressing."""
    for name in ("hallucination", "bias", "toxicity"):
        assert applicability.METRICS[name].higher_is_better is True
    assert _run(_Stub(JUDGE, result=EngineScore(0.9)), "hallucination", 0.7).status == "passed"
    assert _run(_Stub(JUDGE, result=EngineScore(0.2)), "hallucination", 0.7).status == "failed"


def test_normalized_shape_matches_spec():
    r = _run(_Stub(JUDGE, result=EngineScore(0.94, "Supported", 1200, 150, 0.0012)), "faithfulness", 0.9)
    d = r.to_dict()
    for key in ("metric", "engine", "engine_version", "score", "threshold", "status", "reason", "judge_model",
                "judge_config", "repeat_index", "tokens", "judge_cost_usd", "cache_hit", "execution_time_ms"):
        assert key in d
    assert d["status"] == "passed" and d["engine_metric_name"] == "FaithfulnessMetric"
    assert d["tokens"] == {"input": 1200, "output": 150} and d["judge_config"] == {
        "temperature": 0.0, "embedding_model": "openai/text-embedding-3-small"}


def test_not_applicable_never_calls_judge():
    ev = _Stub(JUDGE, exc=AssertionError("must not be called"))
    r = asyncio.run(runner.run_metric(ev, "faithfulness", EvalInputs(question="q", response="a"), 0.7, 0, 5))
    assert r.status == "not_applicable" and r.score is None and r.reason == "Needs context"


def test_cache_key_varies_by_repeat_and_judge():
    k0 = runner.cache_key("ragas", "1", "faithfulness", JUDGE, FULL, 0)
    assert k0 != runner.cache_key("ragas", "1", "faithfulness", JUDGE, FULL, 1)
    other = JudgeSettings(model="x/y", api_key="k", base_url="u")
    assert k0 != runner.cache_key("ragas", "1", "faithfulness", other, FULL, 0)
    assert k0 == runner.cache_key("ragas", "1", "faithfulness", JUDGE, FULL, 0)


# ------------------------------------------------------------------ usage capture (Ragas has no usage API)
def test_usage_hook_records_tokens_and_reported_cost():
    def handler(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 50, "completion_tokens": 7, "cost": 0.002}})

    async def go():
        client = tracking_http_client(5)
        client._transport = httpx.MockTransport(handler)
        u = Usage()
        tok = current_usage.set(u)
        try:
            await client.post("https://x/api/v1/chat/completions", json={})
            await client.post("https://x/api/v1/chat/completions", json={})
        finally:
            current_usage.reset(tok)
            await client.aclose()
        return u

    u = asyncio.run(go())
    assert (u.input_tokens, u.output_tokens, u.calls) == (100, 14, 2)
    assert math.isclose(u.cost(None, None), 0.004)


def test_usage_falls_back_to_price_when_cost_not_reported():
    u = Usage()
    u.add({"prompt_tokens": 1000, "completion_tokens": 100})
    assert u.cost(None, None) is None
    assert math.isclose(u.cost(1e-6, 2e-6), 0.0012)


# ------------------------------------------------------------------ adapter contracts (real engine classes)
RAGAS_METRICS = [m for m, d in applicability.METRICS.items() if d.impl("ragas")]
DEEPEVAL_METRICS = [m for m, d in applicability.METRICS.items() if d.impl("deepeval")]
# agent metrics (D31) need a trace and expectations; the other metrics ignore them
AGENT_FULL = dataclasses.replace(
    FULL, tools_called=[{"name": "lookup_city", "args": {"country": "France"}, "output": "Paris", "side_effect": False}],
    expectations={"must_call": [{"tool": "lookup_city"}], "order": ["lookup_city"]},
)


@pytest.mark.parametrize("metric", RAGAS_METRICS)
def test_ragas_adapter_contract(metric, monkeypatch):
    from ragas.metrics.result import MetricResult

    ev = RagasEvaluator(JUDGE)
    obj, kwargs = ev._build(metric, FULL)  # real ragas 0.4.4 constructors
    # The kwargs we pass must match the pinned ascore() signature exactly.
    real_params = set(inspect.signature(type(obj).ascore).parameters) - {"self"}
    assert set(kwargs) == real_params
    seen = {}

    async def fake_ascore(self, **kw):
        seen.update(kw)
        return MetricResult(value=0.8, reason="mock reason")

    monkeypatch.setattr(type(obj), "ascore", fake_ascore)
    out = asyncio.run(ev.score(metric, FULL, 0.7))
    asyncio.run(ev.aclose())
    assert out.score == 0.8 and out.reason == "mock reason"
    assert seen == kwargs


def test_ragas_zero_relevancy_gets_an_explanation(monkeypatch):
    from ragas.metrics.result import MetricResult

    ev = RagasEvaluator(JUDGE)
    obj, _ = ev._build("answer_relevancy", FULL)

    async def zero(self, **kw):
        return MetricResult(value=0.0)

    monkeypatch.setattr(type(obj), "ascore", zero)
    out = asyncio.run(ev.score("answer_relevancy", FULL, 0.7))
    assert out.score == 0.0 and "noncommittal" in out.reason


def test_ragas_nan_is_an_error(monkeypatch):
    from ragas.metrics.result import MetricResult

    ev = RagasEvaluator(JUDGE)
    obj, _ = ev._build("faithfulness", FULL)

    async def nan(self, **kw):
        return MetricResult(value=float("nan"))

    monkeypatch.setattr(type(obj), "ascore", nan)
    r = asyncio.run(runner.run_metric(ev, "faithfulness", FULL, 0.7, 0, 5))
    assert r.status == "error" and r.score is None


@pytest.mark.parametrize("metric", DEEPEVAL_METRICS)
def test_deepeval_adapter_contract(metric, monkeypatch):
    ev = DeepEvalEvaluator(JUDGE)
    obj, case = ev._build(metric, AGENT_FULL, 0.7)  # real deepeval 4.3.5 constructors
    assert case.input == FULL.question and case.actual_output == FULL.response
    assert [t.name for t in case.tools_called] == ["lookup_city"] and case.tools_called[0].input_parameters == {"country": "France"}
    assert [t.name for t in case.expected_tools] == ["lookup_city"]
    if metric == "task_completion":
        assert obj.task == FULL.criteria  # the scenario's criteria is the intended task

    async def fake_measure(self, test_case, *a, **kw):
        self.score, self.reason, self.evaluation_cost = 0.25, "mock", 0.003
        self.input_tokens, self.output_tokens = 400, 40
        return self.score

    monkeypatch.setattr(type(obj), "a_measure", fake_measure)
    out = asyncio.run(ev.score(metric, AGENT_FULL, 0.7))
    assert (out.score, out.reason, out.cost_usd, out.input_tokens) == (0.25, "mock", 0.003, 400)


def test_deepeval_missing_score_is_error(monkeypatch):
    ev = DeepEvalEvaluator(JUDGE)
    obj, _ = ev._build("bias", FULL, 0.5)

    async def no_score(self, test_case, *a, **kw):
        self.score = None

    monkeypatch.setattr(type(obj), "a_measure", no_score)
    r = asyncio.run(runner.run_metric(ev, "bias", FULL, 0.5, 0, 5))
    assert r.status == "error"
