"""TestMyLLM MCP tools: plain functions over the TestMyLLM HTTP API (no MCP imports, unit-testable).

Every tool takes a `client` with `request(method, path, body=None) -> parsed JSON` (see client.py) and
returns compact JSON-serialisable dicts meant for an AI assistant to read. Tools that start paid work
check the server's cost estimate first and refuse when it exceeds `max_estimated_usd`; the same value is
used as the run's budget cap unless `budget_usd` is given.
"""
from __future__ import annotations

import time
from typing import Any

TERMINAL = ("completed", "failed", "cancelled")
ENGINES = ("ragas", "deepeval")


class ToolError(Exception):
    """A user-facing problem (bad arguments, missing setup, cost guard). Message is shown to the assistant."""


# ------------------------------------------------------------------ helpers
def _project_id(client, project_id: int | None) -> int:
    if project_id:
        return project_id
    projects = client.request("GET", "/projects")
    if len(projects) == 1:
        return projects[0]["id"]
    names = ", ".join(f"{p['id']}={p['name']}" for p in projects)
    raise ToolError(f"Several projects are visible; pass project_id ({names})")


def _judge_id(client, pid: int, judge_config_id: int | None) -> int:
    if judge_config_id:
        return judge_config_id
    judges = client.request("GET", f"/projects/{pid}/judge-configs")
    if not judges:
        raise ToolError("This project has no judge model; add one under Settings → Evaluation Models")
    return next((j for j in judges if j.get("is_default")), judges[0])["id"]


def _metric_selection(client, metrics: list[str], engines: list[str] | None) -> list[dict]:
    catalog = {m["name"]: m for m in client.request("GET", "/metrics")}
    unknown = [m for m in metrics if m not in catalog]
    if unknown:
        raise ToolError(f"Unknown metric(s) {unknown}; available: {sorted(catalog)}")
    want = engines or list(ENGINES)
    out = []
    for m in metrics:
        impls = {i["engine"] for i in catalog[m].get("engines", [])}
        out += [{"metric": m, "engine": e} for e in want if e in impls]
    if not out:
        raise ToolError("None of the requested metric/engine combinations exist")
    return out


def _guard(est: dict, max_estimated_usd: float) -> None:
    total = est.get("total_usd")
    if total is not None and total > max_estimated_usd:
        raise ToolError(f"Estimated cost ${total:.4f} exceeds max_estimated_usd ${max_estimated_usd:.4f}; "
                        "raise the limit or reduce the work")


