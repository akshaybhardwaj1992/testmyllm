"""M5: baselines, regression report (tolerance + standard error), quality gates, API tokens, testlab CLI."""
import json
import sys
from pathlib import Path

import pytest

from tests.test_m2 import imported_dataset, upload

API = "/api/v1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cli"))  # repo layout
sys.path.insert(0, "/cli")  # container: -v ./cli:/cli


# ------------------------------------------------------------------ helpers
def _run(setup, version_id, repeats=1, **kw):
    body = {
        "project_id": setup["project"]["id"], "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": version_id, "repeats": repeats, "use_cache": False,
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas", "threshold": 0.7}],
        "outputs": [{"label": "Bot", "source": "dataset"}],
    }
    body.update(kw)
    r = setup["client"].post(f"{API}/test-runs", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _baseline(setup, run_id, name="v1 release"):
    r = setup["client"].post(f"{API}/baselines", json={"project_id": setup["project"]["id"], "run_id": run_id, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def _gate(setup, rules, name="Release gate"):
    r = setup["client"].post(f"{API}/quality-gates", json={"project_id": setup["project"]["id"], "name": name, "rules": rules})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ baselines + regression
def test_baseline_requires_completed_dataset_run(setup, monkeypatch):
    c = setup["client"]
    studio = c.post(f"{API}/test-runs", json={
        "project_id": setup["project"]["id"], "judge_config_id": setup["judge"]["id"],
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}], "case": {"question": "q"},
        "outputs": [{"label": "A", "source": "manual", "text": "a"}]}).json()
    r = c.post(f"{API}/baselines", json={"project_id": setup["project"]["id"], "run_id": studio["id"], "name": "x"})
    assert r.status_code == 400 and "dataset-backed" in r.json()["message"]
    ds = imported_dataset(setup)["dataset"]
    b = _baseline(setup, _run(setup, ds["current_version"]["id"])["id"])
    assert b["dataset_name"] == "FAQ" and b["version_number"] == 1
    assert len(c.get(f"{API}/projects/{setup['project']['id']}/baselines").json()) == 1


def test_regression_report_flags_drop_beyond_tolerance(setup, fake_judge):
    c = setup["client"]
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    fake_judge.scores[("answer_relevancy", "ragas")] = 0.9
    base = _baseline(setup, _run(setup, vid)["id"])
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.6, 0.6]  # case 1 unchanged, 2-3 drop below 0.7
    cand = _run(setup, vid)
    rep = c.get(f"{API}/regressions/{cand['id']}", params={"baseline_id": base["id"]}).json()
    m = rep["metrics"][0]
    assert rep["paired_cases"] == 3 and rep["dataset_version_match"] is True
    assert m["status"] == "regressed" and abs(m["delta"] - (-0.2)) < 1e-9 and m["standard_error"] is None
    assert len(rep["regressed"]) == 2 and rep["unchanged_count"] == 1 and rep["improved"] == []
    assert rep["regressed"][0]["metric_deltas"]["answer_relevancy|ragas"] < 0
    # small drop within tolerance is not a regression
    fake_judge.scores[("answer_relevancy", "ragas")] = 0.87
    rep2 = c.get(f"{API}/regressions/{_run(setup, vid)['id']}", params={"baseline_id": base["id"]}).json()
    assert rep2["metrics"][0]["status"] == "unchanged"


def test_standard_error_suppresses_noise_with_repeats(setup, fake_judge):
    """With 3+ repeats, a drop beyond the tolerance but within judge noise (one SE) is not a regression."""
    c = setup["client"]
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.95, 0.55, 0.95]  # very noisy judge, mean 0.817
    base = _baseline(setup, _run(setup, vid, repeats=3)["id"])
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.85, 0.45, 0.85]  # mean 0.717: drop 0.1 > 0.05
    rep = c.get(f"{API}/regressions/{_run(setup, vid, repeats=3)['id']}", params={"baseline_id": base["id"]}).json()
    m = rep["metrics"][0]
    assert m["standard_error"] is not None and m["standard_error"] > 0.1
    assert -m["delta"] > m["tolerance"] and m["status"] == "unchanged"  # within noise
    # a quiet judge with the same drop IS a regression
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.91, 0.89]
    base2 = _baseline(setup, _run(setup, vid, repeats=3)["id"], name="quiet")
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.8, 0.81, 0.79]
    m2 = c.get(f"{API}/regressions/{_run(setup, vid, repeats=3)['id']}", params={"baseline_id": base2["id"]}).json()["metrics"][0]
    assert m2["status"] == "regressed" and m2["standard_error"] < 0.1


