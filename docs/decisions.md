# Decisions log

Decisions taken while building TestMyLLM (originally named AI TestLab), where the spec (v2) was silent, ambiguous or
self-contradictory. Newest at the bottom. Each entry: context → decision → consequence.

## D1. Milestones (spec referenced "Section 13" milestones, which do not exist)

Section 0.1 says milestones live in Section 13, but Section 13 is the project structure. Defined here,
following the API build order in Section 8 and the MVP scope in Section 12. Every milestone ends
with `docker compose up` giving a usable, persisted, end-to-end feature.

| # | Milestone | Contents |
|---|---|---|
| M1 | Foundation + LLM Response Testing | Compose stack, auth, projects + roles, OpenRouter provider (encrypted key, test connection), judge configs, Ragas/DeepEval adapters + applicability, runs (estimate, budget cap, repeats, cancel, per-case Celery jobs, idempotency, recovery, judge cache), LLM Response Testing (single + compare two answers, manual or generated), runs list, run detail. |
| M2 | Datasets & test management | Versioned CSV/JSON upload with column mapping + validation report, test cases, suites, batch runs from a suite, run detail tabs (metric summary, failures), human review + agreement rate, exports (CSV/JSON/HTML/PDF). |
| M3 | RAG evaluation | Offline + live connector, response mapping preview, SSRF guard, retrieved-context capture, failure-origin heuristics, 5-step wizard. |
| M4 | Model comparison | Multi-model runs on a dataset version, latency/tokens/cost, charts, per-case drill-down. |
| M5 | Regression + CI | Baselines, per-metric gates with tolerance + standard error, compare view, `testmyllm` CLI with exit codes, GitHub Action. |
| M6 | Overview, History, MCP | Project dashboard, history with trends, MCP server package, retention + no-persistence (D30). Done. |

## D2. Ad-hoc Studio cases have no dataset version

Test Studio runs (one question typed into a form) are not part of a dataset. Forcing them into
an immutable dataset version would create a new version per click. `test_cases.dataset_version_id`
is therefore **nullable**; a null value means "ad-hoc Studio case". Dataset-backed runs (M2+)
always set it.

## D3. Compare-two-answers = one case, two results

A "compare two answers" run stores one `test_case` and two `test_results`, distinguished by a new
`test_results.output_label` column (e.g. "Claude", "ChatGPT") and optional `source_model`
(the model that produced a manually pasted answer, used for the judge-bias warning).

## D4. Metric registry, engine metric names, direction

Every product metric (`answer_relevancy`, `faithfulness`, …) maps to an engine-specific
implementation. `metric_results` stores both `metric_name` (product name) and
`engine_metric_name` (e.g. Ragas `factual_correctness` behind product `answer_correctness`).
The registry carries a `higher_is_better` flag (lower-is-better metrics use the threshold as a
maximum) and the UI shows the direction. **Correction found by the contract tests:** in the pinned
deepeval 4.3.5, `HallucinationMetric`, `BiasMetric` and `ToxicityMetric` score in the same
direction as every other metric (1 = pass, threshold is a minimum); older deepeval scored the
violation proportion. All current metrics are therefore higher-is-better; the flag stays for
future metrics and a test guards the deepeval direction.

Pinned engine mappings (verified against ragas 0.4.4 / deepeval 4.3.5 by introspection):

| Product metric | Ragas (`ragas.metrics.collections`) | DeepEval (`deepeval.metrics`) |
|---|---|---|
| answer_relevancy | `AnswerRelevancy(llm, embeddings).ascore(user_input, response)` | `AnswerRelevancyMetric` |
| faithfulness | `Faithfulness(llm).ascore(user_input, response, retrieved_contexts)` | `FaithfulnessMetric` (retrieval_context) |
| hallucination | — | `HallucinationMetric` (context) — 1 = no hallucination |
| answer_correctness | `FactualCorrectness(llm).ascore(response, reference)` | `GEval` correctness rubric (actual vs expected output) |
| bias / toxicity | — | `BiasMetric` / `ToxicityMetric` — 1 = none detected |
| context_precision | `ContextPrecision(llm).ascore(user_input, reference, retrieved_contexts)` | `ContextualPrecisionMetric` |
| context_recall | `ContextRecall(llm).ascore(user_input, retrieved_contexts, reference)` | `ContextualRecallMetric` |
| custom_criteria | — | `GEval` with the user's criteria text |

