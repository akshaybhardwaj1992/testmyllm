"""Executes one unit of work: one test result (one case × one output) across all metrics and repeats.

Guarantees (spec §5):
- Idempotent: metric rows are keyed by (test_result, metric, engine, repeat); existing rows are skipped
  and inserts use ON CONFLICT DO NOTHING, so retries and re-enqueues never duplicate results or cost.
- Each result is saved as soon as it completes.
- Cancellation and the budget cap are checked before each repeat batch.
- A fresh event loop per unit (asyncio.run); engine clients are created per unit and closed after.
"""
from __future__ import annotations

import asyncio
import gc
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_factory
from app.core.redact import redact
from app.core.security import decrypt_secret
from app.evaluators import runner
from app.evaluators.applicability import check
from app.evaluators.base import EvalInputs, JudgeSettings, NormalizedMetricResult, status_for
from app.models import JudgeCache, MetricResult, Provider, RagConnector, TestResult, TestRun
from app.providers import openrouter
from app.services import runs as run_svc
from app.workers.limiter import provider_slot

log = logging.getLogger(__name__)


def process_unit(result_id: int) -> None:
    asyncio.run(_process(result_id))


def _over_budget(run: TestRun) -> bool:
    return run.budget_usd is not None and Decimal(run.actual_cost_usd or 0) >= Decimal(run.budget_usd)


def _add_cost(db: Session, run_id: int, usd: float | None) -> None:
    if usd:
        db.execute(
            update(TestRun)
            .where(TestRun.id == run_id)
            .values(actual_cost_usd=TestRun.actual_cost_usd + Decimal(str(round(usd, 8))))
        )


def _judge_settings(db: Session, run: TestRun) -> JudgeSettings:
    j = run.config_snapshot["judge"]  # the snapshot, not the live config, is authoritative
    provider = db.get(Provider, j["provider_id"])
    if provider is None:
        raise RuntimeError("The judge's provider was deleted")
    price = openrouter.model_price(j["model"])
    s = get_settings()
    return JudgeSettings(
        model=j["model"],
        api_key=decrypt_secret(provider.encrypted_credentials),
        base_url=s.openrouter_base_url,
        temperature=j["temperature"],
        embedding_model=j["embedding_model"],
        price_in_per_token=price[0] if price else None,
        price_out_per_token=price[1] if price else None,
        judge_config_id=j["id"],
        timeout_s=s.provider_timeout_s,
    )


def _save(db: Session, result: TestResult, run: TestRun, n: NormalizedMetricResult) -> bool:
    """Insert one metric row; returns False if it already existed (idempotent retry)."""
    stmt = (
        insert(MetricResult)
        .values(
            test_result_id=result.id,
            repeat_index=n.repeat_index,
            engine=n.engine,
            engine_version=n.engine_version,
            metric_name=n.metric,
            engine_metric_name=n.engine_metric_name,
            higher_is_better=n.higher_is_better,
            score=n.score,
            threshold=n.threshold,
            status=n.status,
            reason=n.reason,
            error_message=n.error_message,
            # live FK (NULL if the judge config was deleted); the snapshot keeps the full judge record
            judge_config_id=run.judge_config_id if n.judge_model else None,
            judge_model=n.judge_model,
            judge_config=n.judge_config,
            input_tokens=n.tokens.get("input"),
            output_tokens=n.tokens.get("output"),
            judge_cost_usd=Decimal(str(round(n.judge_cost_usd, 8))) if n.judge_cost_usd is not None else None,
            cache_hit=n.cache_hit,
            execution_time_ms=n.execution_time_ms,
        )
        .on_conflict_do_nothing(index_elements=["test_result_id", "metric_name", "engine", "repeat_index"])
        .returning(MetricResult.id)
    )
    inserted = db.execute(stmt).scalar() is not None
    if inserted and not n.cache_hit:
        _add_cost(db, run.id, n.judge_cost_usd)
    db.commit()
    return inserted


def _cached(db: Session, key: str, base: NormalizedMetricResult) -> NormalizedMetricResult | None:
    hit = db.get(JudgeCache, key)
    if hit is None or hit.score is None:
        return None
    base.score = hit.score
    base.reason = hit.reason
    base.status = status_for(hit.score, base.threshold, base.higher_is_better)
    base.cache_hit = True
    base.tokens = {"input": hit.input_tokens, "output": hit.output_tokens}
    base.judge_cost_usd = float(hit.judge_cost_usd) if hit.judge_cost_usd is not None else None
    base.execution_time_ms = 0
    return base