def _wait(client, run: dict, timeout_s: float, poll_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    while run["status"] not in TERMINAL:
        if time.monotonic() > deadline:
            run["note"] = f"Still {run['status']} after {timeout_s:.0f}s; call get_run({run['id']}) later"
            return run
        time.sleep(poll_s)
        run = client.request("GET", f"/test-runs/{run['id']}")
    return run


def _run_brief(run: dict) -> dict:
    decided = run["passed_tests"] + run["failed_tests"] + run["error_tests"]
    return {
        "run_id": run["id"], "name": run["run_name"], "type": run["run_type"], "status": run["status"],
        "status_reason": run.get("status_reason") or None,
        "progress": f"{run.get('completed_tests', 0)}/{run['total_tests']}",
        "passed": run["passed_tests"], "failed": run["failed_tests"], "errors": run["error_tests"],
        "pass_rate": round(run["passed_tests"] / decided, 4) if decided else None,
        "cost_usd": float(run["actual_cost_usd"]),
        "warnings": [w["message"] for w in run.get("warnings") or []],
        **({"note": run["note"]} if run.get("note") else {}),
    }


def _aggregates(item: dict) -> list[dict]:
    return [{
        "metric": a["metric_name"], "engine": a["engine"], "status": a["status"],
        "score": None if a["mean"] is None else round(a["mean"], 4),
        "std": None if a.get("std") is None else round(a["std"], 4), "threshold": a["threshold"],
        "reason": (a.get("reason") or "")[:400] or None,
    } for a in item["aggregates"]]


# ------------------------------------------------------------------ tools
def list_projects(client) -> list[dict]:
    """Projects this token can see, with their latest run."""
    return [{"project_id": p["id"], "name": p["name"], "role": p["role"], "last_run": p.get("last_run")}
            for p in client.request("GET", "/projects")]


def score_answers(client, question: str, answers: list[Any], reference: str | None = None,
                  context: list[str] | None = None, criteria: str | None = None, metrics: list[str] | None = None,
                  engines: list[str] | None = None, project_id: int | None = None, judge_config_id: int | None = None,
                  repeats: int = 1, max_estimated_usd: float = 0.25, timeout_s: float = 300, poll_s: float = 2) -> dict:
    """Score 1-5 answers to one question with Ragas and DeepEval (side by side, never averaged)."""
    if not answers or len(answers) > 5:
        raise ToolError("Give between 1 and 5 answers")
    outputs = []
    for i, a in enumerate(answers):
        label, text = (a.get("label") or f"Answer {i + 1}", a.get("text", "")) if isinstance(a, dict) else (f"Answer {i + 1}", str(a))
        if not text.strip():
            raise ToolError(f"{label} is empty")
        outputs.append({"label": label[:100], "source": "manual", "text": text})
    if metrics is None:
        metrics = ["answer_relevancy"] + (["faithfulness"] if context else []) + \
                  (["answer_correctness"] if reference else []) + (["custom_criteria"] if criteria else [])
    pid = _project_id(client, project_id)
    body = {
        "project_id": pid, "judge_config_id": _judge_id(client, pid, judge_config_id),
        "metrics": _metric_selection(client, metrics, engines), "repeats": repeats,
        "case": {"question": question, "reference": reference, "context": context, "criteria": criteria},
        "outputs": outputs, "budget_usd": max_estimated_usd,
    }
    _guard(client.request("POST", "/test-runs/estimate", body), max_estimated_usd)
    run = _wait(client, client.request("POST", "/test-runs", body), timeout_s, poll_s)
    results = client.request("GET", f"/test-runs/{run['id']}/results?page_size=10")["items"] if run["status"] in TERMINAL else []
    return {
        **_run_brief(run),
        "answers": [{"label": r["output_label"], "status": r["status"], "metrics": _aggregates(r),
                     **({"error": r["error_message"]} if r.get("error_message") else {})} for r in results],
        "note_engines": "Ragas and DeepEval use different methods; compare scores within an engine.",
    }


def run_evaluation(client, suite_id: int | None = None, dataset_version_id: int | None = None,
                   rerun_run_id: int | None = None, metrics: list[str] | None = None, engines: list[str] | None = None,
                   model: str | None = None, judge_config_id: int | None = None, repeats: int = 1,
                   project_id: int | None = None, max_estimated_usd: float = 1.0, budget_usd: float | None = None,
                   wait: bool = False, timeout_s: float = 900, poll_s: float = 5) -> dict:
    """Start a batch evaluation of a suite or dataset version (recorded answers, or generated by `model`),
    or repeat an earlier run exactly (`rerun_run_id`)."""
    if rerun_run_id:
        run = client.request("POST", f"/test-runs/{rerun_run_id}/rerun")
    else:
        if bool(suite_id) == bool(dataset_version_id):
            raise ToolError("Give exactly one of suite_id or dataset_version_id (or rerun_run_id)")
        pid = _project_id(client, project_id)
        if model:
            providers = client.request("GET", f"/projects/{pid}/providers")
            if not providers:
                raise ToolError("No provider configured to generate answers with")
            outputs = [{"label": model.split("/")[-1][:100], "source": "provider", "provider_id": providers[0]["id"],
                        "model": model, "max_tokens": 1000}]
        else:
            outputs = [{"label": "Recorded", "source": "dataset"}]
        body = {
            "project_id": pid, "judge_config_id": _judge_id(client, pid, judge_config_id),
            "metrics": _metric_selection(client, metrics or ["answer_relevancy", "answer_correctness"], engines),
            "outputs": outputs, "repeats": repeats, "budget_usd": budget_usd or max_estimated_usd,
            **({"suite_id": suite_id} if suite_id else {"dataset_version_id": dataset_version_id}),
        }
        est = client.request("POST", "/test-runs/estimate", body)
        _guard(est, max_estimated_usd)
        run = client.request("POST", "/test-runs", body)
        run["estimated_cost_usd"] = est.get("total_usd")
    if wait:
        run = _wait(client, run, timeout_s, poll_s)
    out = _run_brief(run)
    if run["status"] == "completed":
        out["metrics"] = _summary(client, run["id"])
    elif not wait:
        out["next"] = f"Call get_run({run['id']}) to follow progress"
    return out


def _summary(client, run_id: int) -> list[dict]:
    s = client.request("GET", f"/test-runs/{run_id}/summary")
    return [{"metric": m["metric_name"], "engine": m["engine"],
             "mean": None if m["mean"] is None else round(m["mean"], 4),
             "pass_rate": None if m["pass_rate"] is None else round(m["pass_rate"], 4),
             "threshold": m["threshold"], "n": m["n"], "errors": m["errors"], "not_applicable": m["not_applicable"]}
            for m in s["metrics"]]


def get_run(client, run_id: int, include_failures: bool = True, max_failures: int = 10) -> dict:
    """Status, per-metric summary and the failed cases (with judge reasons) of a run or security scan."""
    run = client.request("GET", f"/test-runs/{run_id}")
    out = _run_brief(run)
    if run["run_type"] == "security":
        rep = client.request("GET", f"/security-runs/{run_id}/report")
        out["security"] = {
            "attack_success_rate": rep["asr"], "breached": rep["breached"], "attacks": rep["attacks"],
            "severity_counts": rep["severity_counts"],
            "categories": [{"category": c["label"], "owasp": c["owasp"], "breached": c["breached"],
                            "attacks": c["attacks"], "asr": c["asr"]} for c in rep["categories"]],
            "findings": [{"severity": f["severity"], "attack": f["attack_name"], "owasp": f["owasp"],
                          "evidence": f["evidence"][:300], "remediation": f["remediation"]}
                         for f in rep["findings"][:max_failures]],
        }
        return out
    out["metrics"] = _summary(client, run_id)
    if include_failures:
        page = client.request("GET", f"/test-runs/{run_id}/results?status=failed,error&page_size={max_failures}")
        out["failures_total"] = page["total"]
        out["failures"] = [{
            "input": r["case"]["input"][:300], "answer_label": r["output_label"], "status": r["status"],
            "answer": (r.get("model_output") or "")[:300],
            "failed_metrics": [a for a in _aggregates(r) if a["status"] in ("failed", "error")],
            **({"error": r["error_message"][:300]} if r.get("error_message") else {}),
        } for r in page["items"]]
    return out


def compare_runs(client, candidate_run_id: int, baseline_id: int | None = None, baseline_run_id: int | None = None,
                 gate_id: int | None = None, allow_dataset_mismatch: bool = False, max_cases: int = 10) -> dict:
    """Regression report of a candidate run against a saved baseline, optionally with a quality gate verdict
    (preview only - nothing is recorded)."""
    cand = client.request("GET", f"/test-runs/{candidate_run_id}")
    if baseline_id is None:
        baselines = client.request("GET", f"/projects/{cand['project_id']}/baselines")
        if baseline_run_id is not None:
            match = [b for b in baselines if b["run_id"] == baseline_run_id]
            if not match:
                raise ToolError(f"Run {baseline_run_id} is not saved as a baseline; save it first in the app")
            baseline_id = match[0]["id"]
        elif len(baselines) == 1:
            baseline_id = baselines[0]["id"]
        else:
            raise ToolError("Pass baseline_id or baseline_run_id. Baselines: "
                            + ", ".join(f"{b['id']}={b['name']} (run {b['run_id']})" for b in baselines))
    gate = None
    if gate_id:
        ev = client.request("POST", f"/quality-gates/{gate_id}/evaluate", {
            "run_id": candidate_run_id, "baseline_id": baseline_id,
            "allow_dataset_mismatch": allow_dataset_mismatch, "record": False})
        rep, g = ev["report"], ev["gate"]
        gate = {"name": g["gate_name"], "passed": g["passed"],
                "checks": [f"{r['metric']} ({r['engine']}) {c['check']}: {'skipped' if c.get('skipped') else 'ok' if c['passed'] else 'FAIL'} - {c['detail']}"
                           for r in g["rules"] for c in r["checks"]]}
    else:
        rep = client.request("GET", f"/regressions/{candidate_run_id}?baseline_id={baseline_id}"
                                    f"&allow_dataset_mismatch={str(allow_dataset_mismatch).lower()}")

    def cases(xs):
        return [{"input": c["input"][:200], "label": c["label"], "baseline": c["baseline_status"],
                 "candidate": c["candidate_status"], "deltas": {k: round(v, 4) for k, v in c["metric_deltas"].items()}}
                for c in xs[:max_cases]]

    return {
        "candidate_run_id": candidate_run_id, "baseline_id": baseline_id, "paired_cases": rep["paired_cases"],
        "dataset_version_match": rep["dataset_version_match"],
        "metrics": [{"metric": m["metric_name"], "engine": m["engine"], "status": m["status"],
                     "baseline": m["baseline_mean"], "candidate": m["candidate_mean"], "delta": m["delta"],
                     "tolerance": m["tolerance"]} for m in rep["metrics"]],
        "regressed": cases(rep["regressed"]), "improved": cases(rep["improved"]),
        "unchanged": rep["unchanged_count"], "warnings": rep["warnings"], "gate": gate, "note": rep["note"],
    }
