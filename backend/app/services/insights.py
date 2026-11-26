"""Project overview, quality trends and history export (M6, spec §3 / §10).

No-persistence runs (`expires_at` set) are excluded from overview KPIs, trends and top-failing cases:
they are short-lived by design. Their spend still counts (the money was spent).
Means here are over metric rows (all valid repeats of all cases) - the same as the run summary when every
case has the same number of repeats, which is how runs are created.
"""
from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.datasets.parsing import csv_safe
from app.evaluators.applicability import METRICS
from app.models import Dataset, DatasetVersion, GateEvaluation, MetricResult, Project, QualityGate, SecurityGate, TestCase, TestResult, TestRun
from app.schemas.insights import (
    FailingCase,
    LatestGate,
    LatestRun,
    MetricMean,
    OverviewOut,
    SecurityKpi,
    TrendOut,
    TrendPoint,
)

SCORED = ("passed", "failed")


def _quality(project_id: int) -> list:
    return [TestRun.project_id == project_id, TestRun.expires_at.is_(None), TestRun.run_type != "security"]


def _order(name: str) -> int:
    order = list(METRICS)
    return order.index(name) if name in order else 99


def metric_means(db: Session, run_ids: list[int]) -> dict[int, list[MetricMean]]:
    if not run_ids:
        return {}
    rows = db.execute(
        select(TestResult.run_id, MetricResult.metric_name, MetricResult.engine, func.avg(MetricResult.score),
               func.max(MetricResult.threshold), func.bool_and(MetricResult.higher_is_better),
               func.count(case((MetricResult.status == "passed", 1))), func.count())
        .join(TestResult, TestResult.id == MetricResult.test_result_id)
        .where(TestResult.run_id.in_(run_ids), MetricResult.status.in_(SCORED))
        .group_by(TestResult.run_id, MetricResult.metric_name, MetricResult.engine)
    ).all()
    out: dict[int, list[MetricMean]] = {}
    for run_id, metric, engine, mean, thr, hib, passed, n in rows:
        out.setdefault(run_id, []).append(MetricMean(
            metric=metric, engine=engine, mean=float(mean), threshold=thr, higher_is_better=bool(hib),
            pass_rate=passed / n if n else None))
    for v in out.values():
        v.sort(key=lambda m: (_order(m.metric), m.engine))
    return out


def _gate_name(db: Session, ev: GateEvaluation) -> str:
    name = ((ev.details or {}).get("gate") or {}).get("gate_name")
    if name:
        return name
    g = db.get(SecurityGate, ev.security_gate_id) if ev.security_gate_id else db.get(QualityGate, ev.gate_id) if ev.gate_id else None
    return g.name if g else "deleted gate"


def overview(db: Session, project: Project) -> OverviewOut:
    now = datetime.now(UTC)
    since = now - timedelta(days=30)
    latest = db.scalar(select(TestRun).where(*_quality(project.id), TestRun.status == "completed")
                       .order_by(TestRun.completed_at.desc().nulls_last(), TestRun.id.desc()).limit(1))
    latest_out = None
    if latest is not None:
        decided = latest.passed_tests + latest.failed_tests + latest.error_tests
        latest_out = LatestRun(id=latest.id, run_name=latest.run_name, run_type=latest.run_type,
                               pass_rate=latest.passed_tests / decided if decided else None,
                               completed_at=latest.completed_at, metrics=metric_means(db, [latest.id]).get(latest.id, []))

    spend, runs_30d = db.execute(select(func.coalesce(func.sum(TestRun.actual_cost_usd), 0), func.count())
                                 .where(TestRun.project_id == project.id, TestRun.created_at >= since)).one()

    evals = db.execute(
        select(GateEvaluation, TestRun.run_name).join(TestRun, TestRun.id == GateEvaluation.run_id)
        .where(TestRun.project_id == project.id).order_by(GateEvaluation.created_at)
    ).all()
    latest_per: dict[tuple, GateEvaluation] = {}
    for ev, _ in evals:
        latest_per[(ev.run_id, ev.gate_id, ev.security_gate_id)] = ev
    open_failures = sum(1 for ev in latest_per.values() if ev.status == "failed" and ev.created_at >= since)
    latest_gate = None
    if evals:
        ev, run_name = evals[-1]
        latest_gate = LatestGate(run_id=ev.run_id, run_name=run_name, gate_name=_gate_name(db, ev), status=ev.status,
                                 kind="security" if ev.security_gate_id or (ev.details or {}).get("kind") == "security" else "quality",
                                 created_at=ev.created_at, baseline_id=ev.baseline_id)

    sec = None
    sec_run = db.scalar(select(TestRun).where(TestRun.project_id == project.id, TestRun.run_type == "security",
                                              TestRun.status == "completed", TestRun.expires_at.is_(None))
                        .order_by(TestRun.id.desc()).limit(1))
    if sec_run is not None:
        from app.services.security import report

        r = report(db, sec_run)
        sec = SecurityKpi(run_id=sec_run.id, run_name=sec_run.run_name, asr=r.asr, breached=r.breached, attacks=r.attacks,
                          critical=r.severity_counts.get("critical", 0), created_at=sec_run.created_at)

    return OverviewOut(latest_run=latest_out, spend_30d=float(spend), runs_30d=runs_30d,
                       open_gate_failures=open_failures, latest_gate=latest_gate, security=sec,
                       top_failing=top_failing(db, project.id))


