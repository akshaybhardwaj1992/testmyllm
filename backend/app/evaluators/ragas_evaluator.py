"""Ragas adapter (pinned ragas==0.4.4, `ragas.metrics.collections` API).

Reuses the prototype's approach: one AsyncOpenAI client pointed at OpenRouter drives both the
Ragas LLM (`llm_factory`) and Ragas embeddings. Token usage is captured from HTTP responses
because Ragas does not report it (see usage.py).
"""
from __future__ import annotations

from importlib.metadata import version as pkg_version

from app.evaluators.base import EngineScore, EvalInputs, Evaluator, JudgeSettings
from app.evaluators.usage import Usage, current_usage, tracking_http_client


class RagasEvaluator(Evaluator):
    engine = "ragas"

    def __init__(self, judge: JudgeSettings):
        super().__init__(judge)
        self._http = None
        self._llm = None
        self._emb = None

    @staticmethod
    def version() -> str:
        return pkg_version("ragas")

    def _ensure(self):
        if self._llm is None:
            from openai import AsyncOpenAI
            from ragas.embeddings import OpenAIEmbeddings
            from ragas.llms import llm_factory

            self._http = tracking_http_client(self.judge.timeout_s)
            client = AsyncOpenAI(
                api_key=self.judge.api_key, base_url=self.judge.base_url, http_client=self._http, max_retries=4
            )
            self._llm = llm_factory(
                self.judge.model, provider="openai", client=client, temperature=self.judge.temperature
            )
            self._emb = OpenAIEmbeddings(client=client, model=self.judge.embedding_model)
        return self._llm, self._emb

    def _build(self, metric: str, inputs: EvalInputs):
        """Return (metric object, ascore kwargs). Field names per ragas 0.4.4 signatures."""
        from ragas.metrics.collections import (
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            FactualCorrectness,
            Faithfulness,
        )

        llm, emb = self._ensure()
        ctx = inputs.clean_context()
        if metric == "answer_relevancy":
            return AnswerRelevancy(llm=llm, embeddings=emb), {
                "user_input": inputs.question, "response": inputs.response}
        if metric == "faithfulness":
            return Faithfulness(llm=llm), {
                "user_input": inputs.question, "response": inputs.response, "retrieved_contexts": ctx}
        if metric == "answer_correctness":
            return FactualCorrectness(llm=llm), {"response": inputs.response, "reference": inputs.reference}
        if metric == "context_precision":
            return ContextPrecision(llm=llm), {
                "user_input": inputs.question, "reference": inputs.reference, "retrieved_contexts": ctx}
        if metric == "context_recall":
            return ContextRecall(llm=llm), {
                "user_input": inputs.question, "retrieved_contexts": ctx, "reference": inputs.reference}
        raise ValueError(f"Ragas adapter does not implement '{metric}'")

    async def score(self, metric: str, inputs: EvalInputs, threshold: float | None) -> EngineScore:
        m, kwargs = self._build(metric, inputs)
        usage = Usage()
        token = current_usage.set(usage)
        try:
            result = await m.ascore(**kwargs)
        finally:
            current_usage.reset(token)
        value = float(result.value)
        if value != value:  # NaN: Ragas' way of saying it could not compute
            raise ValueError("Ragas returned NaN (judge output could not be parsed or inputs were empty)")
        reason = getattr(result, "reason", None)
        if metric == "answer_relevancy" and value == 0.0 and not reason:
            # ragas 0.4.4 AnswerRelevancy returns exactly 0 when the judge marks every generated question
            # "noncommittal" (evasive/hedged answer) or generates none; it gives no reason (decision D19).
            reason = ("Ragas scores 0 when the judge judges the answer noncommittal (evasive or hedging) "
                      "or cannot derive a question from it. Compare with DeepEval and review the answer.")
        return EngineScore(
            score=value,
            reason=reason,
            input_tokens=usage.input_tokens or None,
            output_tokens=usage.output_tokens or None,
            cost_usd=usage.cost(self.judge.price_in_per_token, self.judge.price_out_per_token),
        )

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