def test_dataset_version_mismatch_is_refused_unless_allowed(setup, fake_judge):
    c, pid = setup["client"], setup["project"]["id"]
    ds = imported_dataset(setup)["dataset"]
    base = _baseline(setup, _run(setup, ds["current_version"]["id"])["id"])
    first = c.get(f"{API}/dataset-versions/{ds['current_version']['id']}/cases").json()["items"][0]
    v2 = c.patch(f"{API}/test-cases/{first['id']}", json={"input": "Edited question?", "recorded_output": "Edited."}).json()["version"]
    cand = _run(setup, v2["id"])
    r = c.get(f"{API}/regressions/{cand['id']}", params={"baseline_id": base["id"]})
    assert r.status_code == 409 and r.json()["code"] == "dataset_version_mismatch"
    rep = c.get(f"{API}/regressions/{cand['id']}", params={"baseline_id": base["id"], "allow_dataset_mismatch": True}).json()
    assert rep["dataset_version_match"] is False and rep["paired_cases"] == 3  # case keys survive the edit
    assert any("Dataset versions differ" in w for w in rep["warnings"])


# ------------------------------------------------------------------ gates
def test_gate_rules_and_recorded_evaluation(setup, fake_judge):
    c = setup["client"]
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    fake_judge.scores[("answer_relevancy", "ragas")] = 0.9
    base = _baseline(setup, _run(setup, vid)["id"])
    gate = _gate(setup, [
        {"metric": "answer_relevancy", "engine": "ragas", "min_score": 0.8, "max_regression": 0.05, "min_pass_rate": 0.9},
        {"metric": "faithfulness", "engine": "ragas", "min_score": 0.5, "max_regression": None},
    ])
    assert gate["rules"][1]["max_regression"] is None
    cand = _run(setup, vid, metrics=[{"metric": "answer_relevancy", "engine": "ragas"}, {"metric": "faithfulness", "engine": "ragas"}])
    ev = c.post(f"{API}/quality-gates/{gate['id']}/evaluate", json={"run_id": cand["id"], "baseline_id": base["id"]}).json()
    assert ev["status"] == "passed" and ev["id"] is not None
    checks = {ch["check"] for r in ev["gate"]["rules"] for ch in r["checks"]}
    assert checks == {"min_score", "max_regression", "min_pass_rate"}

    fake_judge.scores[("answer_relevancy", "ragas")] = 0.75  # passes threshold 0.7 but below min_score 0.8
    cand2 = _run(setup, vid, metrics=[{"metric": "answer_relevancy", "engine": "ragas"}, {"metric": "faithfulness", "engine": "ragas"}])
    ev2 = c.post(f"{API}/quality-gates/{gate['id']}/evaluate", json={"run_id": cand2["id"], "baseline_id": base["id"]}).json()
    rule = ev2["gate"]["rules"][0]
    assert ev2["status"] == "failed" and not rule["passed"]
    assert {ch["check"]: ch["passed"] for ch in rule["checks"]} == {"min_score": False, "min_pass_rate": True, "max_regression": False}
    stored = c.get(f"{API}/test-runs/{cand2['id']}/gate-evaluations").json()
    assert stored[0]["status"] == "failed" and stored[0]["gate"]["gate_name"] == "Release gate"


def test_gate_without_baseline_skips_regression_and_missing_metric_fails(setup, fake_judge):
    c = setup["client"]
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "max_regression": 0.05},
                         {"metric": "bias", "engine": "deepeval", "min_score": 0.7}])
    run = _run(setup, vid)
    ev = c.post(f"{API}/quality-gates/{gate['id']}/evaluate", json={"run_id": run["id"], "record": False}).json()
    assert ev["id"] is None
    rel, bias = ev["gate"]["rules"]
    assert rel["passed"] and rel["checks"][0]["skipped"]
    assert not bias["passed"] and "No scored results" in bias["checks"][0]["detail"]


