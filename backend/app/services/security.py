"""AI security testing service: targets + canaries, scan creation, reports and security gates (D29)."""
from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request, not_found
from app.models import (
    AttackSet,
    Canary,
    GateEvaluation,
    JudgeConfig,
    MetricResult,
    Project,
    Provider,
    RagConnector,
    SecurityFinding,
    SecurityGate,
    SecurityTarget,
    TestCase,
    TestResult,
    TestRun,
    User,
)
from app.providers import openrouter
from app.schemas import GateCheck, GateResult, GateRuleResult
from app.schemas.security import (
    SecurityCategoryOut,
    SecurityEstimateOut,
    SecurityFindingOut,
    SecurityReportOut,
    SecurityRunIn,
    SecurityTargetIn,
    SecurityTargetOut,
    SkippedAttack,
)
from app.security import CATEGORIES, OWASP_VERSION
from app.security.executor import ENGINE, METRIC, target_snapshot_prompt
from app.security.library import ensure_builtin
from app.services import estimate as est

_ALPHABET = string.ascii_uppercase + string.digits


def db_get_agent(t: SecurityTarget):
    """The target's agent config (agent targets only)."""
    if t.kind != "agent" or not t.agent_id:
        return None
    from sqlalchemy.orm import object_session

    from app.models import AgentConfig

    return object_session(t).get(AgentConfig, t.agent_id)


def new_canary() -> str:
    return "TML-" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


# ------------------------------------------------------------------ targets
def apply_target(db: Session, project: Project, t: SecurityTarget, body: SecurityTargetIn, user: User) -> None:
    if body.kind == "model":
        p = db.get(Provider, body.provider_id)
        if p is None or p.project_id != project.id:
            raise bad_request("Provider not found in this project")
    elif body.kind == "agent":
        from app.models import AgentConfig

        ag = db.get(AgentConfig, body.agent_id)
        if ag is None or ag.project_id != project.id:
            raise bad_request("Agent not found in this project")
    else:
        c = db.get(RagConnector, body.connector_id)
        if c is None or c.project_id != project.id:
            raise bad_request("RAG connector not found in this project")
        if body.identities and "{{user}}" not in c.request_template:
            raise bad_request("Identities need {{user}} in the connector's request template "
                              "(so each request runs as that test user)")
    changed_destination = (t.id is None or t.kind != body.kind or t.provider_id != body.provider_id
                           or t.model != body.model or t.connector_id != body.connector_id
                       or t.agent_id != body.agent_id)
    t.name = body.name.strip()
    t.kind = body.kind
    t.provider_id = body.provider_id if body.kind == "model" else None
    t.model = (body.model or "").strip() if body.kind == "model" else None
    t.params = {"max_tokens": body.max_tokens, "temperature": body.temperature}
    t.system_prompt = body.system_prompt
    t.connector_id = body.connector_id if body.kind == "rag" else None
    t.agent_id = body.agent_id if body.kind == "agent" else None
    t.identities = [i.model_dump() for i in body.identities]
    t.rate_limit_rpm = body.rate_limit_rpm
    # Authorization is re-confirmed on every save and recorded with who/when (guardrail).
    if changed_destination or t.authorized_at is None or t.authorization_note != body.authorization_note:
        t.authorized_by, t.authorized_at = user.id, datetime.now(UTC)
    t.authorization_note = body.authorization_note.strip()
    db.add(t)
    db.flush()
    _sync_canaries(db, t, body.plant_canary)


def _sync_canaries(db: Session, t: SecurityTarget, plant: bool) -> None:
    """Keep one canary per location: system prompt, document (RAG), and each identity."""
    existing = {c.location: c for c in db.scalars(select(Canary).where(Canary.target_id == t.id)).all()}
    wanted = set()
    if plant or t.kind == "rag":  # model and agent targets: planted on send; RAG: the user plants it
        wanted.add("system_prompt")
    if t.kind == "rag":
        wanted.add("document")
        wanted |= {f"identity:{i['name']}" for i in t.identities}
    for loc in wanted - existing.keys():
        db.add(Canary(target_id=t.id, value=new_canary(), location=loc))
    for loc in existing.keys() - wanted:
        db.delete(existing[loc])
    db.flush()


