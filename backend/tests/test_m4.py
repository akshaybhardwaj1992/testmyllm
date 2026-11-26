"""M4: model comparison (benchmark runs), saved model configs, side-by-side cases, re-run."""
import pytest

from app.providers import openrouter
from app.services.comparison import _percentile
from tests.test_m2 import imported_dataset

API = "/api/v1"


def _models(setup, *names):
    return [{"label": n.split("/")[-1], "source": "provider", "provider_id": setup["provider"]["id"], "model": n}
            for n in names]


def _bench(setup, **kw):
    ds = imported_dataset(setup)["dataset"]
    body = {
        "project_id": setup["project"]["id"], "run_type": "benchmark", "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}, {"metric": "answer_correctness", "engine": "ragas"}],
        "outputs": _models(setup, "openai/gpt-4o-mini", "anthropic/claude-sonnet-4.5"),
    }
    body.update(kw)
    return body


def test_percentile_nearest_rank():
    assert _percentile([], 50) is None
    assert _percentile([100], 95) == 100
    assert _percentile(list(range(1, 101)), 50) == 50 and _percentile(list(range(1, 101)), 95) == 95


def test_benchmark_comparison_end_to_end(setup, fake_judge, monkeypatch):
    c = setup["client"]
    latency = {"openai/gpt-4o-mini": [100, 200, 300], "anthropic/claude-sonnet-4.5": [900, 1000, 5000]}

    async def gen(api_key, model, prompt, params=None):
        return openrouter.Generation(f"{model}: {prompt[:20]}", 50, 40 if "gpt" in model else 80,
                                     0.0001 if "gpt" in model else 0.002, latency[model].pop(0))

    monkeypatch.setattr(openrouter, "generate", gen)
    # cycle per evaluation: gpt answers score high, claude low on relevancy
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.5]
    body = _bench(setup)
    est = c.post(f"{API}/test-runs/estimate", json=body).json()
    assert est["model_under_test"]["usd"] > 0 and any(w["type"] == "judge_bias" for w in est["warnings"])
    run = c.post(f"{API}/test-runs", json=body).json()
    assert run["run_type"] == "benchmark" and run["total_tests"] == 6

    cmp = c.get(f"{API}/test-runs/{run['id']}/comparison").json()
    by = {m["label"]: m for m in cmp["models"]}
    gpt, claude = by["gpt-4o-mini"], by["claude-sonnet-4.5"]
    assert gpt["results"] == 3 and gpt["latency_p50_ms"] == 200 and claude["latency_p95_ms"] == 5000
    assert abs(gpt["model_cost_usd"] - 0.0003) < 1e-9 and abs(claude["cost_per_case_usd"] - 0.002) < 1e-9
    assert gpt["avg_output_tokens"] == 40 and claude["output_tokens"] == 240
    rel = {m["label"]: next(x for x in m["metrics"] if x["metric_name"] == "answer_relevancy") for m in cmp["models"]}
    assert rel["gpt-4o-mini"]["mean"] != rel["claude-sonnet-4.5"]["mean"]
    assert gpt["quality"] is not None and cmp["metric_columns"][0] == {"metric": "answer_relevancy", "engine": "ragas"}
    assert gpt["judge_cost_usd"] > 0

    cases = c.get(f"{API}/test-runs/{run['id']}/cases", params={"page_size": 2}).json()
    assert cases["total"] == 3 and len(cases["items"]) == 2 and cases["labels"] == ["gpt-4o-mini", "claude-sonnet-4.5"]
    row = cases["items"][0]
    assert set(row["outputs"]) == {"gpt-4o-mini", "claude-sonnet-4.5"}
    assert row["outputs"]["gpt-4o-mini"]["model_output"].startswith("openai/gpt-4o-mini")
    q = c.get(f"{API}/test-runs/{run['id']}/cases", params={"q": "ship"}).json()
    assert q["total"] == 1


def test_disagree_only_filter(setup, fake_judge):
    c = setup["client"]
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.1]  # first model passes, second fails
    run = c.post(f"{API}/test-runs", json=_bench(setup, metrics=[{"metric": "answer_relevancy", "engine": "ragas"}])).json()
    all_cases = c.get(f"{API}/test-runs/{run['id']}/cases").json()["total"]
    split = c.get(f"{API}/test-runs/{run['id']}/cases", params={"disagree_only": True}).json()
    assert all_cases == 3 and split["total"] == 3
    for row in split["items"]:
        assert len({o["status"] for o in row["outputs"].values()}) == 2


def test_benchmark_validation(setup):
    c = setup["client"]
    body = _bench(setup)
    one = {**body, "outputs": body["outputs"][:1]}
    assert c.post(f"{API}/test-runs", json=one).status_code == 422
    dup = {**body, "outputs": [body["outputs"][0], {**body["outputs"][0], "label": "copy"}]}
    assert c.post(f"{API}/test-runs", json=dup).status_code == 422
    recorded = {**body, "outputs": [body["outputs"][0], {"label": "rec", "source": "dataset"}]}
    assert c.post(f"{API}/test-runs", json=recorded).status_code == 422


def test_model_configs(setup):
    c, pid = setup["client"], setup["project"]["id"]
    m = c.post(f"{API}/projects/{pid}/model-configs", json={
        "provider_id": setup["provider"]["id"], "name": "Cheap", "model": "openai/gpt-4o-mini", "temperature": 0.2}).json()
    assert m["max_tokens"] == 1000 and m["temperature"] == 0.2
    assert [x["name"] for x in c.get(f"{API}/projects/{pid}/model-configs").json()] == ["Cheap"]
    assert c.delete(f"{API}/model-configs/{m['id']}").status_code == 204
    bad = c.post(f"{API}/projects/{pid}/model-configs", json={"provider_id": 999, "name": "x", "model": "y"})
    assert bad.status_code == 400


@pytest.mark.parametrize("kind", ["benchmark", "studio"])
def test_rerun_repeats_the_same_configuration(setup, kind):
    c = setup["client"]
    if kind == "benchmark":
        first = c.post(f"{API}/test-runs", json=_bench(setup)).json()
    else:
        first = c.post(f"{API}/test-runs", json={
            "project_id": setup["project"]["id"], "judge_config_id": setup["judge"]["id"],
            "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}], "case": {"question": "Capital of France?"},
            "outputs": [{"label": "A", "source": "manual", "text": "Paris"}]}).json()
    again = c.post(f"{API}/test-runs/{first['id']}/rerun")
    assert again.status_code == 201, again.text
    second = again.json()
    s1, s2 = first["config_snapshot"], second["config_snapshot"]
    assert second["run_name"].endswith("(re-run)") and second["id"] != first["id"]
    assert s1["metrics"] == s2["metrics"] and s1["outputs"] == s2["outputs"] and s1.get("dataset") == s2.get("dataset")
    assert second["status"] == "completed"
    if kind == "studio":
        item = c.get(f"{API}/test-runs/{second['id']}/results").json()["items"][0]
        assert item["model_output"] == "Paris"


def test_rerun_after_judge_deleted(setup):
    c = setup["client"]
    first = c.post(f"{API}/test-runs", json=_bench(setup)).json()
    c.delete(f"{API}/judge-configs/{setup['judge']['id']}")
    r = c.post(f"{API}/test-runs/{first['id']}/rerun")
    assert r.status_code == 409 and r.json()["code"] == "judge_deleted"