def test_gate_validation(setup):
    c, pid = setup["client"], setup["project"]["id"]
    assert c.post(f"{API}/quality-gates", json={"project_id": pid, "name": "x", "rules": [
        {"metric": "hallucination", "engine": "ragas", "min_score": 0.5}]}).status_code == 422
    assert c.post(f"{API}/quality-gates", json={"project_id": pid, "name": "x", "rules": [
        {"metric": "bias", "engine": "deepeval", "max_regression": None}]}).status_code == 422


# ------------------------------------------------------------------ API tokens
def test_api_token_scope_role_and_revocation(setup, db, client):
    from app.models import Project, ProjectMember

    c, pid = setup["client"], setup["project"]["id"]
    other = c.post(f"{API}/projects", json={"name": "Other"}).json()
    created = c.post(f"{API}/projects/{pid}/api-tokens", json={"name": "CI"}).json()
    token = created["token"]
    assert token.startswith("tml_") and created["prefix"] == token[:10]
    listed = c.get(f"{API}/projects/{pid}/api-tokens").json()
    assert "token" not in listed[0] and listed[0]["last_used_at"] is None

    c.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}
    assert [p["id"] for p in c.get(f"{API}/projects", headers=h).json()] == [pid]  # only its project
    assert c.get(f"{API}/projects/{other['id']}", headers=h).status_code == 404
    assert c.get(f"{API}/projects/{pid}", headers=h).json()["role"] == "editor"  # capped at token role
    assert c.post(f"{API}/projects/{pid}/providers", headers=h, json={"name": "x", "api_key": "sk-or-v1-zzzzzzzzzz"}).status_code == 403
    assert c.post(f"{API}/projects", headers=h, json={"name": "nope"}).status_code == 403
    assert c.post(f"{API}/projects/{pid}/api-tokens", headers=h, json={"name": "y"}).status_code == 403

    c.post(f"{API}/auth/login", json={"email": "qa@example.com", "password": "pw-123456"})
    assert c.get(f"{API}/projects/{pid}/api-tokens").json()[0]["last_used_at"] is not None
    c.delete(f"{API}/api-tokens/{created['id']}")
    c.cookies.clear()
    assert c.get(f"{API}/projects", headers=h).status_code == 401