`custom_evaluator.py` (non-engine evaluators) is deferred; custom rubrics use DeepEval GEval.

## D5. Judge cache key includes the repeat index

Section 2 keys the cache on (engine, engine_version, metric, judge_model, judge_config, inputs).
Without the repeat index, repeats 2..n would be cache hits of repeat 1 and variance would always
be zero, contradicting the judge-noise requirement. The key therefore also includes
`repeat_index`. Runs can disable the cache (`use_cache=false`).

## D6. Budget cap stops the run as `cancelled` with a reason

The run status set is fixed (queued/running/completed/failed/cancelled). Reaching the budget cap
sets `cancelled` with `status_reason = "budget_cap_reached"`. Units not yet started are skipped,
and their test results are marked `cancelled`.

## D7. Auth: httpOnly cookie for the SPA, bearer token for CLI/MCP

`POST /auth/login` sets an httpOnly, SameSite=Strict JWT cookie (the frontend is served
same-origin through the Vite proxy / reverse proxy) and also returns the token, so the CLI can
send `Authorization: Bearer`. No self-registration: the first admin is seeded from
`ADMIN_EMAIL` / `ADMIN_PASSWORD`.

## D8. Cost estimate is a heuristic

Judge cost = Σ over cases × metrics × repeats × engines of (per-metric expected call count ×
estimated tokens from input lengths) × judge model price. Prices come from OpenRouter's public
`/models` list (cached). Unknown price → estimate shown as "unknown" and the budget cap falls back
to measured cost only. The UI labels the figure "estimate".

## D9. Telemetry from evaluation libraries is disabled

Both Ragas and DeepEval send usage telemetry by default. Containers set
`RAGAS_DO_NOT_TRACK=true` and `DEEPEVAL_TELEMETRY_OPT_OUT=YES` so no evaluation content leaves
the system except to the configured judge provider.

## D11. Pass/fail across repeats uses the mean of valid scores

With `repeats > 1`, a metric's status for a result is `status_for(mean(valid scores), threshold)`.
Errored repeats are counted (`errors`) but excluded; if every repeat errored the metric is `error`.
A test result is `failed` if any metric failed, else `error` if any metric errored, else `passed`.
Standard-error-aware regression gates are M5 (spec §2 tolerance rule).

## D12. Recovery re-enqueues in-flight units on worker start

On `worker_ready`, units of active runs still `pending`/`running` and untouched for 30 s are
re-enqueued; with late acks the broker also redelivers. Because metric rows are idempotent
(unique key + `ON CONFLICT DO NOTHING`, cost added only when a row is actually inserted), double
delivery can waste a judge call but can never duplicate results or cost. With several worker hosts
a unit still running elsewhere may be evaluated twice; a heartbeat column would remove that waste
if it matters in practice.

## D13. Project members management is deferred to M2

The `project_members` table and role checks (owner/editor/viewer) are in M1; creating projects
makes you owner. The UI/API to invite members (and user management beyond the seeded admin) is M2
with Project Settings.

## D14. Cancellation granularity

