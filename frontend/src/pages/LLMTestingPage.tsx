import { zodResolver } from "@hookform/resolvers/zod";
import { AlertTriangle, ArrowUpRight, Info, Minus, Play, Plus, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm, useWatch, type UseFormReturn } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { EngineBadge, StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, Tooltip } from "@/components/ui/overlay";
import { Card, Checkbox, Field, Input, Label, NativeSelect, Switch, Textarea } from "@/components/ui/primitives";
import { estimateRun, isActive, useCreateRun, useJudges, useMetricsCatalog, useProviders, useRun } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { ModelDatalist } from "@/pages/JudgesPage";
import { BiasBanner, RunHeaderStats, RunResultsBody } from "@/pages/RunDetailPage";
import { cn, fmtUsd } from "@/lib/utils";
import { ApiError, errorMessage } from "@/services/api";
import type { Estimate, InputName, JudgeConfig, MetricDef, RunCreate } from "@/types";

// ---------------------------------------------------------------- form model
const outputSchema = z.object({
  label: z.string().trim().max(100),
  source: z.enum(["manual", "provider"]),
  text: z.string().max(50000),
  source_model: z.string().max(200),
  provider_id: z.coerce.number().int(),
  model: z.string().max(200),
  max_tokens: z.coerce.number().int().min(1, "At least 1").max(8000, "At most 8000"),
});

const schema = z
  .object({
    mode: z.enum(["single", "compare"]),
    question: z.string().trim().min(1, "Enter the question that was asked").max(20000),
    reference: z.string().max(20000),
    context: z.string().max(200000),
    criteria: z.string().max(5000),
    outputs: z.array(outputSchema).length(2),
    metrics: z.record(z.object({ selected: z.boolean(), threshold: z.coerce.number().min(0).max(1) })),
    judge_config_id: z.coerce.number().int().positive("Choose a judge"),
    repeats: z.coerce.number().int().min(1).max(10),
    budget_usd: z.string().refine((v) => v === "" || (Number(v) > 0 && Number(v) <= 10000), "Positive amount up to 10,000"),
    use_cache: z.boolean(),
    run_name: z.string().max(300),
  })
  .superRefine((v, ctx) => {
    const active = v.mode === "single" ? [0] : [0, 1];
    active.forEach((i) => {
      const o = v.outputs[i];
      if (!o.label.trim()) ctx.addIssue({ code: "custom", path: ["outputs", i, "label"], message: "Label is required" });
      if (o.source === "manual" && !o.text.trim())
        ctx.addIssue({ code: "custom", path: ["outputs", i, "text"], message: "Paste the answer to score" });
      if (o.source === "provider" && !o.provider_id)
        ctx.addIssue({ code: "custom", path: ["outputs", i, "provider_id"], message: "Choose a provider" });
      if (o.source === "provider" && !o.model.trim())
        ctx.addIssue({ code: "custom", path: ["outputs", i, "model"], message: "Choose a model" });
    });
    if (v.mode === "compare" && v.outputs[0].label.trim().toLowerCase() === v.outputs[1].label.trim().toLowerCase())
      ctx.addIssue({ code: "custom", path: ["outputs", 1, "label"], message: "Labels must differ" });
    if (!Object.values(v.metrics).some((m) => m.selected))
      ctx.addIssue({ code: "custom", path: ["metrics"], message: "Select at least one metric" });
  });

type FormValues = z.infer<typeof schema>;

const emptyOutput = (label: string): FormValues["outputs"][number] => ({
  label,
  source: "manual",
  text: "",
  source_model: "",
  provider_id: 0,
  model: "",
  max_tokens: 1000,
});

const metricKey = (metric: string, engine: string) => `${metric}|${engine}`;

function splitContext(raw: string) {
  return raw
    .split(/\n\s*\n/)
    .map((c) => c.trim())
    .filter(Boolean);
}

function presentInputs(v: FormValues): Set<InputName> {
  const s = new Set<InputName>();
  if (v.question?.trim()) s.add("question");
  if (v.reference?.trim()) s.add("reference");
  if (splitContext(v.context ?? "").length) s.add("context");
  if (v.criteria?.trim()) s.add("criteria");
  const active = v.mode === "single" ? v.outputs.slice(0, 1) : v.outputs;
  if (active.some((o) => o.source === "provider" || o.text.trim())) s.add("response");
  return s;
}

