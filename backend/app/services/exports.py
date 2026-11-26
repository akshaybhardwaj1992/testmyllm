"""Run report exports (spec §10.4): CSV (flat per case × metric × engine), JSON (full normalized data),
HTML and PDF reports (config snapshot, summary, gate results, failure-origin breakdown, failed cases)."""
from __future__ import annotations

import csv
import html
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.datasets.parsing import csv_safe
from app.evaluators.applicability import METRICS
from app.models import HumanReview, TestResult, TestRun
from app.services import runs as run_svc

DATA_DIR = Path("/data/exports")
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
}


def _label(metric: str) -> str:
    return METRICS[metric].label if metric in METRICS else metric


def _results(db: Session, run: TestRun) -> list[TestResult]:
    return list(db.scalars(
        select(TestResult).where(TestResult.run_id == run.id)
        .options(selectinload(TestResult.metrics), selectinload(TestResult.test_case))
        .order_by(TestResult.id)
    ).all())


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


# ------------------------------------------------------------------ CSV
CSV_COLUMNS = [
    "result_id", "case_key", "answer_label", "result_status", "input", "expected_output", "model_output",
    "metric", "engine", "engine_version", "engine_metric_name", "higher_is_better", "threshold", "metric_status",
    "mean", "std", "min", "max", "n_repeats", "errors", "reason", "judge_model",
]


