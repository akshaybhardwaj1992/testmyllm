"""M6: overview dashboard, trends, history export, retention + no-persistence mode, MCP tools."""
import csv
import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_m2 import imported_dataset
from tests.test_m5 import _baseline, _gate, _run

API = "/api/v1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp_server"))  # repo layout
sys.path.insert(0, "/mcp_server")  # container: -v ./mcp_server:/mcp_server


def _version(setup):
    return imported_dataset(setup)["version"]["id"]


def _set_scores(fake_judge, values):
    fake_judge.scores[("answer_relevancy", "ragas")] = values


# ------------------------------------------------------------------ overview & trends
def test_overview_empty_project(setup):
    o = setup["client"].get(f"{API}/projects/{setup['project']['id']}/overview").json()
    assert o["latest_run"] is None and o["spend_30d"] == 0 and o["open_gate_failures"] == 0
    assert o["top_failing"] == [] and o["latest_gate"] is None and o["security"] is None


def test_overview_kpis_gate_and_top_failing(setup, fake_judge):
    c, pid = setup["client"], setup["project"]["id"]
    vid = _version(setup)
    _set_scores(fake_judge, 0.9)
    first = _run(setup, vid)
    _set_scores(fake_judge, [0.9, 0.2, 0.2])  # 2 of 3 cases fail
    second = _run(setup, vid)
    base = _baseline(setup, first["id"])
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "min_pass_rate": 0.9}])
    ev = c.post(f"{API}/quality-gates/{gate['id']}/evaluate", json={"run_id": second["id"], "baseline_id": base["id"]}).json()
    assert ev["status"] == "failed"

    o = c.get(f"{API}/projects/{pid}/overview").json()
    assert o["latest_run"]["id"] == second["id"] and o["latest_run"]["pass_rate"] == pytest.approx(1 / 3)
    [m] = o["latest_run"]["metrics"]
    assert m["metric"] == "answer_relevancy" and m["mean"] == pytest.approx((0.9 + 0.2 + 0.2) / 3) and m["threshold"] == 0.7
    assert o["spend_30d"] > 0 and o["runs_30d"] == 2
    assert o["open_gate_failures"] == 1
    assert o["latest_gate"]["gate_name"] == "Release gate" and o["latest_gate"]["kind"] == "quality"
    assert len(o["top_failing"]) == 2 and all(f["failures"] == 1 and f["appearances"] == 2 for f in o["top_failing"])

    # a later passing evaluation of the same run/gate closes the open failure
    c.put(f"{API}/quality-gates/{gate['id']}", json={"project_id": pid, "name": "Release gate", "rules": [
        {"metric": "answer_relevancy", "engine": "ragas", "min_pass_rate": 0.3, "max_regression": None}]})
    c.post(f"{API}/quality-gates/{gate['id']}/evaluate", json={"run_id": second["id"], "baseline_id": base["id"]})
    assert c.get(f"{API}/projects/{pid}/overview").json()["open_gate_failures"] == 0

    t = c.get(f"{API}/projects/{pid}/trends").json()
    assert [p["run_id"] for p in t["points"]] == [first["id"], second["id"]]
    assert t["points"][0]["mean"] == pytest.approx(0.9) and t["points"][1]["threshold"] == 0.7
    assert c.get(f"{API}/projects/{pid}/trends?engine=deepeval").json()["points"] == []


def test_history_filters_and_csv(setup, fake_judge, db):
    from app.models import TestRun

    c, pid = setup["client"], setup["project"]["id"]
    vid = _version(setup)
    old, new = _run(setup, vid, run_name="=HYPERLINK(\"x\")"), _run(setup, vid, run_name="Fresh")
    db.get(TestRun, old["id"]).created_at = datetime.now(UTC) - timedelta(days=40)
    db.commit()
    since = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()
    listed = c.get(f"{API}/projects/{pid}/test-runs?date_from={since}").json()
    assert [r["id"] for r in listed["items"]] == [new["id"]]

    r = c.get(f"{API}/projects/{pid}/test-runs/export?run_type=llm")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert {row["run_id"] for row in rows} == {str(old["id"]), str(new["id"])}
    assert next(row for row in rows if row["run_id"] == str(old["id"]))["run_name"].startswith("'=")  # formula-safe
    assert rows[0]["answer_relevancy (ragas)"] and rows[0]["dataset"] == "FAQ v1"
    assert c.get(f"{API}/projects/{pid}/test-runs/export?date_from={since}").content.decode("utf-8-sig").count("\n") == 2