const INPUT_LABEL: Record<string, string> = {
  question: "question",
  response: "model response",
  reference: "reference answer",
  context: "context",
  criteria: "evaluation criteria",
};

function applicability(def: MetricDef, engine: string, present: Set<InputName>): string | null {
  const impl = def.engines.find((e) => e.engine === engine);
  if (!impl) return `Not available in ${engine}`;
  const missing = impl.requires.filter((r) => !present.has(r as InputName));
  return missing.length ? `Needs ${missing.map((m) => INPUT_LABEL[m] ?? m).join(", ")}` : null;
}

function toPayload(v: FormValues, projectId: number, catalog: MetricDef[]): RunCreate {
  const present = presentInputs(v);
  const metrics = Object.entries(v.metrics)
    .filter(([, m]) => m.selected)
    .map(([k, m]) => {
      const [metric, engine] = k.split("|");
      return { metric, engine: engine as "ragas" | "deepeval", threshold: m.threshold };
    })
    .filter((m) => {
      const def = catalog.find((d) => d.name === m.metric);
      return def && applicability(def, m.engine, present) === null;
    });
  const active = v.mode === "single" ? v.outputs.slice(0, 1) : v.outputs;
  const context = splitContext(v.context);
  return {
    project_id: projectId,
    run_name: v.run_name.trim() || null,
    run_type: "llm",
    judge_config_id: v.judge_config_id,
    repeats: v.repeats,
    budget_usd: v.budget_usd ? Number(v.budget_usd) : null,
    use_cache: v.use_cache,
    metrics,
    case: {
      question: v.question.trim(),
      reference: v.reference.trim() || null,
      context: context.length ? context : null,
      criteria: v.criteria.trim() || null,
    },
    outputs: active.map((o) =>
      o.source === "manual"
        ? { label: o.label.trim(), source: "manual", text: o.text, source_model: o.source_model.trim() || null, max_tokens: 1000 }
        : { label: o.label.trim(), source: "provider", provider_id: o.provider_id, model: o.model.trim(), max_tokens: o.max_tokens },
    ),
  };
}

