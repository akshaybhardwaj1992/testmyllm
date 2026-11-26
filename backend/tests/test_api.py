"""API tests for every M1 endpoint, plus run lifecycle guarantees (idempotency, recovery, budget, cache).

Celery runs eagerly, so POST /test-runs executes the whole run before returning.
"""
from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models import MetricResult, Provider, TestResult, TestRun
from app.services.execution import process_unit
from tests.conftest import make_user, run_payload

API = "/api/v1"


# ------------------------------------------------------------------ auth
def test_login_errors_are_consistent(client, user):
    r = client.post(f"{API}/auth/login", json={"email": "qa@example.com", "password": "wrong"})
    assert r.status_code == 401
    assert r.json() == {"code": "invalid_credentials", "message": "Email or password is incorrect", "details": None}


def test_endpoints_require_auth(client):
    r = client.get(f"{API}/projects")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


def test_login_sets_httponly_cookie_and_bearer_works(client, user):
    r = client.post(f"{API}/auth/login", json={"email": "QA@example.com", "password": "pw-123456"})
    assert r.status_code == 200
    assert "httponly" in r.headers["set-cookie"].lower()
    token = r.json()["token"]
    client.cookies.clear()
    assert client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email"] == "qa@example.com"
    client.post(f"{API}/auth/logout")
    assert client.get(f"{API}/auth/me").status_code == 401


def test_validation_error_shape(auth):
    r = auth.post(f"{API}/projects", json={"name": ""})
    assert r.status_code == 422 and r.json()["code"] == "validation_error" and r.json()["details"]


# ------------------------------------------------------------------ projects & access
def test_project_crud_and_isolation(auth, db, client):
    p = auth.post(f"{API}/projects", json={"name": "Alpha", "description": "d"}).json()
    assert p["role"] == "owner" and p["last_run"] is None
    assert [x["name"] for x in auth.get(f"{API}/projects").json()] == ["Alpha"]
    assert auth.patch(f"{API}/projects/{p['id']}", json={"name": "Beta"}).json()["name"] == "Beta"

    make_user(db, "other@example.com", "pw-other-1")
    auth.cookies.clear()
    auth.post(f"{API}/auth/login", json={"email": "other@example.com", "password": "pw-other-1"})
    assert auth.get(f"{API}/projects/{p['id']}").status_code == 404  # not enumerable
    assert auth.get(f"{API}/projects").json() == []


# ------------------------------------------------------------------ providers & judges
def test_provider_key_is_write_only_and_encrypted(setup, db):
    c, prov = setup["client"], setup["provider"]
    assert "api_key" not in prov and prov["key_hint"] == "••••cdef"
    row = db.get(Provider, prov["id"])
    assert "0123456789" not in row.encrypted_credentials
    assert decrypt_secret(row.encrypted_credentials) == "sk-or-v1-0123456789abcdef"
    listed = c.get(f"{API}/projects/{setup['project']['id']}/providers").text
    assert "0123456789" not in listed

    t = c.post(f"{API}/providers/{prov['id']}/test").json()
    assert t == {"ok": True, "message": "Connected (test)"}
    replaced = c.patch(f"{API}/providers/{prov['id']}", json={"api_key": "bad-key-000000"}).json()
    assert replaced["key_hint"] == "••••0000" and replaced["status"] == "untested"
    assert c.post(f"{API}/providers/{prov['id']}/test").json()["ok"] is False


def test_first_judge_is_default_and_default_is_exclusive(setup):
    c, pid, prov = setup["client"], setup["project"]["id"], setup["provider"]["id"]
    assert setup["judge"]["is_default"] is True
    j2 = c.post(f"{API}/projects/{pid}/judge-configs",
                json={"name": "Claude judge", "provider_id": prov, "model": "anthropic/claude-sonnet-4.5",
                      "is_default": True}).json()
    judges = {j["id"]: j["is_default"] for j in c.get(f"{API}/projects/{pid}/judge-configs").json()}
    assert judges == {setup["judge"]["id"]: False, j2["id"]: True}


def test_metric_catalog(auth):
    metrics = {m["name"]: m for m in auth.get(f"{API}/metrics").json()}
    assert metrics["hallucination"]["higher_is_better"] is True  # deepeval 4.x: 1 = no hallucination
    assert {e["engine"] for e in metrics["faithfulness"]["engines"]} == {"ragas", "deepeval"}
    assert "context" in metrics["faithfulness"]["engines"][0]["requires"]


# ------------------------------------------------------------------ estimate
def test_estimate_splits_costs_and_reports_na(setup):
    body = run_payload(setup, metrics=[
        {"metric": "answer_relevancy", "engine": "ragas"},
        {"metric": "faithfulness", "engine": "ragas"},
    ], outputs=[{"label": "GPT", "source": "provider", "provider_id": setup["provider"]["id"],
                 "model": "openai/gpt-4o-mini"}], repeats=3)
    e = setup["client"].post(f"{API}/test-runs/estimate", json=body).json()
    assert e["judge"]["usd"] > 0 and e["model_under_test"]["usd"] > 0
    assert e["applicable_metric_runs"] == 3  # 1 metric x 3 repeats; faithfulness is N/A
    assert e["not_applicable"] == ["Faithfulness (ragas): Needs context"]
    assert any(w["type"] == "judge_bias" for w in e["warnings"])  # gpt judge vs gpt model


