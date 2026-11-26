import {
  AlertTriangle,
  Ban,
  BarChart3,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  FileSearch,
  Flag,
  RotateCw,
  Search,
  Table2,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useLocation, useNavigate, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";
import { toast } from "sonner";

import { runPath } from "@/components/runs-table";
import { TraceView, type Trace } from "@/components/agent-trace";
import { SecurityReportView } from "@/components/security";
import { ComparisonView } from "@/components/comparison-view";
import { AnswerCard, CompareView } from "@/components/results";
import { ConfirmButton, EmptyState, ErrorState, Pagination, TableSkeleton } from "@/components/states";
import { EngineBadge, Score, StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
} from "@/components/ui/overlay";
import { Card, Input, NativeSelect, Progress, Skeleton, Textarea } from "@/components/ui/primitives";
import {
  exportUrl,
  isActive,
  useCancelRun,
  useMetricsCatalog,
  useProject,
  useRerun,
  useBaselineMutations,
  useReview,
  useRun,
  useRunResults,
  useRunSummary,
  type ResultParams,
} from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { metricLabel } from "@/lib/metrics";
import { cn, fmtDate, fmtDuration, fmtScore, fmtUsd } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { FailureOrigin, MetricResult, Run, RunSummary, RunWarning, TestResult } from "@/types";

type Snapshot = {
  source?: "studio" | "suite" | "dataset";
  dataset?: { id: number; name: string; version_id: number; version_number: number } | null;
  suite?: { id: number; name: string } | null;
  case_count?: number;
  judge?: { name: string; model: string; temperature: number; embedding_model: string };
  repeats?: number;
  metrics?: { metric: string; engine: string; threshold: number }[];
  outputs?: { label: string; source: string; model?: string; source_model?: string }[];
  prompt_template?: string;
};

const snap = (r: Run) => (r.config_snapshot ?? {}) as Snapshot;
export const isDatasetRun = (r: Run) => !!snap(r).source && snap(r).source !== "studio";

// ---------------------------------------------------------------- banner, stats, config
export function BiasBanner({ runId, warnings }: { runId: number; warnings: RunWarning[] }) {
  const key = `bias-dismissed:${runId}`;
  const [dismissed, setDismissed] = useState(() => {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  });
  const bias = warnings.filter((w) => w.type === "judge_bias");
  if (!bias.length || dismissed) return null;
  return (
    <div role="status" className="flex items-start gap-3 rounded-lg border border-warn/40 bg-warn-bg px-4 py-3 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
      <div className="flex-1 space-y-1">
        <p className="font-medium">Judge may favour its own family</p>
        {bias.map((w, i) => (
          <p key={i} className="text-muted-foreground">
            {w.message}
          </p>
        ))}
      </div>
      <button
        type="button"
        aria-label="Dismiss warning for this run"
        className="cursor-pointer rounded p-1 hover:bg-black/5"
        onClick={() => {
          setDismissed(true);
          try {
            localStorage.setItem(key, "1");
          } catch {
            /* ignore */
          }
        }}
      >
        <X className="size-4" />
      </button>
    </div>
  );
}

export function RunHeaderStats({ run, summary }: { run: Run; summary?: RunSummary }) {
  const active = isActive(run);
  const pct = (run.completed_tests / Math.max(1, run.total_tests)) * 100;
  const ag = summary?.agreement;
  return (
    <Card className="grid grid-cols-6 divide-x">
      <div className="col-span-2 space-y-2 p-4">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Progress</span>
          <span className="tabular text-muted-foreground">
            {run.completed_tests} / {run.total_tests} evaluations
          </span>
        </div>
        <Progress value={active ? Math.max(pct, 4) : pct} label="Run progress" />
        <div className="flex gap-3 text-xs">
          <span className="text-pass">{run.passed_tests} passed</span>
          <span className="text-fail">{run.failed_tests} failed</span>
          <span className="text-warn">{run.error_tests} error</span>
        </div>
      </div>
      <div className="p-4">
        <div className="text-xs text-muted-foreground">Duration</div>
        <div className="tabular mt-1 font-semibold">{fmtDuration(run.started_at, run.completed_at)}</div>
      </div>
      <div className="p-4">
        <div className="text-xs text-muted-foreground">Cost (actual / est.)</div>
        <div className="tabular mt-1 font-semibold">
          {fmtUsd(run.actual_cost_usd)} <span className="font-normal text-muted-foreground">/ {fmtUsd(run.estimated_cost_usd)}</span>
        </div>
        {run.budget_usd != null && <div className="text-xs text-muted-foreground">Cap {fmtUsd(run.budget_usd)}</div>}
      </div>
      <div className="p-4">
        <div className="text-xs text-muted-foreground">Repeats · cache</div>
        <div className="mt-1 font-semibold">
          {run.repeats}× <span className="font-normal text-muted-foreground">· {run.use_cache ? "on" : "off"}</span>
        </div>
      </div>
      <div className="p-4">
        <Tooltip content="Share of human reviews that agree with the judge's verdict. Low agreement means the metric may not be trustworthy on this data.">
          <div tabIndex={0} className="text-xs text-muted-foreground underline decoration-dotted">
            Judge agreement
          </div>
        </Tooltip>
        <div className="tabular mt-1 font-semibold">
          {ag?.rate != null ? `${Math.round(ag.rate * 100)}%` : "—"}
          <span className="ml-1 text-xs font-normal text-muted-foreground">{ag?.reviewed ? `${ag.reviewed} review(s)` : "no reviews"}</span>
        </div>
      </div>
    </Card>
  );
}

function ConfigSnapshot({ run, pid }: { run: Run; pid: number }) {
  const [open, setOpen] = useState(false);
  const s = snap(run);
  return (
    <Card>
      <button
        type="button"
        className="flex w-full cursor-pointer items-center gap-2 px-4 py-3 text-sm font-medium"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Config snapshot
        <span className="ml-2 truncate font-normal text-muted-foreground">
          {s.dataset ? `${s.dataset.name} v${s.dataset.version_number} · ` : ""}judge {s.judge?.model} ·{" "}
          {Object.entries(run.engine_versions).map(([k, v]) => `${k} ${v}`).join(" · ")}
        </span>
      </button>
      {open && (
        <dl className="grid grid-cols-[180px_1fr] gap-x-4 gap-y-2 border-t px-4 py-3 text-sm">
          <dt className="text-muted-foreground">Dataset version</dt>
          <dd>
            {s.dataset ? (
              <Link className="text-primary hover:underline" to={`/p/${pid}/datasets/${s.dataset.id}?v=${s.dataset.version_id}`}>
                {s.dataset.name} v{s.dataset.version_number}
              </Link>
            ) : (
              "Ad-hoc Studio case (no dataset)"
            )}
            {s.suite && <span className="text-muted-foreground"> · suite “{s.suite.name}”</span>}
            {s.case_count != null && <span className="text-muted-foreground"> · {s.case_count} case(s)</span>}
          </dd>
          <dt className="text-muted-foreground">Judge</dt>
          <dd>
            {s.judge?.name} — <span className="font-mono">{s.judge?.model}</span>, temperature {s.judge?.temperature}, embeddings{" "}
            <span className="font-mono">{s.judge?.embedding_model}</span>
          </dd>
          <dt className="text-muted-foreground">Answers</dt>
          <dd>
            {s.outputs
              ?.map((o) => `${o.label} (${o.source === "provider" ? o.model : o.source === "dataset" ? "recorded responses" : (o.source_model ?? "manual")})`)
              .join(", ")}
          </dd>
          <dt className="text-muted-foreground">Prompt template</dt>
          <dd className="whitespace-pre-wrap font-mono text-xs">{s.prompt_template}</dd>
          <dt className="text-muted-foreground">Repeats</dt>
          <dd>{s.repeats}</dd>
          <dt className="text-muted-foreground">Engine versions</dt>
          <dd className="flex gap-2">
            {Object.entries(run.engine_versions).map(([k, v]) => (
              <EngineBadge key={k} engine={k} version={String(v)} />
            ))}
          </dd>
        </dl>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- baseline
function SaveBaselineButton({ run, pid, canEdit }: { run: Run; pid: number; canEdit: boolean }) {
  const bm = useBaselineMutations(pid);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const why = !canEdit
    ? "Editors can save baselines"
    : run.status !== "completed"
      ? "Only completed runs can become baselines"
      : !isDatasetRun(run)
        ? "Baselines need a dataset-backed run (Studio cases have no pinned dataset version)"
        : null;
  if (why)
    return (
      <Tooltip content={why}>
        <span tabIndex={0}>
          <Button variant="outline" disabled>
            <Flag /> Save as baseline
          </Button>
        </span>
      </Tooltip>
    );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" onClick={() => (setName(run.run_name), setOpen(true))}>
        <Flag /> Save as baseline
      </Button>
      <DialogContent title="Save as baseline" description="Future runs on the same dataset version can be compared to this one and gated on regressions.">
        <div className="space-y-4">
          <label className="block space-y-1.5 text-sm font-medium">
            Name
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. v1.4 release" />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!name.trim()}
              loading={bm.create.isPending}
              onClick={async () => {
                try {
                  const b = await bm.create.mutateAsync({ run_id: run.id, name: name.trim() });
                  toast.success(`Baseline “${b.name}” saved`, {
                    action: { label: "Compare", onClick: () => navigate(`/p/${pid}/regression?baseline=${b.id}`) },
                  });
                  setOpen(false);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Save baseline
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------- RAG
export const ORIGIN_META: Record<FailureOrigin, { label: string; hint: string; cls: string }> = {
  retrieval: { label: "Retrieval", hint: "Context precision/recall below threshold", cls: "bg-[var(--chart-1)]/12 text-[var(--chart-1)]" },
  generation: { label: "Generation", hint: "Retrieval fine, but faithfulness/correctness below threshold", cls: "bg-[var(--chart-2)]/14 text-[var(--chart-2)]" },
  both: { label: "Both", hint: "Retrieval and generation metrics both below threshold", cls: "bg-fail-bg text-fail" },
  undetermined: {
    label: "Undetermined",
    hint: "Failed only on metrics not used for attribution (e.g. answer relevancy), or retrieval was not measured",
    cls: "bg-na-bg text-na",
  },
};

export function OriginChip({ origin }: { origin: string | null | undefined }) {
  if (!origin) return <span className="text-xs text-muted-foreground">—</span>;
  const m = ORIGIN_META[origin as FailureOrigin];
  return (
    <Tooltip content={`${m?.hint ?? origin} (heuristic)`}>
      <span tabIndex={0} className={cn("rounded px-1.5 py-0.5 text-xs font-medium", m?.cls)}>
        {m?.label ?? origin}
      </span>
    </Tooltip>
  );
}

function RetrievedContexts({ contexts }: { contexts: string[] | null }) {
  const [open, setOpen] = useState<Set<number>>(new Set([0]));
  return (
    <section className="space-y-1">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Retrieved context (captured from your RAG endpoint)
      </h4>
      {contexts == null ? (
        <p className="text-muted-foreground">The connector has no contexts path, so retrieval was not captured.</p>
      ) : contexts.length === 0 ? (
        <p className="rounded-md bg-warn-bg px-3 py-2 text-warn">The endpoint returned no contexts for this question.</p>
      ) : (
        <ol className="space-y-1.5">
          {contexts.map((c, i) => (
            <li key={i} className="rounded-md border">
              <button
                type="button"
                aria-expanded={open.has(i)}
                onClick={() => setOpen((s) => (s.has(i) ? new Set([...s].filter((x) => x !== i)) : new Set([...s, i])))}
                className="flex w-full cursor-pointer items-center gap-2 px-2 py-1.5 text-left"
              >
                {open.has(i) ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                <span className="text-xs font-semibold text-muted-foreground">#{i + 1}</span>
                {!open.has(i) && <span className="truncate text-muted-foreground">{c}</span>}
              </button>
              {open.has(i) && <p className="whitespace-pre-wrap px-3 pb-2">{c}</p>}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function FailureOriginSummary({ summary, onPick }: { summary?: RunSummary; onPick: (o: FailureOrigin) => void }) {
  const counts = summary?.failure_origins ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center gap-2">
        <h3 className="font-semibold">Failure origin</h3>
        <span className="rounded bg-muted px-1.5 text-[11px] text-muted-foreground">heuristic</span>
        <span className="text-xs text-muted-foreground">
          · uses each metric's threshold; retrieval = context precision/recall, generation = faithfulness/correctness/hallucination
        </span>
      </div>
      <div className="grid grid-cols-4 gap-3">
        {(Object.keys(ORIGIN_META) as FailureOrigin[]).map((o) => (
          <button
            key={o}
            type="button"
            onClick={() => onPick(o)}
            disabled={!counts[o]}
            className="cursor-pointer rounded-md border p-3 text-left hover:bg-accent/40 disabled:cursor-default disabled:opacity-60"
          >
            <OriginChip origin={o} />
            <div className="tabular mt-1.5 text-2xl font-semibold">{counts[o] ?? 0}</div>
            <div className="text-xs text-muted-foreground">{total ? `${Math.round(((counts[o] ?? 0) / total) * 100)}% of failures` : "no failures"}</div>
          </button>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------- human review
function ReviewControls({ m, runId, canReview }: { m: MetricResult; runId: number; canReview: boolean }) {
  const review = useReview(runId);
  const [disagreeing, setDisagreeing] = useState(false);
  const [comment, setComment] = useState("");
  const [override, setOverride] = useState<"" | "passed" | "failed">("");
  if (m.status !== "passed" && m.status !== "failed") return null;
  const mine = m.reviews.at(-1);
  const send = async (verdict: "agree" | "disagree") => {
    try {
      await review.mutateAsync({ metricResultId: m.id, verdict, comment, override_status: verdict === "disagree" && override ? override : null });
      toast.success(verdict === "agree" ? "Marked as agree" : "Disagreement recorded");
      setDisagreeing(false);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  return (
    <div className="space-y-1.5">
      {m.reviews.map((r) => (
        <p key={r.id} className={cn("text-xs", r.verdict === "agree" ? "text-pass" : "text-fail")}>
          {r.verdict === "agree" ? "✓ Agreed" : "✗ Disagreed"} · {r.reviewer_email}
          {r.override_status && ` · should be ${r.override_status}`}
          {r.comment && <span className="text-muted-foreground"> — “{r.comment}”</span>}
        </p>
      ))}
      {canReview && !disagreeing && (
        <div className="flex gap-1">
          <Button variant={mine?.verdict === "agree" ? "secondary" : "ghost"} size="sm" onClick={() => send("agree")} loading={review.isPending} aria-label="Agree with the judge">
            <ThumbsUp /> Agree
          </Button>
          <Button variant={mine?.verdict === "disagree" ? "secondary" : "ghost"} size="sm" onClick={() => setDisagreeing(true)} aria-label="Disagree with the judge">
            <ThumbsDown /> Disagree
          </Button>
        </div>
      )}
      {disagreeing && (
        <div className="space-y-2 rounded-md border p-2">
          <NativeSelect aria-label="Correct status" value={override} onChange={(e) => setOverride(e.target.value as typeof override)}>
            <option value="">No override</option>
            <option value="passed">Should have passed</option>
            <option value="failed">Should have failed</option>
          </NativeSelect>
          <Textarea rows={2} placeholder="Why? (optional)" aria-label="Comment" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="flex justify-end gap-1">
            <Button variant="ghost" size="sm" onClick={() => setDisagreeing(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => send("disagree")} loading={review.isPending}>
              Save disagreement
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function CaseDrawer({ result, runId, canReview, onClose }: { result: TestResult | null; runId: number; canReview: boolean; onClose: () => void }) {
  const catalog = useMetricsCatalog();
  return (
    <Dialog open={!!result} onOpenChange={(o) => !o && onClose()}>
      {result && (
        <DialogContent side="right" title={`Case detail · ${result.output_label}`} description={`Result #${result.id}`}>
          <div className="space-y-5 text-sm">
            <div className="flex items-center gap-2">
              <StatusChip status={result.status} />
              {result.case.tags?.map((t) => (
                <span key={t} className="rounded border px-1.5 text-xs text-muted-foreground">
                  {t}
                </span>
              ))}
            </div>
            {result.error_message && <p className="rounded-md bg-warn-bg px-3 py-2 text-warn">{result.error_message}</p>}
            <section className="space-y-1">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Input</h4>
              <p className="whitespace-pre-wrap">{result.case.input}</p>
            </section>
            {result.case.expected_output && (
              <section className="space-y-1">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Expected</h4>
                <p className="whitespace-pre-wrap">{result.case.expected_output}</p>
              </section>
            )}
            <section className="space-y-1">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Actual output</h4>
              <p className="whitespace-pre-wrap rounded-md bg-muted/50 p-3">{result.model_output ?? "—"}</p>
            </section>
            {result.output_source === "rag_api" && (
              <RetrievedContexts contexts={result.retrieved_context} />
            )}
            {result.trace && (
              <section className="space-y-1">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Agent trace</h4>
                <TraceView trace={result.trace as unknown as Trace} />
              </section>
            )}
            {result.case.provided_context?.length ? (
              <section className="space-y-1">
                <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {result.output_source === "rag_api" ? "Uploaded context (reference only, not used for scoring)" : "Context"}
                </h4>
                <ol className="space-y-2">
                  {result.case.provided_context.map((c, i) => (
                    <li key={i} className="rounded-md border p-2">
                      <span className="mr-2 text-xs font-semibold text-muted-foreground">#{i + 1}</span>
                      <span className="whitespace-pre-wrap">{c}</span>
                    </li>
                  ))}
                </ol>
              </section>
            ) : null}
            <section className="space-y-2">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Metric results per repeat · human review</h4>
              <div className="divide-y rounded-md border">
                {result.metrics.map((m) => (
                  <div key={m.id} className="grid grid-cols-[1fr_auto] gap-3 p-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{metricLabel(catalog.data, m.metric_name)}</span>
                        <EngineBadge engine={m.engine} version={m.engine_version} />
                        <span className="text-xs text-muted-foreground">repeat {m.repeat_index + 1}</span>
                        {m.cache_hit && <span className="text-xs text-muted-foreground">· cached</span>}
                      </div>
                      {(m.reason || m.error_message) && (
                        <p className="whitespace-pre-wrap text-xs text-muted-foreground">{m.status === "error" ? m.error_message : m.reason}</p>
                      )}
                      <p className="tabular text-[11px] text-muted-foreground">
                        {m.input_tokens != null ? `${m.input_tokens}/${m.output_tokens ?? 0} tokens · ` : ""}
                        {m.judge_cost_usd != null ? `${fmtUsd(m.judge_cost_usd)} · ` : ""}
                        {m.execution_time_ms != null ? `${m.execution_time_ms} ms` : ""}
                      </p>
                      <ReviewControls m={m} runId={runId} canReview={canReview} />
                    </div>
                    <Score
                      compact
                      score={m.score}
                      threshold={m.threshold}
                      higherIsBetter={m.higher_is_better}
                      status={m.status}
                      reason={m.status === "error" ? m.error_message : m.reason}
                      engine={m.engine}
                      engineVersion={m.engine_version}
                      judgeModel={m.judge_model}
                    />
                  </div>
                ))}
              </div>
            </section>
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}

// ---------------------------------------------------------------- summary
function Histogram({ row }: { row: RunSummary["metrics"][number] }) {
  return (
    <div className="h-28" role="img" aria-label={`Score distribution for ${row.metric_name} (${row.engine})`}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={row.histogram.map((b) => ({ bin: `${b.start.toFixed(1)}`, count: b.count }))} margin={{ top: 4, right: 4, bottom: 0, left: -28 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis dataKey="bin" tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} interval={1} />
          <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} />
          <RTooltip
            contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            labelFormatter={(l) => `${l}–${(Number(l) + 0.1).toFixed(1)}`}
          />
          <Bar dataKey="count" fill={row.engine === "ragas" ? "var(--chart-1)" : "var(--chart-2)"} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function MetricSummary({ summary }: { summary?: RunSummary }) {
  const catalog = useMetricsCatalog();
  const [asTable, setAsTable] = useState(false);
  if (!summary) return <Skeleton className="h-48 w-full" />;
  if (!summary.metrics.length) return <p className="text-sm text-muted-foreground">No metric results yet.</p>;
  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
          {asTable ? <BarChart3 /> : <Table2 />} {asTable ? "View histograms" : "View as table"}
        </Button>
      </div>
      {!asTable ? (
        <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
          {summary.metrics.map((m) => (
            <Card key={`${m.metric_name}${m.engine}`} className="space-y-2 p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{metricLabel(catalog.data, m.metric_name)}</span>
                <EngineBadge engine={m.engine} version={m.engine_version} />
              </div>
              <div className="flex items-baseline gap-3 text-sm">
                <span className="tabular text-xl font-semibold">{fmtScore(m.mean)}</span>
                <span className="tabular text-muted-foreground">± {m.std == null ? "—" : m.std.toFixed(2)}</span>
                <span className="tabular ml-auto">{m.pass_rate == null ? "—" : `${Math.round(m.pass_rate * 100)}% pass`}</span>
              </div>
              <Histogram row={m} />
              <p className="text-xs text-muted-foreground">
                threshold {m.higher_is_better ? "≥" : "≤"} {m.threshold?.toFixed(2)} · n={m.n}
                {m.errors ? ` · ${m.errors} error` : ""}
                {m.not_applicable ? ` · ${m.not_applicable} N/A` : ""}
              </p>
            </Card>
          ))}
        </div>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                {["Metric", "Engine", "Mean", "Std", "Pass rate", "Passed", "Failed", "Errors", "N/A"].map((h, i) => (
                  <th key={h} className={cn("px-4 py-2 font-medium", i > 1 && "text-right")}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summary.metrics.map((m) => (
                <tr key={`${m.metric_name}${m.engine}`} className="border-b last:border-0">
                  <td className="px-4 py-2">{metricLabel(catalog.data, m.metric_name)}</td>
                  <td className="px-4 py-2">
                    <EngineBadge engine={m.engine} />
                  </td>
                  <td className="tabular px-4 py-2 text-right">{fmtScore(m.mean)}</td>
                  <td className="tabular px-4 py-2 text-right">{m.std == null ? "—" : m.std.toFixed(3)}</td>
                  <td className="tabular px-4 py-2 text-right">{m.pass_rate == null ? "—" : `${Math.round(m.pass_rate * 100)}%`}</td>
                  <td className="tabular px-4 py-2 text-right">{m.passed}</td>
                  <td className="tabular px-4 py-2 text-right">{m.failed}</td>
                  <td className="tabular px-4 py-2 text-right">{m.errors}</td>
                  <td className="tabular px-4 py-2 text-right">{m.not_applicable}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      <p className="text-xs text-muted-foreground">
        Each case contributes its mean over repeats. Errors and N/A are counted, never averaged. Ragas and DeepEval are shown separately.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- dataset results table
export function ResultsTable({ run, preset, canReview }: { run: Run; preset?: ResultParams; canReview: boolean }) {
  const catalog = useMetricsCatalog();
  const s = snap(run);
  const cols = s.metrics ?? [];
  const rag = run.run_type === "rag";
  const [page, setPage] = useState(1);
  const [origin, setOrigin] = useState(preset?.failure_origin ?? "");
  useEffect(() => setOrigin(preset?.failure_origin ?? ""), [preset?.failure_origin]);
  const [status, setStatus] = useState(preset?.status ?? "");
  const [label, setLabel] = useState("");
  const [metric, setMetric] = useState("");
  const [metricStatus, setMetricStatus] = useState("");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [drawerId, setDrawerId] = useState<number | null>(null);
  useEffect(() => {
    const t = setTimeout(() => (setDebounced(q), setPage(1)), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [status, label, metric, metricStatus, origin]);
  const [mName, mEngine] = metric ? metric.split("|") : [undefined, undefined];
  const params: ResultParams = {
    page,
    page_size: 50,
    status: status || undefined,
    label: label || undefined,
    q: debounced || undefined,
    metric: mName,
    engine: mEngine,
    metric_status: metric && metricStatus ? metricStatus : undefined,
    failure_origin: origin || undefined,
  };
  const results = useRunResults(run.id, isActive(run), `${run.status}:${run.completed_tests}`, params);
  const items = results.data?.items ?? [];
  const labels = results.data?.labels ?? [];
  const drawer = items.find((i) => i.id === drawerId) ?? null;

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b p-3">
        <div className="relative w-64">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search input or reference" aria-label="Search results" className="pl-8" />
        </div>
        {!preset?.status && (
          <div className="w-40">
            <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
              <option value="">All statuses</option>
              <option value="passed">Passed</option>
              <option value="failed">Failed</option>
              <option value="error">Error</option>
              <option value="pending,running">In progress</option>
              <option value="cancelled">Cancelled</option>
            </NativeSelect>
          </div>
        )}
        {rag && (
          <div className="w-44">
            <NativeSelect value={origin} onChange={(e) => setOrigin(e.target.value)} aria-label="Filter by failure origin">
              <option value="">Any failure origin</option>
              {(Object.keys(ORIGIN_META) as FailureOrigin[]).map((o) => (
                <option key={o} value={o}>
                  {ORIGIN_META[o].label}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}
        {labels.length > 1 && (
          <div className="w-40">
            <NativeSelect value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Filter by answer">
              <option value="">All answers</option>
              {labels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </NativeSelect>
          </div>
        )}
        <div className="w-56">
          <NativeSelect value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Filter by metric">
            <option value="">Any metric</option>
            {cols.map((c) => (
              <option key={`${c.metric}|${c.engine}`} value={`${c.metric}|${c.engine}`}>
                {metricLabel(catalog.data, c.metric)} ({c.engine})
              </option>
            ))}
          </NativeSelect>
        </div>
        {metric && (
          <div className="w-40">
            <NativeSelect value={metricStatus} onChange={(e) => setMetricStatus(e.target.value)} aria-label="Metric status">
              <option value="">any status</option>
              <option value="failed">failed</option>
              <option value="passed">passed</option>
              <option value="error">error</option>
              <option value="not_applicable">N/A</option>
            </NativeSelect>
          </div>
        )}
        <span className="tabular ml-auto text-sm text-muted-foreground">{results.data?.total ?? 0} result(s)</span>
      </div>
      {results.isLoading && <TableSkeleton rows={8} cols={5} />}
      {results.isError && (
        <div className="p-4">
          <ErrorState error={results.error} onRetry={() => results.refetch()} />
        </div>
      )}
      {results.data && items.length === 0 && (
        <div className="p-5">
          <EmptyState icon={FileSearch} title="No results match" description={isActive(run) ? "Results appear as they complete." : "Change the filters."} />
        </div>
      )}
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                <th className="px-3 py-2 font-medium">Input</th>
                {labels.length > 1 && <th className="px-3 py-2 font-medium">Answer</th>}
                <th className="px-3 py-2 font-medium">Status</th>
                {rag && <th className="px-3 py-2 font-medium">Origin</th>}
                {cols.map((c) => (
                  <th key={`${c.metric}|${c.engine}`} className="px-3 py-2 font-medium">
                    {metricLabel(catalog.data, c.metric)}
                    <div className="font-normal">
                      <EngineBadge engine={c.engine} />
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr
                  key={r.id}
                  tabIndex={0}
                  onClick={() => setDrawerId(r.id)}
                  onKeyDown={(e) => e.key === "Enter" && setDrawerId(r.id)}
                  className="cursor-pointer border-b align-top last:border-0 hover:bg-accent/40"
                >
                  <td className="max-w-96 px-3 py-2.5">
                    <span className="line-clamp-2">{r.case.input}</span>
                    {r.error_message && <span className="mt-1 line-clamp-1 text-xs text-warn">{r.error_message}</span>}
                  </td>
                  {labels.length > 1 && <td className="px-3 py-2.5">{r.output_label}</td>}
                  <td className="px-3 py-2.5">
                    <StatusChip status={r.status} />
                  </td>
                  {rag && (
                    <td className="px-3 py-2.5">
                      <OriginChip origin={r.failure_origin} />
                    </td>
                  )}
                  {cols.map((c) => {
                    const a = r.aggregates.find((x) => x.metric_name === c.metric && x.engine === c.engine);
                    return (
                      <td key={`${c.metric}|${c.engine}`} className="px-3 py-2.5">
                        {a ? (
                          <Score
                            compact
                            score={a.mean}
                            threshold={a.threshold}
                            higherIsBetter={a.higher_is_better}
                            status={a.status}
                            engine={a.engine}
                            engineVersion={a.engine_version}
                            judgeModel={a.judge_model}
                            n={a.n}
                            std={a.std}
                            reason={a.reason}
                          />
                        ) : (
                          <span className="text-xs text-muted-foreground">{r.status === "pending" || r.status === "running" ? "…" : "—"}</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {results.data && results.data.total > 50 && <Pagination page={page} pageSize={50} total={results.data.total} onPage={setPage} />}
      <CaseDrawer result={drawer} runId={run.id} canReview={canReview} onClose={() => setDrawerId(null)} />
    </Card>
  );
}

// ---------------------------------------------------------------- studio (few answers): cards
export function RunResultsBody({ run, canReview = true, only }: { run: Run; canReview?: boolean; only?: "failures" }) {
  const progressSig = `${run.status}:${run.completed_tests}`;
  const results = useRunResults(run.id, isActive(run), progressSig);
  const [drawerId, setDrawerId] = useState<number | null>(null);
  const all = results.data?.items ?? [];
  const items = only ? all.filter((r) => r.status === "failed" || r.status === "error") : all;
  const drawer = all.find((r) => r.id === drawerId) ?? null;

  if (results.isLoading) return <Skeleton className="h-64 w-full" />;
  if (results.isError) return <ErrorState error={results.error} onRetry={() => results.refetch()} />;
  if (only && items.length === 0) return <p className="text-sm text-muted-foreground">No failed or errored answers{isActive(run) ? " so far" : ""}.</p>;
  return (
    <>
      <div className="space-y-4">
        {!only && items.length > 1 && <CompareView results={items} />}
        {items.map((r) => (
          <div key={r.id} className="space-y-1">
            <AnswerCard result={r} defaultOpen={items.length === 1} />
            <div className="flex justify-end">
              <Button variant="ghost" size="sm" onClick={() => setDrawerId(r.id)}>
                <FileSearch /> Case detail & review
              </Button>
            </div>
          </div>
        ))}
      </div>
      <CaseDrawer result={drawer} runId={run.id} canReview={canReview} onClose={() => setDrawerId(null)} />
    </>
  );
}

// ---------------------------------------------------------------- page
export function RunDetailPage() {
  const { projectId, runId } = useParams();
  const pid = Number(projectId);
  const id = Number(runId);
  const project = useProject(pid);
  const run = useRun(id);
  const r = run.data;
  const summary = useRunSummary(id, `${r?.status}:${r?.completed_tests}`);
  const cancel = useCancelRun();
  const canEdit = project.data?.role !== "viewer";
  const [tab, setTab] = useState<string | null>(null);
  const rerun = useRerun();
  const navigate = useNavigate();
  const location = useLocation();
  const [originPick, setOriginPick] = useState<FailureOrigin | null>(null);

  useEffect(() => {
    if (r?.status_reason === "budget_cap_reached") toast.warning("This run stopped because it reached its budget cap.", { id: `budget-${id}` });
  }, [r?.status_reason, id]);

  const failures = useMemo(() => {
    const sc = summary.data?.status_counts ?? {};
    return (sc.failed ?? 0) + (sc.error ?? 0);
  }, [summary.data]);

  if (run.isLoading)
    return (
      <div className="p-6">
        <TableSkeleton rows={6} />
      </div>
    );
  if (run.isError || !r)
    return (
      <div className="p-6">
        <ErrorState error={run.error} onRetry={() => run.refetch()} />
      </div>
    );
  const canonical = runPath(pid, r);
  if (canonical !== `/p/${pid}/runs/${r.id}` && location.pathname !== canonical) return <Navigate to={canonical} replace />;
  const dataset = isDatasetRun(r);
  const benchmark = r.run_type === "benchmark";
  const security = r.run_type === "security";
  const activeTab = tab ?? (benchmark ? "comparison" : security ? "security" : "results");

  return (
    <>
      <PageHeader
        crumbs={[
          security
            ? { label: "Security Testing", to: `/p/${pid}/security?tab=scans` }
            : r.run_type === "agent"
              ? { label: "Agent Testing", to: `/p/${pid}/studio/agents?tab=runs` }
              : { label: "Test Runs", to: `/p/${pid}/runs` },
          { label: r.run_name },
        ]}
        title={
          <span className="flex items-center gap-3">
            {r.run_name}
            <StatusChip status={r.status} label={r.status_reason === "budget_cap_reached" ? "Stopped: budget cap" : undefined} />
          </span>
        }
        description={`Run #${r.id} · ${r.run_type.toUpperCase()} · created ${fmtDate(r.created_at)}`}
        actions={
          <>
            {isActive(r) && canEdit && (
              <ConfirmButton
                variant="outline"
                title="Cancel this run?"
                confirmLabel="Cancel run"
                consequences="Evaluations not yet started are skipped. Metrics already scored are kept, and in-flight judge calls finish and are billed."
                onConfirm={async () => {
                  try {
                    await cancel.mutateAsync(r.id);
                    toast.success("Run cancelled");
                  } catch (e) {
                    toast.error(errorMessage(e));
                  }
                }}
              >
                <Ban /> Cancel
              </ConfirmButton>
            )}
            {canEdit && !isActive(r) && (
              <Tooltip content="Run again with the same pinned dataset version, models, metrics and judge">
                <Button
                  variant="outline"
                  loading={rerun.isPending}
                  onClick={async () => {
                    try {
                      const next = await rerun.mutateAsync(r.id);
                      toast.success("Re-run started");
                      setTab(null);
                      navigate(runPath(pid, next));
                    } catch (e) {
                      toast.error(errorMessage(e));
                    }
                  }}
                >
                  <RotateCw /> Re-run
                </Button>
              </Tooltip>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">
                  <Download /> Export <ChevronDown className="opacity-60" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {(
                  [
                    ["csv", "CSV", "flat, one row per case × metric"],
                    ["json", "JSON", "full normalized data"],
                    ["html", "HTML report", "summary + failed cases"],
                    ["pdf", "PDF report", "printable report"],
                  ] as const
                ).map(([f, label, hint]) => (
                  <DropdownMenuItem key={f} asChild>
                    <a href={exportUrl(r.id, f)} download>
                      <span className="flex-1">{label}</span>
                      <span className="text-xs text-muted-foreground">{hint}</span>
                    </a>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            {!security && <SaveBaselineButton run={r} pid={pid} canEdit={canEdit} />}
          </>
        }
      />
      <div className="space-y-4 p-6">
        {r.expires_at && (
          <div role="status" className="flex items-center gap-2 rounded-lg border border-warn/40 bg-warn-bg px-4 py-3 text-sm text-warn">
            <Clock className="size-4 shrink-0" />
            No-persistence mode: this run and its results will be deleted on {fmtDate(r.expires_at)}. Export anything you need before then.
          </div>
        )}
        <BiasBanner runId={r.id} warnings={r.warnings ?? []} />
        <RunHeaderStats run={r} summary={summary.data} />
        {r.run_type === "rag" && (
          <FailureOriginSummary summary={summary.data} onPick={(o) => (setOriginPick(o), setTab("results"))} />
        )}
        {!security && <ConfigSnapshot run={r} pid={pid} />}
        <Tabs value={activeTab} onValueChange={setTab}>
          <TabsList>
            {benchmark && <TabsTrigger value="comparison">Comparison</TabsTrigger>}
            {security && <TabsTrigger value="security">Security report</TabsTrigger>}
            <TabsTrigger value="results">{security ? "Attacks" : "Results"}</TabsTrigger>
            {!security && <TabsTrigger value="summary">Metric summary</TabsTrigger>}
            <TabsTrigger value="failures">
              Failures {failures > 0 && <span className="rounded bg-fail-bg px-1 text-xs text-fail">{failures}</span>}
            </TabsTrigger>
            <TabsTrigger value="config">Config</TabsTrigger>
          </TabsList>
          {benchmark && (
            <TabsContent value="comparison" className="mt-4">
              <ComparisonView run={r} />
            </TabsContent>
          )}
          {security && (
            <TabsContent value="security" className="mt-4">
              <SecurityReportView run={r} pid={pid} canEdit={canEdit} />
            </TabsContent>
          )}
          <TabsContent value="results" className="mt-4">
            {dataset ? (
              <ResultsTable run={r} canReview={canEdit} preset={originPick ? { failure_origin: originPick } : undefined} />
            ) : (
              <RunResultsBody run={r} canReview={canEdit} />
            )}
          </TabsContent>
          <TabsContent value="summary" className="mt-4">
            <MetricSummary summary={summary.data} />
          </TabsContent>
          <TabsContent value="failures" className="mt-4">
            {dataset ? (
              <ResultsTable run={r} preset={{ status: "failed,error" }} canReview={canEdit} />
            ) : (
              <RunResultsBody run={r} canReview={canEdit} only="failures" />
            )}
          </TabsContent>
          <TabsContent value="config" className="mt-4">
            <Card className="p-4">
              <pre className="max-h-[60vh] overflow-auto text-xs leading-relaxed">
                {JSON.stringify({ config_snapshot: r.config_snapshot, engine_versions: r.engine_versions }, null, 2)}
              </pre>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
