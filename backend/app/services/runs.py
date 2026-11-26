"""Run creation, estimates, aggregation, summaries and lifecycle (engine-neutral)."""
from __future__ import annotations

import statistics
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError, bad_request, not_found
from app.evaluators.applicability import METRICS, check
from app.evaluators.base import EvalInputs, status_for
from app.evaluators.runner import engine_versions
from app.models import (
    Dataset,
    DatasetVersion,
    HumanReview,
    JudgeConfig,
    MetricResult,
    Project,
    Provider,
    RagConnector,
    TestCase,
    TestResult,
    TestRun,
    TestSuite,
    User,
)
from app.providers import openrouter
from app.schemas import (
    AgreementOut,
    CostLine,
    EstimateOut,
    HistogramBin,
    MetricAggregate,
    MetricSummaryRow,
    OutputSpec,
    ReviewOut,
    RunCreate,
    RunOut,
    RunSummaryOut,
    RunWarning,
    TestResultOut,
)
from app.services import estimate as est

TERMINAL_RUN = ("completed", "failed", "cancelled")
TERMINAL_RESULT = ("passed", "failed", "error", "cancelled")
MAX_UNITS = 5000  # cases x outputs per run

PROMPT_WITH_CONTEXT = "Answer using only this context.\n\nContext:\n{context}\n\nQuestion: {question}"


def build_prompt(question: str, context: list[str] | None) -> str:
    """Same prompt as the prototype's 'Call APIs live' mode."""
    if not context:
        return question
    return PROMPT_WITH_CONTEXT.format(context="\n\n".join(context), question=question)


# ------------------------------------------------------------------ source resolution
class Source:
    """The cases a run evaluates. Studio cases are not persisted until the run is created."""

    def __init__(self, cases: list[TestCase], dataset_version: DatasetVersion | None = None,
                 dataset: Dataset | None = None, suite: TestSuite | None = None, studio: bool = False):
        self.cases, self.dataset_version, self.dataset, self.suite, self.studio = (
            cases, dataset_version, dataset, suite, studio)


def resolve_source(db: Session, project: Project, payload: RunCreate) -> Source:
    if payload.case is not None:
        c = payload.case
        case = TestCase(
            project_id=project.id, dataset_version_id=None, input=c.question, expected_output=c.reference,
            provided_context=c.context, criteria=c.criteria, case_metadata={"origin": "studio"}, tags=[],
        )
        return Source([case], studio=True)

    suite = None
    if payload.suite_id is not None:
        suite = db.get(TestSuite, payload.suite_id)
        if suite is None or suite.project_id != project.id:
            raise not_found("Suite")
        version_id = suite.dataset_version_id
    else:
        version_id = payload.dataset_version_id
    version = db.get(DatasetVersion, version_id)
    dataset = db.get(Dataset, version.dataset_id) if version else None
    if dataset is None or dataset.project_id != project.id:
        raise not_found("Dataset version")
    stmt = select(TestCase).where(TestCase.dataset_version_id == version.id)
    if suite is not None and suite.case_ids is not None:
        stmt = stmt.where(TestCase.id.in_(suite.case_ids))
    cases = list(db.scalars(stmt.order_by(TestCase.position, TestCase.id)).all())
    if not cases:
        raise bad_request("The selected suite or dataset version has no cases")
    if len(cases) * len(payload.outputs) > MAX_UNITS:
        raise bad_request(f"Runs are limited to {MAX_UNITS} case × answer evaluations; split the suite")
    return Source(cases, version, dataset, suite)


# ------------------------------------------------------------------ validation helpers
def _judge(db: Session, project: Project, judge_config_id: int) -> JudgeConfig:
    judge = db.get(JudgeConfig, judge_config_id)
    if judge is None or judge.project_id != project.id:
        raise not_found("Judge config")
    return judge


def _check_providers(db: Session, project: Project, outputs: list[OutputSpec]) -> dict[int, RagConnector]:
    """Validate referenced providers/connectors belong to the project. Returns the connectors by id."""
    connectors: dict[int, RagConnector] = {}
    for o in outputs:
        if o.source == "provider":
            p = db.get(Provider, o.provider_id)
            if p is None or p.project_id != project.id:
                raise bad_request(f"'{o.label}': provider not found in this project")
        if o.source == "rag_api":
            c = db.get(RagConnector, o.connector_id)
            if c is None or c.project_id != project.id:
                raise bad_request(f"'{o.label}': RAG connector not found in this project")
            connectors[c.id] = c
    return connectors