# ------------------------------------------------------------------ runs
def _metrics(db, run_id):
    return db.scalars(
        select(MetricResult).join(TestResult).where(TestResult.run_id == run_id).order_by(MetricResult.id)
    ).all()


def test_single_answer_run_end_to_end(setup, db, fake_judge):
    c = setup["client"]
    r = c.post(f"{API}/test-runs", json=run_payload(setup))
    assert r.status_code == 201, r.text
    run = c.get(f"{API}/test-runs/{r.json()['id']}").json()
    assert run["status"] == "completed" and run["completed_tests"] == 1 and run["passed_tests"] == 1
    assert run["engine_versions"] == {"ragas": "0.0-test", "deepeval": "0.0-test", "testmyllm": "1"}
    assert run["config_snapshot"]["judge"]["model"] == "openai/gpt-4o-mini"
    assert float(run["actual_cost_usd"]) == 0.002  # 2 judge calls at 0.001

    res = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"][0]
    assert res["case"]["input"] == "What is the capital of France?"
    assert {(a["metric_name"], a["engine"], a["status"]) for a in res["aggregates"]} == {
        ("answer_relevancy", "ragas", "passed"), ("answer_relevancy", "deepeval", "passed")}
    assert all(m["judge_model"] == "openai/gpt-4o-mini" for m in res["metrics"])


def test_compare_two_answers_and_generated_output(setup, fake_judge):
    c = setup["client"]
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.4]
    body = run_payload(setup, metrics=[{"metric": "answer_relevancy", "engine": "ragas", "threshold": 0.5}], outputs=[
        {"label": "Claude", "source": "manual", "text": "Paris.", "source_model": "anthropic/claude-sonnet-4.5"},
        {"label": "ChatGPT", "source": "provider", "provider_id": setup["provider"]["id"], "model": "openai/gpt-4o"},
    ])
    run = c.post(f"{API}/test-runs", json=body).json()
    assert any("ChatGPT" in w["message"] for w in run["warnings"])  # judge is openai family
    items = {i["output_label"]: i for i in c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]}
    assert items["ChatGPT"]["model_output"] == "Generated by openai/gpt-4o"
    assert items["ChatGPT"]["latency_ms"] == 123 and items["ChatGPT"]["output_source"] == "provider"
    assert {items["Claude"]["status"], items["ChatGPT"]["status"]} == {"passed", "failed"}
    final = c.get(f"{API}/test-runs/{run['id']}").json()
    assert (final["passed_tests"], final["failed_tests"]) == (1, 1)


def test_not_applicable_metrics_are_stored_as_na_not_zero(setup, db):
    c = setup["client"]
    body = run_payload(setup, metrics=[
        {"metric": "answer_relevancy", "engine": "ragas"},
        {"metric": "context_recall", "engine": "ragas"},
    ])
    run = c.post(f"{API}/test-runs", json=body).json()
    na = [m for m in _metrics(db, run["id"]) if m.metric_name == "context_recall"][0]
    assert na.status == "not_applicable" and na.score is None and na.reason == "Needs context, reference answer"
    assert c.get(f"{API}/test-runs/{run['id']}").json()["passed_tests"] == 1


def test_run_with_no_applicable_metric_is_rejected(setup):
    r = setup["client"].post(f"{API}/test-runs", json=run_payload(
        setup, metrics=[{"metric": "faithfulness", "engine": "ragas"}]))
    assert r.status_code == 400 and r.json()["code"] == "no_applicable_metrics"


def test_repeats_give_variance_and_mean_drives_status(setup, fake_judge):
    c = setup["client"]
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.6, 0.8, 1.0]
    body = run_payload(setup, repeats=3, metrics=[{"metric": "answer_relevancy", "engine": "ragas", "threshold": 0.75}])
    run = c.post(f"{API}/test-runs", json=body).json()
    agg = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"][0]["aggregates"][0]
    assert agg["n"] == 3 and abs(agg["mean"] - 0.8) < 1e-9 and abs(agg["std"] - 0.2) < 1e-9
    assert (agg["min"], agg["max"], agg["status"]) == (0.6, 1.0, "passed")


def test_errors_are_excluded_from_averages(setup, fake_judge, db):
    c = setup["client"]
    fake_judge.errors.add(("answer_relevancy", "deepeval"))
    run = c.post(f"{API}/test-runs", json=run_payload(setup)).json()
    res = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"][0]
    de = next(a for a in res["aggregates"] if a["engine"] == "deepeval")
    assert de["status"] == "error" and de["mean"] is None and de["errors"] == 1
    assert "[REDACTED]" in de["reason"] and "abcdefgh" not in de["reason"]
    assert res["status"] == "error"
    assert c.get(f"{API}/test-runs/{run['id']}").json()["error_tests"] == 1


