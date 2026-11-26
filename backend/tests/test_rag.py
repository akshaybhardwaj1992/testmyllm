"""M3: RAG connectors (template, JSON paths, SSRF guard), live + offline RAG runs, failure origin."""
import asyncio
import json

import httpx
import pytest

from app.core.config import get_settings
from app.evaluators.base import status_for
from app.rag import connector as rc
from app.rag import ssrf
from app.schemas import MetricAggregate
from app.services.runs import failure_origin
from tests.test_m2 import upload

API = "/api/v1"
PUBLIC_IP = "93.184.216.34"


# ------------------------------------------------------------------ helpers
@pytest.fixture
def fake_dns(monkeypatch):
    table = {"rag.example.com": [PUBLIC_IP], "internal.example.com": ["10.0.0.5"], "evil.example.com": ["169.254.169.254"]}
    monkeypatch.setattr(ssrf, "resolver", lambda host, port: table.get(host, [PUBLIC_IP]))
    return table


@pytest.fixture
def rag_endpoint(monkeypatch, fake_dns):
    """A fake RAG app at https://rag.example.com/ask. Records requests; behaviour per question."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host != "rag.example.com":
            return httpx.Response(200, json={"leak": True})
        q = json.loads(request.content)["query"]["text"]
        if "boom" in q:
            return httpx.Response(500, text="kaboom")
        if "redirect" in q:
            return httpx.Response(302, headers={"location": "http://evil.example.com/latest/meta-data"})
        return httpx.Response(200, json={
            "data": {"answer": f"Answer to: {q}"},
            "sources": [{"text": "Retrieved chunk A"}, {"text": "Retrieved chunk B"}, {"meta": 1}],
        })

    monkeypatch.setattr(rc, "_transport", lambda: httpx.MockTransport(handler))
    return seen


CONNECTOR = {
    "name": "Support RAG",
    "endpoint_url": "https://rag.example.com/ask",
    "auth_header_name": "X-Api-Key",
    "auth_value": "secret-rag-key-123456",
    "request_template": '{"query": {"text": "{{question}}"}, "top_k": 3}',
    "response_mapping": {"answer_path": "data.answer", "contexts_path": "sources[*]", "context_text_path": "text"},
}


def agg(name, status, score=0.5):
    return MetricAggregate(
        metric_name=name, engine="ragas", engine_version="x", engine_metric_name=name, higher_is_better=True,
        threshold=0.7, status=status, mean=score if status in ("passed", "failed") else None, std=None, min=None,
        max=None, n=1, errors=0, reason=None, judge_model="m",
    )


# ------------------------------------------------------------------ units
def test_render_is_json_safe():
    out = rc.render('{"q": "{{question}}", "meta": ["Q: {{question}}"], "k": 3}', 'say "hi"\n')
    assert out == {"q": 'say "hi"\n', "meta": ['Q: say "hi"\n'], "k": 3}


def test_template_validation():
    with pytest.raises(ValueError, match="valid JSON"):
        rc.validate_template("{bad", "POST")
    with pytest.raises(ValueError, match="must contain"):
        rc.validate_template('{"q": "x"}', "POST")
    with pytest.raises(ValueError, match="flat JSON"):
        rc.validate_template('{"q": {"t": "{{question}}"}}', "GET")


def test_json_paths():
    doc = {"a": {"b": [{"t": "x"}, {"t": "y"}]}, "choices": [{"message": {"content": "hi"}}]}
    assert rc.extract(doc, "a.b[*].t") == ["x", "y"]
    assert rc.extract(doc, "choices[0].message.content") == ["hi"]
    assert rc.extract(doc, "a.missing") == []
    with pytest.raises(ValueError):
        rc.validate_path("a..b")


def test_map_response_handles_strings_objects_and_missing():
    cfg = rc.ConnectorConfig(endpoint_url="x", answer_path="answer", contexts_path="docs")
    a, ctx, warn = rc.map_response({"answer": "A", "docs": ["one", {"page_content": "two"}, {"x": None}]}, cfg)
    assert a == "A" and ctx == ["one", "two", '{"x": null}'] and warn == []
    a, ctx, warn = rc.map_response({"answer": "A"}, cfg)
    assert ctx == [] and "No contexts" in warn[0]
    with pytest.raises(rc.ConnectorError, match="No value at answer path"):
        rc.map_response({"result": "A"}, cfg)


@pytest.mark.parametrize("url,msg", [
    ("ftp://rag.example.com", "Only http"),
    ("https://user:pw@rag.example.com", "credentials"),
    ("http://127.0.0.1:8000/", "private or reserved"),
    ("http://[::1]/", "private or reserved"),
    ("http://internal.example.com/", "private or reserved"),
    ("http://evil.example.com/", "private or reserved"),  # cloud metadata (link-local)
    ("http://100.64.1.1/", "private or reserved"),  # CGNAT
    ("http://[::ffff:10.0.0.1]/", "private or reserved"),  # IPv4-mapped IPv6
])
def test_ssrf_blocks(url, msg, fake_dns, monkeypatch):
    monkeypatch.setattr(ssrf, "resolver", lambda h, p: [h.strip("[]")] if h[0].isdigit() or ":" in h else fake_dns.get(h, [PUBLIC_IP]))
    with pytest.raises(ssrf.BlockedDestination, match=msg):
        ssrf.check_url(url)


def test_ssrf_allowlist_and_private_override(fake_dns, monkeypatch):
    ssrf.check_url("https://rag.example.com/ask")
    s = get_settings()
    monkeypatch.setattr(s, "rag_host_allowlist", ".corp.example")
    with pytest.raises(ssrf.BlockedDestination, match="allow-list"):
        ssrf.check_url("https://rag.example.com/ask")
    monkeypatch.setattr(s, "rag_host_allowlist", "")
    monkeypatch.setattr(s, "rag_allow_private", True)
    ssrf.check_url("http://internal.example.com/")


def test_redirect_to_private_is_blocked(rag_endpoint):
    cfg = rc.ConnectorConfig(endpoint_url="https://rag.example.com/ask",
                             request_template='{"query": {"text": "{{question}}"}}', answer_path="data.answer")
    with pytest.raises(rc.ConnectorError, match="private or reserved"):
        asyncio.run(rc.call(cfg, "please redirect"))
    assert all(r.url.host == "rag.example.com" for r in rag_endpoint)  # never reached the metadata host


def test_failure_origin_heuristic():
    assert failure_origin([agg("context_recall", "failed"), agg("faithfulness", "passed")]) == "retrieval"
    assert failure_origin([agg("context_recall", "passed"), agg("faithfulness", "failed")]) == "generation"
    assert failure_origin([agg("context_precision", "failed"), agg("answer_correctness", "failed")]) == "both"
    assert failure_origin([agg("faithfulness", "failed")]) == "undetermined"  # retrieval not measured
    assert failure_origin([agg("context_recall", "passed"), agg("answer_relevancy", "failed")]) == "undetermined"
    assert failure_origin([agg("faithfulness", "passed")]) is None
    assert status_for(0.5, 0.7, True) == "failed"


# ------------------------------------------------------------------ API
def test_connector_crud_auth_is_write_only(setup, rag_endpoint):
    c, pid = setup["client"], setup["project"]["id"]
    conn = c.post(f"{API}/projects/{pid}/rag-connectors", json=CONNECTOR).json()
    assert conn["auth_hint"] == "••••3456" and "auth_value" not in conn and "secret-rag" not in json.dumps(conn)
    bad = c.post(f"{API}/projects/{pid}/rag-connectors", json={**CONNECTOR, "request_template": '{"q": 1}'})
    assert bad.status_code == 422
    # update without auth keeps the stored secret
    upd = c.put(f"{API}/rag-connectors/{conn['id']}", json={k: v for k, v in CONNECTOR.items() if k != "auth_value"} | {"name": "Renamed"}).json()
    assert upd["name"] == "Renamed" and upd["auth_hint"] == "••••3456"

    t = c.post(f"{API}/rag-connectors/test", json={**CONNECTOR, "project_id": pid, "connector_id": conn["id"],
                                                   "auth_value": None, "question": "refund window?"}).json()
    assert t["ok"] and t["answer"] == "Answer to: refund window?"
    assert t["contexts"] == ["Retrieved chunk A", "Retrieved chunk B"]
    assert "1 context item(s) had no text" in t["warnings"][0]
    assert rag_endpoint[-1].headers["x-api-key"] == "secret-rag-key-123456"  # stored secret used
    assert json.loads(rag_endpoint[-1].content) == {"query": {"text": "refund window?"}, "top_k": 3}

    err = c.post(f"{API}/rag-connectors/test", json={**CONNECTOR, "project_id": pid, "question": "boom"}).json()
    assert not err["ok"] and "HTTP 500" in err["error"]
    blocked = c.post(f"{API}/rag-connectors/test", json={**CONNECTOR, "endpoint_url": "http://127.0.0.1:9000/x",
                                                         "project_id": pid, "question": "q"}).json()
    assert not blocked["ok"] and "private or reserved" in blocked["error"]


def _dataset(setup, csv_text):
    c, pid = setup["client"], setup["project"]["id"]
    return upload(c, pid, csv_text, filename="rag.csv", dry_run="false").json()["dataset"]


RAG_CSV = (
    "question,reference,answer,context\n"
    "Refund window?,30 days,Thirty days.,Uploaded doc: 30 day refunds\n"
    "Shipping countries?,40 countries,We ship to 40.,Uploaded doc: 40 countries\n"
)


def test_live_rag_run_captures_retrieved_context_and_origin(setup, rag_endpoint, fake_judge, db):
    c, pid = setup["client"], setup["project"]["id"]
    ds = _dataset(setup, RAG_CSV)
    conn = c.post(f"{API}/projects/{pid}/rag-connectors", json=CONNECTOR).json()
    # recall fails for case 1 only; faithfulness always passes → origin "retrieval" for case 1
    fake_judge.scores[("context_recall", "ragas")] = [0.2, 0.9]
    body = {
        "project_id": pid, "run_type": "rag", "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "context_recall", "engine": "ragas"}, {"metric": "faithfulness", "engine": "ragas"}],
        "outputs": [{"label": "Support RAG", "source": "rag_api", "connector_id": conn["id"]}],
    }
    est = c.post(f"{API}/test-runs/estimate", json=body).json()
    assert est["applicable_metric_runs"] == 4
    run = c.post(f"{API}/test-runs", json=body).json()
    assert run["run_type"] == "rag" and "secret" not in json.dumps(run["config_snapshot"])
    assert run["config_snapshot"]["connectors"][str(conn["id"])]["endpoint_url"] == "https://rag.example.com/ask"

    items = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]
    first = next(i for i in items if i["case"]["input"] == "Refund window?")
    assert first["retrieved_context"] == ["Retrieved chunk A", "Retrieved chunk B"]  # captured on the result
    assert first["case"]["provided_context"] == ["Uploaded doc: 30 day refunds"]  # case untouched
    assert first["model_output"] == "Answer to: Refund window?" and first["output_source"] == "rag_api"
    assert first["failure_origin"] == "retrieval" and first["latency_ms"] is not None

    s = c.get(f"{API}/test-runs/{run['id']}/summary").json()
    assert s["failure_origins"] == {"retrieval": 1}
    f = c.get(f"{API}/test-runs/{run['id']}/results", params={"failure_origin": "retrieval"}).json()
    assert f["total"] == 1


def test_live_rag_never_substitutes_uploaded_context(setup, rag_endpoint, fake_judge, monkeypatch):
    """If the endpoint returns no contexts, context metrics are N/A, not run on the uploaded documents."""
    c, pid = setup["client"], setup["project"]["id"]
    ds = _dataset(setup, RAG_CSV)
    no_ctx = {**CONNECTOR, "response_mapping": {"answer_path": "data.answer", "contexts_path": "missing[*]"}}
    conn = c.post(f"{API}/projects/{pid}/rag-connectors", json=no_ctx).json()
    run = c.post(f"{API}/test-runs", json={
        "project_id": pid, "run_type": "rag", "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "faithfulness", "engine": "ragas"}, {"metric": "answer_correctness", "engine": "ragas"}],
        "outputs": [{"label": "RAG", "source": "rag_api", "connector_id": conn["id"]}],
    }).json()
    item = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"][0]
    assert item["retrieved_context"] == []
    faith = next(a for a in item["aggregates"] if a["metric_name"] == "faithfulness")
    assert faith["status"] == "not_applicable"
    assert ("faithfulness", "ragas", None) not in fake_judge.calls


def test_rag_endpoint_error_is_result_error(setup, rag_endpoint):
    c, pid = setup["client"], setup["project"]["id"]
    ds = _dataset(setup, "question,reference\nboom please,x\n")
    conn = c.post(f"{API}/projects/{pid}/rag-connectors", json=CONNECTOR).json()
    run = c.post(f"{API}/test-runs", json={
        "project_id": pid, "run_type": "rag", "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}],
        "outputs": [{"label": "RAG", "source": "rag_api", "connector_id": conn["id"]}],
    }).json()
    item = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"][0]
    assert item["status"] == "error" and item["error_message"].startswith("RAG endpoint: Endpoint returned HTTP 500")


def test_offline_rag_run_uses_provided_context(setup, fake_judge):
    c, pid = setup["client"], setup["project"]["id"]
    ds = _dataset(setup, RAG_CSV)
    fake_judge.scores[("faithfulness", "ragas")] = 0.3
    run = c.post(f"{API}/test-runs", json={
        "project_id": pid, "run_type": "rag", "judge_config_id": setup["judge"]["id"],
        "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "context_recall", "engine": "ragas"}, {"metric": "faithfulness", "engine": "ragas"}],
        "outputs": [{"label": "Recorded", "source": "dataset"}],
    }).json()
    items = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]
    assert {i["failure_origin"] for i in items} == {"generation"}


def test_rag_validation(setup):
    c, pid = setup["client"], setup["project"]["id"]
    base = {"project_id": pid, "judge_config_id": setup["judge"]["id"], "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}]}
    r = c.post(f"{API}/test-runs", json={**base, "case": {"question": "q"}, "run_type": "rag",
                                         "outputs": [{"label": "A", "source": "manual", "text": "x"}]})
    assert r.status_code == 422
    r = c.post(f"{API}/test-runs", json={**base, "dataset_version_id": 1,
                                         "outputs": [{"label": "A", "source": "rag_api", "connector_id": 1}]})
    assert r.status_code == 422  # rag_api needs run_type rag
