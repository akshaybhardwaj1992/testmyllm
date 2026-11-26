"""Regression reports and quality gates (spec §2 regression tolerance, §4.4).

Pairing: results are matched by the case's stable `case_key` (kept across dataset versions), plus the
answer label when either run has several answers. Only paired cases enter the comparison.

Per metric × engine, each case contributes its mean over repeats. A metric REGRESSED when
  drop = baseline_mean - candidate_mean  (direction-aware)  >  max_regression (default 0.05)
and, when both runs have repeats >= 3, also drop > SE, where SE combines both runs' judge-noise
standard errors: SE_run = sqrt(sum_i(var_i / r_i)) / n  (var_i = case i's variance across r_i repeats).
These are tunable defaults, not statistical guarantees (labelled so in the UI).
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.evaluators.applicability import METRICS
from app.models import Baseline, GateEvaluation, QualityGate, TestResult, TestRun, User
from app.schemas import (
    GateCheck,
    GateResult,
    GateRuleResult,
    RegressionCase,
    RegressionMetric,
    RegressionReport,
)
from app.services import runs as run_svc

DEFAULT_TOLERANCE = 0.05
MIN_REPEATS_FOR_SE = 3
MAX_CASES = 1000


@dataclass
class _Side:
    mean: float | None
    status: str
    var: float | None
    n: int


def _results(db: Session, run: TestRun) -> list[TestResult]:
    return list(db.scalars(
        select(TestResult).where(TestResult.run_id == run.id)
        .options(selectinload(TestResult.metrics), selectinload(TestResult.test_case))
    ).all())


def _index(results: list[TestResult], with_label: bool) -> dict[tuple, TestResult]:
    return {((r.test_case.case_key, r.output_label) if with_label else (r.test_case.case_key,)): r for r in results}


def _sides(r: TestResult) -> dict[tuple[str, str], _Side]:
    out = {}
    for a in run_svc.aggregate(list(r.metrics)):
        out[(a.metric_name, a.engine)] = _Side(a.mean, a.status, a.std ** 2 if a.std is not None else None, a.n)
    return out


def _se(sides: list[_Side], repeats: int) -> float | None:
    if repeats < MIN_REPEATS_FOR_SE:
        return None
    vals = [s for s in sides if s.mean is not None and s.var is not None and s.n > 0]
    if not vals:
        return None
    return math.sqrt(sum(s.var / s.n for s in vals)) / len(vals)


def _pass_rate(sides: list[_Side]) -> float | None:
    decided = [s for s in sides if s.status in ("passed", "failed")]
    return sum(1 for s in decided if s.status == "passed") / len(decided) if decided else None


def build_report(
    db: Session, baseline: Baseline | None, candidate: TestRun, tolerances: dict[tuple[str, str], float] | None = None,
    allow_mismatch: bool = False,
) -> RegressionReport:
    tolerances = tolerances or {}
    warnings: list[str] = []
    cand_results = _results(db, candidate)
    if baseline is None:
        metrics = _candidate_only(cand_results)
        return RegressionReport(
            baseline_run_id=None, candidate_run_id=candidate.id, dataset_version_match=None, paired_cases=0,
            metrics=metrics, regressed=[], improved=[], unchanged_count=0, warnings=["No baseline: regression checks skipped"],
        )
    base_run = db.get(TestRun, baseline.run_id)
    match = base_run.dataset_version_id == candidate.dataset_version_id
    if not match:
        if not allow_mismatch:
            raise ApiError(
                409, "dataset_version_mismatch",
                "The candidate ran on a different dataset version than the baseline. Run it on the baseline's "
                "version, or explicitly allow the mismatch (only unchanged cases are compared).",
                {"baseline_dataset_version_id": base_run.dataset_version_id,
                 "candidate_dataset_version_id": candidate.dataset_version_id},
            )
        warnings.append("Dataset versions differ: only cases present in both versions (same case key) are compared.")

    base_results = _results(db, base_run)
    base_labels = {r.output_label for r in base_results}
    cand_labels = {r.output_label for r in cand_results}
    with_label = not (len(base_labels) == 1 and len(cand_labels) == 1)
    if with_label and base_labels != cand_labels:
        warnings.append(f"Answer labels differ (baseline {sorted(base_labels)}, candidate {sorted(cand_labels)}); "
                        "only matching labels are paired.")
    b_idx, c_idx = _index(base_results, with_label), _index(cand_results, with_label)
    keys = [k for k in c_idx if k in b_idx]
    unpaired = len(c_idx) - len(keys)
    if unpaired:
        warnings.append(f"{unpaired} candidate result(s) have no baseline counterpart and are excluded.")

    per_metric: dict[tuple[str, str], tuple[list[_Side], list[_Side]]] = defaultdict(lambda: ([], []))
    cases_regressed, cases_improved, unchanged = [], [], 0
    for k in keys:
        b, c = b_idx[k], c_idx[k]
        bs, cs = _sides(b), _sides(c)
        deltas = {}
        for mk in set(bs) | set(cs):
            if mk in bs and mk in cs:
                per_metric[mk][0].append(bs[mk])
                per_metric[mk][1].append(cs[mk])
                if bs[mk].mean is not None and cs[mk].mean is not None:
                    hib = METRICS[mk[0]].higher_is_better if mk[0] in METRICS else True
                    deltas[f"{mk[0]}|{mk[1]}"] = (cs[mk].mean - bs[mk].mean) * (1 if hib else -1)
        row = RegressionCase(
            case_key=c.test_case.case_key, input=c.test_case.input, label=c.output_label,
            baseline_result_id=b.id, candidate_result_id=c.id, baseline_status=b.status, candidate_status=c.status,
            baseline_output=b.model_output, candidate_output=c.model_output, metric_deltas=deltas,
        )
        if b.status == "passed" and c.status in ("failed", "error"):
            cases_regressed.append(row)
        elif b.status in ("failed", "error") and c.status == "passed":
            cases_improved.append(row)
        else:
            unchanged += 1

    # Candidate-level figures (gate min_score / min_pass_rate) use the WHOLE candidate run; the delta and
    # regression status use paired cases only. Metrics the baseline never measured appear as no_baseline.
    cand_all = {(m.metric_name, m.engine): m for m in _candidate_only(cand_results)}
    metrics: list[RegressionMetric] = []
    order = list(METRICS)
    for name, engine in sorted(
        set(per_metric) | set(cand_all), key=lambda k: (order.index(k[0]) if k[0] in order else 99, k[1])
    ):
        bsides, csides = per_metric.get((name, engine), ([], []))
        whole = cand_all.get((name, engine))
        pairs = [(b, c) for b, c in zip(bsides, csides) if b.mean is not None and c.mean is not None]
        tol = tolerances.get((name, engine), DEFAULT_TOLERANCE)
        bm = statistics.fmean([b.mean for b, _ in pairs]) if pairs else None
        cm_paired = statistics.fmean([c.mean for _, c in pairs]) if pairs else None
        hib = METRICS[name].higher_is_better if name in METRICS else True
        delta = None if bm is None or cm_paired is None else (cm_paired - bm) * (1 if hib else -1)
        se_b, se_c = _se([b for b, _ in pairs], base_run.repeats), _se([c for _, c in pairs], candidate.repeats)
        se = math.sqrt(se_b ** 2 + se_c ** 2) if se_b is not None and se_c is not None else None
        regressed = delta is not None and -delta > tol and (se is None or -delta > se)
        improved = delta is not None and delta > tol and (se is None or delta > se)
        if delta is not None:
            status = "regressed" if regressed else "improved" if improved else "unchanged"
        else:
            status = "no_baseline" if not bsides else "no_data"
        paired_c = [c for b, c in zip(bsides, csides) if b.mean is not None and c.mean is not None]
        paired_b = [b for b, c in zip(bsides, csides) if b.mean is not None and c.mean is not None]
        metrics.append(RegressionMetric(
            metric_name=name, engine=engine, baseline_mean=bm, candidate_mean_paired=cm_paired,
            candidate_pass_rate_paired=_pass_rate(paired_c),
            candidate_mean=whole.candidate_mean if whole else cm_paired, delta=delta, tolerance=tol,
            standard_error=se, paired=len(pairs),
            candidate_pass_rate=whole.candidate_pass_rate if whole else _pass_rate(csides),
            baseline_pass_rate=_pass_rate(paired_b), status=status,
        ))
    return RegressionReport(
        baseline_run_id=base_run.id, candidate_run_id=candidate.id, dataset_version_match=match,
        paired_cases=len(keys), metrics=metrics, regressed=cases_regressed[:MAX_CASES],
        improved=cases_improved[:MAX_CASES], unchanged_count=unchanged, warnings=warnings,
    )


def _candidate_only(results: list[TestResult]) -> list[RegressionMetric]:
    per: dict[tuple[str, str], list[_Side]] = defaultdict(list)
    for r in results:
        for k, s in _sides(r).items():
            per[k].append(s)
    out = []
    for (name, engine), sides in per.items():
        vals = [s.mean for s in sides if s.mean is not None]
        out.append(RegressionMetric(
            metric_name=name, engine=engine, baseline_mean=None, candidate_mean=statistics.fmean(vals) if vals else None,
            delta=None, tolerance=DEFAULT_TOLERANCE, standard_error=None, paired=0, candidate_pass_rate=_pass_rate(sides),
            baseline_pass_rate=None, status="no_baseline",
        ))
    return out


# ------------------------------------------------------------------ gates
def evaluate_gate(
    db: Session, gate: QualityGate, candidate: TestRun, baseline: Baseline | None, allow_mismatch: bool = False,
) -> tuple[GateResult, RegressionReport]:
    tolerances = {
        (r["metric"], r["engine"]): r["max_regression"] for r in gate.rules if r.get("max_regression") is not None
    }
    report = build_report(db, baseline, candidate, tolerances, allow_mismatch)
    by = {(m.metric_name, m.engine): m for m in report.metrics}
    rule_results = []
    for rule in gate.rules:
        m = by.get((rule["metric"], rule["engine"]))
        checks: list[GateCheck] = []
        if m is None or m.candidate_mean is None:
            checks.append(GateCheck(check="scored", passed=False, detail="No scored results for this metric in the candidate run"))
        else:
            if rule.get("min_score") is not None:
                ok = m.candidate_mean >= rule["min_score"]
                checks.append(GateCheck(check="min_score", passed=ok,
                                        detail=f"mean {m.candidate_mean:.3f} {'≥' if ok else '<'} {rule['min_score']:.2f}"))
            if rule.get("min_pass_rate") is not None:
                pr = m.candidate_pass_rate
                ok = pr is not None and pr >= rule["min_pass_rate"]
                checks.append(GateCheck(check="min_pass_rate", passed=ok,
                                        detail=f"pass rate {'—' if pr is None else f'{pr:.0%}'} vs required {rule['min_pass_rate']:.0%}"))
            if rule.get("max_regression") is not None:
                if baseline is None:
                    checks.append(GateCheck(check="max_regression", passed=True, skipped=True, detail="No baseline given: skipped"))
                elif m.delta is None:
                    checks.append(GateCheck(check="max_regression", passed=False, detail="No paired scores to compare with the baseline"))
                else:
                    ok = m.status != "regressed"
                    se = f", SE {m.standard_error:.3f}" if m.standard_error is not None else ""
                    checks.append(GateCheck(check="max_regression", passed=ok,
                                            detail=f"Δ {m.delta:+.3f} vs tolerance −{rule['max_regression']:.2f}{se}"))
        rule_results.append(GateRuleResult(metric=rule["metric"], engine=rule["engine"],
                                           passed=all(c.passed for c in checks), checks=checks))
    passed = all(r.passed for r in rule_results)
    return GateResult(gate_id=gate.id, gate_name=gate.name, passed=passed, rules=rule_results), report


def record(db: Session, result: GateResult, report: RegressionReport, run: TestRun, baseline: Baseline | None,
           user: User) -> GateEvaluation:
    ev = GateEvaluation(
        run_id=run.id, baseline_id=baseline.id if baseline else None, gate_id=result.gate_id,
        status="passed" if result.passed else "failed",
        details={"gate": result.model_dump(), "metrics": [m.model_dump() for m in report.metrics],
                 "warnings": report.warnings, "paired_cases": report.paired_cases},
        created_by=user.id,
    )
    db.add(ev)
    db.commit()
    return ev
