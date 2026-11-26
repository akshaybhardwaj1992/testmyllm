import { ArrowLeft, ArrowRight, ArrowUpRight, Database, FlaskConical, Info, Minus, Play, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { ConnectorEditor } from "@/components/connector-editor";
import { MetricPicker } from "@/components/metric-picker";
import { ConfirmButton, EmptyState, TableSkeleton } from "@/components/states";
import { StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Card, Field, Input, Label, NativeSelect, Switch } from "@/components/ui/primitives";
import {
  estimateRun,
  useCases,
  useConnectorMutations,
  useConnectors,
  useCreateRun,
  useDatasets,
  useJudges,
  useMetricsCatalog,
  useRun,
  useRunSummary,
  useVersions,
} from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { defaultMetricState, selectedMetrics, type MetricState } from "@/lib/metrics";
import { cn, fmtUsd } from "@/lib/utils";
import { FieldBadges } from "@/pages/DatasetsPage";
import { BiasBanner, FailureOriginSummary, ResultsTable, RunHeaderStats } from "@/pages/RunDetailPage";
import { errorMessage } from "@/services/api";
import type { Estimate, FailureOrigin, InputName, RunCreate } from "@/types";

const STEPS = ["Data", "Connector", "Metrics", "Estimate & run", "Results"] as const;

/** RAG Evaluation wizard (spec §10.2): offline datasets or a live RAG endpoint, with failure diagnosis. */
export function RAGPage() {
  const pid = Number(useParams().projectId);
  const datasets = useDatasets(pid);
  const connectors = useConnectors(pid);
  const cm = useConnectorMutations(pid);
  const judges = useJudges(pid);
  const catalog = useMetricsCatalog();
  const create = useCreateRun();

  const [step, setStep] = useState(0);
  const [datasetId, setDatasetId] = useState(0);
  const versions = useVersions(datasetId);
  const [versionId, setVersionId] = useState(0);
  const [mode, setMode] = useState<"live" | "offline">("live");
  const [connectorId, setConnectorId] = useState<number | "new">("new");
  const [metrics, setMetrics] = useState<MetricState>({});
  const [judgeId, setJudgeId] = useState(0);
  const [repeats, setRepeats] = useState(1);
  const [budget, setBudget] = useState("");
  const [useCache, setUseCache] = useState(true);
  const [runName, setRunName] = useState("");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [runId, setRunId] = useState<number | null>(null);

  const version = versions.data?.find((v) => v.id === versionId);
  const fields = version?.fields_present ?? [];
  const connector = connectors.data?.find((c) => c.id === connectorId);
  const sample = useCases(versionId, { page: 1, page_size: 1 }).data?.items[0]?.input ?? "";
  const contextsMapped = !!(connector?.response_mapping as Record<string, string> | undefined)?.contexts_path;

  useEffect(() => {
    if (!datasetId && datasets.data?.length) setDatasetId(datasets.data[0].id);
  }, [datasets.data, datasetId]);
  useEffect(() => {
    const ds = datasets.data?.find((d) => d.id === datasetId);
    if (ds?.current_version) setVersionId(ds.current_version.id);
  }, [datasetId, datasets.data]);
  useEffect(() => {
    if (connectorId === "new" && connectors.data?.length) setConnectorId(connectors.data[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectors.data]);
  useEffect(() => {
    if (judges.data?.length && !judgeId) setJudgeId((judges.data.find((j) => j.is_default) ?? judges.data[0]).id);
  }, [judges.data, judgeId]);
  useEffect(() => {
    if (mode === "offline" && !fields.includes("response")) setMode("live");
  }, [fields, mode]);

  const present = useMemo(() => {
    const s = new Set<InputName>(["question", "response"]);
    if (fields.includes("reference")) s.add("reference");
    if (fields.includes("criteria")) s.add("criteria");
    if (mode === "offline" ? fields.includes("context") : contextsMapped) s.add("context");
    return s;
  }, [fields, mode, contextsMapped]);

  // sensible RAG defaults once per data/mode choice
  const defaultsKey = `${versionId}:${mode}:${contextsMapped}`;
  const lastDefaults = useRef("");
  useEffect(() => {
    if (!catalog.data || lastDefaults.current === defaultsKey) return;
    lastDefaults.current = defaultsKey;
    setMetrics(defaultMetricState(catalog.data, ["context_precision", "context_recall", "faithfulness", "answer_correctness", "answer_relevancy"]));
  }, [catalog.data, defaultsKey]);

  const chosen = catalog.data ? selectedMetrics(metrics, catalog.data, present) : [];
  const hasRetrieval = chosen.some((m) => m.metric === "context_precision" || m.metric === "context_recall");
  const hasGeneration = chosen.some((m) => ["faithfulness", "answer_correctness", "hallucination"].includes(m.metric));
  const budgetValid = budget === "" || (Number(budget) > 0 && Number(budget) <= 10000);

  const payload: RunCreate | null =
    versionId && judgeId && chosen.length && budgetValid && (mode === "offline" || typeof connectorId === "number")
      ? {
          project_id: pid,
          run_type: "rag",
          run_name: runName.trim() || null,
          judge_config_id: judgeId,
          repeats,
          budget_usd: budget ? Number(budget) : null,
          use_cache: useCache,
          metrics: chosen,
          dataset_version_id: versionId,
          outputs: [
            mode === "live"
              ? { label: connector?.name ?? "RAG", source: "rag_api", connector_id: connectorId as number, max_tokens: 1000 }
              : { label: "Recorded", source: "dataset", max_tokens: 1000 },
          ],
        }
      : null;

  const sig = payload && step === 3 ? JSON.stringify(payload) : "";
  const req = useRef(0);
  useEffect(() => {
    if (!sig) return;
    const my = ++req.current;
    const t = setTimeout(async () => {
      try {
        const e = await estimateRun(JSON.parse(sig));
        if (my === req.current) (setEstimate(e), setEstimateError(null));
      } catch (e) {
        if (my === req.current) (setEstimate(null), setEstimateError(errorMessage(e)));
      }
    }, 500);
    return () => clearTimeout(t);
  }, [sig]);

  const canNext = [
    !!versionId && (mode === "live" || fields.includes("response")),
    mode === "offline" || typeof connectorId === "number",
    chosen.length > 0,
    !!payload,
  ][step];

  if (datasets.data?.length === 0) {
    return (
      <>
        <PageHeader crumbs={[{ label: "Test Studio" }, { label: "RAG Evaluation" }]} title="RAG Evaluation" />
        <div className="p-6">
          <EmptyState
            icon={Database}
            title="Start with a dataset"
            description="Upload questions with reference answers (and, for offline evaluation, recorded answers and contexts)."
            action={<Button asChild><Link to={`/p/${pid}/datasets`}>Go to Datasets</Link></Button>}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Studio" }, { label: "RAG Evaluation" }]}
        title="RAG Evaluation"
        description="Evaluate retrieval and generation separately, and find out whether failures come from retrieval or from the answer."
      />
      <div className="space-y-5 p-6">
        <ol className="flex items-center gap-2 text-sm" aria-label="Wizard steps">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-2" aria-current={i === step ? "step" : undefined}>
              <span className={cn("flex size-6 items-center justify-center rounded-full border text-xs", i <= step ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground")}>
                {i + 1}
              </span>
              <span className={i === step ? "font-medium" : "text-muted-foreground"}>{s}</span>
              {i < STEPS.length - 1 && <span className="mx-1 h-px w-8 bg-border" />}
            </li>
          ))}
        </ol>

        {step === 0 && (
          <Card className="space-y-5 p-5">
            {datasets.isLoading ? (
              <TableSkeleton rows={3} cols={2} />
            ) : (
              <div className="grid grid-cols-2 gap-4">
                <Field label="Dataset" htmlFor="rg-ds">
                  <NativeSelect id="rg-ds" value={datasetId} onChange={(e) => setDatasetId(Number(e.target.value))}>
                    {datasets.data?.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Version" htmlFor="rg-v" hint="Runs pin this version for reproducibility.">
                  <NativeSelect id="rg-v" value={versionId} onChange={(e) => setVersionId(Number(e.target.value))}>
                    {versions.data?.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number} · {v.row_count} cases
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              </div>
            )}
            {version && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                Fields in this version: <FieldBadges fields={fields} />
              </p>
            )}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Mode</legend>
              <div className="grid grid-cols-2 gap-3">
                {(
                  [
                    ["live", "Live: call your RAG API", "Each question is sent to your endpoint. Its answer and the contexts it actually retrieved are captured on each result."],
                    ["offline", "Offline: use the dataset", "Scores the recorded responses against the dataset's context column. No endpoint is called."],
                  ] as const
                ).map(([v, title, desc]) => {
                  const disabled = v === "offline" && !fields.includes("response");
                  return (
                    <label
                      key={v}
                      className={cn(
                        "flex cursor-pointer gap-3 rounded-lg border p-3",
                        mode === v && "border-primary bg-primary/5",
                        disabled && "cursor-not-allowed opacity-50",
                      )}
                    >
                      <input type="radio" name="mode" className="mt-1" checked={mode === v} disabled={disabled} onChange={() => setMode(v)} />
                      <span>
                        <span className="font-medium">{title}</span>
                        <span className="block text-sm text-muted-foreground">
                          {desc}
                          {disabled && " This version has no recorded responses."}
                        </span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          </Card>
        )}

        {step === 1 && (
          <Card className="space-y-4 p-5">
            {mode === "offline" ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Info className="size-4" aria-hidden /> Offline mode uses recorded responses and the dataset's context; no connector is needed.
              </p>
            ) : (
              <>
                <div className="flex items-end gap-3">
                  <Field label="Connector" htmlFor="rg-conn" className="w-80">
                    <NativeSelect id="rg-conn" value={connectorId} onChange={(e) => setConnectorId(e.target.value === "new" ? "new" : Number(e.target.value))}>
                      {connectors.data?.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} · {new URL(c.endpoint_url).host}
                        </option>
                      ))}
                      <option value="new">+ New connector…</option>
                    </NativeSelect>
                  </Field>
                  {connector && (
                    <ConfirmButton
                      variant="ghost"
                      title={`Delete connector "${connector.name}"?`}
                      confirmLabel="Delete connector"
                      consequences="Its stored auth secret is destroyed. Past runs keep their results and a snapshot of the connector settings."
                      onConfirm={async () => {
                        try {
                          await cm.remove.mutateAsync(connector.id);
                          setConnectorId("new");
                          toast.success("Connector deleted");
                        } catch (e) {
                          toast.error(errorMessage(e));
                        }
                      }}
                    >
                      <Trash2 /> Delete
                    </ConfirmButton>
                  )}
                </div>
                <ConnectorEditor
                  key={connectorId}
                  pid={pid}
                  connector={connector}
                  sampleQuestion={sample}
                  onSaved={(c) => setConnectorId(c.id)}
                />
              </>
            )}
          </Card>
        )}

        {step === 2 && catalog.data && (
          <Card className="space-y-4 p-5">
            {!(hasRetrieval && hasGeneration) && (
              <p className="flex items-start gap-2 rounded-md bg-info-bg px-3 py-2 text-sm text-info">
                <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
                Failure-origin diagnosis needs at least one retrieval metric (context precision/recall) and one generation metric
                (faithfulness, correctness or hallucination). Without both, failures are labelled “undetermined”.
              </p>
            )}
            {mode === "live" && !contextsMapped && (
              <p className="rounded-md bg-warn-bg px-3 py-2 text-sm text-warn">
                This connector has no retrieved-contexts path, so context metrics are unavailable. Add the path in step 2 to evaluate retrieval.
              </p>
            )}
            <MetricPicker
              catalog={catalog.data}
              present={present}
              value={metrics}
              onChange={setMetrics}
              presentHint={
                mode === "live"
                  ? "Context metrics use the contexts your endpoint returns, never the dataset's uploaded context."
                  : "Context metrics use the dataset's context column. Cases missing a field record that metric as N/A."
              }
            />
          </Card>
        )}

        {step === 3 && (
          <Card className="space-y-4 p-5">
            <div className="grid grid-cols-2 gap-4">
              <Field label="Judge" htmlFor="rg-judge">
                <NativeSelect id="rg-judge" value={judgeId} onChange={(e) => setJudgeId(Number(e.target.value))}>
                  {judges.data?.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.name} ({j.model})
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <div className="space-y-1.5">
                <Label htmlFor="rg-rep">Repeats</Label>
                <div className="flex items-center gap-1">
                  <Button variant="outline" size="icon" aria-label="Fewer repeats" disabled={repeats <= 1} onClick={() => setRepeats(repeats - 1)}>
                    <Minus />
                  </Button>
                  <Input id="rg-rep" type="number" className="w-14 text-center" value={repeats} min={1} max={10} onChange={(e) => setRepeats(Number(e.target.value))} />
                  <Button variant="outline" size="icon" aria-label="More repeats" disabled={repeats >= 10} onClick={() => setRepeats(repeats + 1)}>
                    <Plus />
                  </Button>
                </div>
              </div>
              <Field label="Budget cap, USD (optional)" htmlFor="rg-budget" error={budgetValid ? undefined : "Positive amount up to 10,000"}>
                <Input id="rg-budget" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="e.g. 2.00" />
              </Field>
              <Field label="Run name (optional)" htmlFor="rg-name">
                <Input id="rg-name" value={runName} onChange={(e) => setRunName(e.target.value)} />
              </Field>
            </div>
            <label className="flex items-center gap-3 text-sm">
              <Switch checked={useCache} onCheckedChange={setUseCache} /> Reuse cached judge results for identical inputs
            </label>
            <div className="rounded-lg border bg-muted/30 p-4 text-sm">
              {estimateError ? (
                <span className="text-destructive">{estimateError}</span>
              ) : estimate ? (
                <div className="space-y-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-muted-foreground">Estimated judge cost</span>
                    <span className="tabular text-lg font-semibold">{fmtUsd(estimate.judge.usd)}</span>
                    <span className="tabular text-xs text-muted-foreground">({estimate.judge_calls} judge calls, {version?.row_count} questions)</span>
                  </div>
                  {mode === "live" && (
                    <p className="text-xs text-muted-foreground">
                      Your RAG API is called once per question; its own costs are not included. Live answers and contexts are sized with
                      assumptions until they are captured.
                    </p>
                  )}
                  {estimate.not_applicable.length > 0 && <p className="text-xs text-muted-foreground">N/A: {estimate.not_applicable.join("; ")}</p>}
                </div>
              ) : (
                <span className="text-muted-foreground">Estimating…</span>
              )}
            </div>
          </Card>
        )}

        {step === 4 && runId && <WizardResults runId={runId} pid={pid} />}

        {step < 4 && (
          <div className="sticky bottom-0 z-10 -mx-6 flex items-center justify-between border-t bg-background/95 px-6 py-3 backdrop-blur">
            <Button variant="outline" disabled={step === 0} onClick={() => setStep(step - 1)}>
              <ArrowLeft /> Back
            </Button>
            {step < 3 ? (
              <Button disabled={!canNext} onClick={() => setStep(step + 1)}>
                Next <ArrowRight />
              </Button>
            ) : (
              <Button
                disabled={!payload}
                loading={create.isPending}
                onClick={async () => {
                  if (!payload) return;
                  try {
                    const run = await create.mutateAsync(payload);
                    setRunId(run.id);
                    setStep(4);
                    toast.success(`RAG run started: ${run.total_tests} question(s)`);
                  } catch (e) {
                    toast.error(errorMessage(e));
                  }
                }}
              >
                <Play /> Start RAG run
              </Button>
            )}
          </div>
        )}
      </div>
    </>
  );
}

function WizardResults({ runId, pid }: { runId: number; pid: number }) {
  const run = useRun(runId);
  const r = run.data;
  const summary = useRunSummary(runId, `${r?.status}:${r?.completed_tests}`);
  const [origin, setOrigin] = useState<FailureOrigin | null>(null);
  if (!r) return <TableSkeleton rows={4} />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FlaskConical className="size-5 text-muted-foreground" aria-hidden />
          <h2 className="font-semibold">{r.run_name}</h2>
          <StatusChip status={r.status} />
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to={`/p/${pid}/runs/${r.id}`}>
            Open run detail <ArrowUpRight />
          </Link>
        </Button>
      </div>
      <BiasBanner runId={r.id} warnings={r.warnings ?? []} />
      <RunHeaderStats run={r} summary={summary.data} />
      <FailureOriginSummary summary={summary.data} onPick={setOrigin} />
      <ResultsTable run={r} canReview preset={origin ? { failure_origin: origin } : undefined} />
    </div>
  );
}
