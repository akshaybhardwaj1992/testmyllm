import { AlertTriangle, Info, Minus, Play, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { MetricPicker } from "@/components/metric-picker";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Field, Input, Label, NativeSelect, Switch } from "@/components/ui/primitives";
import { estimateRun, useCreateRun, useJudges, useMetricsCatalog, useProviders } from "@/hooks/queries";
import { ModelDatalist } from "@/pages/JudgesPage";
import { defaultMetricState, selectedMetrics, type MetricState } from "@/lib/metrics";
import { fmtUsd } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { Estimate, InputName, RunCreate } from "@/types";

export type RunSource = { suite_id: number } | { dataset_version_id: number };

/** Start an evaluation over a suite or a dataset version (spec §9: dataset-backed LLM testing). */
export function RunDialog({
  pid,
  source,
  sourceLabel,
  caseCount,
  fieldsPresent,
  open,
  onOpenChange,
}: {
  pid: number;
  source: RunSource;
  sourceLabel: string;
  caseCount: number;
  fieldsPresent: string[];
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const catalog = useMetricsCatalog();
  const judges = useJudges(pid);
  const providers = useProviders(pid);
  const create = useCreateRun();
  const navigate = useNavigate();
  const hasRecorded = fieldsPresent.includes("response");

  const [answerSource, setAnswerSource] = useState<"dataset" | "provider">(hasRecorded ? "dataset" : "provider");
  const [label, setLabel] = useState(hasRecorded ? "Recorded" : "Model");
  const [providerId, setProviderId] = useState(0);
  const [model, setModel] = useState("");
  const [maxTokens, setMaxTokens] = useState(1000);
  const [metrics, setMetrics] = useState<MetricState>({});
  const [judgeId, setJudgeId] = useState(0);
  const [repeats, setRepeats] = useState(1);
  const [budget, setBudget] = useState("");
  const [useCache, setUseCache] = useState(true);
  const [runName, setRunName] = useState("");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimateError, setEstimateError] = useState<string | null>(null);

  useEffect(() => {
    if (catalog.data && Object.keys(metrics).length === 0) {
      const defaults = ["answer_relevancy"];
      if (fieldsPresent.includes("context")) defaults.push("faithfulness");
      if (fieldsPresent.includes("reference")) defaults.push("answer_correctness");
      setMetrics(defaultMetricState(catalog.data, defaults));
    }
  }, [catalog.data, metrics, fieldsPresent]);
  useEffect(() => {
    if (judges.data?.length && !judgeId) setJudgeId((judges.data.find((j) => j.is_default) ?? judges.data[0]).id);
  }, [judges.data, judgeId]);
  useEffect(() => {
    if (providers.data?.length && !providerId) setProviderId(providers.data[0].id);
  }, [providers.data, providerId]);

  const present = useMemo(() => {
    const s = new Set<InputName>(["question"]);
    if (fieldsPresent.includes("reference")) s.add("reference");
    if (fieldsPresent.includes("context")) s.add("context");
    if (fieldsPresent.includes("criteria")) s.add("criteria");
    if (answerSource === "provider" || hasRecorded) s.add("response");
    return s;
  }, [fieldsPresent, answerSource, hasRecorded]);

  const chosen = catalog.data ? selectedMetrics(metrics, catalog.data, present) : [];
  const budgetValid = budget === "" || (Number(budget) > 0 && Number(budget) <= 10000);
  const outputValid = label.trim() && (answerSource === "dataset" ? hasRecorded : providerId > 0 && model.trim());
  const valid = !!(chosen.length && judgeId && outputValid && budgetValid && repeats >= 1 && repeats <= 10);

  const payload: RunCreate | null = valid
    ? {
        project_id: pid,
        run_name: runName.trim() || null,
        run_type: "llm",
        judge_config_id: judgeId,
        repeats,
        budget_usd: budget ? Number(budget) : null,
        use_cache: useCache,
        metrics: chosen,
        ...source,
        outputs: [
          answerSource === "dataset"
            ? { label: label.trim(), source: "dataset", max_tokens: 1000 }
            : { label: label.trim(), source: "provider", provider_id: providerId, model: model.trim(), max_tokens: maxTokens },
        ],
      }
    : null;
  const sig = payload ? JSON.stringify(payload) : "";
  const req = useRef(0);
  useEffect(() => {
    if (!sig || !open) {
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
  }, [sig, open]);

  const bias = estimate?.warnings.filter((w) => w.type === "judge_bias") ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title={`Run ${sourceLabel}`} description={`${caseCount} case(s). Dataset fields: ${fieldsPresent.join(", ") || "question only"}.`}>
        <ModelDatalist id="run-models" />
        <div className="space-y-5">
          <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
            <div className="flex items-end gap-3">
              <Field label="Answer label" htmlFor="rd-label" className="w-40">
                <Input id="rd-label" value={label} onChange={(e) => setLabel(e.target.value)} />
              </Field>
              <div className="space-y-1.5">
                <span className="text-sm font-medium">Answers to score</span>
                <div role="radiogroup" aria-label="Answers to score" className="flex rounded-md border bg-card p-0.5">
                  {(
                    [
                      ["dataset", "Recorded responses"],
                      ["provider", "Generate with a model"],
                    ] as const
                  ).map(([v, text]) => (
                    <Tooltip key={v} content={v === "dataset" && !hasRecorded ? "This dataset has no response column" : null}>
                      <button
                        type="button"
                        role="radio"
                        aria-checked={answerSource === v}
                        disabled={v === "dataset" && !hasRecorded}
                        onClick={() => setAnswerSource(v)}
                        className={`h-7 cursor-pointer rounded px-3 text-sm disabled:cursor-not-allowed disabled:opacity-40 ${
                          answerSource === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {text}
                      </button>
                    </Tooltip>
                  ))}
                </div>
              </div>
            </div>
            {answerSource === "provider" && (
              <div className="grid grid-cols-[1fr_1.4fr_110px] gap-3">
                <Field label="Provider" htmlFor="rd-prov">
                  <NativeSelect id="rd-prov" value={providerId} onChange={(e) => setProviderId(Number(e.target.value))}>
                    {providers.data?.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Model" htmlFor="rd-model">
                  <Input id="rd-model" list="run-models" placeholder="openai/gpt-4o" value={model} onChange={(e) => setModel(e.target.value)} />
                </Field>
                <Field label="Max tokens" htmlFor="rd-mt">
                  <Input id="rd-mt" type="number" min={1} max={8000} value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} />
                </Field>
              </div>
            )}
          </div>

          {catalog.data && (
            <MetricPicker
              catalog={catalog.data}
              present={present}
              value={metrics}
              onChange={setMetrics}
              presentHint="Availability is based on the fields any case in this dataset version has. Cases missing a field record that metric as N/A."
            />
          )}

          <div className="grid grid-cols-2 gap-4">
            <Field label="Judge" htmlFor="rd-judge">
              <NativeSelect id="rd-judge" value={judgeId} onChange={(e) => setJudgeId(Number(e.target.value))}>
                {judges.data?.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.name} ({j.model})
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <div className="space-y-1.5">
              <Label htmlFor="rd-rep">Repeats</Label>
              <div className="flex items-center gap-1">
                <Button type="button" variant="outline" size="icon" aria-label="Fewer repeats" disabled={repeats <= 1} onClick={() => setRepeats(repeats - 1)}>
                  <Minus />
                </Button>
                <Input id="rd-rep" type="number" min={1} max={10} className="w-14 text-center" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))} />
                <Button type="button" variant="outline" size="icon" aria-label="More repeats" disabled={repeats >= 10} onClick={() => setRepeats(repeats + 1)}>
                  <Plus />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">3 recommended for regression runs</p>
            </div>
            <Field label="Budget cap, USD (optional)" htmlFor="rd-budget" error={budgetValid ? undefined : "Positive amount up to 10,000"}>
              <Input id="rd-budget" inputMode="decimal" placeholder="e.g. 2.00" value={budget} onChange={(e) => setBudget(e.target.value)} />
            </Field>
            <Field label="Run name (optional)" htmlFor="rd-name">
              <Input id="rd-name" value={runName} onChange={(e) => setRunName(e.target.value)} />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Switch id="rd-cache" checked={useCache} onCheckedChange={setUseCache} />
            <label htmlFor="rd-cache" className="text-sm">
              Reuse cached judge results for identical inputs
            </label>
          </div>
          {bias.length > 0 && (
            <div role="status" className="flex gap-2 rounded-md border border-warn/40 bg-warn-bg p-3 text-xs">
              <AlertTriangle className="size-4 shrink-0 text-warn" aria-hidden />
              <div>{bias.map((w, i) => <p key={i}>{w.message}</p>)}</div>
            </div>
          )}
          <div className="sticky bottom-0 -mx-5 -mb-5 flex items-center justify-between gap-3 border-t bg-card px-5 py-3">
            {estimateError ? (
              <span className="text-xs text-destructive">{estimateError}</span>
            ) : estimate ? (
              <span className="inline-flex items-center gap-2 text-sm">
                <Info className="size-4 text-muted-foreground" aria-hidden />
                <span className="text-muted-foreground">Estimated cost</span>
                <span className="tabular font-semibold">{fmtUsd(estimate.total_usd)}</span>
                <span className="tabular text-xs text-muted-foreground">
                  (model {fmtUsd(estimate.model_under_test.usd)} · judge {fmtUsd(estimate.judge.usd)} · {estimate.judge_calls} judge calls)
                </span>
              </span>
            ) : (
              <span className="text-xs text-muted-foreground">{valid ? "Estimating…" : "Choose answers and at least one metric."}</span>
            )}
            <Button
              disabled={!payload}
              loading={create.isPending}
              onClick={async () => {
                if (!payload) return;
                try {
                  const run = await create.mutateAsync(payload);
                  toast.success(`Run started: ${run.total_tests} evaluation(s) queued`);
                  onOpenChange(false);
                  navigate(`/p/${pid}/runs/${run.id}`);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              <Play /> Start run
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
