"""AI security testing API: targets, attack library, scans, reports and security gates (decision D29)."""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import current_user
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, not_found
from app.core.redact import redact
from app.core.security import decrypt_secret
from app.models import Attack, AttackSet, JudgeConfig, Provider, RagConnector, SecurityGate, SecurityTarget, TestRun, User
from app.providers import openrouter
from app.schemas import GateEvaluationOut, RegressionReport, RunOut
from app.schemas.security import (
    AttackSetIn,
    AttackSetOut,
    DeepTeamEstimateOut,
    DeepTeamIn,
    EnhancerOut,
    SecurityEstimateOut,
    SecurityGateEvaluateIn,
    SecurityGateIn,
    SecurityGateOut,
    SecurityReportOut,
    SecurityRunIn,
    SecurityTargetIn,
    SecurityTargetOut,
    TargetProbeOut,
)
from app.security.library import attack_fields, ensure_builtin
from app.services import runs as run_svc
from app.services import security as svc
from app.services.access import require_project

router = APIRouter(tags=["security"])


def _target(db: Session, target_id: int, user: User, min_role: str = "viewer") -> SecurityTarget:
    t = db.get(SecurityTarget, target_id)
    if t is None:
        raise not_found("Security target")
    require_project(db, t.project_id, user, min_role)
    return t


def _gate(db: Session, gate_id: int, user: User, min_role: str = "viewer") -> SecurityGate:
    g = db.get(SecurityGate, gate_id)
    if g is None:
        raise not_found("Security gate")
    require_project(db, g.project_id, user, min_role)
    return g


def _run(db: Session, run_id: int, user: User, min_role: str = "viewer") -> TestRun:
    run = db.get(TestRun, run_id)
    if run is None or run.run_type != "security":
        raise not_found("Security run")
    require_project(db, run.project_id, user, min_role)
    return run