def _store_cache(db: Session, key: str, n: NormalizedMetricResult) -> None:
    if n.status not in ("passed", "failed") or n.score is None:
        return  # never cache errors
    db.execute(
        insert(JudgeCache)
        .values(
            key=key,
            score=n.score,
            reason=n.reason,
            input_tokens=n.tokens.get("input"),
            output_tokens=n.tokens.get("output"),
            judge_cost_usd=Decimal(str(round(n.judge_cost_usd, 8))) if n.judge_cost_usd is not None else None,
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    db.commit()


def _stop_for_budget(db: Session, run: TestRun) -> None:
    log.info("run %s reached its budget cap", run.id)
    run_svc.cancel_run(db, run, reason="budget_cap_reached")


async def _generate(db: Session, result: TestResult, run: TestRun) -> bool:
    spec = next(o for o in run.config_snapshot["outputs"] if o["label"] == result.output_label)
    provider = db.get(Provider, spec["provider_id"])
    if provider is None:
        result.status, result.error_message = "error", "The provider for this answer was deleted"
        db.commit()
        return False
    params = {"max_tokens": spec.get("max_tokens") or 1000}
    if spec.get("temperature") is not None:
        params["temperature"] = spec["temperature"]
    case = result.test_case
    prompt = run_svc.build_prompt(case.input, case.provided_context)
    try:
        async with provider_slot(provider.id):
            gen = await openrouter.generate(decrypt_secret(provider.encrypted_credentials), spec["model"], prompt, params)
    except Exception as e:  # noqa: BLE001 - provider errors are result errors, not crashes
        result.status = "error"
        result.error_message = redact(f"Generation failed: {type(e).__name__}: {e}")[:2000]
        db.commit()
        return False
    result.model_output = gen.text
    result.latency_ms = gen.latency_ms
    result.input_tokens = gen.input_tokens
    result.output_tokens = gen.output_tokens
    result.cost_usd = Decimal(str(round(gen.cost_usd, 8))) if gen.cost_usd is not None else None
    _add_cost(db, run.id, gen.cost_usd)
    db.commit()
    return True


async def _call_rag(db: Session, result: TestResult, run: TestRun) -> bool:
    """Live RAG: ask the user's endpoint; store its answer and the contexts it actually retrieved."""
    from app.rag.connector import ConnectorError, call

    spec = next(o for o in run.config_snapshot["outputs"] if o["label"] == result.output_label)
    conn = db.get(RagConnector, spec["connector_id"])
    if conn is None:
        result.status, result.error_message = "error", "The RAG connector for this run was deleted"
        db.commit()
        return False
    from app.api.rag import config_of

    try:
        async with provider_slot(f"connector:{conn.id}"):
            r = await call(config_of(conn), result.test_case.input)
    except ConnectorError as e:
        result.status = "error"
        result.error_message = redact(f"RAG endpoint: {e}")[:2000]
        db.commit()
        return False
    result.model_output = r.answer
    result.retrieved_context = r.contexts
    result.latency_ms = r.latency_ms
    db.commit()
    return True


async def _call_agent(db: Session, result: TestResult, run: TestRun) -> bool:
    """Agent runs (D31): run the sandboxed agent loop; store its final answer and full trace."""
    from app.agents.runner import run_agent

    spec = next(o for o in run.config_snapshot["outputs"] if o["label"] == result.output_label)
    agent = run.config_snapshot["agents"][str(spec["agent_id"])]  # the snapshot, not the live config
    provider = db.get(Provider, agent["provider_id"])
    if provider is None:
        result.status, result.error_message = "error", "The agent's provider was deleted"
        db.commit()
        return False
    scenario = result.test_case.case_metadata.get("scenario") or {}
    try:
        async with provider_slot(provider.id):
            r = await run_agent(agent, scenario.get("turns") or [result.test_case.input],
                                decrypt_secret(provider.encrypted_credentials))
    except Exception as e:  # noqa: BLE001 - provider errors are result errors, not crashes
        result.status = "error"
        result.error_message = redact(f"Agent run failed: {type(e).__name__}: {e}")[:2000]
        db.commit()
        return False
    result.model_output = r.final
    result.trace = r.trace()
    result.latency_ms = r.latency_ms
    result.input_tokens, result.output_tokens = r.input_tokens, r.output_tokens
    result.cost_usd = Decimal(str(round(r.cost_usd, 8)))
    _add_cost(db, run.id, r.cost_usd)
    db.commit()
    return True


def agent_inputs(result: TestResult) -> tuple[list[dict] | None, dict | None]:
    if result.output_source != "agent" or not result.trace:
        return None, None
    calls = [{"name": s["name"], "args": s.get("args"), "output": s.get("output"), "side_effect": s.get("side_effect", False)}
             for s in result.trace.get("steps", []) if s.get("type") == "tool_call"]
    return calls, (result.test_case.case_metadata.get("scenario") or {}).get("expectations")


async def _process(result_id: int) -> None:
    db = session_factory()()
    try:
        await _process_in(db, result_id)
    finally:
        db.close()
        # Engines create HTTP clients lazily and close them on garbage collection. Collect while this
        # unit's event loop is still running so their close() runs here, not in the next unit's loop
        # (which otherwise logs "RuntimeError: Event loop is closed").
        gc.collect()
        await asyncio.sleep(0.05)


async def _process_in(db: Session, result_id: int) -> None:
    result = db.get(TestResult, result_id)
    if result is None:
        return
    run = db.get(TestRun, result.run_id)
    if run.status in run_svc.TERMINAL_RUN:
        if result.status in ("pending", "running"):
            result.status = "cancelled"
            db.commit()
        return
    if result.status in run_svc.TERMINAL_RESULT:
        run_svc.finalize_if_done(db, run.id)
        return

    db.execute(
        update(TestRun)
        .where(TestRun.id == run.id, TestRun.status == "queued")
        .values(status="running", started_at=datetime.now(UTC))
    )
    result.status = "running"
    db.commit()
    db.refresh(run)

    if run.run_type == "security":
        from app.security.executor import process as security_process

        try:
            await security_process(db, result, run)
        except Exception as e:  # noqa: BLE001 - never leave a unit 'running'
            db.rollback()
            log.exception("security unit %s failed", result_id)
            result = db.get(TestResult, result_id)
            result.status = "error"
            result.error_message = redact(f"Internal error: {type(e).__name__}: {e}")[:2000]
            db.commit()
        finally:
            run_svc.finalize_if_done(db, run.id)
        return

    try:
        if result.output_source == "provider" and result.model_output is None:
            if _over_budget(run):
                _stop_for_budget(db, run)
                result.status = "cancelled"
                db.commit()
                return
            if not await _generate(db, result, run):
                return
            db.refresh(run)
        if result.output_source == "agent" and result.model_output is None:
            if _over_budget(run):
                _stop_for_budget(db, run)
                result.status = "cancelled"
                db.commit()
                return
            if not await _call_agent(db, result, run):
                return
            db.refresh(run)
        if result.output_source == "rag_api" and result.model_output is None:
            if _over_budget(run):
                _stop_for_budget(db, run)
                result.status = "cancelled"
                db.commit()
                return
            if not await _call_rag(db, result, run):
                return

        judge = _judge_settings(db, run)
        case = result.test_case
        # Live RAG is judged on what the application actually retrieved, never on uploaded context.
        context = result.retrieved_context if result.output_source == "rag_api" else case.provided_context
        tools_called, expectations = agent_inputs(result)
        inputs = EvalInputs(
            question=case.input,
            response=result.model_output,
            reference=case.expected_output,
            context=context,
            criteria=case.criteria,
            tools_called=tools_called,
            expectations=expectations,
        )
        plan = [(m["metric"], m["engine"], m["threshold"]) for m in run.config_snapshot["metrics"]]
        engines = {e for _, e, _ in plan}
        evaluators = {e: runner.EVALUATORS[e](judge) for e in engines}
        timeout = float(get_settings().unit_timeout_s)
        try:
            for rep in range(run.repeats):
                db.refresh(run)
                if run.status == "cancelled":
                    break
                if _over_budget(run):
                    _stop_for_budget(db, run)
                    break
                done = set(
                    db.execute(
                        select(MetricResult.metric_name, MetricResult.engine).where(
                            MetricResult.test_result_id == result.id, MetricResult.repeat_index == rep
                        )
                    ).all()
                )
                pending = []
                for metric, engine, threshold in plan:
                    if (metric, engine) in done:
                        continue
                    ev = evaluators[engine]
                    ok, why = check(metric, engine, inputs)
                    if not ok:
                        _save(db, result, run, runner.not_applicable(metric, engine, ev.version(), threshold, rep, why or ""))
                        continue
                    key = runner.cache_key(engine, ev.version(), metric, judge, inputs, rep)
                    if run.use_cache:
                        base = runner.not_applicable(metric, engine, ev.version(), threshold, rep, "")
                        base.judge_model, base.judge_config = judge.model, judge.public_config()
                        hit = _cached(db, key, base)
                        if hit is not None:
                            _save(db, result, run, hit)
                            continue
                    pending.append((key, ev, metric, threshold))

                async def one(key, ev, metric, threshold, rep=rep):
                    async with provider_slot(run.config_snapshot["judge"]["provider_id"]):
                        return key, await runner.run_metric(ev, metric, inputs, threshold, rep, timeout)

                for fut in asyncio.as_completed([one(*p) for p in pending]):
                    key, n = await fut
                    if _save(db, result, run, n) and run.use_cache:
                        _store_cache(db, key, n)
        finally:
            for ev in evaluators.values():
                await ev.aclose()

        rows = list(db.scalars(select(MetricResult).where(MetricResult.test_result_id == result.id)).all())
        expected = len(plan) * run.repeats
        if len(rows) < expected:
            result.status = "cancelled"  # stopped early by cancel/budget
        else:
            aggs = run_svc.aggregate(rows)
            result.status = run_svc.result_status(aggs)
            if run.run_type == "rag":
                result.failure_origin = run_svc.failure_origin(aggs)
            if aggs and all(a.status == "not_applicable" for a in aggs):
                reasons = sorted({a.reason for a in aggs if a.reason})
                result.error_message = "No selected metric could run for this case: " + "; ".join(reasons)
        db.commit()
    except Exception as e:  # noqa: BLE001 - never leave a unit 'running' on an unexpected failure
        db.rollback()
        log.exception("unit %s failed", result_id)
        result = db.get(TestResult, result_id)
        result.status = "error"
        result.error_message = redact(f"Internal error: {type(e).__name__}: {e}")[:2000]
        db.commit()
    finally:
        run_svc.finalize_if_done(db, run.id)