def top_failing(db: Session, project_id: int, runs: int = 20, limit: int = 8) -> list[FailingCase]:
    """Cases (by stable case_key) that failed most often across the last `runs` completed quality runs."""
    recent = select(TestRun.id).where(*_quality(project_id), TestRun.status == "completed",
                                      TestRun.dataset_version_id.is_not(None)).order_by(TestRun.id.desc()).limit(runs)
    rows = db.execute(
        select(TestCase.case_key, func.max(TestCase.input), func.count(case((TestResult.status == "failed", 1))),
               func.count(), func.max(case((TestResult.status == "failed", TestResult.run_id))), func.max(Dataset.name))
        .join(TestCase, TestCase.id == TestResult.test_case_id)
        .join(DatasetVersion, DatasetVersion.id == TestCase.dataset_version_id)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(TestResult.run_id.in_(recent.scalar_subquery()))
        .group_by(TestCase.case_key)
        .having(func.count(case((TestResult.status == "failed", 1))) > 0)
        .order_by(func.count(case((TestResult.status == "failed", 1))).desc(), func.max(TestResult.run_id).desc())
        .limit(limit)
    ).all()
    return [FailingCase(case_key=k, input=i, failures=f, appearances=n, last_failed_run_id=r, dataset=d)
            for k, i, f, n, r, d in rows]


def trends(db: Session, project: Project, days: int = 90, max_runs: int = 30, run_type: str | None = None,
           metric: str | None = None, engine: str | None = None) -> TrendOut:
    since = datetime.now(UTC) - timedelta(days=days)
    types = TestRun.run_type.in_(run_type.split(",")) if run_type else TestRun.run_type != "security"
    cond = [TestRun.project_id == project.id, TestRun.expires_at.is_(None), types,
            TestRun.status == "completed", TestRun.created_at >= since]
    run_ids = list(db.scalars(select(TestRun.id).where(*cond).order_by(TestRun.id.desc()).limit(max_runs)))
    runs = {r.id: r for r in db.scalars(select(TestRun).where(TestRun.id.in_(run_ids)))} if run_ids else {}
    points = []
    for run_id, means in metric_means(db, run_ids).items():
        r = runs[run_id]
        for m in means:
            if (metric and m.metric != metric) or (engine and m.engine != engine):
                continue
            points.append(TrendPoint(run_id=run_id, run_name=r.run_name, run_type=r.run_type,
                                     created_at=r.created_at, judge_model=(r.config_snapshot.get("judge") or {}).get("model"),
                                     **m.model_dump()))
    points.sort(key=lambda p: (p.created_at, p.run_id, _order(p.metric), p.engine))
    return TrendOut(points=points, days=days, max_runs=max_runs)


def history_csv(db: Session, cond: list) -> bytes:
    """One row per run with run-level figures and per-metric means (formula-safe, UTF-8 BOM for Excel)."""
    runs = list(db.scalars(select(TestRun).where(*cond).order_by(TestRun.created_at.desc()).limit(5000)))
    means = metric_means(db, [r.id for r in runs])
    cols = sorted({(m.metric, m.engine) for v in means.values() for m in v}, key=lambda k: (_order(k[0]), k[1]))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["run_id", "run_name", "run_type", "status", "created_at", "completed_at", "cases", "passed", "failed",
                "errors", "pass_rate", "cost_usd", "estimated_cost_usd", "judge_model", "dataset", "repeats", "ephemeral",
                *[f"{m} ({e})" for m, e in cols]])
    for r in runs:
        decided = r.passed_tests + r.failed_tests + r.error_tests
        snap = r.config_snapshot or {}
        ds = snap.get("dataset")
        by = {(m.metric, m.engine): m.mean for m in means.get(r.id, [])}
        w.writerow([csv_safe(v) for v in [
            r.id, r.run_name, r.run_type, r.status, r.created_at.isoformat(),
            r.completed_at.isoformat() if r.completed_at else "", r.total_tests, r.passed_tests, r.failed_tests,
            r.error_tests, f"{r.passed_tests / decided:.4f}" if decided else "", f"{r.actual_cost_usd:.6f}",
            f"{r.estimated_cost_usd:.6f}" if r.estimated_cost_usd is not None else "",
            (snap.get("judge") or {}).get("model", ""), f"{ds['name']} v{ds['version_number']}" if ds else "",
            r.repeats, "yes" if r.expires_at else "no",
            *[f"{by[k]:.4f}" if k in by else "" for k in cols],
        ]])
    return ("﻿" + buf.getvalue()).encode("utf-8")

