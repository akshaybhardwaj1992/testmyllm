<p align="center"><img src="frontend/public/logo-full.png" alt="TestMyLLM — Evaluate · Test · Analyze · Improve" width="420"></p>

# TestMyLLM

A web platform for testing AI applications: score LLM responses with **Ragas and DeepEval side by
side**, with the judge, engine version, variance and cost recorded on every result.

**Status: Milestones 1–5 of 6** (see [docs/decisions.md](docs/decisions.md) D1 for the roadmap).

- **M1** foundation + LLM Response Testing (single / compare two answers)
- **M2** versioned datasets (CSV/JSON upload with mapping + validation report), test cases, suites,
  batch runs, metric summary histograms, human review + judge agreement, CSV/JSON/HTML/PDF exports, members
- **M3** RAG Evaluation: offline (dataset) or live (calls your RAG API and captures the contexts it
  actually retrieved), SSRF-protected connectors with test-connection preview, failure-origin diagnosis
  (retrieval / generation / both / undetermined)
- **M4** Model Comparison: the same questions to 2–5 models with one shared judge; per-model quality,
  latency p50/p95, tokens and cost, charts (quality, cost vs quality, latency), side-by-side per-case
  drill-down, saved model configs, and Re-run for any run
- **M5** Regression testing: baselines, per-metric quality gates (min score, max regression with a
  judge-noise standard-error rule, min pass rate), compare view with regressed/improved cases,
  project API tokens, the `testmyllm` CLI (exit 0/1/2) and a GitHub Action — see [cli/README.md](cli/README.md)

### Try live RAG with the demo endpoint

```bash
# .env: RAG_ALLOW_PRIVATE=true   (local development only; lets connectors reach the compose network)
docker compose --profile demo up -d --build
```

Upload `docs/examples/support-faq.csv`, open **Test Studio → RAG Evaluation**, choose *Live*, and
create a connector: URL `http://demo-rag:8090/ask`, contexts path `sources[*]`, text path `text`.
The demo index deliberately lacks the warranty document, so that question shows a retrieval failure.

## Quick start

Requires Docker.

```bash
cp .env.example .env      # then fill in the secrets (commands are in the file)
docker compose up --build
```

Open http://localhost:8080 and sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `.env`, then:

1. **New project**
2. **Settings → Providers & API Keys**: add your OpenRouter key (encrypted at rest, never shown again).
3. **Settings → Evaluation Models**: create a judge (e.g. `openai/gpt-4o-mini`, temperature 0).
4. **Test Studio → LLM Response Testing**: enter a question and an answer (or two to compare, or
   let a provider generate them), pick metrics, check the cost estimate, **Run**.

Services: `frontend` (nginx, :8080) → `backend` (FastAPI, :8000, docs at `/api/docs`) → `redis` →
`worker` (Celery, prefork) → `db` (PostgreSQL). Ports bind to 127.0.0.1 only.

## What M1 includes

| Area | Delivered |
|---|---|
| Evaluation engine | Ragas 0.4.4 + DeepEval 4.3.5 behind adapters returning one normalized result shape; metric registry with per-engine applicability; lower-is-better metrics handled |
| Reliability | Repeats with mean/std/min/max; errors stored as `error` (never 0) and excluded from averages; N/A with the missing input named; judge model + config + engine version on every result; judge-family bias warning |
| Cost control | Pre-run estimate (model vs judge), per-run budget cap, measured cost per call, judge result cache |
| Execution | One Celery job per answer; idempotent metric rows (unique per result/metric/engine/repeat); late-ack + restart recovery; cancellation; per-provider concurrency limit across workers |
| UI | Login, projects, providers (write-only keys, test connection), judges, LLM Response Testing (single / compare two, manual / generated), runs list (search, filter, sort, columns, pagination), run detail (live progress, config snapshot, results, summary, failures, case drawer), command palette, dark mode |
| Security | Fernet-encrypted credentials, secrets redacted from logs and errors, httpOnly SameSite cookie sessions, project roles (owner/editor/viewer), telemetry from Ragas/DeepEval disabled |

Screens for later milestones appear in the sidebar labelled with their milestone.

## AI security testing

**Security Testing → AI Security Testing** attacks your own LLM app and measures how often attacks succeed.

1. **Targets:** add a model plus its system prompt, or one of your RAG connectors. Confirm that you are
   authorized to test it; the confirmation is recorded with your name and the time.
2. **Attack library:** two built-in, versioned sets ship with the app:
   - prompt injection and jailbreak
   - sensitive-information disclosure

   You can also add custom sets as JSON. Each attack has a category, technique, severity, OWASP LLM ID
   (2025) and detector.
3. **New scan:** pick the target, sets, categories and attempts per attack. Optionally add a judge to
   confirm code-word hits, and a budget cap.
4. **Scan report:** shows the attack success rate per category, plus findings with evidence and
   remediation. Evaluate a **security gate** (maximum ASR per category, zero critical findings) here or
   from CI:

```bash
testmyllm security --target 1 --attack-set 1 --attack-set 2 --attempts 2 --gate 1 --fail-on-gate
```

Attacks use harmless goals: output a code word, or leak a planted canary. See docs/decisions.md D29.

## Overview, history, MCP and data retention (M6)

- **Overview** shows:
  - the latest pass rate and per-metric means
  - open gate failures and 30-day spend
  - the security attack success rate (ASR)
  - a gate banner, a quality trend chart (with a table view) and the top failing cases
- **Evaluation History** lists every run with filters (type, status, dates, search), shows the trend, and
  exports CSV.
- **MCP server:** `pip install ./mcp_server`, then
  `claude mcp add testmyllm --env TESTMYLLM_URL=... --env TESTMYLLM_TOKEN=tml_... -- testmyllm-mcp`.
  Tools: `score_answers`, `run_evaluation`, `get_run`, `compare_runs` and `list_projects`. See
  `mcp_server/README.md`.
- **Project Settings → Data retention & privacy:**
  - delete runs older than N days (baseline runs are kept)
  - no-persistence mode: new runs are deleted N hours after they start and are never cached

## Development

```bash
# backend tests (adapter contract tests with a mocked judge + API tests), against the compose Postgres
docker compose up -d db
docker compose run --rm backend python -m pytest -q

# frontend dev server with hot reload (proxies /api to localhost:8000)
cd frontend && npm install && npm run dev

# regenerate TypeScript API types after changing backend schemas
npm run gen:api
```

Upgrading Ragas or DeepEval: bump the pin in `backend/requirements.txt` and run
`tests/test_evaluators.py`; the contract tests build the real engine metric classes with the exact
arguments the adapters use and fail if a signature changed.

## Layout

```
backend/app/
  api/          routes (/api/v1)          evaluators/  base, applicability, ragas, deepeval, runner, usage
  core/         config, db, security,     providers/   openrouter (generation, pricing, key test)
                errors, redaction         services/    runs, execution (unit of work), estimate, access
  models/       SQLAlchemy tables         workers/     celery app, tasks, provider concurrency limiter
  schemas/      Pydantic API models
frontend/src/   pages, layouts, components (shadcn-style), hooks (TanStack Query), types (generated)
docs/decisions.md   decisions where the spec was silent or ambiguous
```

The original Streamlit prototype's logic (OpenRouter client, Ragas metric calls, the
"answer using only this context" prompt) lives on in `evaluators/ragas_evaluator.py`,
`providers/openrouter.py` and `services/runs.py::build_prompt`.