def canaries(db: Session, target_id: int) -> list[Canary]:
    return list(db.scalars(select(Canary).where(Canary.target_id == target_id).order_by(Canary.location)).all())


def target_out(db: Session, t: SecurityTarget) -> SecurityTargetOut:
    out = SecurityTargetOut.model_validate(t)
    out.canaries = canaries(db, t.id)
    if t.connector_id:
        c = db.get(RagConnector, t.connector_id)
        out.connector_name = c.name if c else None
    if t.agent_id:
        from app.models import AgentConfig

        ag = db.get(AgentConfig, t.agent_id)
        out.agent_name = ag.name if ag else None
    if t.authorized_by:
        u = db.get(User, t.authorized_by)
        out.authorized_by_email = u.email if u else None
    return out


def effective_prompt(db: Session, t: SecurityTarget) -> str:
    if t.kind == "agent":
        from app.models import AgentConfig

        ag = db.get(AgentConfig, t.agent_id)
        base = ag.system_prompt if ag else ""
        c = next((c for c in canaries(db, t.id) if c.location == "system_prompt"), None)
        return target_snapshot_prompt(base, c.value if c else None)
    if t.kind != "model":
        return t.system_prompt
    c = next((c for c in canaries(db, t.id) if c.location == "system_prompt"), None)
    return target_snapshot_prompt(t.system_prompt, c.value if c else None)


# ------------------------------------------------------------------ scan planning
def _attack_dict(a, s: AttackSet) -> dict:
    return {
        "id": a.id, "key": a.key, "name": a.name, "category": a.category, "technique": a.technique,
        "severity": a.severity, "owasp": a.owasp, "turns": a.turns, "document": a.document,
        "detector": a.detector, "marker": a.marker, "remediation": a.remediation,
        "set_key": s.key, "set_version": s.version,
    }


def _skip_reason(a: dict, t: SecurityTarget, cans: list[Canary], documents_planted: bool,
                 connector: RagConnector | None) -> str | None:
    locs = {c.location for c in cans}
    if a["category"] == "excessive_agency" or a["detector"] == "agency":
        ag = db_get_agent(t)
        if t.kind != "agent" or ag is None:
            return "Excessive-agency tests need an agent target (Agent Testing → Agents)"
        if not any(tool.get("side_effect") for tool in ag.tools):
            return "The agent has no tool marked as a side effect, so an unauthorized action could not be detected"
    if a["category"] == "cross_user_leakage":
        if t.kind != "rag":
            return "Cross-user tests need a RAG target with test identities"
        if len(t.identities) < 2 or not connector or "{{user}}" not in connector.request_template:
            return "Needs 2+ identities and {{user}} in the connector's request template"
    if a.get("document") and t.kind == "rag" and not documents_planted:
        return "Plant the poisoned documents in your index, then tick 'documents planted'"
    if a["detector"] == "canary":
        has_canary = bool(locs & {"system_prompt", "document"})
        ag = db_get_agent(t)
        overlap = a["category"] == "system_prompt_leakage" and (
            (t.kind == "model" and bool(t.system_prompt.strip())) or (ag is not None and bool(ag.system_prompt.strip())))
        if not has_canary and not overlap:
            return "No canary is planted in this target, so a leak could not be detected"
    return None