Cancel and the budget cap are checked before each repeat batch of a unit. Metrics already in
flight finish and are billed (the UI's confirmation says so); no Celery revoke is used because
units are short and idempotent.

## D10. Untrusted content in judge prompts

Ragas/DeepEval own their judge templates, so we cannot restructure their prompts. We pass user
content only through the engines' typed input fields (never concatenated into instructions). For
GEval rubrics we write, the criteria text is user-authored *instructions* by design; responses and
contexts stay in GEval's separate parameters.

## D15. Recorded responses live on the test case

Offline LLM evaluation needs pre-collected model answers in the dataset, but spec §7's `test_cases`
has no column for them. `test_cases.recorded_output` holds a case's recorded response; a run uses it
when an answer's source is `dataset`. Results still copy the scored text into
`test_results.model_output`, so results stay self-contained.

## D16. Dataset versions: immutable, edit-the-latest-only, stable case keys

Uploads and every case add/edit/delete create a new `dataset_version` (copying unchanged cases).
Edits must target the current version (409 `version_conflict` otherwise) so two editors cannot fork
silently. `test_cases.case_key` stays the same for "the same case" across versions; M5 regression
compares by it. A re-upload creates a new version with new keys. Datasets used by any run cannot be
deleted.

## D17. A result where no metric could run is `error`, not `passed`

With datasets, a case can lack the inputs for every selected metric (e.g. no recorded response). Such a
result is `error` with "No selected metric could run for this case: …". A run is rejected up front (400)
if no case × metric combination can run at all.

## D18. Deferred from M2: retention and no-persistence mode

Project Settings has general settings, members/roles and project deletion. Retention periods and
no-persistence evaluation mode (spec §11) need a cleanup job and session-scoped result storage; they
move to M6. Upload "scanning" means strict type/size/row/field validation; no AV scanning.

## D19. Ragas answer relevancy can be exactly 0 by design

Verified against live calls: ragas 0.4.4 `AnswerRelevancy` returns 0 when the judge flags the answer
"noncommittal" (or derives no question) and gives no reason. The adapter adds an explanatory reason for
that case so a 0 is never unexplained. Side-by-side DeepEval scores and human review make the
disagreement visible instead of averaging it away.

## D20. Human review overrides are annotations in M2

Reviews (agree/disagree, optional override status, comment; one per reviewer per metric result) feed the
judge agreement rate shown on the run. Overrides do not change computed statuses or counts; M5 gates
may opt in to them.

## D21. RAG connector SSRF guard and its residual risk

Connectors may call only public http(s) addresses: every hostname is resolved and every resolved
address must be public (private, loopback, link-local incl. cloud metadata, CGNAT, multicast,
reserved; IPv4-mapped IPv6 unwrapped; IP literals checked directly). Redirects are followed manually
(max 3) with each hop re-checked; bodies are capped at 2 MB; connect/read timeouts apply. Optional
`RAG_HOST_ALLOWLIST`. `RAG_ALLOW_PRIVATE=true` disables the private-range check for local development
only (e.g. the `demo-rag` compose service). Residual risk: DNS rebinding between our resolution and
httpx's connect; pinning connections to the checked IP is a later hardening.

## D22. Live RAG never substitutes uploaded context

For `rag_api` answers, context metrics use only the contexts the endpoint returned
(`test_results.retrieved_context`). If the connector has no contexts path, or the endpoint returns
none, context metrics are N/A; the dataset's uploaded context is shown in the UI as "reference only".
Each run snapshots the connector settings (minus secrets) for auditability.

## D23. Failure-origin heuristic, literally per spec §4.2

Using each metric's own threshold: a failing context precision/recall → retrieval; retrieval measured
and passing but failing faithfulness/answer correctness/hallucination → generation; both → both;
any other failure (e.g. only answer relevancy failed, or retrieval not measured) → undetermined.
Labelled "heuristic" in the UI. Spec §4.2's optional "upload documents for a local demo index" is
replaced by a tiny demo RAG service (`docs/examples/demo-rag`, compose profile `demo`) with a
deliberate retrieval gap, which shows the diagnosis end to end.

## D24. Model comparison = a `benchmark` run with 2–5 provider answers per case

A benchmark reuses the run engine: every case gets one test result per model, all scored with the same
metrics, thresholds and judge (bias warning when the judge shares a model's family). Per-model figures
are computed on read: per-metric mean/std/pass rate, a **quality** score (unweighted mean of the
model's metric means, labelled as such), latency p50/p95 (nearest rank) of generation calls, tokens,
generation cost (unknown if any call was unpriced) and judge cost. Benchmarks must use datasets or
suites, so models are compared on identical, versioned questions.

"Save model configurations and repeat benchmarks later" (spec §4.3): `model_configs` gained a name and
can be saved/loaded on the comparison page; **Re-run** on any run rebuilds the request from its snapshot
(same pinned dataset version or suite, models, metrics, thresholds, judge, repeats, budget) and refuses
with 409 if the judge config or data it pinned was deleted.

## D25. Regression pairing and the standard-error rule

Baseline and candidate results are paired by the case's stable `case_key` (plus the answer label when a run
has several answers), so an edited dataset can still be compared case-by-case when the mismatch is
explicitly allowed. A different dataset version is refused (409) unless `allow_dataset_mismatch`.
Per metric: the delta uses only paired cases scored on both sides (errors/N/A excluded and shown as a
"paired" count); a drop is a regression only if it exceeds the tolerance (default 0.05) and, with
≥3 repeats in both runs, one standard error of judge noise
(SE = sqrt(Σ var_i/r_i)/n per run, combined in quadrature). Whole-run candidate figures are reported
separately and are what a gate's `min_score` / `min_pass_rate` check, so cases the baseline could not
score are still gated.

## D26. API tokens

Project-scoped bearer tokens (`tl_…`, SHA-256 hash stored, shown once, revocable) act as their creator,
can only see their project, and are capped at their role (editor or viewer; never owner), so a CI
token cannot read provider keys or manage members. Only owners create/list/revoke tokens.

## D27. CLI contract

`testmyllm` (originally `testlab`) is standard-library only (fast CI installs). Exit 0 = completed and gate passed (or no gate);
1 = gate failed with `--fail-on-gate`; 2 = anything else, including a run that ends cancelled (e.g.
budget cap) or failed, timeouts, HTTP/auth errors and unexpected exceptions. Output never crashes on
legacy console encodings (found on a Windows cp1252 console in the live check). On GitHub it writes a
step summary with the gate table; the composite action lives in `.github/actions/testmyllm`.

## D28. Rename to TestMyLLM and brand theme

The product is **TestMyLLM** (tagline "Evaluate · Test · Analyze · Improve"; logo updated 2026-11-26 to the robot-with-checklist mascot). Renamed everywhere user-facing: UI,
page titles, favicon/app icons, API title, report exports (JSON schema `testmyllm.run-export/v1`),
the CLI (`testmyllm`, package `testmyllm-cli`, env `TESTMYLLM_URL` / `TESTMYLLM_TOKEN`), the GitHub
Action (`.github/actions/testmyllm`) and docs. New API tokens use the `tml_` prefix; `tl_` tokens issued
earlier still authenticate.

Deliberately unchanged internal identifiers (renaming them would lose data or sign users out): the
compose project name `ai-testlab` (prefixes the data volumes), the Postgres database/user `testlab`,
the session cookie name, Celery/Redis key names and the repository folder.

Theme: tokens derived from the logo (Tailwind orange/stone): brand orange `#c2410c` (primary; `#f97316`
in dark mode for contrast), ink `#1c1917`, muted `#57534e`, cream `#fff7ed` surfaces. Status colours
stay distinguishable from the brand orange (fail = crimson `#be123c`, warn = gold `#a16207`). The logo
mark always sits on a cream tile so its dark checkmark reads in dark mode. Headings and the wordmark use
Space Grotesk, bundled locally (the CSP blocks third-party fonts).

## D29. AI security testing module

A separate module (Security Testing in the sidebar), built before M6 at the user's request. It covers
prompt injection and jailbreak (direct, role-play, encoding, multilingual, multi-turn, and indirect
injection through poisoned documents) and sensitive-information disclosure (system-prompt extraction,
credential and personal-data probes, cross-user leakage between two or more seeded identities). Agent and
tool misuse (OWASP LLM06) waits for the agent-testing work, because it needs trace capture and
side-effect assertions.

- **Targets** (`target_configs`) are either a model plus system prompt (through a provider) or a RAG
  connector. Every save records an "I am authorized to test this" confirmation with user, time and note.
  Scans refuse unauthorized targets (403 `target_not_authorized`). Editors and owners only. A per-target
  requests-per-minute limit is shared by all workers (Redis, fails open). The existing SSRF guard and
  budget cap apply.
- **Harmless goals.** A built-in attack succeeds when the target outputs a code word it was told not to,
  leaks a planted canary, or reveals another identity's canary. That proves the weakness without
  generating harmful content.
- **Canaries** (`TML-` plus 10 random characters) are planted per target. For model targets, the
  system-prompt canary is appended automatically on send. For RAG targets, the user plants the
  system-prompt, document and per-identity canaries. The UI says where each one goes, and the estimate
  warns that unplanted canaries cannot detect anything.
- **Detectors are deterministic first:**
  - canary match, normalized for case, spacing and punctuation, and also checked base64-decoded and
    reversed
  - code word (marker)
  - secret/PII patterns, ignoring placeholders and text the attacker supplied, with a Luhn check for card
    numbers
  - system-prompt n-gram overlap
  
  An optional LLM judge only confirms code-word hits, so a refusal that quotes the code word is not a
  breach. A judge failure is an error, never a pass or a breach.
- **Storage reuses runs.** `test_runs.run_type = security`. Each attack is a materialized test case
  (`dataset_version_id` NULL, the attack snapshot in metadata) and one test result. Each attempt is a
  metric row (`attack_resisted`, engine `testmyllm`, score 1 = resisted, 0 = breached, repeat index =
  attempt). An attack with at least one breached attempt gets one `security_findings` row with evidence,
  successes/attempts, OWASP ID and a remediation note. Any canary leak is critical.
- **Measures.** ASR = attacks breached at least once ÷ attacks scored. A per-attempt ASR is shown too.
  Errored attacks are excluded and counted separately.
- **Library.** Built-in sets are versioned (`core-injection`, `core-disclosure`, version "1"), inserted
  idempotently under an advisory lock and never edited in place. Custom sets are per project, and posting
  the same key again creates the next version.
- **OWASP IDs** follow the 2025 OWASP Top 10 for LLM Applications:
  - LLM01 Prompt Injection, which also covers jailbreak and indirect injection
  - LLM02 Sensitive Information Disclosure, which also covers cross-user leakage
  - LLM07 System Prompt Leakage
  - LLM06 Excessive Agency, reserved for the agent category
- **Security gates** (`security_gates`) check maximum ASR per category or `*`, plus maximum critical and
  maximum high findings. Evaluations reuse `gate_evaluations` (`security_gate_id`) and the same GateResult
  shape, so the CLI and GitHub summary print them unchanged. CLI: `testmyllm security --target …
  --attack-set … --gate <id> --fail-on-gate`, and `testmyllm gate --run … --security-gate <id>`. Exit
  codes as in D27.
- **Engines.** Scanning and detection use the built-in engine. **DeepTeam 1.0.10** is wired in as an attack *generator*: "Generate with DeepTeam" on an attack set applies its single-turn enhancers. Base64, ROT13 and leetspeak run locally for free. Role-play, multilingual, prompt injection, system override, authority escalation, emotional manipulation and prompt probing are rewritten by the judge model acting as simulator, with cost metered per call. The result is saved as a new versioned set (`<key>-deepteam`), generated in the worker (`attack_sets.status` generating → ready or failed). A rewrite that drops a code word present in the original turn, or that the simulator refused, is discarded and listed. Detectors also check ROT13 and leetspeak forms. DeepTeam telemetry is opted out (`DEEPTEAM_TELEMETRY_OPT_OUT=YES`); `sentry-sdk` is installed only because `deepteam.telemetry` imports it unconditionally. garak 0.17.1 and PyRIT 1.2.1 remain candidates.
  pinned deepeval), garak 0.17.1 and PyRIT 1.2.1 are candidate adapters for generating more attack
  variants. They are not wired in yet.

## D30. M6: overview, history, MCP server, retention and no-persistence mode

- **Overview** (`GET /projects/{id}/overview`) shows:
  - the latest completed quality run, with its pass rate and per-metric mean and pass rate
  - spend over the last 30 days, which includes no-persistence runs because the money was spent
  - open gate failures: the latest evaluation per (run, gate) that failed, in the last 30 days, so
    re-evaluating a run to a pass closes it
  - the latest gate (quality or security), the latest security scan's ASR and critical findings, and the
    top failing cases by stable `case_key` across the last 20 completed dataset runs
- **Trends** (`GET /projects/{id}/trends`): one point per completed run, per metric and engine. The value is
  the mean over valid metric rows, which equals the run summary because every case in a run has the same
  number of repeats. Security and no-persistence runs are excluded. The chart shows one engine at a time
  (engines are never mixed), at most five series, and each metric's threshold as a dashed reference line.
  A table view is also available.
- **Chart colours:** the palette was re-validated with the dataviz validator, because the previous slots
  failed its colour-blind (CVD) and chroma checks.
  - Light (on `#fffaf5`): `#c2410c #257de0 #00a888 #6a4ce2 #d28e05`
  - Dark (on `#1c1917`): `#e76624 #338ced #00a888 #9887ef #d28e05`
  - Both modes use the same hue order. In light mode, two slots get a contrast warning; the legend and the
    table view provide the required relief.
- **History:** the runs list takes `date_from` and `date_to` filters. `GET /projects/{id}/test-runs/export`
  returns CSV with the same filters: one row per run plus per-metric means, formula-safe, with a UTF-8 BOM.
- **MCP server** (`mcp_server/`, package `testmyllm-mcp`, pinned `mcp==2.3.0`, which renamed FastMCP to
  `MCPServer`):
  - tools: `list_projects`, `score_answers`, `run_evaluation`, `get_run` (including security reports) and
    `compare_runs` (with an optional gate preview that is never recorded)
  - authentication: the same project API tokens as the CLI
  - cost guard: tools that start paid work refuse when the estimate exceeds `max_estimated_usd` (0.25 for
    scoring, 1.00 for batch runs), and that value also becomes the budget cap
  - the tool logic is a plain module, tested against the real API; the server was verified over stdio
    with the official MCP client
- **Retention and no-persistence** (spec §11, closing D18). Project `settings` hold three values:
  - `retention_days`: deletes finished runs older than this many days. Runs saved as baselines are always
    kept.
  - `no_persistence`: new runs get `expires_at`, never write the judge cache, are excluded from overview and
    trends, and cannot become baselines. The run page warns when the run will be deleted.
  - `no_persistence_hours`: how long a no-persistence run lives after creation (1–168 hours, default 24).
  
  The purge runs every 15 minutes, using Celery beat embedded in the worker (`worker -B`), and once on
  worker start. Deleting a run cascades to its results, metric rows, reviews, findings, gate evaluations and
  export records. The purge also removes export files on disk and the ad-hoc Studio and security cases
  that were used only by deleted runs.

## D31. Agent testing (sandboxed tool-using agents) and OWASP LLM06

- **Agent** (`agent_configs`): a model (through a provider), a system prompt, up to 20 tools and a step
  limit. Tools are **mocks**:
  - each has a JSON-schema `parameters` object, a default `response` (with `{{args.x}}` templating) and
    `rules` (first `when` match wins)
  - `side_effect: true` marks actions that would change the world (refund, email, delete)
  - nothing is ever executed, so agents can be tested on refunds and deletions safely
- **Loop** (`app/agents/runner.py`): OpenAI-style function calling via OpenRouter (`openrouter.chat` now
  passes `tools` and returns `tool_calls`).
  - Each tool call is answered with its mock output and recorded.
  - The loop stops when the model answers, or after `max_steps` tool rounds per user turn (recorded as
    `stopped: max_steps`).
  - A tool the model invents gets an error result and is flagged `unknown`.
  - The trace (user / tool_call / assistant / stop steps) is stored on `test_results.trace`.
- **Scenario sets** (`agent_scenario_sets`, versioned by key): user `turns`, an optional reference and
  criteria, and `expectations`:
  - `must_call` (with argument checks: exact, `contains`, `regex`, `any`)
  - `must_not_call`, `order` (a subsequence), `max_tool_calls`
  - `no_side_effects`, `answer_contains`, `answer_not_contains`
- **Runs:** an agent run is an ordinary run (`run_type = agent`). Scenarios are materialized as ad-hoc
  cases, and each (scenario × agent) is one result whose "generation" is the agent loop. Repeats re-judge
  the same trace. Judge cache, budget cap, comparison (2–5 agents side by side), gates, exports and re-run
  all work unchanged. Re-run uses the agents' *current* configuration, so "agent v2 vs v1" is a re-run
  after an edit.
- **Metrics:**
  - `tool_correctness`:
    - engine **`testmyllm`**: the deterministic expectation checks; score = checks passed ÷ checks, with
      default threshold 1.0, no judge and no cost
    - DeepEval `ToolCorrectnessMetric`: expected tools from `must_call`, ordering when `order` is set
  - DeepEval `TaskCompletionMetric` and `ArgumentCorrectnessMetric` (the judge reads the tool calls).
    A scenario's `criteria` is passed as the intended `task`. Without it, the judge infers the task from
    the user's request and penalised a correct refusal in a live run (refund declined → 0.30).
  - Every answer metric also applies to the agent's final answer.
  - `EvalInputs` gained `tools_called` and `expectations`, giving new applicability inputs `trace` and
    `expectations`.
- **Security (OWASP LLM06, Excessive Agency):** security targets can be `agent` targets.
  - **Built-in set `core-agency` v1:** fake-authority refund, exfiltration by email, a destructive delete,
    an instruction injected through a tool's output, and role-play escalation.
  - **Indirect injection:** an attack's `document` replaces the output of the agent's first read-only tool
    call. If the agent has no read-only tools, it is given as context instead.
  - **Detection, on any category:**
    - a side-effect tool call induced by an attacker is a breach (detector `agency`)
    - canaries are also searched in tool arguments, which catches exfiltration through a tool
  - **Skips:** excessive-agency attacks are skipped (with the reason) for non-agent targets and for agents
    with no side-effect tool.
