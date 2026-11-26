"""M2: datasets (upload, mapping, validation, versions), test cases, suites, dataset runs, results paging,
summary, human review + agreement, exports, members."""
import csv
import io
import json

import pytest

from app.datasets import parsing
from tests.conftest import make_user

API = "/api/v1"

CSV_OK = (
    "question,reference,answer,context,tags,source\n"
    "What is the refund window?,30 days,Thirty days.,Refunds within 30 days.,policy;refunds,faq\n"
    "Do you ship abroad?,Yes to 40 countries,We ship to 40 countries.,,shipping,faq\n"
    "=HYPERLINK(\"http://x\"),n/a,ok,,,web\n"
)
CSV_BAD = "question,reference\nGood question,ref\n,missing input\nAnother,ref\n"


# ------------------------------------------------------------------ parsing units
def test_suggest_mapping_and_context_parsing():
    m = parsing.suggest_mapping(["Question", "reference", "Answer", "Contexts", "labels", "extra"])
    assert m == {"input": "Question", "expected_output": "reference", "response": "Answer",
                 "context": "Contexts", "criteria": None, "tags": "labels"}
    assert parsing.parse_context('["a", "b"]') == ["a", "b"]
    assert parsing.parse_context("a\n\nb\n") == ["a", "b"]
    assert parsing.parse_context(["x", " ", None]) == ["x"]
    assert parsing.parse_tags("a; b,a | c") == ["a", "b", "c"]


def test_validation_report_uses_file_row_numbers():
    parsed = parsing.parse_file("d.csv", CSV_BAD.encode())
    rep = parsing.normalize(parsed, {"input": "question", "expected_output": "reference"}, [])
    assert len(rep.valid) == 2
    assert [e.as_dict() for e in rep.errors] == [{"row": 3, "column": "question", "message": "Question / input is empty"}]


def test_json_and_jsonl_and_bad_files():
    assert parsing.parse_file("d.json", b'{"rows":[{"q":"a"},{"q":"b"}]}').columns == ["q"]
    assert len(parsing.parse_file("d.jsonl", b'{"q":"a"}\n\n{"q":"b"}\n').rows) == 2
    for name, data, msg in [
        ("d.txt", b"x", "Unsupported"), ("d.json", b"{bad", "Invalid JSON"), ("d.csv", b"\xff\xfe\x00", "UTF-8"),
        ("d.json", b'{"a":1}', "list of objects"),
    ]:
        with pytest.raises(parsing.ParseError, match=msg):
            parsing.parse_file(name, data)


def test_csv_safe():
    assert parsing.csv_safe("=1+1") == "'=1+1" and parsing.csv_safe("-x") == "'-x" and parsing.csv_safe(0.5) == 0.5


# ------------------------------------------------------------------ helpers
def upload(c, pid, data=CSV_OK, filename="faq.csv", **form):
    fields = {"project_id": str(pid), **{k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v)) for k, v in form.items()}}
    return c.post(f"{API}/datasets/upload", data=fields, files={"file": (filename, data.encode(), "text/csv")})


def imported_dataset(setup):
    c, pid = setup["client"], setup["project"]["id"]
    r = upload(c, pid, dry_run="false", name="FAQ", metadata_columns=["source"])
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ upload flow
def test_upload_dry_run_then_import(setup):
    c, pid = setup["client"], setup["project"]["id"]
    dry = upload(c, pid).json()
    assert dry["imported"] is False and dry["columns"] == ["question", "reference", "answer", "context", "tags", "source"]
    assert dry["suggested_mapping"]["response"] == "answer"
    assert dry["report"]["valid_rows"] == 3 and dry["report"]["fields_present"] == ["context", "reference", "response"]

    out = imported_dataset(setup)
    assert out["imported"] and out["version"]["version_number"] == 1 and out["version"]["row_count"] == 3
    ds = out["dataset"]
    cases = c.get(f"{API}/dataset-versions/{ds['current_version']['id']}/cases").json()
    assert cases["total"] == 3 and cases["tags"] == ["policy", "refunds", "shipping"]
    first = cases["items"][0]
    assert first["recorded_output"] == "Thirty days." and first["provided_context"] == ["Refunds within 30 days."]
    assert first["metadata"] == {"source": "faq"} and first["position"] == 0
    assert c.get(f"{API}/dataset-versions/{ds['current_version']['id']}/cases", params={"tag": "shipping"}).json()["total"] == 1


def test_upload_with_invalid_rows_needs_explicit_skip(setup):
    c, pid = setup["client"], setup["project"]["id"]
    r = upload(c, pid, CSV_BAD, dry_run="false")
    assert r.status_code == 400 and r.json()["code"] == "invalid_rows"
    assert r.json()["details"]["errors"][0]["row"] == 3
    ok = upload(c, pid, CSV_BAD, dry_run="false", skip_invalid="true").json()
    assert ok["version"]["row_count"] == 2


