"""Overview dashboard, quality trends and history export (M6)."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.api.runs import run_filters
from app.core.db import get_db
from app.models import User
from app.schemas.insights import OverviewOut, TrendOut
from app.services import insights as svc
from app.services.access import require_project

router = APIRouter(tags=["insights"])


@router.get("/projects/{project_id}/overview", response_model=OverviewOut)
def overview(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, project_id, user)
    return svc.overview(db, project)


@router.get("/projects/{project_id}/trends", response_model=TrendOut)
def trends(
    project_id: int,
    days: int = Query(90, ge=1, le=3650),
    max_runs: int = Query(30, ge=2, le=200),
    run_type: str | None = Query(None, max_length=60, description="Comma-separated; default = all but security"),
    metric: str | None = Query(None, max_length=60),
    engine: str | None = Query(None, max_length=30),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    project, _ = require_project(db, project_id, user)
    return svc.trends(db, project, days, max_runs, run_type, metric, engine)


@router.get("/projects/{project_id}/test-runs/export")
def export_history(
    project_id: int,
    status: str | None = None,
    run_type: str | None = None,
    q: str | None = Query(None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """History as CSV: one row per run (filters as in the runs list) with per-metric means."""
    require_project(db, project_id, user)
    content = svc.history_csv(db, run_filters(project_id, status, run_type, q, date_from, date_to))
    name = f"testmyllm-history-p{project_id}-{datetime.now(UTC):%Y%m%d}.csv"
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"})
