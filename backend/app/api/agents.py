"""Agent testing API (decision D31): sandboxed agents with mock tools, scenario sets, playground, runs."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import ApiError, not_found
from app.core.redact import redact
from app.core.security import decrypt_secret
from app.models import AgentConfig, AgentScenarioSet, Provider, SecurityTarget, User
from app.schemas import RunOut
from app.schemas.agents import (
    AgentEstimateOut,
    AgentIn,
    AgentOut,
    AgentRunIn,
    AgentTryIn,
    AgentTryOut,
    ScenarioSetIn,
    ScenarioSetOut,
)
from app.services import agents as svc
from app.services import runs as run_svc
from app.services.access import require_project

router = APIRouter(tags=["agents"])


def _agent(db: Session, agent_id: int, user: User, min_role: str = "viewer") -> AgentConfig:
    a = db.get(AgentConfig, agent_id)
    if a is None:
        raise not_found("Agent")
    require_project(db, a.project_id, user, min_role)
    return a


@router.post("/projects/{project_id}/agents", response_model=AgentOut, status_code=201)
def create_agent(project_id: int, body: AgentIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, project_id, user, "editor")
    a = AgentConfig(project_id=project.id)
    svc.apply_agent(db, project, a, body)
    db.add(a)
    db.commit()
    return a


@router.get("/projects/{project_id}/agents", response_model=list[AgentOut])
def list_agents(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(select(AgentConfig).where(AgentConfig.project_id == project_id).order_by(AgentConfig.id)).all()


@router.put("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = _agent(db, agent_id, user, "editor")
    project, _ = require_project(db, a.project_id, user, "editor")
    svc.apply_agent(db, project, a, body)
    db.commit()
    return a


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    a = _agent(db, agent_id, user, "editor")
    if db.scalar(select(SecurityTarget.id).where(SecurityTarget.agent_id == a.id).limit(1)):
        raise ApiError(409, "agent_in_use", "A security target uses this agent; delete the target first")
    db.delete(a)
    db.commit()
    return Response(status_code=204)


@router.post("/agents/{agent_id}/try", response_model=AgentTryOut)
async def try_agent(agent_id: int, body: AgentTryIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Playground: run the sandboxed agent once on a message and return its trace (costs a few calls)."""
    from app.agents.runner import run_agent

    a = _agent(db, agent_id, user, "editor")
    p = db.get(Provider, a.provider_id)
    try:
        r = await run_agent(svc.agent_snapshot(a), [body.message], decrypt_secret(p.encrypted_credentials))
    except Exception as e:  # noqa: BLE001 - shown to the user, secrets redacted
        return AgentTryOut(ok=False, error=redact(f"{type(e).__name__}: {e}")[:600])
    return AgentTryOut(ok=True, final=r.final, trace=r.trace(), cost_usd=round(r.cost_usd, 8), latency_ms=r.latency_ms)


# ------------------------------------------------------------------ scenario sets
@router.get("/projects/{project_id}/agent-scenario-sets", response_model=list[ScenarioSetOut])
def list_sets(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Latest version of each scenario set."""
    require_project(db, project_id, user)
    rows = db.scalars(select(AgentScenarioSet).where(AgentScenarioSet.project_id == project_id)
                      .order_by(AgentScenarioSet.key, AgentScenarioSet.version.desc())).all()
    latest: dict[str, AgentScenarioSet] = {}
    for s in rows:
        latest.setdefault(s.key, s)
    return list(latest.values())


@router.post("/projects/{project_id}/agent-scenario-sets", response_model=ScenarioSetOut, status_code=201)
def create_set(project_id: int, body: ScenarioSetIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Posting an existing key creates its next version; earlier versions stay for reproducibility."""
    require_project(db, project_id, user, "editor")
    return svc.create_set(db, project_id, body)


@router.delete("/agent-scenario-sets/{set_id}", status_code=204)
def delete_set(set_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = db.get(AgentScenarioSet, set_id)
    if s is None:
        raise not_found("Scenario set")
    require_project(db, s.project_id, user, "editor")
    db.delete(s)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ runs
@router.post("/agent-runs/estimate", response_model=AgentEstimateOut)
def estimate(body: AgentRunIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    return svc.estimate(db, project, body)


@router.post("/agent-runs", response_model=RunOut, status_code=201)
def create_run(body: AgentRunIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    run = svc.create_run(db, project, body, user)
    run_svc.enqueue_run(db, run)
    db.refresh(run)
    return run_svc.run_out(db, run)