# ------------------------------------------------------------------ targets
@router.post("/projects/{project_id}/security-targets", response_model=SecurityTargetOut, status_code=201)
def create_target(project_id: int, body: SecurityTargetIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    project, _ = require_project(db, project_id, user, "editor")
    t = SecurityTarget(project_id=project.id)
    svc.apply_target(db, project, t, body, user)
    db.commit()
    return svc.target_out(db, t)


@router.get("/projects/{project_id}/security-targets", response_model=list[SecurityTargetOut])
def list_targets(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    rows = db.scalars(select(SecurityTarget).where(SecurityTarget.project_id == project_id).order_by(SecurityTarget.id)).all()
    return [svc.target_out(db, t) for t in rows]


@router.put("/security-targets/{target_id}", response_model=SecurityTargetOut)
def update_target(target_id: int, body: SecurityTargetIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    t = _target(db, target_id, user, "editor")
    project, _ = require_project(db, t.project_id, user, "editor")
    svc.apply_target(db, project, t, body, user)
    db.commit()
    return svc.target_out(db, t)


@router.delete("/security-targets/{target_id}", status_code=204)
def delete_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    t = _target(db, target_id, user, "editor")
    db.delete(t)
    db.commit()
    return Response(status_code=204)


@router.post("/security-targets/{target_id}/probe", response_model=TargetProbeOut)
async def probe_target(target_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Send one harmless message to check the target answers (costs one tiny call)."""
    t = _target(db, target_id, user, "editor")
    try:
        if t.kind == "model":
            p = db.get(Provider, t.provider_id)
            msgs = [{"role": "system", "content": svc.effective_prompt(db, t)}] if t.system_prompt or svc.canaries(db, t.id) else []
            gen = await openrouter.chat(decrypt_secret(p.encrypted_credentials), t.model,
                                        msgs + [{"role": "user", "content": "Hello! Reply with one short sentence."}],
                                        {"max_tokens": 60})
            return TargetProbeOut(ok=True, reply=gen.text[:500], latency_ms=gen.latency_ms)
        from app.api.rag import config_of
        from app.rag.connector import ConnectorError, call

        c = db.get(RagConnector, t.connector_id)
        user_var = {"user": t.identities[0]["value"]} if t.identities else None
        try:
            r = await call(config_of(c), "Hello! What can you help me with?", user_var)
        except ConnectorError as e:
            return TargetProbeOut(ok=False, error=redact(str(e))[:500])
        return TargetProbeOut(ok=True, reply=r.answer[:500], latency_ms=r.latency_ms)
    except Exception as e:  # noqa: BLE001 - shown to the user, secrets redacted
        return TargetProbeOut(ok=False, error=redact(f"{type(e).__name__}: {e}")[:500])


# ------------------------------------------------------------------ attack library
@router.get("/projects/{project_id}/attack-sets", response_model=list[AttackSetOut])
def list_attack_sets(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    ensure_builtin(db)
    rows = db.scalars(
        select(AttackSet).where(or_(AttackSet.project_id == project_id, AttackSet.project_id.is_(None)))
        .options(selectinload(AttackSet.attacks)).order_by(AttackSet.builtin.desc(), AttackSet.key, AttackSet.id)
    ).all()
    # newest version of each key only
    latest: dict[tuple, AttackSet] = {}
    for s in rows:
        k = (s.project_id, s.key)
        if k not in latest or int(s.version) > int(latest[k].version):
            latest[k] = s
    return list(latest.values())


@router.post("/projects/{project_id}/attack-sets", response_model=AttackSetOut, status_code=201)
def create_attack_set(project_id: int, body: AttackSetIn, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    """Create a custom set; posting an existing key creates its next version (old versions are kept)."""
    require_project(db, project_id, user, "editor")
    prev = db.scalars(select(AttackSet.version).where(AttackSet.project_id == project_id, AttackSet.key == body.key)).all()
    version = str(max((int(v) for v in prev), default=0) + 1)
    s = AttackSet(project_id=project_id, key=body.key, name=body.name.strip(), version=version,
                  description=body.description, builtin=False)
    s.attacks = [Attack(**attack_fields(a.model_dump())) for a in body.attacks]
    db.add(s)
    db.commit()
    return s


@router.delete("/attack-sets/{set_id}", status_code=204)
def delete_attack_set(set_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    s = db.get(AttackSet, set_id)
    if s is None or s.builtin or s.project_id is None:
        raise not_found("Attack set")
    require_project(db, s.project_id, user, "editor")
    db.delete(s)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ DeepTeam generation
def _deepteam_plan(db: Session, set_id: int, body: DeepTeamIn, user: User):
    from app.security import deepteam_engine as dt

    project, _ = require_project(db, body.project_id, user, "editor")
    src = db.get(AttackSet, set_id)
    if src is None or (src.project_id is not None and src.project_id != project.id) or src.status != "ready":
        raise not_found("Attack set")
    judge = None
    if body.judge_config_id:
        judge = db.get(JudgeConfig, body.judge_config_id)
        if judge is None or judge.project_id != project.id:
            raise not_found("Judge config")
    base = [{"turns": a.turns, "key": a.key} for a in src.attacks]
    pairs = dt.plan(base, body.enhancers, body.max_variants)
    if any(e.needs_llm for _, e in pairs) and judge is None:
        raise ApiError(400, "judge_required", "Rewriting enhancers need a judge model to act as the simulator")
    return project, src, judge, pairs, sum(1 for a in src.attacks if len(a.turns) > 1)


@router.get("/security/deepteam/enhancers", response_model=list[EnhancerOut])
def list_enhancers(user: User = Depends(current_user)):
    from app.security.deepteam_engine import ENHANCERS

    return [EnhancerOut(key=e.key, label=e.label, technique=e.technique, needs_llm=e.needs_llm,
                        description=e.description) for e in ENHANCERS.values()]


@router.post("/attack-sets/{set_id}/deepteam/estimate", response_model=DeepTeamEstimateOut)
def estimate_deepteam(set_id: int, body: DeepTeamIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    from app.security import deepteam_engine as dt

    _, _, judge, pairs, multi = _deepteam_plan(db, set_id, body, user)
    price = openrouter.model_price(judge.model) if judge else None
    typical, maximum = dt.estimate_usd(pairs, price)
    llm = sum(1 for _, e in pairs if e.needs_llm)
    warnings = []
    if llm and price is None:
        warnings.append(f"No price known for {judge.model}; cost will still be measured.")
    if multi:
        warnings.append(f"{multi} multi-turn attack(s) are not expanded (DeepTeam enhancers are single-turn).")
    if llm:
        warnings.append("Rewrites that drop the code word or that the simulator refuses are discarded, "
                        "so you may get fewer variants than the maximum.")
    return DeepTeamEstimateOut(variants_max=len(pairs), llm_rewrites=llm, local_variants=len(pairs) - llm,
                               skipped_multi_turn=multi, usd_typical=typical, usd_max=maximum, warnings=warnings)


@router.post("/attack-sets/{set_id}/deepteam", response_model=AttackSetOut, status_code=202)
def generate_with_deepteam(set_id: int, body: DeepTeamIn, db: Session = Depends(get_db),
                           user: User = Depends(current_user)):
    """Queue a DeepTeam expansion; the new set version is `generating` until the worker finishes."""
    from app.security import deepteam_engine as dt
    from app.workers.tasks import generate_attacks

    project, src, judge, pairs, _ = _deepteam_plan(db, set_id, body, user)
    if not pairs:
        raise bad_request("This set has no single-turn attacks to expand")
    key = f"{src.key}-deepteam"[:100]
    prev = db.scalars(select(AttackSet.version).where(AttackSet.project_id == project.id, AttackSet.key == key)).all()
    s = AttackSet(
        project_id=project.id, key=key, name=f"{src.name} + DeepTeam"[:200],
        version=str(max((int(v) for v in prev), default=0) + 1), builtin=False, status="generating",
        description=f"Generated with DeepTeam {dt.version()} from {src.key} v{src.version} "
                    f"({', '.join(dt.ENHANCERS[e].label for e in body.enhancers)}).",
        generation={"engine": "deepteam", "engine_version": dt.version(), "source_set_id": src.id,
                    "source": f"{src.key}@{src.version}", "enhancers": body.enhancers,
                    "judge_config_id": judge.id if judge else None, "judge_model": judge.model if judge else None,
                    "max_variants": body.max_variants, "requested_by": user.id, "cost_usd": None, "error": None},
    )
    db.add(s)
    db.commit()
    generate_attacks.delay(s.id)
    db.refresh(s)
    return s


# ------------------------------------------------------------------ scans
@router.post("/security-runs/estimate", response_model=SecurityEstimateOut)
def estimate(body: SecurityRunIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    return svc.estimate(db, project, body)


@router.post("/security-runs", response_model=RunOut, status_code=201)
def create_scan(body: SecurityRunIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    project, _ = require_project(db, body.project_id, user, "editor")
    run = svc.create_run(db, project, body, user)
    run_svc.enqueue_run(db, run)
    db.refresh(run)
    return run_svc.run_out(db, run)


@router.get("/security-runs/{run_id}/report", response_model=SecurityReportOut)
def get_report(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return svc.report(db, _run(db, run_id, user))


# ------------------------------------------------------------------ gates
@router.post("/projects/{project_id}/security-gates", response_model=SecurityGateOut, status_code=201)
def create_gate(project_id: int, body: SecurityGateIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    require_project(db, project_id, user, "editor")
    g = SecurityGate(project_id=project_id, name=body.name.strip(), rules=[r.model_dump() for r in body.rules],
                     max_critical=body.max_critical, max_high=body.max_high)
    db.add(g)
    db.commit()
    return g


@router.get("/projects/{project_id}/security-gates", response_model=list[SecurityGateOut])
def list_gates(project_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    require_project(db, project_id, user)
    return db.scalars(select(SecurityGate).where(SecurityGate.project_id == project_id).order_by(SecurityGate.id)).all()


@router.put("/security-gates/{gate_id}", response_model=SecurityGateOut)
def update_gate(gate_id: int, body: SecurityGateIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = _gate(db, gate_id, user, "editor")
    g.name, g.rules = body.name.strip(), [r.model_dump() for r in body.rules]
    g.max_critical, g.max_high = body.max_critical, body.max_high
    db.commit()
    return g


@router.delete("/security-gates/{gate_id}", status_code=204)
def delete_gate(gate_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    g = _gate(db, gate_id, user, "editor")
    db.delete(g)
    db.commit()
    return Response(status_code=204)


@router.post("/security-gates/{gate_id}/evaluate", response_model=GateEvaluationOut)
def evaluate_gate(gate_id: int, body: SecurityGateEvaluateIn, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    g = _gate(db, gate_id, user, "editor" if body.record else "viewer")
    run = _run(db, body.run_id, user)
    if run.project_id != g.project_id:
        raise not_found("Security run")
    if run.status in ("queued", "running"):
        raise ApiError(409, "run_active", "The scan is still in progress; evaluate the gate when it has finished")
    result = svc.evaluate_gate(db, g, run)
    ev = svc.record(db, g, run, result, user) if body.record else None
    empty = RegressionReport(baseline_run_id=None, candidate_run_id=run.id, dataset_version_match=None,
                             paired_cases=0, metrics=[], regressed=[], improved=[], unchanged_count=0, warnings=[],
                             note="Security gate: attack success rates and finding counts; no baseline.")
    return GateEvaluationOut(id=ev.id if ev else None, run_id=run.id, baseline_id=None,
                             status="passed" if result.passed else "failed", gate=result, report=empty,
                             created_at=ev.created_at if ev else None)