# Estimate assumptions for live RAG (unknown until the endpoint is called): ~300-token answer,
# 3 retrieved chunks of ~200 tokens when the case has no provided context to size from.
_RAG_ANSWER = "x" * 1200
_RAG_CONTEXT = ["x" * 800] * 3


def _inputs_for(
    case: TestCase, output: OutputSpec, for_estimate: bool, connectors: dict[int, RagConnector] | None = None
) -> EvalInputs:
    context = case.provided_context
    if output.source == "manual":
        response = output.text
    elif output.source == "dataset":
        response = case.recorded_output
    elif output.source == "rag_api":
        # Live RAG: answer and contexts come from the endpoint. Never fall back to provided context.
        response = _RAG_ANSWER if for_estimate else "pending"
        conn = (connectors or {}).get(output.connector_id or 0)
        returns_contexts = bool(conn and (conn.response_mapping or {}).get("contexts_path"))
        context = (case.provided_context or _RAG_CONTEXT) if (returns_contexts and for_estimate) else (
            ["pending"] if returns_contexts else None)
    else:  # generated: unknown until called; assume half of max_tokens for the estimate
        response = "x" * (output.max_tokens * 2) if for_estimate else "pending"
    return EvalInputs(
        question=case.input, response=response, reference=case.expected_output,
        context=context, criteria=case.criteria,
    )


def bias_warnings(judge_model: str, outputs: list[dict]) -> list[RunWarning]:
    """Spec §2 judge bias: warn when the judge shares a family with a model under test."""
    jf = openrouter.model_family(judge_model)
    if jf is None:
        return []
    out = []
    for o in outputs:
        who = o.get("model") or o.get("source_model") or o.get("label")
        if openrouter.model_family(who) == jf:
            out.append(RunWarning(
                type="judge_bias",
                message=(
                    f"Judge {judge_model} is in the same model family as '{o.get('label')}' ({who}). "
                    "The judge may favour its own family; consider re-scoring with a different judge."
                ),
            ))
    return out


def _public_outputs(outputs: list[OutputSpec]) -> list[dict]:
    return [
        {
            "label": o.label, "source": o.source, "source_model": o.source_model, "provider_id": o.provider_id,
            "connector_id": o.connector_id,
            "model": o.model, "max_tokens": o.max_tokens, "temperature": o.temperature,
        }
        for o in outputs
    ]


# ------------------------------------------------------------------ estimate
def estimate(db: Session, project: Project, payload: RunCreate, source: Source | None = None) -> EstimateOut:
    judge = _judge(db, project, payload.judge_config_id)
    connectors = _check_providers(db, project, payload.outputs)
    source = source or resolve_source(db, project, payload)
    pairs = [(m.metric, m.engine) for m in payload.metrics]
    inputs = [_inputs_for(c, o, True, connectors) for c in source.cases for o in payload.outputs]
    j, notes = est.estimate_judge(pairs, inputs, payload.repeats)
    warnings = bias_warnings(judge.model, _public_outputs(payload.outputs))

    judge_price = openrouter.model_price(judge.model)
    judge_usd = est.usd(j.input_tokens, j.output_tokens, judge_price)
    if judge_price is None:
        warnings.append(RunWarning(type="pricing_unknown", message=f"No price known for judge {judge.model}."))

    mut_in = mut_out = 0
    mut_usd: float | None = 0.0
    for o in payload.outputs:
        if o.source != "provider":
            continue
        price = openrouter.model_price(o.model or "")
        if price is None:
            warnings.append(RunWarning(type="pricing_unknown", message=f"No price known for {o.model}; check the model id exists on OpenRouter."))
            mut_usd = None
        for c in source.cases:
            p_tok = est.tokens_of(build_prompt(c.input, c.provided_context))
            out_tok = o.max_tokens // 2
            mut_in += p_tok
            mut_out += out_tok
            if price is not None and mut_usd is not None:
                mut_usd += p_tok * price[0] + out_tok * price[1]
    if notes:
        warnings.append(RunWarning(
            type="not_applicable", message=f"{len(notes)} metric/input combination(s) will be recorded as N/A."))
    total = None if judge_usd is None or mut_usd is None else judge_usd + mut_usd
    return EstimateOut(
        model_under_test=CostLine(usd=mut_usd, input_tokens=mut_in, output_tokens=mut_out),
        judge=CostLine(usd=judge_usd, input_tokens=j.input_tokens, output_tokens=j.output_tokens),
        total_usd=total, judge_calls=j.calls, applicable_metric_runs=j.applicable, not_applicable=notes[:50],
        warnings=warnings,
    )


