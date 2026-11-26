# testmyllm CLI

Run TestMyLLM evaluations from CI and gate releases on the result. Standard library only.

```bash
pip install ./cli
export TESTMYLLM_URL=https://testmyllm.example.com
export TESTMYLLM_TOKEN=tml_...            # Settings -> Integrations -> API tokens (project-scoped)

# Run a suite with a config, compare to a baseline, fail on the gate
testmyllm run --suite 3 --config docs/examples/run-config.json --baseline 2 --gate 1 --fail-on-gate

# Repeat an earlier run exactly (same pinned dataset version, models, metrics, judge)
testmyllm run --rerun 42 --gate 1 --baseline 2 --fail-on-gate

# Evaluate a gate on a run that already finished
testmyllm gate --run 42 --gate 1 --baseline 2 --fail-on-gate
```

| Exit code | Meaning |
|---|---|
| 0 | Run completed and the gate passed (or no gate was given) |
| 1 | The quality gate failed (`--fail-on-gate`) |
| 2 | Infrastructure or usage error: bad arguments, HTTP/auth error, run failed or was cancelled (e.g. budget cap), timeout |

Config (`--config`): the body of `POST /api/v1/test-runs` minus the data source, e.g.
`judge_config_id`, `metrics`, `outputs`, `repeats`, `budget_usd`, `run_type`. See
`docs/examples/run-config.json`. On GitHub, a step summary with the gate table is written automatically;
`--report-html` saves the HTML report; `--json` prints machine-readable output.

GitHub Action: `.github/actions/testmyllm` (sample workflow: `.github/workflows/llm-regression.example.yml`).