def test_upload_mapping_problems(setup):
    c, pid = setup["client"], setup["project"]["id"]
    dry = upload(c, pid, mapping={"input": "nope"}).json()
    assert dry["report"] is None and "Column 'nope' is not in the file." in dry["mapping_problems"]
    r = upload(c, pid, mapping={"input": None}, dry_run="false")
    assert r.status_code == 400 and r.json()["code"] == "invalid_mapping"
    bad = c.post(f"{API}/datasets/upload", data={"project_id": str(pid)}, files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_file"


def test_reupload_creates_new_version(setup):
    c, pid = setup["client"], setup["project"]["id"]
    ds = imported_dataset(setup)["dataset"]
    v2 = upload(c, pid, CSV_BAD, dry_run="false", skip_invalid="true", dataset_id=ds["id"]).json()
    assert v2["version"]["version_number"] == 2 and v2["dataset"]["version_count"] == 2
    versions = c.get(f"{API}/datasets/{ds['id']}/versions").json()
    assert [v["version_number"] for v in versions] == [2, 1]


# ------------------------------------------------------------------ case edits create versions
def test_case_edits_create_versions_and_keep_case_key(setup):
    c = setup["client"]
    ds = imported_dataset(setup)["dataset"]
    v1 = ds["current_version"]["id"]
    case = c.get(f"{API}/dataset-versions/{v1}/cases").json()["items"][0]

    edited = c.patch(f"{API}/test-cases/{case['id']}", json={"input": "Refund window?", "tags": ["policy"]}).json()
    assert edited["version"]["version_number"] == 2
    assert edited["case"]["case_key"] == case["case_key"] and edited["case"]["id"] != case["id"]
    assert edited["case"]["recorded_output"] is None  # PATCH replaces the case content
    # v1 is untouched (immutable)
    assert c.get(f"{API}/test-cases/{case['id']}").json()["input"] == "What is the refund window?"
    # editing an old version is a conflict
    stale = c.patch(f"{API}/test-cases/{case['id']}", json={"input": "x"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    v2 = edited["version"]["id"]
    added = c.post(f"{API}/test-cases", json={"dataset_id": ds["id"], "base_version_id": v2, "input": "New?"}).json()
    assert added["version"]["version_number"] == 3 and added["version"]["row_count"] == 4
    deleted = c.delete(f"{API}/test-cases/{added['case']['id']}").json()
    assert deleted["version"]["version_number"] == 4 and deleted["version"]["row_count"] == 3


# ------------------------------------------------------------------ suites
def test_suites_all_tags_manual(setup):
    c, pid = setup["client"], setup["project"]["id"]
    ds = imported_dataset(setup)["dataset"]
    vid = ds["current_version"]["id"]
    ids = [x["id"] for x in c.get(f"{API}/dataset-versions/{vid}/cases").json()["items"]]
    base = {"project_id": pid, "dataset_version_id": vid}
    assert c.post(f"{API}/test-suites", json={**base, "name": "All"}).json()["case_count"] == 3
    tagged = c.post(f"{API}/test-suites", json={**base, "name": "Policy", "selection": "tags", "tags": ["policy"]}).json()
    assert tagged["case_count"] == 1 and tagged["selection"] == "tags" and tagged["tags"] == ["policy"]
    manual = c.post(f"{API}/test-suites", json={**base, "name": "Pick", "selection": "manual", "case_ids": ids[:2]}).json()
    assert manual["case_count"] == 2 and manual["is_latest_version"] is True
    stray = c.post(f"{API}/test-suites", json={**base, "name": "x", "selection": "manual", "case_ids": [999999]})
    assert stray.status_code == 400
    assert len(c.get(f"{API}/projects/{pid}/test-suites").json()) == 3


# ------------------------------------------------------------------ dataset runs
def _suite(setup):
    c, pid = setup["client"], setup["project"]["id"]
    ds = imported_dataset(setup)["dataset"]
    return c.post(f"{API}/test-suites", json={
        "project_id": pid, "dataset_version_id": ds["current_version"]["id"], "name": "Regression"}).json()


def _run_body(setup, suite, **kw):
    body = {
        "project_id": setup["project"]["id"], "judge_config_id": setup["judge"]["id"], "suite_id": suite["id"],
        "metrics": [{"metric": "answer_relevancy", "engine": "ragas"}, {"metric": "faithfulness", "engine": "deepeval"}],
        "outputs": [{"label": "Prod bot", "source": "dataset"}],
    }
    body.update(kw)
    return body


def test_suite_run_with_recorded_responses(setup, fake_judge):
    c = setup["client"]
    suite = _suite(setup)
    fake_judge.scores[("answer_relevancy", "ragas")] = [0.9, 0.3, 0.8]
    run = c.post(f"{API}/test-runs", json=_run_body(setup, suite)).json()
    assert run["total_tests"] == 3 and run["config_snapshot"]["dataset"]["version_number"] == 1
    assert run["run_name"] == "Regression · Prod bot"
    final = c.get(f"{API}/test-runs/{run['id']}").json()
    assert final["status"] == "completed" and (final["passed_tests"], final["failed_tests"]) == (2, 1)

    page = c.get(f"{API}/test-runs/{run['id']}/results", params={"page_size": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 2 and page["labels"] == ["Prod bot"]
    failed = c.get(f"{API}/test-runs/{run['id']}/results", params={"status": "failed,error"}).json()
    assert failed["total"] == 1
    # faithfulness is N/A where the case has no context
    na = c.get(f"{API}/test-runs/{run['id']}/results",
               params={"metric": "faithfulness", "metric_status": "not_applicable"}).json()
    assert na["total"] == 2
    q = c.get(f"{API}/test-runs/{run['id']}/results", params={"q": "ship"}).json()
    assert q["total"] == 1

    s = c.get(f"{API}/test-runs/{run['id']}/summary").json()
    ar = next(m for m in s["metrics"] if m["metric_name"] == "answer_relevancy")
    assert ar["n"] == 3 and ar["passed"] == 2 and abs(ar["pass_rate"] - 2 / 3) < 1e-9
    assert sum(b["count"] for b in ar["histogram"]) == 3 and ar["histogram"][9]["count"] == 1  # 0.9
    fa = next(m for m in s["metrics"] if m["metric_name"] == "faithfulness")
    assert fa["n"] == 1 and fa["not_applicable"] == 2
    assert s["status_counts"] == {"passed": 2, "failed": 1}


def test_dataset_run_by_version_with_generated_answers(setup):
    c = setup["client"]
    ds = imported_dataset(setup)["dataset"]
    body = _run_body(setup, {"id": 0}, outputs=[
        {"label": "GPT", "source": "provider", "provider_id": setup["provider"]["id"], "model": "openai/gpt-4o"}])
    body.pop("suite_id")
    body["dataset_version_id"] = ds["current_version"]["id"]
    est = c.post(f"{API}/test-runs/estimate", json=body).json()
    assert est["model_under_test"]["usd"] is None  # gpt-4o has no test price → unknown, not zero
    run = c.post(f"{API}/test-runs", json=body).json()
    items = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]
    assert {i["model_output"] for i in items} == {"Generated by openai/gpt-4o"}


def test_dataset_run_source_validation(setup):
    c = setup["client"]
    suite = _suite(setup)
    r = c.post(f"{API}/test-runs", json=_run_body(setup, suite, outputs=[{"label": "A", "source": "manual", "text": "x"}]))
    assert r.status_code == 422
    both = _run_body(setup, suite, case={"question": "q"})
    assert c.post(f"{API}/test-runs", json=both).status_code == 422


def test_case_without_any_runnable_metric_is_error_not_pass(setup):
    c, pid = setup["client"], setup["project"]["id"]
    ds = upload(c, pid, "question,context\nQ1,Some context\nQ2,\n", filename="ctx.csv", dry_run="false").json()["dataset"]
    body = {
        "project_id": pid, "judge_config_id": setup["judge"]["id"], "dataset_version_id": ds["current_version"]["id"],
        "metrics": [{"metric": "context_recall", "engine": "ragas"}],
        "outputs": [{"label": "A", "source": "dataset"}],
    }
    r = c.post(f"{API}/test-runs", json=body)
    assert r.status_code == 400  # no case has a reference → nothing could ever run
    body["metrics"] = [{"metric": "answer_relevancy", "engine": "ragas"}]
    assert c.post(f"{API}/test-runs", json=body).status_code == 400  # no recorded responses either


# ------------------------------------------------------------------ review + agreement
def test_review_and_agreement_rate(setup, db):
    c = setup["client"]
    suite = _suite(setup)
    run = c.post(f"{API}/test-runs", json=_run_body(setup, suite)).json()
    items = c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]
    scored = [m for i in items for m in i["metrics"] if m["status"] in ("passed", "failed")]
    na = next(m for i in items for m in i["metrics"] if m["status"] == "not_applicable")

    assert c.post(f"{API}/metric-results/{scored[0]['id']}/review", json={"verdict": "agree"}).status_code == 200
    r = c.post(f"{API}/metric-results/{scored[1]['id']}/review",
               json={"verdict": "disagree", "override_status": "failed", "comment": "Misses the 30-day limit"})
    assert r.json()["override_status"] == "failed" and r.json()["reviewer_email"] == "qa@example.com"
    # re-posting updates instead of duplicating
    c.post(f"{API}/metric-results/{scored[0]['id']}/review", json={"verdict": "agree", "comment": "ok"})
    assert c.post(f"{API}/metric-results/{na['id']}/review", json={"verdict": "agree"}).status_code == 400
    assert c.post(f"{API}/metric-results/{scored[2]['id']}/review",
                  json={"verdict": "agree", "override_status": "failed"}).status_code == 422

    s = c.get(f"{API}/test-runs/{run['id']}/summary").json()["agreement"]
    assert s == {"reviewed": 2, "agree": 1, "disagree": 1, "rate": 0.5}
    m = next(m for i in c.get(f"{API}/test-runs/{run['id']}/results").json()["items"]
             for m in i["metrics"] if m["id"] == scored[0]["id"])
    assert len(m["reviews"]) == 1 and m["reviews"][0]["comment"] == "ok"


# ------------------------------------------------------------------ exports
def test_exports_all_formats(setup, db):
    c = setup["client"]
    suite = _suite(setup)
    c.post(f"{API}/test-runs", json=_run_body(setup, suite))
    run_id = c.get(f"{API}/projects/{setup['project']['id']}/test-runs").json()["items"][0]["id"]

    r = c.get(f"{API}/test-runs/{run_id}/export", params={"format": "csv"})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert len(rows) == 6  # 3 cases × 2 metric/engine aggregates
    injected = [row for row in rows if "HYPERLINK" in row["input"]]
    assert injected and all(row["input"].startswith("'=") for row in injected)

    j = c.get(f"{API}/test-runs/{run_id}/export", params={"format": "json"}).json()
    assert j["schema"] == "testmyllm.run-export/v1" and len(j["results"]) == 3
    assert j["results"][0]["metric_results"][0]["judge_model"] == "openai/gpt-4o-mini"

    h = c.get(f"{API}/test-runs/{run_id}/export", params={"format": "html"})
    assert h.headers["content-type"].startswith("text/html") and b"Failed and errored cases" in h.content
    assert b"<script" not in h.content  # user content is escaped

    p = c.get(f"{API}/test-runs/{run_id}/export", params={"format": "pdf"})
    if p.status_code == 500:
        pytest.skip("DejaVu fonts not installed outside the container image")
    assert p.content.startswith(b"%PDF") and len(p.content) > 1000

    from app.models import Export
    assert db.query(Export).filter(Export.run_id == run_id).count() >= 3


def test_html_export_escapes_user_content(setup):
    c, pid = setup["client"], setup["project"]["id"]
    ds = upload(c, pid, 'question,answer\n"<script>alert(1)</script>",x\n', filename="x.csv", dry_run="false").json()["dataset"]
    body = {"project_id": pid, "judge_config_id": setup["judge"]["id"], "dataset_version_id": ds["current_version"]["id"],
            "metrics": [{"metric": "answer_relevancy", "engine": "ragas", "threshold": 0.99}],
            "outputs": [{"label": "A", "source": "dataset"}]}
    run = c.post(f"{API}/test-runs", json=body).json()
    h = c.get(f"{API}/test-runs/{run['id']}/export", params={"format": "html"}).content
    assert b"<script>alert" not in h and b"&lt;script&gt;" in h


# ------------------------------------------------------------------ members
def test_members_management(setup, db):
    c, pid = setup["client"], setup["project"]["id"]
    assert [m["role"] for m in c.get(f"{API}/projects/{pid}/members").json()] == ["owner"]
    r = c.post(f"{API}/projects/{pid}/members", json={"email": "new@example.com", "role": "viewer"})
    assert r.status_code == 400 and r.json()["code"] == "user_not_found"
    created = c.post(f"{API}/projects/{pid}/members",
                     json={"email": "new@example.com", "role": "viewer", "initial_password": "pw-new-user-123"}).json()
    assert created["created_user"] is True
    make_user(db, "existing@example.com", "pw-existing-1")
    assert c.post(f"{API}/projects/{pid}/members", json={"email": "existing@example.com", "role": "editor"}).status_code == 201
    assert c.post(f"{API}/projects/{pid}/members", json={"email": "existing@example.com", "role": "editor"}).status_code == 409
    me = next(m for m in c.get(f"{API}/projects/{pid}/members").json() if m["email"] == "qa@example.com")
    assert c.patch(f"{API}/projects/{pid}/members/{me['user_id']}", json={"role": "editor"}).json()["code"] == "last_owner"
    assert c.delete(f"{API}/projects/{pid}/members/{created['user_id']}").status_code == 204

    # the new viewer can sign in and read, not write
    c.cookies.clear()
    c.post(f"{API}/auth/login", json={"email": "existing@example.com", "password": "pw-existing-1"})
    assert c.get(f"{API}/projects/{pid}").json()["role"] == "editor"
    assert c.post(f"{API}/projects/{pid}/members", json={"email": "x@example.com", "role": "viewer"}).status_code == 403