# ------------------------------------------------------------------ create
def ephemeral_expiry(project: Project) -> datetime | None:
    """No-persistence mode (D30): new runs expire this long after creation."""
    from datetime import timedelta

    s = project.settings or {}
    if not s.get("no_persistence"):
        return None
    return datetime.now(UTC) + timedelta(hours=int(s.get("no_persistence_hours") or 24))


def create_run(db: Session, project: Project, payload: RunCreate, user: User) -> TestRun:
    judge = _judge(db, project, payload.judge_config_id)
    connectors = _check_providers(db, project, payload.outputs)
    source = resolve_source(db, project, payload)
    pairs = [(m.metric, m.engine) for m in payload.metrics]
    if not any(
        check(m, e, _inputs_for(c, o, False, connectors))[0]
        for c in source.cases for o in payload.outputs for m, e in pairs
    ):
        raise ApiError(
            400, "no_applicable_metrics",
            "None of the selected metrics can run with the inputs available "
            "(e.g. faithfulness needs context; dataset answers need a recorded response or a provider).",
        )
    e = estimate(db, project, payload, source)

    if source.studio:
        db.add(source.cases[0])
        db.flush()

    outputs = _public_outputs(payload.outputs)
    has_context = any(c.provided_context for c in source.cases)
    snapshot = {
        "source": "studio" if source.studio else ("suite" if source.suite else "dataset"),
        "case_id": source.cases[0].id if source.studio else None,
        "case_count": len(source.cases),
        "dataset": None if source.studio else {
            "id": source.dataset.id, "name": source.dataset.name,
            "version_id": source.dataset_version.id, "version_number": source.dataset_version.version_number,
        },
        "suite": {"id": source.suite.id, "name": source.suite.name} if source.suite else None,
        "metrics": [m.model_dump() for m in payload.metrics],
        "outputs": outputs,
        # connector config at run time (no secrets) so live RAG runs are reproducible/auditable
        "connectors": {
            str(c.id): {
                "name": c.name, "endpoint_url": c.endpoint_url, "method": c.method,
                "request_template": c.request_template, "response_mapping": c.response_mapping,
                "timeout_s": c.timeout_s, "auth_header_name": c.auth_header_name,
            }
            for c in connectors.values()
        },
        "repeats": payload.repeats,
        "use_cache": payload.use_cache,
        "budget_usd": payload.budget_usd,
        "prompt_template": PROMPT_WITH_CONTEXT if has_context else "{question}",
        "judge": {
            "id": judge.id, "name": judge.name, "provider_id": judge.provider_id, "model": judge.model,
            "temperature": judge.temperature, "embedding_model": judge.embedding_model,
        },
        "warnings": [w.model_dump() for w in e.warnings if w.type == "judge_bias"],
    }
    labels = " vs ".join(o.label for o in payload.outputs)
    if source.studio:
        default_name = f"Studio: {labels}"
    elif source.suite:
        default_name = f"{source.suite.name} · {labels}"
    else:
        default_name = f"{source.dataset.name} v{source.dataset_version.version_number} · {labels}"
    expires_at = ephemeral_expiry(project)
    if expires_at:
        snapshot["ephemeral"] = True
    run = TestRun(
        project_id=project.id,
        expires_at=expires_at,
        suite_id=source.suite.id if source.suite else None,
        dataset_version_id=source.dataset_version.id if source.dataset_version else None,
        run_name=(payload.run_name or "").strip() or default_name,
        run_type=payload.run_type,
        judge_config_id=judge.id,
        repeats=payload.repeats,
        use_cache=payload.use_cache and expires_at is None,  # no-persistence: nothing kept in the judge cache
        budget_usd=Decimal(str(payload.budget_usd)) if payload.budget_usd else None,
        estimated_cost_usd=Decimal(str(round(e.total_usd, 6))) if e.total_usd is not None else None,
        status="queued",
        total_tests=len(source.cases) * len(payload.outputs),
        config_snapshot=snapshot,
        engine_versions=engine_versions(),
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    rows = []
    for c in source.cases:
        for o in payload.outputs:
            rows.append({
                "run_id": run.id,
                "test_case_id": c.id,
                "output_label": o.label,
                "source_model": o.model if o.source == "provider" else o.source_model,
                "output_source": o.source,
                "model_output": o.text if o.source == "manual" else (c.recorded_output if o.source == "dataset" else None),
                "status": "pending",
            })
    db.execute(TestResult.__table__.insert(), rows)
    db.commit()
    return run


def enqueue_run(db: Session, run: TestRun) -> None:
    from app.workers.tasks import evaluate_result  # local import: keeps API importable without Celery wiring

    ids = db.scalars(select(TestResult.id).where(TestResult.run_id == run.id).order_by(TestResult.id)).all()
    for rid in ids:
        evaluate_result.delay(rid)


# ------------------------------------------------------------------ aggregation
def aggregate(metrics: list[MetricResult]) -> list[MetricAggregate]:
    """Per (metric, engine) across repeats. Decision D11: pass/fail uses the mean of valid scores."""
    groups: dict[tuple[str, str], list[MetricResult]] = {}
    for m in metrics:
        groups.setdefault((m.metric_name, m.engine), []).append(m)
    out = []
    order = list(METRICS)
    for (name, engine), rows in sorted(
        groups.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, kv[0][1])
    ):
        rows.sort(key=lambda r: r.repeat_index)
        scores = [r.score for r in rows if r.status in ("passed", "failed") and r.score is not None]
        errors = sum(1 for r in rows if r.status == "error")
        first = rows[0]
        if scores:
            mean = statistics.fmean(scores)
            status = status_for(mean, first.threshold, first.higher_is_better)
        elif errors:
            mean, status = None, "error"
        else:
            mean, status = None, "not_applicable"
        reason = next((r.reason for r in rows if r.reason), None)
        if status == "error":
            reason = next((r.error_message for r in rows if r.error_message), reason)
        out.append(MetricAggregate(
            metric_name=name, engine=engine, engine_version=first.engine_version,
            engine_metric_name=first.engine_metric_name, higher_is_better=first.higher_is_better,
            threshold=first.threshold, status=status, mean=mean,
            std=statistics.stdev(scores) if len(scores) > 1 else None,
            min=min(scores) if scores else None, max=max(scores) if scores else None,
            n=len(scores), errors=errors, reason=reason,
            judge_model=next((r.judge_model for r in rows if r.judge_model), None),
        ))
    return out