# ------------------------------------------------------------------ settings, no-persistence, retention
def test_project_settings_owner_only(setup):
    c, pid = setup["client"], setup["project"]["id"]
    assert c.get(f"{API}/projects/{pid}").json()["settings"] == {"retention_days": None, "no_persistence": False, "no_persistence_hours": 24}
    r = c.patch(f"{API}/projects/{pid}", json={"settings": {"retention_days": 30, "no_persistence": True, "no_persistence_hours": 2}})
    assert r.status_code == 200 and r.json()["settings"]["retention_days"] == 30
    assert c.patch(f"{API}/projects/{pid}", json={"settings": {"no_persistence_hours": 500}}).status_code == 422


def test_no_persistence_runs_expire_and_are_purged(setup, fake_judge, db, monkeypatch, tmp_path):
    from app.models import Export, JudgeCache, TestCase, TestRun
    from app.services import exports, retention

    c, pid = setup["client"], setup["project"]["id"]
    vid = _version(setup)
    keep = _run(setup, vid, use_cache=True)
    c.patch(f"{API}/projects/{pid}", json={"settings": {"no_persistence": True, "no_persistence_hours": 1}})
    cache_before = db.query(JudgeCache).count()
    eph = c.post(f"{API}/test-runs", json={
        "project_id": pid, "judge_config_id": setup["judge"]["id"], "case": {"question": "Q?"},
        "metrics": [{"metric": "answer_relevancy", "engine": "deepeval"}],
        "outputs": [{"label": "A", "source": "manual", "text": "Answer"}]}).json()
    assert eph["expires_at"] is not None
    assert db.query(JudgeCache).count() == cache_before  # nothing written to the judge cache
    ds_eph = _run(setup, vid, use_cache=True)
    assert c.post(f"{API}/baselines", json={"project_id": pid, "run_id": ds_eph["id"], "name": "x"}).status_code == 400
    # excluded from the overview and trends
    assert c.get(f"{API}/projects/{pid}/overview").json()["latest_run"]["id"] == keep["id"]

    monkeypatch.setattr(exports, "DATA_DIR", tmp_path)
    c.get(f"{API}/test-runs/{eph['id']}/export?format=json")
    path = Path(db.query(Export).filter_by(run_id=eph["id"]).one().path)
    assert path.exists()
    studio_case = db.get(TestRun, eph["id"]).config_snapshot["case_id"]

    assert retention.purge(db) == {"no_persistence": 0, "retention": 0}  # not yet expired
    out = retention.purge(db, now=datetime.now(UTC) + timedelta(hours=2))
    assert out == {"no_persistence": 2, "retention": 0}
    db.expire_all()
    assert db.get(TestRun, eph["id"]) is None and db.get(TestRun, keep["id"]) is not None
    assert db.get(TestCase, studio_case) is None and not path.exists()
    assert c.get(f"{API}/test-runs/{eph['id']}").status_code == 404


def test_retention_keeps_baselines_and_recent_runs(setup, fake_judge, db):
    from app.models import TestRun
    from app.services.retention import purge

    c, pid = setup["client"], setup["project"]["id"]
    vid = _version(setup)
    old_base, old_plain, recent = _run(setup, vid), _run(setup, vid), _run(setup, vid)
    _baseline(setup, old_base["id"])
    for rid in (old_base["id"], old_plain["id"]):
        db.get(TestRun, rid).created_at = datetime.now(UTC) - timedelta(days=100)
    db.commit()
    assert purge(db) == {"no_persistence": 0, "retention": 0}  # no retention configured
    c.patch(f"{API}/projects/{pid}", json={"settings": {"retention_days": 90}})
    assert purge(db) == {"no_persistence": 0, "retention": 1}
    db.expire_all()
    assert db.get(TestRun, old_plain["id"]) is None
    assert db.get(TestRun, old_base["id"]) is not None and db.get(TestRun, recent["id"]) is not None