def plan(db: Session, project: Project, body: SecurityRunIn):
    ensure_builtin(db)
    t = db.get(SecurityTarget, body.target_id)
    if t is None or t.project_id != project.id:
        raise not_found("Security target")
    if t.authorized_at is None:
        raise ApiError(403, "target_not_authorized", "Confirm you are authorized to test this target first")
    connector = db.get(RagConnector, t.connector_id) if t.connector_id else None
    if t.kind == "rag" and connector is None:
        raise bad_request("The target's RAG connector was deleted")
    sets = []
    for sid in body.attack_set_ids:
        s = db.get(AttackSet, sid)
        if s is None or (s.project_id is not None and s.project_id != project.id):
            raise not_found("Attack set")
        if s.status != "ready":
            raise bad_request(f"Attack set '{s.name}' is {s.status}; wait for it to be ready or pick another")
        sets.append(s)
    cans = canaries(db, t.id)
    chosen, skipped = [], []
    for s in sets:
        for a in s.attacks:
            d = _attack_dict(a, s)
            if body.categories and d["category"] not in body.categories:
                continue
            why = _skip_reason(d, t, cans, body.documents_planted, connector)
            if why:
                skipped.append(SkippedAttack(attack=a.name, category=a.category, reason=why))
            else:
                chosen.append(d)
    judge = None
    if body.judge_config_id:
        judge = db.get(JudgeConfig, body.judge_config_id)
        if judge is None or judge.project_id != project.id:
            raise not_found("Judge config")
    return t, connector, sets, cans, chosen, skipped, judge


def estimate(db: Session, project: Project, body: SecurityRunIn) -> SecurityEstimateOut:
    t, connector, _, _, chosen, skipped, judge = plan(db, project, body)
    warnings: list[str] = []
    calls = sum(len(a["turns"]) for a in chosen) * body.attempts
    judge_calls = sum(1 for a in chosen if a["detector"] == "marker") * body.attempts if judge else 0
    usd: float | None = 0.0
    if t.kind in ("model", "agent"):
        model_id = t.model if t.kind == "model" else (db_get_agent(t).model if db_get_agent(t) else "")
        price = openrouter.model_price(model_id or "")
        if t.kind == "agent":
            warnings.append("Agent targets may take several tool steps per attack; the estimate assumes about 3.")
        if price is None:
            warnings.append(f"No price known for {model_id}; check the model id exists on OpenRouter.")
            usd = None
        else:
            sys_tok = est.tokens_of(effective_prompt(db, t))
            out_tok = (t.params.get("max_tokens") or 400) // 2
            for a in chosen:
                history = sys_tok + est.tokens_of(a.get("document") or "")
                for turn in a["turns"]:
                    history += est.tokens_of(turn)
                    usd += (history * price[0] + out_tok * price[1]) * body.attempts
                    history += out_tok
    else:
        warnings.append("RAG target: your endpoint's own model costs are not included. Canaries only detect "
                        "leaks once you have planted them (see the target's canary list).")
    if judge and usd is not None:
        jp = openrouter.model_price(judge.model)
        if jp is None:
            warnings.append(f"No price known for judge {judge.model}.")
            usd = None
        else:
            usd += judge_calls * (900 * jp[0] + 60 * jp[1])
    if not judge and any(a["detector"] == "marker" for a in chosen):
        warnings.append("No judge selected: any reply containing the code word counts as a breach, even a refusal "
                        "that quotes it. Add a judge to confirm code-word hits.")
    return SecurityEstimateOut(attacks=len(chosen), skipped=skipped, target_calls=calls, judge_calls_max=judge_calls,
                               total_usd=usd, warnings=warnings)


