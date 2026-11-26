"""`testmyllm` engine: deterministic agent checks (no judge call, no cost). Decision D31."""
from __future__ import annotations

from app.evaluators.base import EngineScore, EvalInputs, Evaluator


class TestMyLLMEvaluator(Evaluator):
    __test__ = False  # not a pytest class
    engine = "testmyllm"
    uses_judge = False

    @staticmethod
    def version() -> str:
        return "1"

    async def score(self, metric: str, inputs: EvalInputs, threshold: float | None) -> EngineScore:
        if metric != "tool_correctness":
            raise ValueError(f"The testmyllm engine does not implement '{metric}'")
        from app.agents.assertions import evaluate, score

        value, reason = score(evaluate(inputs.expectations or {}, inputs.tools_called or [], inputs.response))
        return EngineScore(score=value, reason=reason, cost_usd=0.0)
