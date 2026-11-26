"""Agent testing service (D31): agent configs, versioned scenario sets, estimates and run creation.

An agent run is an ordinary run (run_type 'agent'): each scenario is materialized as an ad-hoc test case
(dataset_version_id NULL, the scenario in metadata), each (scenario x agent) is one test result whose
"generation" is the sandboxed agent loop. Metrics, repeats, judge cache, budget cap, gates, comparison
(2+ agents), exports and re-run all work unchanged.
"""
from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request, not_found
from app.evaluators.applicability import check
from app.evaluators.base import EvalInputs
from app.evaluators.runner import engine_versions
from app.models import AgentConfig, AgentScenarioSet, JudgeConfig, Project, Provider, TestCase, TestResult, TestRun, User
from app.providers import openrouter
from app.schemas import CostLine
from app.schemas.agents import AgentEstimateOut, AgentIn, AgentRunIn, ScenarioSetIn
from app.services import estimate as est
from app.services.runs import bias_warnings, ephemeral_expiry


# ------------------------------------------------------------------ agents
def apply_agent(db: Session, project: Project, a: AgentConfig, body: AgentIn) -> None:
    p = db.get(Provider, body.provider_id)
    if p is None or p.project_id != project.id:
        raise bad_request("Provider not found in this project")
    a.name, a.provider_id, a.model = body.name.strip(), body.provider_id, body.model.strip()
    a.system_prompt, a.max_steps = body.system_prompt, body.max_steps
    a.tools = [t.model_dump() for t in body.tools]
    a.params = {"max_tokens": body.max_tokens, "temperature": body.temperature}


def agent_snapshot(a: AgentConfig) -> dict:
    return {"id": a.id, "name": a.name, "provider_id": a.provider_id, "model": a.model,
            "system_prompt": a.system_prompt, "tools": a.tools, "max_steps": a.max_steps, "params": a.params}


# ------------------------------------------------------------------ scenario sets
def create_set(db: Session, project_id: int, body: ScenarioSetIn) -> AgentScenarioSet:
    prev = db.scalars(select(AgentScenarioSet.version).where(
        AgentScenarioSet.project_id == project_id, AgentScenarioSet.key == body.key)).all()
    s = AgentScenarioSet(project_id=project_id, key=body.key, name=body.name.strip(), description=body.description,
                         version=max(prev, default=0) + 1,
                         scenarios=[sc.model_dump(exclude_none=True) for sc in body.scenarios])
    db.add(s)
    db.commit()
    return s


def _scenario_input(sc: dict) -> str:
    turns = sc["turns"]
    return turns[0] if len(turns) == 1 else "\n\n".join(f"User turn {i + 1}: {t}" for i, t in enumerate(turns))


# ------------------------------------------------------------------ planning
def _plan(db: Session, project: Project, body: AgentRunIn):
    agents = []
    for aid in body.agent_ids:
        a = db.get(AgentConfig, aid)
        if a is None or a.project_id != project.id:
            raise not_found("Agent")
        agents.append(a)
    sset = db.get(AgentScenarioSet, body.scenario_set_id)
    if sset is None or sset.project_id != project.id:
        raise not_found("Scenario set")
    judge = db.get(JudgeConfig, body.judge_config_id)
    if judge is None or judge.project_id != project.id:
        raise not_found("Judge config")
    return agents, sset, judge


def _placeholder_inputs(sc: dict) -> EvalInputs:
    """Size an agent result for the estimate (answer and trace are unknown until the agent runs)."""
    exp = sc.get("expectations") or {}
    calls = [{"name": m["tool"], "args": m.get("args") or {}, "output": "x" * 400} for m in exp.get("must_call", [])] \
        or [{"name": "tool", "args": {}, "output": "x" * 400}]
    return EvalInputs(question=_scenario_input(sc), response="x" * 800, reference=sc.get("reference"),
                      criteria=sc.get("criteria"), tools_called=calls,
                      expectations=exp if any(v not in (None, [], False) for v in exp.values()) else None)