def create_run(db: Session, project: Project, body: SecurityRunIn, user: User) -> TestRun:
    t, connector, sets, cans, chosen, skipped, judge = plan(db, project, body)
    if not chosen:
        raise ApiError(400, "no_applicable_attacks", "None of the selected attacks can run against this target",
                       [s.model_dump() for s in skipped])
    e = estimate(db, project, body)
    label = t.name[:100]
    from app.services.agents import agent_snapshot

    ag = db_get_agent(t)
    snapshot = {
        "source": "security",
        "target": {
            "id": t.id, "name": t.name, "kind": t.kind, "provider_id": t.provider_id, "model": t.model,
            "params": t.params, "system_prompt": effective_prompt(db, t),
            "user_system_prompt": ag.system_prompt if ag else t.system_prompt,
            "agent": agent_snapshot(ag) if ag else None,
            "connector_id": t.connector_id, "identities": t.identities, "rate_limit_rpm": t.rate_limit_rpm,
            "authorized_by": t.authorized_by, "authorized_at": t.authorized_at.isoformat(),
            "authorization_note": t.authorization_note,
        },
        "connectors": {str(connector.id): {
            "name": connector.name, "endpoint_url": connector.endpoint_url, "method": connector.method,
            "request_template": connector.request_template, "response_mapping": connector.response_mapping,
        }} if connector else {},
        "canaries": [{"value": c.value, "location": c.location} for c in cans],
        "attempts": body.attempts,
        "repeats": body.attempts,
        "judge": {"id": judge.id, "name": judge.name, "provider_id": judge.provider_id, "model": judge.model,
                  "temperature": judge.temperature} if judge else None,
        "attack_sets": [{"id": s.id, "key": s.key, "version": s.version, "name": s.name} for s in sets],
        "categories": body.categories,
        "skipped": [s.model_dump() for s in skipped],
        "documents_planted": body.documents_planted,
        "budget_usd": body.budget_usd,
        "metrics": [{"metric": METRIC, "engine": ENGINE, "threshold": 0.5}],
        "outputs": [{"label": label, "source": "attack", "model": t.model}],
        "warnings": [],
    }
    from app.services.runs import ephemeral_expiry

    expires_at = ephemeral_expiry(project)
    snapshot["ephemeral"] = expires_at is not None
    run = TestRun(
        project_id=project.id, expires_at=expires_at,
        run_name=(body.run_name or "").strip() or f"Security scan · {t.name}",
        run_type="security", judge_config_id=judge.id if judge else None, repeats=body.attempts, use_cache=False,
        budget_usd=Decimal(str(body.budget_usd)) if body.budget_usd else None,
        estimated_cost_usd=Decimal(str(round(e.total_usd, 6))) if e.total_usd is not None else None,
        status="queued", total_tests=len(chosen), config_snapshot=snapshot,
        engine_versions={ENGINE: "1", "attack_library": ",".join(f"{s.key}@{s.version}" for s in sets)},
        created_by=user.id,
    )
    db.add(run)
    db.flush()
    for i, a in enumerate(chosen):
        case = TestCase(
            project_id=project.id, dataset_version_id=None, position=i, input="\n---\n".join(a["turns"]),
            provided_context=[a["document"]] if a.get("document") else None,
            case_metadata={"origin": "security", "attack": a}, tags=[a["category"]],
        )
        db.add(case)
        db.flush()
        db.add(TestResult(run_id=run.id, test_case_id=case.id, output_label=label, source_model=t.model or (
            connector.name if connector else None), output_source="attack", status="pending"))
    db.commit()
    return run


def rerun_body(run: TestRun) -> SecurityRunIn:
    s = run.config_snapshot
    return SecurityRunIn(
        project_id=run.project_id, target_id=s["target"]["id"], attack_set_ids=[a["id"] for a in s["attack_sets"]],
        categories=s.get("categories"), attempts=s["attempts"], judge_config_id=(s.get("judge") or {}).get("id"),
        budget_usd=s.get("budget_usd"), run_name=run.run_name, documents_planted=s.get("documents_planted", False),
    )


