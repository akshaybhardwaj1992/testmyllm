"""Baselines, quality gates, regression reports and project API tokens (M5)."""
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TOKEN_PREFIX, current_user, hash_token, token_scope
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, not_found
from app.models import ApiToken, Baseline, Dataset, DatasetVersion, GateEvaluation, QualityGate, TestRun, User
from app.schemas import (
    ApiTokenCreated,
    ApiTokenIn,
    ApiTokenOut,
    BaselineIn,
    BaselineOut,
    GateEvaluateIn,
    GateEvaluationOut,
    GateResult,
    QualityGateIn,
    QualityGateOut,
    RegressionReport,
)
from app.services import regression as reg
from app.services.access import require_project

router = APIRouter(tags=["regression"])


def _run(db: Session, run_id: int, user: User, min_role: str = "viewer") -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None:
        raise not_found("Run")
    require_project(db, run.project_id, user, min_role)
    return run


def _baseline(db: Session, baseline_id: int, user: User, min_role: str = "viewer") -> Baseline:
    b = db.get(Baseline, baseline_id)
    if b is None:
        raise not_found("Baseline")
    require_project(db, b.project_id, user, min_role)
    return b


def _gate(db: Session, gate_id: int, user: User, min_role: str = "viewer") -> QualityGate:
    g = db.get(QualityGate, gate_id)
    if g is None:
        raise not_found("Quality gate")
    require_project(db, g.project_id, user, min_role)
    return g


def baseline_out(db: Session, b: Baseline) -> BaselineOut:
    v = db.get(DatasetVersion, b.dataset_version_id)
    ds = db.get(Dataset, v.dataset_id)
    run = db.get(TestRun, b.run_id)
    return BaselineOut(
        id=b.id, project_id=b.project_id, name=b.name, run_id=b.run_id, run_name=run.run_name,
        dataset_version_id=v.id, dataset_name=ds.name, version_number=v.version_number, created_at=b.created_at,
    )


# ------------------------------------------------------------------ baselines
@router.post("/baselines", response_model=BaselineOut, status_code=201)
def create_baseline(body: BaselineIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, body.project_id, user, "editor")
    run = _run(db, body.run_id, user, "editor")
    if run.project_id != body.project_id:
        raise not_found("Run")
    if run.status != "completed":
        raise bad_request("Only completed runs can become baselines")
    if run.dataset_version_id is None:
        raise bad_request("Baselines need a dataset-backed run (a pinned dataset version), not a Studio case")
    if run.expires_at is not None:
        raise bad_request("This run was made in no-persistence mode and will be deleted; it cannot be a baseline")
    b = Baseline(project_id=run.project_id, name=body.name.strip(), run_id=run.id,
                 dataset_version_id=run.dataset_version_id, created_by=user.id)
    db.add(b)
    db.commit()
    return baseline_out(db, b)


