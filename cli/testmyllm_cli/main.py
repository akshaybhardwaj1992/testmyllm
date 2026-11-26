"""`testmyllm` - run TestMyLLM evaluations from CI and gate releases on the result.

Exit codes:  0 = finished and gates passed (or no gate)
             1 = a quality gate failed (only with --fail-on-gate)
             2 = infrastructure / usage error (bad args, HTTP error, run failed/cancelled, timeout)

Auth: TESTMYLLM_TOKEN (a project API token from Settings -> Integrations); TESTMYLLM_URL (default
http://localhost:8080). Standard library only, so CI installs it in a second.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

EXIT_OK, EXIT_GATE_FAILED, EXIT_ERROR = 0, 1, 2
TERMINAL = ("completed", "failed", "cancelled")


class CliError(Exception):
    """Usage or infrastructure problem -> exit code 2."""


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 60):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token, self.timeout = token, timeout

    def request(self, method: str, path: str, body: Any = None, raw: bool = False) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "testmyllm-cli/0.1",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - user-configured URL
                payload = resp.read()
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = f"{err.get('message')} [{err.get('code')}]"
                if err.get("details"):
                    msg += f" {json.dumps(err['details'])[:500]}"
            except (ValueError, AttributeError):
                msg = e.reason
            raise CliError(f"{method} {path} -> HTTP {e.code}: {msg}") from e
        except urllib.error.URLError as e:
            raise CliError(f"Cannot reach {self.base}: {e.reason}") from e
        return payload if raw else (json.loads(payload) if payload else None)


# ------------------------------------------------------------------ helpers
def _out(args: argparse.Namespace, msg: str) -> None:
    if not args.json:
        print(msg, flush=True)


def _project_id(client: Client, args: argparse.Namespace, config: dict) -> int:
    if args.project:
        return args.project
    if config.get("project_id"):
        return int(config["project_id"])
    projects = client.request("GET", "/projects")
    if len(projects) == 1:  # a project token sees exactly its project
        return projects[0]["id"]
    raise CliError("Pass --project (the token can see several projects)")


def _load_config(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise CliError(f"Cannot read config {path}: {e}") from e


def _wait(client: Client, args: argparse.Namespace, run: dict) -> dict:
    deadline = time.monotonic() + args.timeout
    last = None
    while run["status"] not in TERMINAL:
        if time.monotonic() > deadline:
            raise CliError(f"Run {run['id']} did not finish within {args.timeout}s (it keeps running server-side)")
        time.sleep(args.poll)
        run = client.request("GET", f"/test-runs/{run['id']}")
        progress = f"{run['completed_tests']}/{run['total_tests']}"
        if progress != last:
            _out(args, f"  … {run['status']} {progress} (${float(run['actual_cost_usd']):.4f})")
            last = progress
    return run


def _summary_markdown(run: dict, evaluation: dict | None, url: str) -> str:
    lines = [f"### TestMyLLM · {run['run_name']}", "",
             f"Run [#{run['id']}]({url}) · **{run['status']}** · {run['passed_tests']} passed, "
             f"{run['failed_tests']} failed, {run['error_tests']} error · ${float(run['actual_cost_usd']):.4f}", ""]
    if evaluation:
        g = evaluation["gate"]
        lines += [f"**Gate “{g['gate_name']}”: {'✅ passed' if g['passed'] else '❌ failed'}**", "",
                  "| Metric | Engine | Result | Checks |", "|---|---|---|---|"]
        for r in g["rules"]:
            checks = "; ".join(("skipped: " if c.get("skipped") else "") + c["detail"] for c in r["checks"])
            lines.append(f"| {r['metric']} | {r['engine']} | {'✅' if r['passed'] else '❌'} | {checks} |")
        rep = evaluation["report"]
        if rep.get("regressed"):
            lines += ["", f"{len(rep['regressed'])} case(s) regressed vs the baseline."]
        for w in rep.get("warnings", []):
            lines.append(f"> ⚠️ {w}")
    return "\n".join(lines) + "\n"


def _report_gate(args: argparse.Namespace, client: Client, run: dict) -> tuple[int, dict | None]:
    evaluation = None
    if getattr(args, "security_gate", None):
        evaluation = client.request("POST", f"/security-gates/{args.security_gate}/evaluate", {"run_id": run["id"]})
    elif args.gate:
        evaluation = client.request("POST", f"/quality-gates/{args.gate}/evaluate", {
            "run_id": run["id"], "baseline_id": args.baseline, "allow_dataset_mismatch": args.allow_dataset_mismatch,
        })
    if evaluation is not None:
        g = evaluation["gate"]
        _out(args, f"Gate “{g['gate_name']}”: {'PASSED' if g['passed'] else 'FAILED'}")
        for r in g["rules"]:
            for c in r["checks"]:
                mark = "skip" if c.get("skipped") else ("ok  " if c["passed"] else "FAIL")
                _out(args, f"  [{mark}] {r['metric']} ({r['engine']}) {c['check']}: {c['detail']}")
        for w in evaluation["report"].get("warnings", []):
            _out(args, f"  warning: {w}")
    elif args.fail_on_gate:
        raise CliError("--fail-on-gate needs --gate (or --security-gate)")
    url = f"{args.url.rstrip('/')}/p/{run['project_id']}/runs/{run['id']}"
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(_summary_markdown(run, evaluation, url))
    if args.report_html:
        with open(args.report_html, "wb") as f:
            f.write(client.request("GET", f"/test-runs/{run['id']}/export?format=html", raw=True))
        _out(args, f"HTML report written to {args.report_html}")
    if args.json:
        print(json.dumps({"run": run, "gate_evaluation": evaluation, "url": url}, indent=2))
    else:
        print(f"Details: {url}")
    failed = evaluation is not None and not evaluation["gate"]["passed"]
    return (EXIT_GATE_FAILED if failed and args.fail_on_gate else EXIT_OK), evaluation


# ------------------------------------------------------------------ commands
def cmd_run(args: argparse.Namespace, client: Client) -> int:
    config = _load_config(args.config)
    if args.rerun:
        run = client.request("POST", f"/test-runs/{args.rerun}/rerun")
    else:
        if not (args.suite or args.dataset_version):
            raise CliError("Give --suite, --dataset-version or --rerun")
        for key in ("judge_config_id", "metrics", "outputs"):
            if key not in config:
                raise CliError(f"--config must define '{key}' (see cli/README.md)")
        body = {k: v for k, v in config.items() if k != "project_id"}
        body["project_id"] = _project_id(client, args, config)
        body.pop("suite_id", None)
        body.pop("dataset_version_id", None)
        body["suite_id" if args.suite else "dataset_version_id"] = args.suite or args.dataset_version
        if args.name:
            body["run_name"] = args.name
        est = client.request("POST", "/test-runs/estimate", body)
        cost = "unknown" if est["total_usd"] is None else "${:.4f}".format(est["total_usd"])
        _out(args, f"Estimated cost: {cost} ({est['judge_calls']} judge calls)")
        run = client.request("POST", "/test-runs", body)
    _out(args, f"Started run #{run['id']} “{run['run_name']}” ({run['total_tests']} evaluations)")
    run = _wait(client, args, run)
    _out(args, f"Run {run['status']}: {run['passed_tests']} passed, {run['failed_tests']} failed, "
               f"{run['error_tests']} error, cost ${float(run['actual_cost_usd']):.4f}")
    if run["status"] != "completed":
        reason = f" ({run['status_reason']})" if run.get("status_reason") else ""
        raise CliError(f"Run {run['id']} ended as {run['status']}{reason}")
    code, _ = _report_gate(args, client, run)
    return code


def _security_report(args: argparse.Namespace, client: Client, run: dict) -> None:
    rep = client.request("GET", f"/security-runs/{run['id']}/report")
    _out(args, f"Attacks: {rep['attacks']} · breached: {rep['breached']} · "
               f"ASR {('n/a' if rep['asr'] is None else format(rep['asr'], '.0%'))}")
    for c in rep["categories"]:
        asr = "n/a" if c["asr"] is None else format(c["asr"], ".0%")
        _out(args, f"  {c['owasp']} {c['label']}: {c['breached']}/{c['attacks']} breached (ASR {asr})"
                   + (f", {c['errors']} error" if c["errors"] else ""))
    sev = ", ".join(f"{n} {k}" for k, n in rep["severity_counts"].items() if n)
    _out(args, f"Findings: {sev or 'none'}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        lines = ["", "#### Security findings", "", "| Category | OWASP | Breached | ASR |", "|---|---|---|---|"]
        for c in rep["categories"]:
            lines.append(f"| {c['label']} | {c['owasp']} | {c['breached']}/{c['attacks']} | "
                         f"{'n/a' if c['asr'] is None else format(c['asr'], '.0%')} |")
        lines += ["", f"Findings by severity: {sev or 'none'}", ""]
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def cmd_security(args: argparse.Namespace, client: Client) -> int:
    """Run a security scan against an authorized target, then evaluate a security gate."""
    if args.rerun:
        run = client.request("POST", f"/test-runs/{args.rerun}/rerun")
    else:
        if not (args.target and args.attack_set):
            raise CliError("Give --target and at least one --attack-set (or --rerun)")
        config = _load_config(args.config)
        body = {
            "project_id": _project_id(client, args, config), "target_id": args.target,
            "attack_set_ids": args.attack_set, "attempts": args.attempts,
            "categories": args.category or None, "judge_config_id": args.judge,
            "budget_usd": args.budget, "documents_planted": args.documents_planted, "run_name": args.name,
        }
        est = client.request("POST", "/security-runs/estimate", body)
        cost = "unknown" if est["total_usd"] is None else "${:.4f}".format(est["total_usd"])
        _out(args, f"Estimated cost: {cost} ({est['attacks']} attacks, {est['target_calls']} target calls, "
                   f"{len(est['skipped'])} skipped)")
        for w in est["warnings"]:
            _out(args, f"  warning: {w}")
        run = client.request("POST", "/security-runs", body)
    _out(args, f"Started security scan #{run['id']} “{run['run_name']}” ({run['total_tests']} attacks)")
    run = _wait(client, args, run)
    if run["status"] != "completed":
        reason = f" ({run['status_reason']})" if run.get("status_reason") else ""
        raise CliError(f"Scan {run['id']} ended as {run['status']}{reason}")
    _security_report(args, client, run)
    args.security_gate, args.gate, args.baseline, args.allow_dataset_mismatch = args.gate, None, None, False
    code, _ = _report_gate(args, client, run)
    return code


def cmd_gate(args: argparse.Namespace, client: Client) -> int:
    run = client.request("GET", f"/test-runs/{args.run}")
    if run["status"] not in TERMINAL:
        raise CliError(f"Run {args.run} is still {run['status']}")
    code, _ = _report_gate(args, client, run)
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="testmyllm", description="TestMyLLM CLI: run evaluations and quality gates in CI.")
    p.add_argument("--url", default=os.environ.get("TESTMYLLM_URL", "http://localhost:8080"), help="TestMyLLM URL (env TESTMYLLM_URL)")
    p.add_argument("--token", default=os.environ.get("TESTMYLLM_TOKEN"), help="Project API token (env TESTMYLLM_TOKEN)")
    sub = p.add_subparsers(dest="command", required=True)

    def gate_opts(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--gate", type=int, help="Quality gate id")
        sp.add_argument("--baseline", type=int, help="Baseline id for regression checks")
        sp.add_argument("--fail-on-gate", action="store_true", help="Exit 1 if the gate fails")
        sp.add_argument("--allow-dataset-mismatch", action="store_true", help="Compare even if dataset versions differ")
        sp.add_argument("--report-html", help="Write the HTML report to this path")
        sp.add_argument("--json", action="store_true", help="Print machine-readable JSON only")

    r = sub.add_parser("run", help="Start a run, wait for it, evaluate a gate")
    src = r.add_mutually_exclusive_group()
    src.add_argument("--suite", type=int, help="Suite id")
    src.add_argument("--dataset-version", type=int, help="Dataset version id")
    src.add_argument("--rerun", type=int, help="Repeat an existing run with its pinned configuration")
    r.add_argument("--config", help="JSON run config: judge_config_id, metrics, outputs, repeats, budget_usd, run_type")
    r.add_argument("--project", type=int, help="Project id (inferred from a project token)")
    r.add_argument("--name", help="Run name")
    r.add_argument("--timeout", type=int, default=3600, help="Seconds to wait for completion (default 3600)")
    r.add_argument("--poll", type=float, default=5, help="Polling interval in seconds")
    gate_opts(r)
    r.set_defaults(func=cmd_run)

    sec = sub.add_parser("security", help="Run a security scan (prompt injection, jailbreak, disclosure)")
    sec.add_argument("--target", type=int, help="Security target id (must be authorized in the app)")
    sec.add_argument("--attack-set", type=int, action="append", help="Attack set id (repeatable)")
    sec.add_argument("--category", action="append", help="Only these categories (repeatable)")
    sec.add_argument("--attempts", type=int, default=2, help="Attempts per attack (1-5, default 2)")
    sec.add_argument("--judge", type=int, help="Judge config id to confirm code-word hits")
    sec.add_argument("--budget", type=float, help="Budget cap in USD")
    sec.add_argument("--documents-planted", action="store_true", help="RAG: poisoned documents are in the index")
    sec.add_argument("--rerun", type=int, help="Repeat an earlier scan with the same configuration")
    sec.add_argument("--config", help="Optional JSON with project_id")
    sec.add_argument("--project", type=int, help="Project id (inferred from a project token)")
    sec.add_argument("--name", help="Scan name")
    sec.add_argument("--timeout", type=int, default=3600, help="Seconds to wait for completion")
    sec.add_argument("--poll", type=float, default=5, help="Polling interval in seconds")
    sec.add_argument("--gate", type=int, help="Security gate id")
    sec.add_argument("--fail-on-gate", action="store_true", help="Exit 1 if the security gate fails")
    sec.add_argument("--report-html", help="Write the HTML report to this path")
    sec.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    sec.set_defaults(func=cmd_security)

    g = sub.add_parser("gate", help="Evaluate a gate on a finished run")
    g.add_argument("--run", type=int, required=True)
    g.add_argument("--security-gate", type=int, help="Security gate id (for security scans)")
    gate_opts(g)
    g.set_defaults(func=cmd_gate)
    return p


def _safe_console() -> None:
    """Never crash on output: legacy consoles (e.g. Windows cp1252) cannot encode ≥ or curly quotes."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None, client: Client | None = None) -> int:
    _safe_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:  # argparse exits 2 on usage errors already; keep that contract
        return EXIT_ERROR if e.code else EXIT_OK
    try:
        if client is None:
            if not args.token:
                raise CliError("Set TESTMYLLM_TOKEN (Settings → Integrations → API tokens)")
            client = Client(args.url, args.token)
        return args.func(args, client)
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_ERROR
    except Exception as e:  # noqa: BLE001 - an unexpected crash must read as "error" (2), never as "gate failed" (1)
        print(f"error: unexpected {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())


def main_entry() -> None:
    sys.exit(main())
