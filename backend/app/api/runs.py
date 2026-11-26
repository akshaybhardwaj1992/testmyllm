from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import not_found
from app.models import HumanReview, MetricResult, TestResult, TestRun, User
from app.schemas import (
    CaseComparisonOut,
    ComparisonOut,
    EstimateOut,
    ResultsOut,
    ReviewIn,
    ReviewOut,
    RunCreate,
    RunListOut,
    RunOut,
    RunSummaryOut,
)
from app.services import comparison as cmp_svc
from app.services import exports as export_svc
from app.services import runs as svc
from app.services.access import require_project

router = APIRouter(tags=["runs"])


def run_filters(project_id: int, status: str | None, run_type: str | None, q: str | None,
                date_from: date | None, date_to: date | None) -> list:
    """Shared by the runs list, History and its CSV export."""
    cond = [TestRun.project_id == project_id]
    if status:
        cond.append(TestRun.status.in_(status.split(",")))
    if run_type:
        cond.append(TestRun.run_type.in_(run_type.split(",")))
    if q:
        cond.append(or_(TestRun.run_name.ilike(f"%{q.strip()}%")))
    if date_from:
        cond.append(TestRun.created_at >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        cond.append(TestRun.created_at < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    return cond


def _run(db: Session, run_id: int, user: User, min_role: str = "viewer") -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None:
        raise not_found("Run")
    require_project(db, run.project_id, user, min_role)
    return run


@router.post("/test-runs/estimate", response_model=EstimateOut)
def estimate_run(body: RunCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    return svc.estimate(db, project, body)


@router.post("/test-runs", response_model=RunOut, status_code=201)
def create_run(body: RunCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    run = svc.create_run(db, project, body, user)
    svc.enqueue_run(db, run)
    db.refresh(run)
    return svc.run_out(db, run)


@router.get("/projects/{project_id}/test-runs", response_model=RunListOut)
def list_runs(
    project_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    run_type: str | None = None,
    q: str | None = Query(None, max_length=200),
    sort: str = Query("-created_at", pattern="^-?(created_at|run_name|status|actual_cost_usd)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    require_project(db, project_id, user)
    cond = run_filters(project_id, status, run_type, q, date_from, date_to)
    col = getattr(TestRun, sort.lstrip("-"))
    order = col.desc() if sort.startswith("-") else col.asc()
    total = db.scalar(select(func.count()).select_from(TestRun).where(*cond)) or 0
    rows = db.scalars(
        select(TestRun).where(*cond).order_by(order, TestRun.id.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return RunListOut(items=[svc.run_out(db, r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/test-runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.run_out(db, _run(db, run_id, user))


@router.get("/test-runs/{run_id}/results", response_model=ResultsOut)
def get_results(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = Query(None, description="Comma-separated result statuses"),
    label: str | None = Query(None, max_length=100),
    q: str | None = Query(None, max_length=200),
    metric: str | None = Query(None, max_length=60),
    engine: Literal["ragas", "deepeval", "testmyllm"] | None = None,
    metric_status: str | None = Query(None, description="Comma-separated metric statuses"),
    failure_origin: str | None = Query(None, description="RAG: retrieval,generation,both,undetermined"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _run(db, run_id, user)
    items, total, labels = svc.results_page(
        db, run, page, page_size, status, label, q, metric, engine, metric_status, failure_origin)
    return ResultsOut(items=items, total=total, page=page, page_size=page_size, labels=labels)


@router.get("/test-runs/{run_id}/summary", response_model=RunSummaryOut)
def get_summary(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.summary(db, _run(db, run_id, user))


@router.get("/test-runs/{run_id}/comparison", response_model=ComparisonOut)
def get_comparison(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Per-model quality, latency, tokens and cost (spec §4.3). Works for any run with 2+ answers."""
    return cmp_svc.comparison(db, _run(db, run_id, user))


@router.get("/test-runs/{run_id}/cases", response_model=CaseComparisonOut)
def get_case_comparison(
    run_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, max_length=200),
    disagree_only: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Per-case drill-down with every model's output side by side."""
    return cmp_svc.case_comparison(db, _run(db, run_id, user), page, page_size, q, disagree_only)


@router.post("/test-runs/{run_id}/rerun", response_model=RunOut, status_code=201)
def rerun(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Repeat a run with the same pinned data, models, metrics and judge (e.g. a benchmark later)."""
    original = _run(db, run_id, user, "editor")
    if original.run_type == "security":
        from app.services import security as sec_svc

        project, _ = require_project(db, original.project_id, user, "editor")
        run = sec_svc.create_run(db, project, sec_svc.rerun_body(original), user)
    elif original.run_type == "agent":
        from app.services import agents as agent_svc

        project, _ = require_project(db, original.project_id, user, "editor")
        run = agent_svc.create_run(db, project, agent_svc.rerun_body(original), user)
    else:
        run = cmp_svc.rerun(db, original, user)
    svc.enqueue_run(db, run)
    db.refresh(run)
    return svc.run_out(db, run)


@router.post("/test-runs/{run_id}/cancel", response_model=RunOut)
def cancel(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _run(db, run_id, user, "editor")
    svc.cancel_run(db, run)
    db.refresh(run)
    return svc.run_out(db, run)


@router.get("/test-runs/{run_id}/export")
def export_run(
    run_id: int,
    format: Literal["csv", "json", "html", "pdf"] = Query("csv"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _run(db, run_id, user)
    content, filename = export_svc.export(db, run, format, user.id)
    return Response(
        content,
        media_type=export_svc.CONTENT_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"},
    )


# ------------------------------------------------------------------ human review
@router.post("/metric-results/{metric_result_id}/review", response_model=ReviewOut)
def review(
    metric_result_id: int, body: ReviewIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    """Agree/disagree with a judge verdict (one review per reviewer; re-posting updates it)."""
    m = db.get(MetricResult, metric_result_id)
    if m is None:
        raise not_found("Metric result")
    result = db.get(TestResult, m.test_result_id)
    _run(db, result.run_id, user, "editor")
    if m.status not in ("passed", "failed"):
        from app.core.errors import bad_request

        raise bad_request("Only scored metric results (passed or failed) can be reviewed")
    rv = db.scalar(select(HumanReview).where(
        HumanReview.metric_result_id == m.id, HumanReview.reviewer_id == user.id))
    if rv is None:
        rv = HumanReview(metric_result_id=m.id, reviewer_id=user.id)
        db.add(rv)
    rv.verdict, rv.override_status, rv.comment = body.verdict, body.override_status, body.comment
    db.commit()
    out = ReviewOut.model_validate(rv)
    out.reviewer_email = user.email
    return out