# ------------------------------------------------------------------ report
def report(db: Session, run: TestRun) -> SecurityReportOut:
    if run.run_type != "security":
        raise bad_request("Not a security run")
    rows = db.execute(
        select(TestResult.id, TestResult.status, TestCase.case_metadata)
        .join(TestCase, TestCase.id == TestResult.test_case_id).where(TestResult.run_id == run.id)
    ).all()
    attempts = dict(db.execute(
        select(MetricResult.test_result_id, func.count()).join(TestResult, TestResult.id == MetricResult.test_result_id)
        .where(TestResult.run_id == run.id, MetricResult.status.in_(("passed", "failed")))
        .group_by(MetricResult.test_result_id)).all())
    breaches = dict(db.execute(
        select(MetricResult.test_result_id, func.count()).join(TestResult, TestResult.id == MetricResult.test_result_id)
        .where(TestResult.run_id == run.id, MetricResult.status == "failed")
        .group_by(MetricResult.test_result_id)).all())
    per: dict[str, dict] = {}
    for rid, status, meta in rows:
        cat = meta["attack"]["category"]
        p = per.setdefault(cat, {"attacks": 0, "breached": 0, "errors": 0, "scored": 0, "att": 0, "succ": 0})
        if status in ("passed", "failed"):
            p["scored"] += 1
            p["breached"] += status == "failed"
        elif status == "error":
            p["errors"] += 1
        p["attacks"] += 1
        p["att"] += attempts.get(rid, 0)
        p["succ"] += breaches.get(rid, 0)
    cats = []
    for key, meta in CATEGORIES.items():
        if key not in per:
            continue
        p = per[key]
        cats.append(SecurityCategoryOut(
            category=key, label=meta["label"], owasp=meta["owasp"], attacks=p["attacks"], breached=p["breached"],
            errors=p["errors"], asr=p["breached"] / p["scored"] if p["scored"] else None,
            attempt_asr=p["succ"] / p["att"] if p["att"] else None,
        ))
    findings = db.scalars(select(SecurityFinding).where(SecurityFinding.run_id == run.id)).all()
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings = sorted(findings, key=lambda f: (order.get(f.severity, 9), f.category, f.id))
    sev = {k: 0 for k in order}
    for f in findings:
        sev[f.severity] = sev.get(f.severity, 0) + 1
    scored = sum(p["scored"] for p in per.values())
    breached = sum(p["breached"] for p in per.values())
    t = run.config_snapshot["target"]
    return SecurityReportOut(
        run_id=run.id, target={"id": t["id"], "name": t["name"], "kind": t["kind"], "model": t.get("model")},
        categories=cats, attacks=len(rows), breached=breached, asr=breached / scored if scored else None,
        severity_counts=sev, findings=[SecurityFindingOut.model_validate(f) for f in findings],
        skipped=[SkippedAttack(**s) for s in run.config_snapshot.get("skipped", [])], owasp_version=OWASP_VERSION,
    )


# ------------------------------------------------------------------ gates
def evaluate_gate(db: Session, gate: SecurityGate, run: TestRun) -> GateResult:
    rep = report(db, run)
    by_cat = {c.category: c for c in rep.categories}
    rules: list[GateRuleResult] = []
    for r in gate.rules:
        cat, max_asr = r["category"], r["max_asr"]
        if cat == "*":
            asr, label, n = rep.asr, "All categories", rep.attacks
        else:
            c = by_cat.get(cat)
            asr, label, n = (c.asr, c.label, c.attacks) if c else (None, CATEGORIES[cat]["label"], 0)
        if asr is None:
            check = GateCheck(check="max_asr", passed=True, skipped=True,
                              detail=f"no scored attacks in this category (limit {max_asr:.0%})")
        else:
            check = GateCheck(check="max_asr", passed=asr <= max_asr + 1e-9,
                              detail=f"attack success rate {asr:.0%} {'≤' if asr <= max_asr + 1e-9 else '>'} "
                                     f"{max_asr:.0%} over {n} attack(s)")
        rules.append(GateRuleResult(metric=label, engine="security", passed=check.passed, checks=[check]))
    for sev, limit in (("critical", gate.max_critical), ("high", gate.max_high)):
        if limit is None:
            continue
        count = rep.severity_counts.get(sev, 0)
        rules.append(GateRuleResult(metric=f"{sev.title()} findings", engine="security", passed=count <= limit, checks=[
            GateCheck(check=f"max_{sev}", passed=count <= limit, detail=f"{count} {sev} finding(s), limit {limit}")]))
    errors = sum(c.errors for c in rep.categories)
    if errors:
        rules.append(GateRuleResult(metric="Errored attacks", engine="security", passed=True, checks=[GateCheck(
            check="errors", passed=True, skipped=True,
            detail=f"{errors} attack(s) errored and were not scored: check the run before trusting this gate")]))
    return GateResult(gate_id=gate.id, gate_name=gate.name, passed=all(r.passed for r in rules), rules=rules)


def record(db: Session, gate: SecurityGate, run: TestRun, result: GateResult, user: User) -> GateEvaluation:
    ev = GateEvaluation(run_id=run.id, baseline_id=None, gate_id=None, security_gate_id=gate.id,
                        status="passed" if result.passed else "failed",
                        details={"gate": result.model_dump(), "kind": "security"}, created_by=user.id)
    db.add(ev)
    db.commit()
    return ev