def test_judge_cache_hits_on_identical_rerun(setup, fake_judge, db):
    c = setup["client"]
    c.post(f"{API}/test-runs", json=run_payload(setup))
    calls_after_first = len(fake_judge.calls)
    second = c.post(f"{API}/test-runs", json=run_payload(setup)).json()
    assert len(fake_judge.calls) == calls_after_first  # no new judge calls
    rows = _metrics(db, second["id"])
    assert rows and all(m.cache_hit for m in rows)
    assert float(c.get(f"{API}/test-runs/{second['id']}").json()["actual_cost_usd"]) == 0
    third = c.post(f"{API}/test-runs", json=run_payload(setup, use_cache=False)).json()
    assert len(fake_judge.calls) == calls_after_first + 2 and not any(m.cache_hit for m in _metrics(db, third["id"]))


def test_budget_cap_stops_run(setup, fake_judge, db):
    c = setup["client"]
    fake_judge.cost = 0.05
    body = run_payload(setup, repeats=5, budget_usd=0.08, use_cache=False)
    run = c.post(f"{API}/test-runs", json=body).json()
    final = c.get(f"{API}/test-runs/{run['id']}").json()
    assert final["status"] == "cancelled" and final["status_reason"] == "budget_cap_reached"
    assert len(_metrics(db, run["id"])) == 2  # one repeat batch (2 metrics) then the cap halted it
    assert db.scalar(select(TestResult.status).where(TestResult.run_id == run["id"])) == "cancelled"


def test_cancel_run(setup, db, monkeypatch):
    from app.services import runs as svc

    monkeypatch.setattr(svc, "enqueue_run", lambda db, run: None)  # leave it queued
    c = setup["client"]
    run = c.post(f"{API}/test-runs", json=run_payload(setup)).json()
    assert run["status"] == "queued"
    out = c.post(f"{API}/test-runs/{run['id']}/cancel").json()
    assert out["status"] == "cancelled" and out["status_reason"] == "cancelled_by_user"
    # a unit delivered after cancellation does nothing
    rid = db.scalar(select(TestResult.id).where(TestResult.run_id == run["id"]))
    process_unit(rid)
    assert _metrics(db, run["id"]) == []


def test_unit_is_idempotent_and_resumes_after_worker_death(setup, fake_judge, db, monkeypatch):
    """Simulate a worker killed mid-unit: some metric rows saved, unit still 'running'. Re-delivery must
    evaluate only the missing work and never duplicate rows or cost."""
    from app.services import runs as svc

    monkeypatch.setattr(svc, "enqueue_run", lambda db, run: None)
    c = setup["client"]
    run = c.post(f"{API}/test-runs", json=run_payload(setup, repeats=2, use_cache=False)).json()
    rid = db.scalar(select(TestResult.id).where(TestResult.run_id == run["id"]))
    process_unit(rid)
    assert len(fake_judge.calls) == 4
    db.expire_all()

    # roll back to a "crashed half-way" state: drop repeat 1 rows, mark unit running again
    for m in _metrics(db, run["id"]):
        if m.repeat_index == 1:
            db.delete(m)
    r = db.get(TestResult, rid)
    r.status = "running"
    t = db.get(TestRun, run["id"])
    t.status = "running"
    cost_before = t.actual_cost_usd
    db.commit()

    process_unit(rid)  # redelivery
    process_unit(rid)  # duplicate delivery
    db.expire_all()
    rows = _metrics(db, run["id"])
    assert len(rows) == 4 and len({(m.metric_name, m.engine, m.repeat_index) for m in rows}) == 4
    assert len(fake_judge.calls) == 6  # only the 2 missing evaluations re-ran
    t = db.get(TestRun, run["id"])
    assert t.status == "completed" and float(t.actual_cost_usd) == float(cost_before) + 0.002


def test_runs_list_filters_and_pagination(setup):
    c, pid = setup["client"], setup["project"]["id"]
    for i in range(3):
        c.post(f"{API}/test-runs", json=run_payload(setup, run_name=f"run {i}"))
    page = c.get(f"{API}/projects/{pid}/test-runs", params={"page_size": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["items"][0]["run_name"] == "run 2"
    assert c.get(f"{API}/projects/{pid}/test-runs", params={"q": "run 1"}).json()["total"] == 1
    assert c.get(f"{API}/projects/{pid}/test-runs", params={"status": "queued"}).json()["total"] == 0
    proj = c.get(f"{API}/projects/{pid}").json()
    assert proj["last_run"]["pass_rate"] == 1.0


def test_viewer_cannot_start_runs(setup, db):
    from app.models import ProjectMember

    other = make_user(db, "viewer@example.com", "pw-viewer-1")
    db.add(ProjectMember(project_id=setup["project"]["id"], user_id=other.id, role="viewer"))
    db.commit()
    c = setup["client"]
    c.cookies.clear()
    c.post(f"{API}/auth/login", json={"email": "viewer@example.com", "password": "pw-viewer-1"})
    assert c.get(f"{API}/projects/{setup['project']['id']}").status_code == 200
    r = c.post(f"{API}/test-runs", json=run_payload(setup))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
