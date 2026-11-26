"""TestMyLLM MCP server (stdio). Exposes score_answers, run_evaluation, get_run, compare_runs and
list_projects to MCP clients such as Claude Code or Claude Desktop, using a project API token.

Run:  TESTMYLLM_URL=http://localhost:8080 TESTMYLLM_TOKEN=tml_... testmyllm-mcp
"""
from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from testmyllm_mcp import tools
from testmyllm_mcp.client import Client

INSTRUCTIONS = (
    "TestMyLLM evaluates LLM answers with Ragas and DeepEval using a judge model. Scores are 0-1, higher is "
    "better; never average Ragas and DeepEval together. Tools that start paid work refuse to run when the "
    "cost estimate exceeds max_estimated_usd - ask the user before raising it. 'error' and 'not_applicable' "
    "results are not zero scores."
)

server = MCPServer(name="testmyllm", title="TestMyLLM", instructions=INSTRUCTIONS, version="0.1.0")
_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = Client()
    return _client


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(client(), *args, **kwargs)
    except tools.ToolError as e:
        return {"error": str(e)}


@server.tool(description="List the TestMyLLM projects this API token can see, with each project's latest run.")
def list_projects() -> Any:
    return _call(tools.list_projects)


@server.tool(description=(
    "Score 1-5 candidate answers to one question with Ragas and DeepEval. Optional reference answer enables "
    "answer_correctness, context (list of passages) enables faithfulness, criteria enables custom_criteria. "
    "Waits for the result. Refuses if the estimated cost exceeds max_estimated_usd."))
def score_answers(question: str, answers: list[str], reference: str | None = None, context: list[str] | None = None,
                  criteria: str | None = None, metrics: list[str] | None = None, engines: list[str] | None = None,
                  project_id: int | None = None, judge_config_id: int | None = None, repeats: int = 1,
                  max_estimated_usd: float = 0.25) -> Any:
    return _call(tools.score_answers, question, answers, reference=reference, context=context, criteria=criteria,
                 metrics=metrics, engines=engines, project_id=project_id, judge_config_id=judge_config_id,
                 repeats=repeats, max_estimated_usd=max_estimated_usd)


@server.tool(description=(
    "Start a batch evaluation of a test suite or dataset version (scoring recorded answers, or answers generated "
    "by `model`, an OpenRouter model id), or repeat an earlier run with rerun_run_id. Returns immediately unless "
    "wait=true; follow with get_run. Refuses if the estimated cost exceeds max_estimated_usd."))
def run_evaluation(suite_id: int | None = None, dataset_version_id: int | None = None, rerun_run_id: int | None = None,
                   metrics: list[str] | None = None, engines: list[str] | None = None, model: str | None = None,
                   judge_config_id: int | None = None, repeats: int = 1, project_id: int | None = None,
                   max_estimated_usd: float = 1.0, wait: bool = False) -> Any:
    return _call(tools.run_evaluation, suite_id=suite_id, dataset_version_id=dataset_version_id,
                 rerun_run_id=rerun_run_id, metrics=metrics, engines=engines, model=model,
                 judge_config_id=judge_config_id, repeats=repeats, project_id=project_id,
                 max_estimated_usd=max_estimated_usd, wait=wait)


@server.tool(description=(
    "Get a run's status, per-metric summary and failed cases with the judge's reasons. For security scans, "
    "returns attack success rates per OWASP category and the findings."))
def get_run(run_id: int, include_failures: bool = True, max_failures: int = 10) -> Any:
    return _call(tools.get_run, run_id, include_failures=include_failures, max_failures=max_failures)


@server.tool(description=(
    "Compare a candidate run with a saved baseline: per-metric deltas, regressed and improved cases, and "
    "optionally a quality gate verdict (preview, not recorded)."))
def compare_runs(candidate_run_id: int, baseline_id: int | None = None, baseline_run_id: int | None = None,
                 gate_id: int | None = None, allow_dataset_mismatch: bool = False) -> Any:
    return _call(tools.compare_runs, candidate_run_id, baseline_id=baseline_id, baseline_run_id=baseline_run_id,
                 gate_id=gate_id, allow_dataset_mismatch=allow_dataset_mismatch)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