def result_status(aggs: list[MetricAggregate]) -> str:
    statuses = {a.status for a in aggs}
    if "failed" in statuses:
        return "failed"
    if "error" in statuses:
        return "error"
    if statuses <= {"not_applicable"}:
        return "error"  # nothing could run: not a pass (decision D17)
    return "passed"


# ------------------------------------------------------------------ serialization
def completed_count(db: Session, run_id: int) -> int:
    return db.scalar(
        select(func.count()).where(TestResult.run_id == run_id, TestResult.status.in_(TERMINAL_RESULT))
    ) or 0


def run_out(db: Session, run: TestRun) -> RunOut:
    out = RunOut.model_validate(run)
    out.completed_tests = completed_count(db, run.id)
    out.warnings = [RunWarning(**w) for w in (run.config_snapshot or {}).get("warnings", [])]
    return out


def _filters(run_id: int, status: str | None, label: str | None, q: str | None,
             metric: str | None, engine: str | None, metric_status: str | None,
             failure_origin: str | None = None) -> list:
    cond = [TestResult.run_id == run_id]
    if status:
        cond.append(TestResult.status.in_(status.split(",")))
    if failure_origin:
        cond.append(TestResult.failure_origin.in_(failure_origin.split(",")))
    if label:
        cond.append(TestResult.output_label == label)
    if q:
        like = f"%{q.strip()}%"
        cond.append(TestResult.test_case_id.in_(
            select(TestCase.id).where(or_(TestCase.input.ilike(like), TestCase.expected_output.ilike(like)))))
    if metric or engine or metric_status:
        sub = [MetricResult.test_result_id == TestResult.id]
        if metric:
            sub.append(MetricResult.metric_name == metric)
        if engine:
            sub.append(MetricResult.engine == engine)
        if metric_status:
            sub.append(MetricResult.status.in_(metric_status.split(",")))
        cond.append(exists().where(*sub))
    return cond