def estimate(db: Session, project: Project, body: AgentRunIn) -> AgentEstimateOut:
    agents, sset, judge = _plan(db, project, body)
    pairs = [(m.metric, m.engine) for m in body.metrics]
    inputs = [_placeholder_inputs(sc) for sc in sset.scenarios for _ in agents]
    j, notes = est.estimate_judge(pairs, inputs, body.repeats)
    warnings = [w.message for w in bias_warnings(judge.model, [{"label": a.name, "model": a.model} for a in agents])]
    judge_price = openrouter.model_price(judge.model)
    judge_usd = est.usd(j.input_tokens, j.output_tokens, judge_price)
    if judge_price is None:
        warnings.append(f"No price known for judge {judge.model}.")
    calls = in_tok = out_tok = 0
    a_usd: float | None = 0.0
    for a in agents:
        price = openrouter.model_price(a.model)
        if price is None:
            warnings.append(f"No price known for {a.model}; check the model id exists on OpenRouter.")
            a_usd = None
        base = est.tokens_of(a.system_prompt) + est.tokens_of(json.dumps(a.tools))
        out = min(150, int((a.params or {}).get("max_tokens") or 500))
        for sc in sset.scenarios:
            steps = min(a.max_steps, len((sc.get("expectations") or {}).get("must_call", [])) + 1) + 1
            for k in range(steps * len(sc["turns"])):
                tok = base + est.tokens_of(_scenario_input(sc)) + k * 250
                calls, in_tok, out_tok = calls + 1, in_tok + tok, out_tok + out
                if price is not None and a_usd is not None:
                    a_usd += tok * price[0] + out * price[1]
    if notes:
        warnings.append(f"{len(notes)} metric/input combination(s) will be recorded as N/A.")
    warnings.append("Agent cost is an estimate: the number of tool steps depends on the model.")
    total = None if judge_usd is None or a_usd is None else judge_usd + a_usd
    return AgentEstimateOut(
        scenarios=len(sset.scenarios), agents=len(agents), agent_calls=calls,
        agents_cost=CostLine(usd=a_usd, input_tokens=in_tok, output_tokens=out_tok),
        judge=CostLine(usd=judge_usd, input_tokens=j.input_tokens, output_tokens=j.output_tokens),
        total_usd=total, judge_calls=j.calls, not_applicable=notes[:50], warnings=warnings,
    )


def create_run(db: Session, project: Project, body: AgentRunIn, user: User) -> TestRun:
    agents, sset, judge = _plan(db, project, body)
    pairs = [(m.metric, m.engine) for m in body.metrics]
    if not any(check(m, e, _placeholder_inputs(sc))[0] for sc in sset.scenarios for m, e in pairs):
        raise ApiError(400, "no_applicable_metrics", "None of the selected metrics can run on these scenarios")
    e = estimate(db, project, body)
    labels, outputs = set(), []
    for a in agents:
        label = a.name[:100] if a.name[:100] not in labels else f"{a.name[:90]} #{a.id}"
        labels.add(label)
        outputs.append({"label": label, "source": "agent", "agent_id": a.id, "model": a.model})
    expires_at = ephemeral_expiry(project)
    snapshot = {
        "source": "agent",
        "scenario_set": {"id": sset.id, "key": sset.key, "version": sset.version, "name": sset.name},
        "case_count": len(sset.scenarios),
        "metrics": [m.model_dump() for m in body.metrics],
        "outputs": outputs,
        "agents": {str(a.id): agent_snapshot(a) for a in agents},
        "repeats": body.repeats, "use_cache": body.use_cache and expires_at is None, "budget_usd": body.budget_usd,
        "judge": {"id": judge.id, "name": judge.name, "provider_id": judge.provider_id, "model": judge.model,
                  "temperature": judge.temperature, "embedding_model": judge.embedding_model},
        "warnings": [w.model_dump() for w in bias_warnings(judge.model, outputs)],
        "ephemeral": expires_at is not None,
    }
    run = TestRun(
        project_id=project.id, expires_at=expires_at, run_type="agent", judge_config_id=judge.id,
        run_name=(body.run_name or "").strip() or f"{sset.name} v{sset.version} · {' vs '.join(o['label'] for o in outputs)}",
        repeats=body.repeats, use_cache=snapshot["use_cache"],
        budget_usd=Decimal(str(body.budget_usd)) if body.budget_usd else None,
        estimated_cost_usd=Decimal(str(round(e.total_usd, 6))) if e.total_usd is not None else None,
        status="queued", total_tests=len(sset.scenarios) * len(agents), config_snapshot=snapshot,
        engine_versions=engine_versions(), created_by=user.id,
    )
    db.add(run)
    db.flush()
    for i, sc in enumerate(sset.scenarios):
        case = TestCase(project_id=project.id, dataset_version_id=None, position=i, input=_scenario_input(sc),
                        expected_output=sc.get("reference"), criteria=sc.get("criteria"), tags=sc.get("tags") or [],
                        case_metadata={"origin": "agent", "scenario": sc, "set": f"{sset.key}@{sset.version}"})
        db.add(case)
        db.flush()
        for o in outputs:
            db.add(TestResult(run_id=run.id, test_case_id=case.id, output_label=o["label"], source_model=o["model"],
                              output_source="agent", status="pending"))
    db.commit()
    return run


def rerun_body(run: TestRun) -> AgentRunIn:
    s = run.config_snapshot
    if run.judge_config_id is None:
        raise ApiError(409, "judge_deleted", "The judge used by this run was deleted; start a new run instead")
    return AgentRunIn(project_id=run.project_id, agent_ids=[o["agent_id"] for o in s["outputs"]],
                      scenario_set_id=s["scenario_set"]["id"], metrics=s["metrics"], judge_config_id=run.judge_config_id,
                      repeats=s["repeats"], use_cache=s.get("use_cache", True), budget_usd=s.get("budget_usd"),
                      run_name=run.run_name)