def test_legacy_tl_tokens_still_authenticate(setup, db):
    """Tokens issued before the TestMyLLM rename used the tl_ prefix; they must keep working."""
    from app.api.deps import hash_token
    from app.models import ApiToken, User

    user = db.query(User).filter(User.email == "qa@example.com").one()
    legacy = "tl_legacy-token-issued-before-rename"
    db.add(ApiToken(project_id=setup["project"]["id"], name="old", token_hash=hash_token(legacy), prefix=legacy[:10],
                    role="viewer", created_by=user.id))
    db.commit()
    c = setup["client"]
    c.cookies.clear()
    r = c.get(f"{API}/projects", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 200 and [p["role"] for p in r.json()] == ["viewer"]


# ------------------------------------------------------------------ CLI (in-process against the real API)
class _TCClient:
    """testmyllm_cli.Client contract over FastAPI's TestClient."""

    def __init__(self, tc, token):
        self.tc, self.h = tc, {"Authorization": f"Bearer {token}"}

    def request(self, method, path, body=None, raw=False):
        from testmyllm_cli.main import CliError

        self.tc.cookies.clear()
        r = self.tc.request(method, "/api/v1" + path, json=body, headers=self.h)
        if r.status_code >= 400:
            raise CliError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.content if raw else (r.json() if r.content else None)


@pytest.fixture
def cli(setup):
    c, pid = setup["client"], setup["project"]["id"]
    token = c.post(f"{API}/projects/{pid}/api-tokens", json={"name": "CI"}).json()["token"]
    from testmyllm_cli.main import main

    client = _TCClient(c, token)
    return lambda *argv: main(list(argv), client=client)


def _config(setup, tmp_path):
    p = tmp_path / "run.json"
    p.write_text(json.dumps({
        "judge_config_id": setup["judge"]["id"], "use_cache": False,
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas", "threshold": 0.7}],
        "outputs": [{"label": "Bot", "source": "dataset"}],
    }))
    return str(p)


def test_cli_exit_codes(setup, cli, fake_judge, tmp_path, capsys, monkeypatch):
    c = setup["client"]
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    suite = c.post(f"{API}/test-suites", json={"project_id": setup["project"]["id"], "dataset_version_id": vid, "name": "S"}).json()
    fake_judge.scores[("answer_relevancy", "ragas")] = 0.9
    c.post(f"{API}/auth/login", json={"email": "qa@example.com", "password": "pw-123456"})
    base = _baseline(setup, _run(setup, vid)["id"])
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "min_score": 0.8, "max_regression": 0.05}])
    cfg = _config(setup, tmp_path)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    # 0: gate passes
    assert cli("run", "--suite", str(suite["id"]), "--config", cfg, "--baseline", str(base["id"]),
               "--gate", str(gate["id"]), "--fail-on-gate", "--poll", "0") == 0
    out = capsys.readouterr().out
    assert "Gate “Release gate”: PASSED" in out and "Estimated cost" in out
    assert "✅ passed" in summary.read_text(encoding="utf-8")

    # 1: regression fails the gate
    fake_judge.scores[("answer_relevancy", "ragas")] = 0.6
    report = tmp_path / "r.html"
    assert cli("run", "--suite", str(suite["id"]), "--config", cfg, "--baseline", str(base["id"]),
               "--gate", str(gate["id"]), "--fail-on-gate", "--poll", "0", "--report-html", str(report)) == 1
    assert b"TestMyLLM run report" in report.read_bytes()
    # same failure without --fail-on-gate reports but exits 0
    assert cli("run", "--suite", str(suite["id"]), "--config", cfg, "--gate", str(gate["id"]), "--poll", "0") == 0

    # 2: usage / infrastructure errors
    assert cli("run", "--suite", str(suite["id"]), "--poll", "0") == 2  # config missing judge/metrics/outputs
    assert cli("run", "--suite", "999999", "--config", cfg, "--poll", "0") == 2  # HTTP 404
    assert cli("run", "--suite", str(suite["id"]), "--config", cfg, "--fail-on-gate", "--poll", "0") == 2  # no --gate
    assert cli("bogus-command") == 2
    err = capsys.readouterr().err
    assert "error:" in err


def test_cli_rerun_and_gate_command(setup, cli, fake_judge, capsys):
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    first = _run(setup, vid)
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "min_score": 0.5}])
    assert cli("run", "--rerun", str(first["id"]), "--gate", str(gate["id"]), "--fail-on-gate", "--poll", "0", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["run"]["run_name"].endswith("(re-run)") and payload["gate_evaluation"]["status"] == "passed"
    assert cli("gate", "--run", str(first["id"]), "--gate", str(gate["id"]), "--fail-on-gate") == 0


def test_cli_survives_legacy_console_encoding(setup, cli, fake_judge, monkeypatch):
    """Windows cp1252 consoles cannot encode ≥ / curly quotes: output must degrade, not crash (which exited 1)."""
    import io

    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    run = _run(setup, vid)
    gate = _gate(setup, [{"metric": "answer_relevancy", "engine": "ragas", "min_score": 0.99}])
    cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", cp1252)
    assert cli("gate", "--run", str(run["id"]), "--gate", str(gate["id"]), "--fail-on-gate") == 1  # the gate, not a crash
    assert cli("gate", "--run", str(run["id"]), "--gate", str(gate["id"])) == 0


def test_cli_unexpected_error_is_exit_2(setup, cli, monkeypatch):
    import testmyllm_cli.main as m

    monkeypatch.setattr(m, "_report_gate", lambda *a, **k: 1 / 0)
    run = _run(setup, imported_dataset(setup)["dataset"]["current_version"]["id"])
    assert cli("gate", "--run", str(run["id"]), "--gate", "1") == 2


def test_cli_budget_cancel_is_exit_2(setup, cli, fake_judge, tmp_path):
    vid = imported_dataset(setup)["dataset"]["current_version"]["id"]
    fake_judge.cost = 1.0
    cfg = json.loads(open(_config(setup, tmp_path)).read())
    cfg["budget_usd"] = 0.5
    p = tmp_path / "b.json"
    p.write_text(json.dumps(cfg))
    assert cli("run", "--dataset-version", str(vid), "--config", str(p), "--poll", "0") == 2