def results_page(
    db: Session, run: TestRun, page: int = 1, page_size: int = 50, status: str | None = None,
    label: str | None = None, q: str | None = None, metric: str | None = None, engine: str | None = None,
    metric_status: str | None = None, failure_origin: str | None = None,
) -> tuple[list[TestResultOut], int, list[str]]:
    cond = _filters(run.id, status, label, q, metric, engine, metric_status, failure_origin)
    total = db.scalar(select(func.count()).select_from(TestResult).where(*cond)) or 0
    rows = db.scalars(
        select(TestResult).where(*cond)
        .options(selectinload(TestResult.metrics), selectinload(TestResult.test_case))
        .order_by(TestResult.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    metric_ids = [m.id for r in rows for m in r.metrics]
    reviews: dict[int, list[ReviewOut]] = {}
    if metric_ids:
        for rv, email in db.execute(
            select(HumanReview, User.email).join(User, User.id == HumanReview.reviewer_id)
            .where(HumanReview.metric_result_id.in_(metric_ids)).order_by(HumanReview.created_at)
        ).all():
            out = ReviewOut.model_validate(rv)
            out.reviewer_email = email
            reviews.setdefault(rv.metric_result_id, []).append(out)
    items = []
    for r in rows:
        data = TestResultOut.model_validate({
            **{k: getattr(r, k) for k in TestResultOut.model_fields if k not in ("case", "metrics", "aggregates")},
            "case": r.test_case, "metrics": r.metrics, "aggregates": [],
        })
        for m in data.metrics:
            m.reviews = reviews.get(m.id, [])
        data.aggregates = aggregate(list(r.metrics))
        items.append(data)
    labels = [o["label"] for o in (run.config_snapshot or {}).get("outputs", [])]
    return items, total, labels


def _histogram(values: list[float], bins: int = 10) -> list[HistogramBin]:
    out = [HistogramBin(start=round(i / bins, 2), end=round((i + 1) / bins, 2), count=0) for i in range(bins)]
    for v in values:
        idx = min(bins - 1, max(0, int(v * bins)))
        out[idx].count += 1
    return out


def summary(db: Session, run: TestRun) -> RunSummaryOut:
    """Per metric × engine across results (each result contributes its mean over repeats)."""
    metrics = db.scalars(
        select(MetricResult).join(TestResult, TestResult.id == MetricResult.test_result_id)
        .where(TestResult.run_id == run.id)
    ).all()
    by_result: dict[int, list[MetricResult]] = {}
    for m in metrics:
        by_result.setdefault(m.test_result_id, []).append(m)
    groups: dict[tuple[str, str], list[MetricAggregate]] = {}
    for rows in by_result.values():
        for a in aggregate(rows):
            groups.setdefault((a.metric_name, a.engine), []).append(a)
    order = list(METRICS)
    out_rows = []
    for (name, engine), aggs in sorted(groups.items(), key=lambda kv: (order.index(kv[0][0]) if kv[0][0] in order else 99, kv[0][1])):
        vals = [a.mean for a in aggs if a.mean is not None]
        passed = sum(1 for a in aggs if a.status == "passed")
        failed = sum(1 for a in aggs if a.status == "failed")
        first = aggs[0]
        out_rows.append(MetricSummaryRow(
            metric_name=name, engine=engine, engine_version=first.engine_version,
            higher_is_better=first.higher_is_better, threshold=first.threshold, n=len(vals),
            mean=statistics.fmean(vals) if vals else None,
            std=statistics.stdev(vals) if len(vals) > 1 else None,
            pass_rate=passed / (passed + failed) if passed + failed else None,
            passed=passed, failed=failed,
            errors=sum(1 for a in aggs if a.status == "error"),
            not_applicable=sum(1 for a in aggs if a.status == "not_applicable"),
            histogram=_histogram(vals),
        ))
    verdicts = dict(db.execute(
        select(HumanReview.verdict, func.count())
        .join(MetricResult, MetricResult.id == HumanReview.metric_result_id)
        .join(TestResult, TestResult.id == MetricResult.test_result_id)
        .where(TestResult.run_id == run.id).group_by(HumanReview.verdict)
    ).all())
    agree, disagree = verdicts.get("agree", 0), verdicts.get("disagree", 0)
    status_counts = dict(db.execute(
        select(TestResult.status, func.count()).where(TestResult.run_id == run.id).group_by(TestResult.status)
    ).all())
    origins = dict(db.execute(
        select(TestResult.failure_origin, func.count())
        .where(TestResult.run_id == run.id, TestResult.failure_origin.is_not(None))
        .group_by(TestResult.failure_origin)
    ).all())
    return RunSummaryOut(
        metrics=out_rows,
        agreement=AgreementOut(
            reviewed=agree + disagree, agree=agree, disagree=disagree,
            rate=agree / (agree + disagree) if agree + disagree else None,
        ),
        status_counts=status_counts,
        failure_origins=origins,
    )


# ------------------------------------------------------------------ RAG failure attribution
RETRIEVAL_METRICS = {"context_precision", "context_recall"}
GENERATION_METRICS = {"faithfulness", "answer_correctness", "hallucination"}


def failure_origin(aggs: list[MetricAggregate]) -> str | None:
    """Heuristic attribution (spec §4.2), using each metric's own threshold:
    retrieval metric failed -> retrieval; retrieval measured and passing but a generation metric failed
    -> generation; both failed -> both; any other failure -> undetermined. None if nothing failed."""
    scored = [a for a in aggs if a.status in ("passed", "failed")]
    if not any(a.status == "failed" for a in scored):
        return None
    retrieval = [a for a in scored if a.metric_name in RETRIEVAL_METRICS]
    r_low = any(a.status == "failed" for a in retrieval)
    g_low = any(a.status == "failed" for a in scored if a.metric_name in GENERATION_METRICS)
    if r_low and g_low:
        return "both"
    if r_low:
        return "retrieval"
    if g_low and retrieval:
        return "generation"
    return "undetermined"


# ------------------------------------------------------------------ lifecycle
def cancel_run(db: Session, run: TestRun, reason: str = "cancelled_by_user") -> bool:
    """Set cancelled if still active. In-flight units finish their current metric batch."""
    now = datetime.now(UTC)
    changed = db.execute(
        update(TestRun)
        .where(TestRun.id == run.id, TestRun.status.in_(("queued", "running")))
        .values(status="cancelled", status_reason=reason, completed_at=now)
    ).rowcount
    db.execute(
        update(TestResult).where(TestResult.run_id == run.id, TestResult.status == "pending").values(status="cancelled")
    )
    db.commit()
    if changed:
        recount(db, run.id)
    return bool(changed)


def recount(db: Session, run_id: int) -> None:
    counts = dict(db.execute(
        select(TestResult.status, func.count()).where(TestResult.run_id == run_id).group_by(TestResult.status)
    ).all())
    db.execute(update(TestRun).where(TestRun.id == run_id).values(
        passed_tests=counts.get("passed", 0), failed_tests=counts.get("failed", 0), error_tests=counts.get("error", 0)))
    db.commit()


def finalize_if_done(db: Session, run_id: int) -> None:
    """Called after every unit. Row lock prevents two workers finalizing at once."""
    run = db.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
    if run is None:
        db.rollback()
        return
    counts = dict(db.execute(
        select(TestResult.status, func.count()).where(TestResult.run_id == run_id).group_by(TestResult.status)
    ).all())
    run.passed_tests = counts.get("passed", 0)
    run.failed_tests = counts.get("failed", 0)
    run.error_tests = counts.get("error", 0)
    open_units = counts.get("pending", 0) + counts.get("running", 0)
    if run.status in ("queued", "running") and open_units == 0:
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
    db.commit()
