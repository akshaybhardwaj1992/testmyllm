"""Model comparison (spec §4.3) and re-running a run from its snapshot ("repeat benchmarks later")."""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.models import DatasetVersion, TestCase, TestResult, TestRun, TestSuite, User
from app.schemas import (
    CaseComparisonOut,
    CaseComparisonRow,
    CaseOut,
    CaseOutputOut,
    ComparisonOut,
    ModelMetricSummary,
    ModelSummary,
    RunCreate,
)
from app.services import runs as run_svc


def _percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile (no interpolation): p50/p95 of observed latencies."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def _f(v: Decimal | float | None) -> float | None:
    return None if v is None else float(v)


def comparison(db: Session, run: TestRun) -> ComparisonOut:
    results = db.scalars(
        select(TestResult).where(TestResult.run_id == run.id).options(selectinload(TestResult.metrics))
    ).all()
    outputs = (run.config_snapshot or {}).get("outputs", [])
    by_label: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        by_label[r.output_label].append(r)
    cols = [{"metric": m["metric"], "engine": m["engine"]} for m in (run.config_snapshot or {}).get("metrics", [])]

    models = []
    for o in outputs:
        rows = by_label.get(o["label"], [])
        per_metric: dict[tuple[str, str], list] = defaultdict(list)
        for r in rows:
            for a in run_svc.aggregate(list(r.metrics)):
                per_metric[(a.metric_name, a.engine)].append(a)
        metric_rows = []
        for c in cols:
            aggs = per_metric.get((c["metric"], c["engine"]), [])
            vals = [a.mean for a in aggs if a.mean is not None]
            passed = sum(1 for a in aggs if a.status == "passed")
            decided = passed + sum(1 for a in aggs if a.status == "failed")
            metric_rows.append(ModelMetricSummary(
                metric_name=c["metric"], engine=c["engine"],
                mean=statistics.fmean(vals) if vals else None,
                std=statistics.stdev(vals) if len(vals) > 1 else None,
                pass_rate=passed / decided if decided else None, n=len(vals),
            ))
        means = [m.mean for m in metric_rows if m.mean is not None]
        latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
        costs = [r.cost_usd for r in rows if r.model_output is not None]
        model_cost = None if any(c is None for c in costs) else sum(_f(c) or 0 for c in costs)
        judge_cost = sum(
            _f(m.judge_cost_usd) or 0 for r in rows for m in r.metrics if not m.cache_hit
        )
        out_tokens = [r.output_tokens for r in rows if r.output_tokens is not None]
        generated = sum(1 for r in rows if r.model_output is not None)
        models.append(ModelSummary(
            label=o["label"], model=o.get("model"), results=len(rows),
            passed=sum(1 for r in rows if r.status == "passed"),
            failed=sum(1 for r in rows if r.status == "failed"),
            errors=sum(1 for r in rows if r.status == "error"),
            quality=statistics.fmean(means) if means else None, metrics=metric_rows,
            latency_p50_ms=_percentile(latencies, 50), latency_p95_ms=_percentile(latencies, 95),
            input_tokens=sum(r.input_tokens or 0 for r in rows), output_tokens=sum(out_tokens),
            avg_output_tokens=statistics.fmean(out_tokens) if out_tokens else None,
            model_cost_usd=model_cost, judge_cost_usd=judge_cost,
            cost_per_case_usd=(model_cost / generated) if model_cost is not None and generated else None,
        ))
    return ComparisonOut(models=models, metric_columns=cols)


def case_comparison(
    db: Session, run: TestRun, page: int, page_size: int, q: str | None = None, disagree_only: bool = False
) -> CaseComparisonOut:
    """Cases with every model's output side by side. `disagree_only`: models got different statuses."""
    cond = [TestResult.run_id == run.id]
    case_ids = select(TestResult.test_case_id).where(*cond).group_by(TestResult.test_case_id)
    if disagree_only:
        case_ids = case_ids.having(func.count(func.distinct(TestResult.status)) > 1)
    stmt = select(TestCase).where(TestCase.id.in_(case_ids))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(TestCase.input.ilike(like), TestCase.expected_output.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    cases = db.scalars(stmt.order_by(TestCase.position, TestCase.id).offset((page - 1) * page_size).limit(page_size)).all()
    results = db.scalars(
        select(TestResult).where(TestResult.run_id == run.id, TestResult.test_case_id.in_([c.id for c in cases]))
        .options(selectinload(TestResult.metrics))
    ).all()
    by_case: dict[int, dict[str, CaseOutputOut]] = defaultdict(dict)
    for r in results:
        by_case[r.test_case_id][r.output_label] = CaseOutputOut(
            result_id=r.id, status=r.status, model_output=r.model_output, error_message=r.error_message,
            latency_ms=r.latency_ms, output_tokens=r.output_tokens, cost_usd=_f(r.cost_usd),
            aggregates=run_svc.aggregate(list(r.metrics)),
        )
    labels = [o["label"] for o in (run.config_snapshot or {}).get("outputs", [])]
    return CaseComparisonOut(
        items=[CaseComparisonRow(case=CaseOut.model_validate(c), outputs=by_case.get(c.id, {})) for c in cases],
        total=total, page=page, page_size=page_size, labels=labels,
    )


def rerun_payload(db: Session, run: TestRun) -> RunCreate:
    """Rebuild a RunCreate from a run's snapshot: same data (pinned version), models, metrics, judge."""
    snap = run.config_snapshot or {}
    if run.judge_config_id is None:
        raise ApiError(409, "judge_deleted", "The judge configuration used by this run was deleted; start a new run instead")
    outputs = []
    for o in snap.get("outputs", []):
        spec = {k: o.get(k) for k in ("label", "source", "source_model", "provider_id", "model", "max_tokens",
                                      "temperature", "connector_id")}
        spec = {k: v for k, v in spec.items() if v is not None}
        if o["source"] == "manual":  # Studio answers live on the results
            spec["text"] = db.scalar(
                select(TestResult.model_output).where(TestResult.run_id == run.id, TestResult.output_label == o["label"]).limit(1)
            )
        outputs.append(spec)
    body: dict = {
        "project_id": run.project_id,
        "run_name": f"{run.run_name} (re-run)"[:300],
        "run_type": run.run_type,
        "judge_config_id": run.judge_config_id,
        "repeats": run.repeats,
        "budget_usd": float(run.budget_usd) if run.budget_usd is not None else None,
        "use_cache": run.use_cache,
        "metrics": snap.get("metrics", []),
        "outputs": outputs,
    }
    suite = snap.get("suite")
    if snap.get("source") == "studio":
        case = db.get(TestCase, snap.get("case_id"))
        if case is None:
            raise ApiError(409, "case_deleted", "The Studio case for this run no longer exists")
        body["case"] = {"question": case.input, "reference": case.expected_output,
                        "context": case.provided_context, "criteria": case.criteria}
    elif suite and db.get(TestSuite, suite["id"]):
        body["suite_id"] = suite["id"]
    else:
        vid = (snap.get("dataset") or {}).get("version_id") or run.dataset_version_id
        if vid is None or db.get(DatasetVersion, vid) is None:
            raise ApiError(409, "dataset_deleted", "The dataset version for this run no longer exists")
        body["dataset_version_id"] = vid
    return RunCreate.model_validate(body)


def rerun(db: Session, run: TestRun, user: User) -> TestRun:
    from app.models import Project

    payload = rerun_payload(db, run)
    return run_svc.create_run(db, db.get(Project, run.project_id), payload, user)