def build_csv(db: Session, run: TestRun) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in _results(db, run):
        aggs = run_svc.aggregate(list(r.metrics)) or [None]
        for a in aggs:
            row = [
                r.id, r.test_case.case_key, r.output_label, r.status, r.test_case.input,
                r.test_case.expected_output, r.model_output,
            ]
            if a is None:
                row += [""] * (len(CSV_COLUMNS) - len(row))
            else:
                row += [
                    a.metric_name, a.engine, a.engine_version, a.engine_metric_name, a.higher_is_better,
                    a.threshold, a.status, a.mean, a.std, a.min, a.max, a.n, a.errors, a.reason, a.judge_model,
                ]
            w.writerow([csv_safe(v) if v is not None else "" for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens UTF-8 correctly


# ------------------------------------------------------------------ JSON
def build_json(db: Session, run: TestRun) -> bytes:
    results = _results(db, run)
    metric_ids = [m.id for r in results for m in r.metrics]
    reviews: dict[int, list[dict]] = {}
    if metric_ids:
        for rv in db.scalars(select(HumanReview).where(HumanReview.metric_result_id.in_(metric_ids))).all():
            reviews.setdefault(rv.metric_result_id, []).append({
                "reviewer_id": rv.reviewer_id, "verdict": rv.verdict, "override_status": rv.override_status,
                "comment": rv.comment, "created_at": rv.created_at.isoformat(),
            })
    out = {
        "schema": "testmyllm.run-export/v1",
        "exported_at": datetime.now(UTC).isoformat(),
        "run": run_svc.run_out(db, run).model_dump(mode="json"),
        "summary": run_svc.summary(db, run).model_dump(mode="json"),
        "results": [
            {
                "id": r.id,
                "answer_label": r.output_label,
                "output_source": r.output_source,
                "source_model": r.source_model,
                "status": r.status,
                "error_message": r.error_message,
                "failure_origin": r.failure_origin,
                "latency_ms": r.latency_ms,
                "cost_usd": _num(r.cost_usd),
                "case": {
                    "id": r.test_case.id, "case_key": r.test_case.case_key, "input": r.test_case.input,
                    "expected_output": r.test_case.expected_output, "provided_context": r.test_case.provided_context,
                    "criteria": r.test_case.criteria, "tags": r.test_case.tags,
                },
                "model_output": r.model_output,
                "retrieved_context": r.retrieved_context,
                "aggregates": [a.model_dump() for a in run_svc.aggregate(list(r.metrics))],
                "metric_results": [
                    {
                        "id": m.id, "metric": m.metric_name, "engine": m.engine, "engine_version": m.engine_version,
                        "engine_metric_name": m.engine_metric_name, "repeat_index": m.repeat_index,
                        "score": m.score, "threshold": m.threshold, "status": m.status, "reason": m.reason,
                        "error_message": m.error_message, "judge_model": m.judge_model,
                        "judge_config": m.judge_config, "tokens": {"input": m.input_tokens, "output": m.output_tokens},
                        "judge_cost_usd": _num(m.judge_cost_usd), "cache_hit": m.cache_hit,
                        "execution_time_ms": m.execution_time_ms, "reviews": reviews.get(m.id, []),
                    }
                    for m in sorted(r.metrics, key=lambda x: (x.metric_name, x.engine, x.repeat_index))
                ],
            }
            for r in results
        ],
    }
    return json.dumps(out, ensure_ascii=False, indent=2).encode("utf-8")


# ------------------------------------------------------------------ report model shared by HTML + PDF
def _report(db: Session, run: TestRun) -> dict[str, Any]:
    snap = run.config_snapshot or {}
    summ = run_svc.summary(db, run)
    failed = [r for r in _results(db, run) if r.status in ("failed", "error")]
    origins: dict[str, int] = {}
    for r in _results(db, run):
        if r.failure_origin:
            origins[r.failure_origin] = origins.get(r.failure_origin, 0) + 1
    ds = snap.get("dataset")
    return {
        "title": run.run_name,
        "meta": [
            ("Run", f"#{run.id} · {run.run_type.upper()} · {run.status}" + (
                f" ({run.status_reason})" if run.status_reason else "")),
            ("Created", run.created_at.strftime("%Y-%m-%d %H:%M UTC") if run.created_at else ""),
            ("Dataset", f"{ds['name']} v{ds['version_number']}" if ds else "Ad-hoc Studio case"),
            ("Suite", (snap.get("suite") or {}).get("name", "—")),
            ("Judge", f"{snap.get('judge', {}).get('model')} (temperature {snap.get('judge', {}).get('temperature')})"),
            ("Engines", ", ".join(f"{k} {v}" for k, v in (run.engine_versions or {}).items())),
            ("Answers", ", ".join(
                f"{o['label']} ({o.get('model') or o.get('source_model') or o['source']})" for o in snap.get("outputs", []))),
            ("Repeats", str(run.repeats)),
            ("Results", f"{run.passed_tests} passed · {run.failed_tests} failed · {run.error_tests} error of {run.total_tests}"),
            ("Cost", f"${float(run.actual_cost_usd or 0):.4f} actual / "
                     + (f"${float(run.estimated_cost_usd):.4f} estimated" if run.estimated_cost_usd is not None else "estimate unknown")),
            ("Judge agreement", f"{summ.agreement.rate:.0%} of {summ.agreement.reviewed} reviews"
             if summ.agreement.rate is not None else "No human reviews"),
        ],
        "summary": [
            [
                f"{_label(m.metric_name)}", f"{m.engine} {m.engine_version}",
                "—" if m.mean is None else f"{m.mean:.2f}", "—" if m.std is None else f"{m.std:.3f}",
                "—" if m.pass_rate is None else f"{m.pass_rate:.0%}",
                f"{'≥' if m.higher_is_better else '≤'} {m.threshold:.2f}" if m.threshold is not None else "—",
                str(m.errors), str(m.not_applicable),
            ]
            for m in summ.metrics
        ],
        "origins": origins,
        "failed": [
            {
                "label": r.output_label, "status": r.status, "input": r.test_case.input,
                "expected": r.test_case.expected_output, "output": r.model_output, "error": r.error_message,
                "metrics": [
                    (f"{_label(a.metric_name)} ({a.engine})", a.status,
                     "—" if a.mean is None else f"{a.mean:.2f}", a.reason or "")
                    for a in run_svc.aggregate(list(r.metrics)) if a.status in ("failed", "error")
                ],
            }
            for r in failed[:200]
        ],
        "failed_total": len(failed),
    }


SUMMARY_HEAD = ["Metric", "Engine", "Mean", "Std", "Pass rate", "Threshold", "Errors", "N/A"]


def build_html(db: Session, run: TestRun) -> bytes:
    rep = _report(db, run)
    e = html.escape
    meta = "".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in rep["meta"])
    summ = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>" for row in rep["summary"])
    origins = (
        "".join(f"<li>{e(k)}: {v}</li>" for k, v in rep["origins"].items())
        or "<li>Not applicable to this run (failure origin is computed for RAG runs).</li>"
    )
    failed = ""
    for f in rep["failed"]:
        rows = "".join(
            f"<tr><td>{e(m)}</td><td>{e(s)}</td><td>{e(sc)}</td><td>{e(rs)}</td></tr>" for m, s, sc, rs in f["metrics"]
        )
        failed += (
            f"<section class='case'><h3>{e(f['label'])} · {e(f['status'])}</h3>"
            f"<p><b>Input</b><br>{e(f['input'])}</p>"
            + (f"<p><b>Expected</b><br>{e(f['expected'])}</p>" if f["expected"] else "")
            + f"<p><b>Output</b><br>{e(f['output'] or '—')}</p>"
            + (f"<p class='err'>{e(f['error'])}</p>" if f["error"] else "")
            + (f"<table><tr><th>Metric</th><th>Status</th><th>Score</th><th>Reason</th></tr>{rows}</table>" if rows else "")
            + "</section>"
        )
    more = (f"<p>Showing 200 of {rep['failed_total']} failed cases; see the CSV/JSON export for all.</p>"
            if rep["failed_total"] > 200 else "")
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(rep['title'])} · TestMyLLM report</title>
<style>
body{{font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#1a1d2b;max-width:1000px;margin:32px auto;padding:0 16px}}
h1{{font-size:22px}} h2{{font-size:17px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}} h3{{font-size:15px;margin:0 0 8px}}
table{{border-collapse:collapse;width:100%;margin:8px 0}} th,td{{border:1px solid #e2e4ea;padding:5px 8px;text-align:left;vertical-align:top}}
th{{background:#f5f6fa;font-weight:600}} .case{{border:1px solid #e2e4ea;border-radius:8px;padding:12px;margin:12px 0}}
.err{{color:#9a5b00}} .muted{{color:#6b7080;font-size:12px}}
</style></head><body>
<h1>{e(rep['title'])}</h1><p class="muted">TestMyLLM run report · generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC</p>
<h2>Configuration</h2><table>{meta}</table>
<h2>Summary</h2><table><tr>{''.join(f'<th>{h}</th>' for h in SUMMARY_HEAD)}</tr>{summ}</table>
<p class="muted">Ragas and DeepEval scores are separate implementations and are never averaged together. Errors are excluded from means.</p>
<h2>Quality gates</h2><p>No gates evaluated for this run (baselines and gates arrive in M5).</p>
<h2>Failure origin</h2><ul>{origins}</ul>
<h2>Failed and errored cases ({rep['failed_total']})</h2>{failed or '<p>None.</p>'}{more}
</body></html>"""
    return doc.encode("utf-8")


def build_pdf(db: Session, run: TestRun) -> bytes:
    from fpdf import FPDF

    rep = _report(db, run)
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.add_page()
    w = pdf.epw

    def h(text: str, size: int = 13) -> None:
        pdf.ln(3)
        pdf.set_font("DejaVu", "B", size)
        pdf.multi_cell(w, 7, text)
        pdf.set_font("DejaVu", "", 9)

    def para(text: str, bold_label: str | None = None) -> None:
        pdf.set_font("DejaVu", "", 9)
        if bold_label:
            pdf.set_font("DejaVu", "B", 9)
            pdf.multi_cell(w, 5, bold_label)
            pdf.set_font("DejaVu", "", 9)
        pdf.multi_cell(w, 5, text[:3000])

    def table(head: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
        pdf.set_font("DejaVu", "", 8)
        with pdf.table(col_widths=widths, text_align="LEFT", line_height=4.5, padding=1.2) as t:
            r = t.row()
            for c in head:
                r.cell(c)
            for row in rows:
                r = t.row()
                for c in row:
                    r.cell(str(c)[:600])

    h(rep["title"], 16)
    para(f"TestMyLLM run report · generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    h("Configuration")
    table(["Field", "Value"], [[k, str(v)] for k, v in rep["meta"]], [1, 3])
    h("Summary")
    table(SUMMARY_HEAD, rep["summary"] or [["—"] * len(SUMMARY_HEAD)])
    para("Ragas and DeepEval scores are separate implementations and are never averaged together.")
    h("Quality gates")
    para("No gates evaluated for this run (baselines and gates arrive in M5).")
    h("Failure origin")
    para(", ".join(f"{k}: {v}" for k, v in rep["origins"].items())
         or "Not applicable to this run (failure origin is computed for RAG runs).")
    h(f"Failed and errored cases ({rep['failed_total']})")
    if not rep["failed"]:
        para("None.")
    for f in rep["failed"][:100]:
        h(f"{f['label']} · {f['status']}", 10)
        para(f["input"], "Input")
        if f["expected"]:
            para(f["expected"], "Expected")
        para(f["output"] or "—", "Output")
        if f["error"]:
            para(f["error"], "Error")
        if f["metrics"]:
            table(["Metric", "Status", "Score", "Reason"], [list(m) for m in f["metrics"]], [2, 1, 0.7, 4])
    if rep["failed_total"] > 100:
        para(f"Showing 100 of {rep['failed_total']} failed cases; see the CSV/JSON export for all.")
    return bytes(pdf.output())


BUILDERS = {"csv": build_csv, "json": build_json, "html": build_html, "pdf": build_pdf}


def export(db: Session, run: TestRun, fmt: str, user_id: int) -> tuple[bytes, str]:
    """Build, persist to the exports volume, record an `exports` row. Returns (content, filename)."""
    from app.models import Export

    content = BUILDERS[fmt](db, run)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"run-{run.id}-{stamp}.{fmt}"
    path = DATA_DIR / filename
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        stored = str(path)
    except OSError:
        stored = ""  # storage unavailable (e.g. tests): still return the file
    db.add(Export(run_id=run.id, format=fmt, path=stored, size_bytes=len(content), created_by=user_id))
    db.commit()
    return content, filename