# ------------------------------------------------------------------ MCP tools
class _ToolClient:
    """The MCP client contract over FastAPI's TestClient, with a bearer token (cookies restored after)."""

    def __init__(self, tc, token):
        self.tc, self.h = tc, {"Authorization": f"Bearer {token}"}

    def request(self, method, path, body=None):
        from testmyllm_mcp.tools import ToolError

        saved = dict(self.tc.cookies)
        self.tc.cookies.clear()
        try:
            r = self.tc.request(method, "/api/v1" + path, json=body, headers=self.h)
        finally:
            for k, v in saved.items():
                self.tc.cookies.set(k, v)
        if r.status_code >= 400:
            raise ToolError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None


@pytest.fixture
def mcp_client(setup):
    c, pid = setup["client"], setup["project"]["id"]
    token = c.post(f"{API}/projects/{pid}/api-tokens", json={"name": "MCP", "role": "editor"}).json()["token"]
    return _ToolClient(c, token)


def test_mcp_list_and_score_answers(setup, mcp_client, fake_judge):
    from testmyllm_mcp import tools

    [p] = tools.list_projects(mcp_client)
    assert p["project_id"] == setup["project"]["id"] and p["role"] == "editor"
    fake_judge.scores[("answer_correctness", "deepeval")] = [0.9, 0.1]
    out = tools.score_answers(mcp_client, "How long to return?", ["30 days.", "60 days."],
                              reference="30 days", poll_s=0)
    assert out["status"] == "completed" and len(out["answers"]) == 2
    first = {(m["metric"], m["engine"]): m for m in out["answers"][0]["metrics"]}
    assert set(first) == {("answer_relevancy", "ragas"), ("answer_relevancy", "deepeval"),
                          ("answer_correctness", "ragas"), ("answer_correctness", "deepeval")}
    assert out["answers"][1]["status"] == "failed"
    with pytest.raises(tools.ToolError, match="exceeds max_estimated_usd"):
        tools.score_answers(mcp_client, "Q?", ["A"], max_estimated_usd=0.0000001, poll_s=0)
    with pytest.raises(tools.ToolError, match="Unknown metric"):
        tools.score_answers(mcp_client, "Q?", ["A"], metrics=["vibes"], poll_s=0)


def test_mcp_run_get_and_compare(setup, mcp_client, fake_judge, db):
    from testmyllm_mcp import tools

    vid = _version(setup)
    _set_scores(fake_judge, 0.9)
    base_run = tools.run_evaluation(mcp_client, dataset_version_id=vid, metrics=["answer_relevancy"],
                                    engines=["ragas"], wait=True, poll_s=0)
    assert base_run["status"] == "completed" and base_run["metrics"][0]["mean"] == pytest.approx(0.9)
    base = _baseline(setup, base_run["run_id"])
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "max_regression": 0.05}])

    from app.models import JudgeCache

    db.query(JudgeCache).delete()  # the re-run would otherwise reuse the cached judge scores (by design)
    db.commit()
    _set_scores(fake_judge, [0.9, 0.2, 0.9])
    cand = tools.run_evaluation(mcp_client, rerun_run_id=base_run["run_id"], wait=True, poll_s=0)
    got = tools.get_run(mcp_client, cand["run_id"])
    assert got["failures_total"] == 1 and got["failures"][0]["failed_metrics"][0]["score"] == pytest.approx(0.2)

    cmp = tools.compare_runs(mcp_client, cand["run_id"], baseline_run_id=base_run["run_id"], gate_id=gate["id"])
    assert cmp["baseline_id"] == base["id"] and len(cmp["regressed"]) == 1 and cmp["gate"]["passed"] is False
    assert tools.compare_runs(mcp_client, cand["run_id"])["baseline_id"] == base["id"]  # only one baseline
    with pytest.raises(tools.ToolError, match="not saved as a baseline"):
        tools.compare_runs(mcp_client, cand["run_id"], baseline_run_id=cand["run_id"])
    with pytest.raises(tools.ToolError, match="exactly one"):
        tools.run_evaluation(mcp_client)


def test_mcp_server_registers_tools():
    pytest.importorskip("mcp")
    import asyncio

    from testmyllm_mcp.server import server

    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"list_projects", "score_answers", "run_evaluation", "get_run", "compare_runs"}