// ---------------------------------------------------------------- sub-forms
function AnswerFields({ form, index, pid }: { form: UseFormReturn<FormValues>; index: number; pid: number }) {
  const providers = useProviders(pid);
  const source = useWatch({ control: form.control, name: `outputs.${index}.source` });
  const err = form.formState.errors.outputs?.[index];
  const id = (f: string) => `out-${index}-${f}`;
  return (
    <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <div className="flex items-end gap-3">
        <Field label="Label" htmlFor={id("label")} error={err?.label?.message} className="w-40">
          <Input id={id("label")} aria-invalid={!!err?.label} {...form.register(`outputs.${index}.label`)} />
        </Field>
        <Controller
          control={form.control}
          name={`outputs.${index}.source`}
          render={({ field }) => (
            <div className="space-y-1.5">
              <span className="text-sm font-medium">Response source</span>
              <div role="radiogroup" aria-label="Response source" className="flex rounded-md border bg-card p-0.5">
                {(["manual", "provider"] as const).map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={field.value === s}
                    onClick={() => field.onChange(s)}
                    className={cn(
                      "h-7 cursor-pointer rounded px-3 text-sm",
                      field.value === s ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {s === "manual" ? "Manual" : "Generate"}
                  </button>
                ))}
              </div>
            </div>
          )}
        />
      </div>
      {source === "manual" ? (
        <>
          <Field label="Answer" htmlFor={id("text")} error={err?.text?.message}>
            <Textarea id={id("text")} rows={5} aria-invalid={!!err?.text} {...form.register(`outputs.${index}.text`)} />
          </Field>
          <Field
            label="Produced by (optional)"
            htmlFor={id("sm")}
            hint="Model id, e.g. anthropic/claude-sonnet-4.5. Used for the judge-bias check."
          >
            <Input id={id("sm")} list="studio-models" {...form.register(`outputs.${index}.source_model`)} />
          </Field>
        </>
      ) : (
        <div className="grid grid-cols-[1fr_1.4fr_110px] gap-3">
          <Field label="Provider" htmlFor={id("prov")} error={err?.provider_id?.message}>
            <NativeSelect id={id("prov")} {...form.register(`outputs.${index}.provider_id`)}>
              <option value={0}>Choose…</option>
              {providers.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </NativeSelect>
          </Field>
          <Field label="Model" htmlFor={id("model")} error={err?.model?.message}>
            <Input
              id={id("model")}
              list="studio-models"
              placeholder="openai/gpt-4o"
              aria-invalid={!!err?.model}
              {...form.register(`outputs.${index}.model`)}
            />
          </Field>
          <Field label="Max tokens" htmlFor={id("mt")} error={err?.max_tokens?.message}>
            <Input id={id("mt")} type="number" min={1} max={8000} {...form.register(`outputs.${index}.max_tokens`)} />
          </Field>
        </div>
      )}
    </div>
  );
}

function MetricSelector({ form, catalog }: { form: UseFormReturn<FormValues>; catalog: MetricDef[] }) {
  const values = useWatch({ control: form.control }) as FormValues;
  const present = presentInputs(values);
  const metricsError = (form.formState.errors.metrics as { message?: string } | undefined)?.message;
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-medium">Metrics</legend>
      <p className="-mt-1 text-xs text-muted-foreground">
        Only metrics whose inputs you supplied can run; the rest are disabled with the reason. Thresholds are editable.
      </p>
      {(["ragas", "deepeval"] as const).map((engine) => (
        <div key={engine} className="rounded-lg border">
          <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-1.5">
            <EngineBadge engine={engine} version={catalog[0]?.engines.find((e) => e.engine === engine)?.engine_version} />
          </div>
          <ul className="divide-y">
            {catalog
              .filter((d) => d.engines.some((e) => e.engine === engine))
              .map((d) => {
                const key = metricKey(d.name, engine);
                const reason = applicability(d, engine, present);
                const disabled = reason !== null;
                return (
                  <li key={key} className={cn("flex items-center gap-3 px-3 py-2", disabled && "opacity-55")}>
                    <Controller
                      control={form.control}
                      name={`metrics.${key}.selected` as const}
                      render={({ field }) => (
                        <Checkbox
                          id={`m-${key}`}
                          checked={field.value && !disabled}
                          disabled={disabled}
                          onCheckedChange={(c) => field.onChange(c === true)}
                          aria-describedby={disabled ? `m-${key}-why` : undefined}
                        />
                      )}
                    />
                    <Tooltip content={disabled ? reason : d.description}>
                      <label htmlFor={`m-${key}`} className="flex-1 cursor-pointer text-sm">
                        {d.label}
                        {!d.higher_is_better && <span className="ml-1.5 text-xs text-muted-foreground">(lower is better)</span>}
                        {disabled && (
                          <span id={`m-${key}-why`} className="ml-2 text-xs text-muted-foreground">
                            — {reason}
                          </span>
                        )}
                      </label>
                    </Tooltip>
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      {d.higher_is_better ? "≥" : "≤"}
                      <Input
                        type="number"
                        step="0.05"
                        min={0}
                        max={1}
                        disabled={disabled}
                        aria-label={`${d.label} (${engine}) threshold`}
                        className="h-7 w-16 px-2 text-xs"
                        {...form.register(`metrics.${key}.threshold` as const)}
                      />
                    </label>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
      {metricsError && (
        <p role="alert" className="text-xs text-destructive">
          {metricsError}
        </p>
      )}
    </fieldset>
  );
}

function EstimateBar({ estimate, error, loading }: { estimate: Estimate | null; error: string | null; loading: boolean }) {
  if (error) return <span className="text-xs text-destructive">{error}</span>;
  if (!estimate) return <span className="text-xs text-muted-foreground">{loading ? "Estimating…" : "Fill in the form to see the estimated cost."}</span>;
  return (
    <Tooltip
      content={
        <div className="space-y-1">
          <div>
            Model under test: {fmtUsd(estimate.model_under_test.usd)} ({estimate.model_under_test.input_tokens.toLocaleString()} in /{" "}
            {estimate.model_under_test.output_tokens.toLocaleString()} out tokens)
          </div>
          <div>
            Judge: {fmtUsd(estimate.judge.usd)} ({estimate.judge_calls} calls, {estimate.judge.input_tokens.toLocaleString()} in /{" "}
            {estimate.judge.output_tokens.toLocaleString()} out tokens)
          </div>
          <div className="opacity-80">{estimate.note}</div>
        </div>
      }
    >
      <span tabIndex={0} className="inline-flex items-center gap-2 text-sm">
        <Info className="size-4 text-muted-foreground" aria-hidden />
        <span className="text-muted-foreground">Estimated cost</span>
        <span className="tabular font-semibold">{fmtUsd(estimate.total_usd)}</span>
        <span className="tabular text-xs text-muted-foreground">
          (model {fmtUsd(estimate.model_under_test.usd)} · judge {fmtUsd(estimate.judge.usd)})
        </span>
      </span>
    </Tooltip>
  );
}

// ---------------------------------------------------------------- page
export function LLMTestingPage() {
  const pid = Number(useParams().projectId);
  const catalog = useMetricsCatalog();
  const judges = useJudges(pid);
  const providers = useProviders(pid);

  const header = (
    <PageHeader
      crumbs={[{ label: "Test Studio" }, { label: "LLM Response Testing" }]}
      title="LLM Response Testing"
      description="Score one answer, or compare two, with Ragas and DeepEval using the same judge."
    />
  );
  if (catalog.isError || judges.isError || providers.isError) {
    return (
      <>
        {header}
        <div className="p-6">
          <ErrorState
            error={catalog.error ?? judges.error ?? providers.error}
            onRetry={() => (catalog.refetch(), judges.refetch(), providers.refetch())}
          />
        </div>
      </>
    );
  }
  if (!catalog.data || !judges.data || !providers.data) {
    return (
      <>
        {header}
        <div className="p-6">
          <TableSkeleton rows={8} cols={2} />
        </div>
      </>
    );
  }
  if (providers.data.length === 0 || judges.data.length === 0) {
    return (
      <>
        {header}
        <div className="p-6">
          <EmptyState
            icon={Sparkles}
            title="Add a provider and a judge first"
            description="Scoring needs an OpenRouter key and a judge model."
            action={
              <Button asChild>
                <Link to={`/p/${pid}/settings/${providers.data.length ? "judges" : "providers"}`}>
                  {providers.data.length ? "Configure a judge" : "Add OpenRouter key"}
                </Link>
              </Button>
            }
          />
        </div>
      </>
    );
  }
  // Form mounts only once catalogs are loaded, so every field gets its default on first render.
  return (
    <>
      {header}
      <StudioForm pid={pid} catalog={catalog.data} judges={judges.data} />
    </>
  );
}

function StudioForm({ pid, catalog, judges }: { pid: number; catalog: MetricDef[]; judges: JudgeConfig[] }) {
  const create = useCreateRun();
  const [runId, setRunId] = useState<number | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);
  const [estimating, setEstimating] = useState(false);

  const defaults = useMemo<FormValues>(() => {
    const metrics: FormValues["metrics"] = {};
    catalog.forEach((d) =>
      d.engines.forEach((e) => {
        metrics[metricKey(d.name, e.engine)] = { selected: d.name === "answer_relevancy", threshold: d.default_threshold };
      }),
    );
    return {
      mode: "single",
      question: "",
      reference: "",
      context: "",
      criteria: "",
      outputs: [emptyOutput("Claude"), emptyOutput("ChatGPT")],
      metrics,
      judge_config_id: (judges.find((j) => j.is_default) ?? judges[0]).id,
      repeats: 1,
      budget_usd: "",
      use_cache: true,
      run_name: "",
    };
  }, [catalog, judges]);

  const form = useForm<FormValues>({ resolver: zodResolver(schema), mode: "onChange", defaultValues: defaults });

  const values = useWatch({ control: form.control }) as FormValues;
  const mode = values.mode;
  const { isValid, isDirty } = form.formState;

  // debounced live estimate
  const payloadSig = useMemo(
    () => (isValid ? JSON.stringify(toPayload(values, pid, catalog)) : ""),
    [values, isValid, pid, catalog],
  );
  const reqId = useRef(0);
  useEffect(() => {
    if (!payloadSig) {
      setEstimate(null);
      setEstimateError(null);
      return;
    }
    const my = ++reqId.current;
    setEstimating(true);
    const t = setTimeout(async () => {
      try {
        const e = await estimateRun(JSON.parse(payloadSig));
        if (my === reqId.current) (setEstimate(e), setEstimateError(null));
      } catch (e) {
        if (my === reqId.current) (setEstimate(null), setEstimateError(errorMessage(e)));
      } finally {
        if (my === reqId.current) setEstimating(false);
      }
    }, 600);
    return () => clearTimeout(t);
  }, [payloadSig]);

  // unsaved-changes warning
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (isDirty && !runId) e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [isDirty, runId]);

  const onRun = form.handleSubmit(async (v) => {
    try {
      const run = await create.mutateAsync(toPayload(v, pid, catalog));
      setRunId(run.id);
      form.reset(v); // keep inputs, clear dirty state
      toast.success("Run started");
    } catch (e) {
      toast.error(e instanceof ApiError ? errorMessage(e) : "Could not start the run");
    }
  });

  const biasWarnings = estimate?.warnings.filter((w) => w.type === "judge_bias") ?? [];

  return (
    <>
      <ModelDatalist id="studio-models" />
      <div className="grid grid-cols-[minmax(440px,1fr)_minmax(480px,1.15fr)] items-start gap-6 p-6">
        {/* ------------------------------------------------ left: input form */}
        {/* One page scroll (no nested scroll areas); the estimate + Run footer stays pinned to the viewport bottom. */}
        <form noValidate onSubmit={onRun} className="flex flex-col rounded-lg border bg-card shadow-xs">
          <div className="space-y-5 p-5">
            <Controller
              control={form.control}
              name="mode"
              render={({ field }) => (
                <Tabs value={field.value} onValueChange={(v) => field.onChange(v)}>
                  <TabsList>
                    <TabsTrigger value="single">Single answer</TabsTrigger>
                    <TabsTrigger value="compare">Compare two answers</TabsTrigger>
                  </TabsList>
                </Tabs>
              )}
            />
            <Field label="Question" htmlFor="q" error={form.formState.errors.question?.message}>
              <Textarea id="q" rows={3} aria-invalid={!!form.formState.errors.question} {...form.register("question")} />
            </Field>
            <AnswerFields form={form} index={0} pid={pid} />
            {mode === "compare" && <AnswerFields form={form} index={1} pid={pid} />}
            <Field label="Reference answer (optional)" htmlFor="ref" hint="Enables answer correctness and context metrics.">
              <Textarea id="ref" rows={2} {...form.register("reference")} />
            </Field>
            <Field
              label="Context (optional)"
              htmlFor="ctx"
              hint={`Separate chunks with a blank line. Enables faithfulness and hallucination.${
                splitContext(values.context ?? "").length ? ` ${splitContext(values.context).length} chunk(s).` : ""
              }`}
            >
              <Textarea id="ctx" rows={4} {...form.register("context")} />
            </Field>
            <Field label="Evaluation criteria (optional)" htmlFor="crit" hint="A rubric for the Custom criteria metric (DeepEval GEval).">
              <Textarea id="crit" rows={2} {...form.register("criteria")} />
            </Field>

            <MetricSelector form={form} catalog={catalog} />

            <div className="grid grid-cols-2 gap-4">
              <Field label="Judge" htmlFor="judge" error={form.formState.errors.judge_config_id?.message}>
                <NativeSelect id="judge" {...form.register("judge_config_id")}>
                  {judges.map((j) => (
                    <option key={j.id} value={j.id}>
                      {j.name} ({j.model})
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <div className="space-y-1.5">
                <Label htmlFor="repeats">Repeats</Label>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label="Fewer repeats"
                    disabled={values.repeats <= 1}
                    onClick={() => form.setValue("repeats", Math.max(1, Number(values.repeats) - 1), { shouldValidate: true, shouldDirty: true })}
                  >
                    <Minus />
                  </Button>
                  <Input id="repeats" type="number" min={1} max={10} className="w-14 text-center" {...form.register("repeats")} />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    aria-label="More repeats"
                    disabled={values.repeats >= 10}
                    onClick={() => form.setValue("repeats", Math.min(10, Number(values.repeats) + 1), { shouldValidate: true, shouldDirty: true })}
                  >
                    <Plus />
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">1 to explore · 3 recommended to measure judge noise</p>
              </div>
            </div>
            {biasWarnings.length > 0 && (
              <div role="status" className="flex gap-2 rounded-md border border-warn/40 bg-warn-bg p-3 text-xs">
                <AlertTriangle className="size-4 shrink-0 text-warn" aria-hidden />
                <div className="space-y-1">
                  {biasWarnings.map((w, i) => (
                    <p key={i}>{w.message}</p>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <Field
                label="Budget cap, USD (optional)"
                htmlFor="budget"
                error={form.formState.errors.budget_usd?.message}
                hint="The run stops when measured cost reaches this."
              >
                <Input id="budget" inputMode="decimal" placeholder="e.g. 0.50" {...form.register("budget_usd")} />
              </Field>
              <Field label="Run name (optional)" htmlFor="rname">
                <Input id="rname" placeholder={`Studio: ${values.outputs?.[0]?.label ?? ""}…`} {...form.register("run_name")} />
              </Field>
            </div>
            <div className="flex items-center gap-3">
              <Controller
                control={form.control}
                name="use_cache"
                render={({ field }) => <Switch id="cache" checked={field.value} onCheckedChange={field.onChange} />}
              />
              <label htmlFor="cache" className="text-sm">
                Reuse cached judge results for identical inputs
              </label>
            </div>
          </div>
          {estimate?.not_applicable.length ? (
            <p className="border-t px-5 py-2 text-xs text-muted-foreground">
              N/A for some answers: {estimate.not_applicable.join("; ")}
            </p>
          ) : null}
          <div className="sticky bottom-0 z-10 flex items-center justify-between gap-3 rounded-b-lg border-t bg-card/95 px-5 py-3 shadow-[0_-4px_12px_-6px_rgb(0_0_0/0.15)] backdrop-blur">
            <EstimateBar estimate={estimate} error={estimateError} loading={estimating} />
            <Button type="submit" disabled={!isValid} loading={create.isPending}>
              <Play /> Run
            </Button>
          </div>
        </form>

        {/* ------------------------------------------------ right: results */}
        <section aria-label="Results" aria-live="polite" className="min-w-0 space-y-4">
          {runId ? (
            <ActiveRun runId={runId} pid={pid} />
          ) : (
            <Card className="flex min-h-80 flex-col items-center justify-center gap-2 p-8 text-center">
              <Play className="size-8 text-muted-foreground" aria-hidden />
              <p className="font-medium">Results appear here</p>
              <p className="max-w-sm text-sm text-muted-foreground">
                Each metric shows its score, threshold, status and engine. Shared metrics show Ragas and DeepEval side by
                side; they are different implementations and are never averaged together.
              </p>
            </Card>
          )}
        </section>
      </div>
    </>
  );
}

function ActiveRun({ runId, pid }: { runId: number; pid: number }) {
  const run = useRun(runId);
  if (!run.data) return null;
  const r = run.data;
  return (
    <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">{r.run_name}</h2>
          <StatusChip status={r.status} label={r.status_reason === "budget_cap_reached" ? "Stopped: budget cap" : undefined} />
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link to={`/p/${pid}/runs/${r.id}`}>
            Open run detail <ArrowUpRight />
          </Link>
        </Button>
      </div>
      <BiasBanner runId={r.id} warnings={r.warnings ?? []} />
      <RunHeaderStats run={r} />
      {(isActive(r) || r.completed_tests > 0) && <RunResultsBody run={r} />}
    </>
  );
}
