import { AlertTriangle, Bookmark, Info, Minus, Play, Plus, Scale, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { MetricPicker } from "@/components/metric-picker";
import { EmptyState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/overlay";
import { Card, CardContent, CardHeader, CardTitle, Field, Input, Label, NativeSelect, Switch } from "@/components/ui/primitives";
import {
  estimateRun,
  useCreateRun,
  useDatasets,
  useJudges,
  useMetricsCatalog,
  useModelConfigMutations,
  useModelConfigs,
  useProviders,
  useSuites,
  useVersions,
} from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { defaultMetricState, selectedMetrics, type MetricState } from "@/lib/metrics";
import { fmtUsd } from "@/lib/utils";
import { FieldBadges } from "@/pages/DatasetsPage";
import { ModelDatalist } from "@/pages/JudgesPage";
import { errorMessage } from "@/services/api";
import type { Estimate, InputName, RunCreate } from "@/types";

type ModelRow = { key: number; label: string; provider_id: number; model: string; max_tokens: number; temperature: string };

let nextKey = 1;
const newRow = (provider_id: number, model = ""): ModelRow => ({
  key: nextKey++, label: model.split("/").pop() ?? "", provider_id, model, max_tokens: 1000, temperature: "",
});

/** Model Comparison (spec §4.3): identical questions to 2–5 models, one shared judge and metric set. */
export function ModelComparisonPage() {
  const pid = Number(useParams().projectId);
  const navigate = useNavigate();
  const datasets = useDatasets(pid);
  const suites = useSuites(pid);
  const providers = useProviders(pid);
  const judges = useJudges(pid);
  const configs = useModelConfigs(pid);
  const mc = useModelConfigMutations(pid);
  const catalog = useMetricsCatalog();
  const create = useCreateRun();

  const [sourceKind, setSourceKind] = useState<"dataset" | "suite">("dataset");
  const [datasetId, setDatasetId] = useState(0);
  const versions = useVersions(datasetId);
  const [versionId, setVersionId] = useState(0);
  const [suiteId, setSuiteId] = useState(0);
  const [rows, setRows] = useState<ModelRow[]>([]);
  const [metrics, setMetrics] = useState<MetricState>({});
  const [judgeId, setJudgeId] = useState(0);
  const [repeats, setRepeats] = useState(1);
  const [budget, setBudget] = useState("");
  const [useCache, setUseCache] = useState(true);
  const [runName, setRunName] = useState("");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);

  useEffect(() => {
    if (!datasetId && datasets.data?.length) setDatasetId(datasets.data[0].id);
  }, [datasets.data, datasetId]);
  useEffect(() => {
    const ds = datasets.data?.find((d) => d.id === datasetId);
    if (ds?.current_version) setVersionId(ds.current_version.id);
  }, [datasetId, datasets.data]);
  useEffect(() => {
    if (!suiteId && suites.data?.length) setSuiteId(suites.data[0].id);
  }, [suites.data, suiteId]);
  useEffect(() => {
    if (judges.data?.length && !judgeId) setJudgeId((judges.data.find((j) => j.is_default) ?? judges.data[0]).id);
  }, [judges.data, judgeId]);
  useEffect(() => {
    if (providers.data?.length && rows.length === 0)
      setRows([newRow(providers.data[0].id, "openai/gpt-4o-mini"), newRow(providers.data[0].id, "meta-llama/llama-3.1-8b-instruct")]);
  }, [providers.data, rows.length]);
  useEffect(() => {
    if (catalog.data && Object.keys(metrics).length === 0) setMetrics(defaultMetricState(catalog.data, ["answer_relevancy", "answer_correctness"]));
  }, [catalog.data, metrics]);

  const suite = suites.data?.find((s) => s.id === suiteId);
  const suiteVersions = useVersions(suite?.dataset_id ?? 0);
  const fields =
    (sourceKind === "dataset"
      ? versions.data?.find((v) => v.id === versionId)?.fields_present
      : suiteVersions.data?.find((v) => v.id === suite?.dataset_version_id)?.fields_present) ?? [];
  const caseCount =
    sourceKind === "dataset" ? (versions.data?.find((v) => v.id === versionId)?.row_count ?? 0) : (suite?.case_count ?? 0);

  const present = useMemo(() => {
    const s = new Set<InputName>(["question", "response"]);
    if (fields.includes("reference")) s.add("reference");
    if (fields.includes("context")) s.add("context");
    if (fields.includes("criteria")) s.add("criteria");
    return s;
  }, [fields]);

  const chosen = catalog.data ? selectedMetrics(metrics, catalog.data, present) : [];
  const rowProblems = rows.map((r) => {
    if (!r.label.trim()) return "Label is required";
    if (!r.model.trim()) return "Choose a model";
    if (r.temperature !== "" && !(Number(r.temperature) >= 0 && Number(r.temperature) <= 2)) return "Temperature 0–2";
    if (!(r.max_tokens >= 1 && r.max_tokens <= 8000)) return "Max tokens 1–8000";
    return null;
  });
  const labels = rows.map((r) => r.label.trim().toLowerCase());
  const dupLabel = labels.length !== new Set(labels).size;
  const budgetValid = budget === "" || (Number(budget) > 0 && Number(budget) <= 10000);
  const sourceOk = sourceKind === "dataset" ? versionId > 0 : suiteId > 0;
  const valid = sourceOk && rows.length >= 2 && !rowProblems.some(Boolean) && !dupLabel && chosen.length > 0 && judgeId > 0 && budgetValid;

  const payload: RunCreate | null = valid
    ? {
        project_id: pid,
        run_type: "benchmark",
        run_name: runName.trim() || null,
        judge_config_id: judgeId,
        repeats,
        budget_usd: budget ? Number(budget) : null,
        use_cache: useCache,
        metrics: chosen,
        ...(sourceKind === "dataset" ? { dataset_version_id: versionId } : { suite_id: suiteId }),
        outputs: rows.map((r) => ({
          label: r.label.trim(),
          source: "provider" as const,
          provider_id: r.provider_id,
          model: r.model.trim(),
          max_tokens: r.max_tokens,
          temperature: r.temperature === "" ? null : Number(r.temperature),
        })),
      }
    : null;

  const sig = payload ? JSON.stringify(payload) : "";
  const req = useRef(0);
  useEffect(() => {
    if (!sig) {
      setEstimate(null);
      return;
    }
    const my = ++req.current;
    const t = setTimeout(async () => {
      try {
        const e = await estimateRun(JSON.parse(sig));
        if (my === req.current) (setEstimate(e), setEstimateError(null));
      } catch (e) {
        if (my === req.current) (setEstimate(null), setEstimateError(errorMessage(e)));
      }
    }, 600);
    return () => clearTimeout(t);
  }, [sig]);

  const update = (key: number, patch: Partial<ModelRow>) => setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  if (providers.data?.length === 0 || judges.data?.length === 0 || datasets.data?.length === 0) {
    return (
      <>
        <PageHeader crumbs={[{ label: "Test Studio" }, { label: "Model Comparison" }]} title="Model Comparison" />
        <div className="p-6">
          <EmptyState
            icon={Scale}
            title="Set up first"
            description="Model comparison needs a provider, a judge and a dataset of questions."
            action={
              <Button asChild>
                <Link to={`/p/${pid}/${providers.data?.length === 0 ? "settings/providers" : judges.data?.length === 0 ? "settings/judges" : "datasets"}`}>
                  {providers.data?.length === 0 ? "Add a provider" : judges.data?.length === 0 ? "Configure a judge" : "Upload a dataset"}
                </Link>
              </Button>
            }
          />
        </div>
      </>
    );
  }
  if (!providers.data || !catalog.data) return <div className="p-6"><TableSkeleton rows={6} /></div>;

  const bias = estimate?.warnings.filter((w) => w.type === "judge_bias") ?? [];
  const unknownPrice = estimate?.warnings.filter((w) => w.type === "pricing_unknown") ?? [];

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Studio" }, { label: "Model Comparison" }]}
        title="Model Comparison"
        description="Send the same questions to 2–5 models, score them with one shared judge and metric set, and compare quality, latency and cost."
      />
      <ModelDatalist id="cmp-models" />
      <div className="space-y-5 p-6">
        <Card>
          <CardHeader>
            <CardTitle>1. Questions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div role="radiogroup" aria-label="Question source" className="flex w-fit rounded-md border bg-card p-0.5">
              {(
                [
                  ["dataset", "Dataset version"],
                  ["suite", "Suite"],
                ] as const
              ).map(([v, l]) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  aria-checked={sourceKind === v}
                  disabled={v === "suite" && !suites.data?.length}
                  onClick={() => setSourceKind(v)}
                  className={`h-7 cursor-pointer rounded px-3 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${sourceKind === v ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}
                >
                  {l}
                </button>
              ))}
            </div>
            {sourceKind === "dataset" ? (
              <div className="grid grid-cols-2 gap-4">
                <Field label="Dataset" htmlFor="mc-ds">
                  <NativeSelect id="mc-ds" value={datasetId} onChange={(e) => setDatasetId(Number(e.target.value))}>
                    {datasets.data?.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Version" htmlFor="mc-v">
                  <NativeSelect id="mc-v" value={versionId} onChange={(e) => setVersionId(Number(e.target.value))}>
                    {versions.data?.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number} · {v.row_count} cases
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              </div>
            ) : (
              <Field label="Suite" htmlFor="mc-suite" className="max-w-md">
                <NativeSelect id="mc-suite" value={suiteId} onChange={(e) => setSuiteId(Number(e.target.value))}>
                  {suites.data?.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name} · {s.dataset_name} v{s.version_number} · {s.case_count} cases
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            )}
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              {caseCount} question(s) · fields: <FieldBadges fields={fields} />
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>2. Models ({rows.length} of 5)</CardTitle>
            <div className="flex items-center gap-2">
              {!!configs.data?.length && (
                <NativeSelect
                  aria-label="Add a saved configuration"
                  value=""
                  disabled={rows.length >= 5}
                  onChange={(e) => {
                    const c = configs.data?.find((x) => x.id === Number(e.target.value));
                    if (c)
                      setRows([...rows, { key: nextKey++, label: c.name, provider_id: c.provider_id, model: c.model, max_tokens: c.max_tokens, temperature: c.temperature == null ? "" : String(c.temperature) }]);
                  }}
                >
                  <option value="">Add saved configuration…</option>
                  {configs.data.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · {c.model}
                    </option>
                  ))}
                </NativeSelect>
              )}
              <Button variant="outline" size="sm" disabled={rows.length >= 5} onClick={() => setRows([...rows, newRow(providers.data![0].id)])}>
                <Plus /> Add model
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-2">
            {rows.map((r, i) => (
              <div key={r.key} className="grid grid-cols-[150px_150px_1fr_100px_100px_auto] items-start gap-2">
                <Field label={i === 0 ? "Label" : ""} htmlFor={`mc-l-${r.key}`} error={rowProblems[i] === "Label is required" ? rowProblems[i]! : undefined}>
                  <Input id={`mc-l-${r.key}`} value={r.label} onChange={(e) => update(r.key, { label: e.target.value })} aria-label={`Label for model ${i + 1}`} />
                </Field>
                <Field label={i === 0 ? "Provider" : ""} htmlFor={`mc-p-${r.key}`}>
                  <NativeSelect id={`mc-p-${r.key}`} value={r.provider_id} onChange={(e) => update(r.key, { provider_id: Number(e.target.value) })} aria-label={`Provider for model ${i + 1}`}>
                    {providers.data!.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label={i === 0 ? "Model" : ""} htmlFor={`mc-m-${r.key}`} error={rowProblems[i] === "Choose a model" ? rowProblems[i]! : undefined}>
                  <Input
                    id={`mc-m-${r.key}`}
                    list="cmp-models"
                    className="font-mono text-xs"
                    value={r.model}
                    placeholder="provider/model"
                    aria-label={`Model id for model ${i + 1}`}
                    onChange={(e) => update(r.key, { model: e.target.value, label: r.label || e.target.value.split("/").pop() || "" })}
                  />
                </Field>
                <Field label={i === 0 ? "Max tokens" : ""} htmlFor={`mc-t-${r.key}`}>
                  <Input id={`mc-t-${r.key}`} type="number" value={r.max_tokens} min={1} max={8000} onChange={(e) => update(r.key, { max_tokens: Number(e.target.value) })} aria-label={`Max tokens for model ${i + 1}`} />
                </Field>
                <Field label={i === 0 ? "Temperature" : ""} htmlFor={`mc-temp-${r.key}`}>
                  <Input id={`mc-temp-${r.key}`} placeholder="default" value={r.temperature} onChange={(e) => update(r.key, { temperature: e.target.value })} aria-label={`Temperature for model ${i + 1}`} />
                </Field>
                <div className={`flex gap-1 ${i === 0 ? "pt-6" : ""}`}>
                  <Tooltip content="Save as a reusable configuration">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Save model ${i + 1} as configuration`}
                      disabled={!!rowProblems[i]}
                      onClick={async () => {
                        try {
                          await mc.create.mutateAsync({
                            provider_id: r.provider_id, name: r.label.trim(), model: r.model.trim(), max_tokens: r.max_tokens,
                            temperature: r.temperature === "" ? null : Number(r.temperature),
                          });
                          toast.success(`Saved "${r.label}"`);
                        } catch (e) {
                          toast.error(errorMessage(e));
                        }
                      }}
                    >
                      <Bookmark />
                    </Button>
                  </Tooltip>
                  <Button variant="ghost" size="icon" aria-label={`Remove model ${i + 1}`} disabled={rows.length <= 2} onClick={() => setRows(rows.filter((x) => x.key !== r.key))}>
                    <X />
                  </Button>
                </div>
              </div>
            ))}
            {dupLabel && <p className="text-xs text-destructive">Labels must be unique.</p>}
            {rowProblems.filter((p) => p && !["Label is required", "Choose a model"].includes(p)).map((p, i) => (
              <p key={i} className="text-xs text-destructive">{p}</p>
            ))}
            {!!configs.data?.length && (
              <details className="pt-2 text-sm">
                <summary className="cursor-pointer text-muted-foreground">Manage saved configurations ({configs.data.length})</summary>
                <ul className="mt-2 space-y-1">
                  {configs.data.map((c) => (
                    <li key={c.id} className="flex items-center gap-2">
                      <span className="font-medium">{c.name}</span>
                      <span className="font-mono text-xs text-muted-foreground">{c.model}</span>
                      <Button variant="ghost" size="icon" aria-label={`Delete saved configuration ${c.name}`} onClick={() => mc.remove.mutate(c.id)}>
                        <Trash2 />
                      </Button>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>3. Shared metrics & judge</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <MetricPicker catalog={catalog.data} present={present} value={metrics} onChange={setMetrics} presentHint="Every model is scored with the same metrics, thresholds and judge." />
            <div className="grid grid-cols-2 gap-4">
              <Field label="Judge" htmlFor="mc-judge">
                <NativeSelect id="mc-judge" value={judgeId} onChange={(e) => setJudgeId(Number(e.target.value))}>
                  {judges.data?.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.name} ({j.model})
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <div className="space-y-1.5">
                <Label htmlFor="mc-rep">Repeats</Label>
                <div className="flex items-center gap-1">
                  <Button variant="outline" size="icon" aria-label="Fewer repeats" disabled={repeats <= 1} onClick={() => setRepeats(repeats - 1)}>
                    <Minus />
                  </Button>
                  <Input id="mc-rep" type="number" className="w-14 text-center" value={repeats} min={1} max={10} onChange={(e) => setRepeats(Number(e.target.value))} />
                  <Button variant="outline" size="icon" aria-label="More repeats" disabled={repeats >= 10} onClick={() => setRepeats(repeats + 1)}>
                    <Plus />
                  </Button>
                </div>
              </div>
              <Field label="Budget cap, USD (optional)" htmlFor="mc-budget" error={budgetValid ? undefined : "Positive amount up to 10,000"}>
                <Input id="mc-budget" inputMode="decimal" value={budget} placeholder="e.g. 5.00" onChange={(e) => setBudget(e.target.value)} />
              </Field>
              <Field label="Run name (optional)" htmlFor="mc-name">
                <Input id="mc-name" value={runName} onChange={(e) => setRunName(e.target.value)} />
              </Field>
            </div>
            <label className="flex items-center gap-3 text-sm">
              <Switch checked={useCache} onCheckedChange={setUseCache} /> Reuse cached judge results for identical inputs
            </label>
            {bias.length > 0 && (
              <div role="status" className="flex gap-2 rounded-md border border-warn/40 bg-warn-bg p-3 text-sm">
                <AlertTriangle className="size-4 shrink-0 text-warn" aria-hidden />
                <div className="space-y-1">
                  <p className="font-medium">Judge may favour its own family</p>
                  {bias.map((w, i) => <p key={i} className="text-xs">{w.message}</p>)}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="sticky bottom-0 z-10 -mx-6 flex items-center justify-between gap-3 border-t bg-background/95 px-6 py-3 backdrop-blur">
          {estimateError ? (
            <span className="text-sm text-destructive">{estimateError}</span>
          ) : estimate ? (
            <span className="inline-flex flex-wrap items-center gap-2 text-sm">
              <Info className="size-4 text-muted-foreground" aria-hidden />
              <span className="text-muted-foreground">Estimated cost</span>
              <span className="tabular font-semibold">{fmtUsd(estimate.total_usd)}</span>
              <span className="tabular text-xs text-muted-foreground">
                (models {fmtUsd(estimate.model_under_test.usd)} · judge {fmtUsd(estimate.judge.usd)} · {caseCount} questions × {rows.length} models)
              </span>
              {unknownPrice.length > 0 && <span className="text-xs text-warn">{unknownPrice.map((w) => w.message).join(" ")}</span>}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">{valid ? "Estimating…" : "Choose questions, at least two models and one metric."}</span>
          )}
          <Button
            disabled={!payload}
            loading={create.isPending}
            onClick={async () => {
              if (!payload) return;
              try {
                const run = await create.mutateAsync(payload);
                toast.success(`Comparison started: ${run.total_tests} answers to generate and score`);
                navigate(`/p/${pid}/runs/${run.id}`);
              } catch (e) {
                toast.error(errorMessage(e));
              }
            }}
          >
            <Play /> Start comparison
          </Button>
        </div>
      </div>
    </>
  );
}