@router.get("/projects/{project_id}/baselines", response_model=list[BaselineOut])
def list_baselines(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    rows = db.scalars(select(Baseline).where(Baseline.project_id == project_id).order_by(Baseline.created_at.desc())).all()
    return [baseline_out(db, b) for b in rows]


@router.delete("/baselines/{baseline_id}", status_code=204)
def delete_baseline(baseline_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    b = _baseline(db, baseline_id, user, "editor")
    db.delete(b)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ gates
def _validate_rules_project(db: Session, body: QualityGateIn) -> list[dict]:
    return [r.model_dump() for r in body.rules]


@router.post("/quality-gates", response_model=QualityGateOut, status_code=201)
def create_gate(body: QualityGateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, body.project_id, user, "editor")
    g = QualityGate(project_id=body.project_id, name=body.name.strip(), rules=_validate_rules_project(db, body))
    db.add(g)
    db.commit()
    return g


@router.get("/projects/{project_id}/quality-gates", response_model=list[QualityGateOut])
def list_gates(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(select(QualityGate).where(QualityGate.project_id == project_id).order_by(QualityGate.name)).all()


@router.put("/quality-gates/{gate_id}", response_model=QualityGateOut)
def update_gate(gate_id: int, body: QualityGateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = _gate(db, gate_id, user, "editor")
    g.name, g.rules = body.name.strip(), _validate_rules_project(db, body)
    db.commit()
    return g


@router.delete("/quality-gates/{gate_id}", status_code=204)
def delete_gate(gate_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = _gate(db, gate_id, user, "editor")
    db.delete(g)
    db.commit()
    return Response(status_code=204)


@router.post("/quality-gates/{gate_id}/evaluate", response_model=GateEvaluationOut)
def evaluate_gate(gate_id: int, body: GateEvaluateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Evaluate a gate on a completed run (optionally vs a baseline). Recorded unless record=false."""
    g = _gate(db, gate_id, user, "editor" if body.record else "viewer")
    run = _run(db, body.run_id, user)
    if run.project_id != g.project_id:
        raise not_found("Run")
    if run.status in ("queued", "running"):
        raise ApiError(409, "run_active", "The run is still in progress; evaluate the gate when it has finished")
    baseline = _baseline(db, body.baseline_id, user) if body.baseline_id else None
    if baseline and baseline.project_id != g.project_id:
        raise not_found("Baseline")
    result, report = reg.evaluate_gate(db, g, run, baseline, body.allow_dataset_mismatch)
    ev = reg.record(db, result, report, run, baseline, user) if body.record else None
    return GateEvaluationOut(
        id=ev.id if ev else None, run_id=run.id, baseline_id=baseline.id if baseline else None,
        status="passed" if result.passed else "failed", gate=result, report=report,
        created_at=ev.created_at if ev else None,
    )


@router.get("/test-runs/{run_id}/gate-evaluations", response_model=list[GateEvaluationOut])
def list_evaluations(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    run = _run(db, run_id, user)
    rows = db.scalars(select(GateEvaluation).where(GateEvaluation.run_id == run.id).order_by(GateEvaluation.created_at.desc())).all()
    out = []
    for ev in rows:
        d = ev.details or {}
        out.append(GateEvaluationOut(
            id=ev.id, run_id=ev.run_id, baseline_id=ev.baseline_id, status=ev.status,
            gate=GateResult.model_validate(d["gate"]),
            report=RegressionReport(
                baseline_run_id=None, candidate_run_id=run.id, dataset_version_match=None,
                paired_cases=d.get("paired_cases", 0), metrics=d.get("metrics", []), regressed=[], improved=[],
                unchanged_count=0, warnings=d.get("warnings", []),
            ),
            created_at=ev.created_at,
        ))
    return out


# ------------------------------------------------------------------ regression report
@router.get("/regressions/{candidate_run_id}", response_model=RegressionReport)
def regression_report(
    candidate_run_id: int,
    baseline_id: int = Query(...),
    allow_dataset_mismatch: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = _run(db, candidate_run_id, user)
    baseline = _baseline(db, baseline_id, user)
    if baseline.project_id != run.project_id:
        raise not_found("Baseline")
    return reg.build_report(db, baseline, run, allow_mismatch=allow_dataset_mismatch)


# ------------------------------------------------------------------ API tokens
@router.post("/projects/{project_id}/api-tokens", response_model=ApiTokenCreated, status_code=201)
def create_token(project_id: int, body: ApiTokenIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if token_scope(user):
        raise ApiError(403, "forbidden", "API tokens cannot create API tokens")
    require_project(db, project_id, user, "owner")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    t = ApiToken(project_id=project_id, name=body.name.strip(), token_hash=hash_token(raw), prefix=raw[:10],
                 role=body.role, created_by=user.id)
    db.add(t)
    db.commit()
    return ApiTokenCreated(**ApiTokenOut.model_validate(t).model_dump(), token=raw)


@router.get("/projects/{project_id}/api-tokens", response_model=list[ApiTokenOut])
def list_tokens(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user, "owner")
    return db.scalars(select(ApiToken).where(ApiToken.project_id == project_id).order_by(ApiToken.created_at.desc())).all()


@router.delete("/api-tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = db.get(ApiToken, token_id)
    if t is None:
        raise not_found("API token")
    require_project(db, t.project_id, user, "owner")
    t.revoked_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)
