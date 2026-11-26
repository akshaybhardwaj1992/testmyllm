import { Info, Pencil, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { EngineBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Card, Field, Input, NativeSelect } from "@/components/ui/primitives";
import { evaluateGate, useBaselines, useGateMutations, useGates, useMetricsCatalog, useProject, useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { metricLabel } from "@/lib/metrics";
import { GateBanner } from "@/pages/RegressionPage";
import { errorMessage } from "@/services/api";
import type { GateEvaluation, QualityGate } from "@/types";

type RuleRow = { key: number; metricEngine: string; min_score: string; max_regression: string; min_pass_rate: string };
let k = 1;
const toNum = (s: string) => (s.trim() === "" ? null : Number(s));
const valid01 = (s: string) => s.trim() === "" || (Number(s) >= 0 && Number(s) <= 1);

function GateEditor({ pid, gate, open, onOpenChange }: { pid: number; gate?: QualityGate; open: boolean; onOpenChange: (o: boolean) => void }) {
  const catalog = useMetricsCatalog();
  const m = useGateMutations(pid);
  const [name, setName] = useState("");
  const [rows, setRows] = useState<RuleRow[]>([]);
  useEffect(() => {
    if (!open) return;
    setName(gate?.name ?? "Release gate");
    setRows(
      gate
        ? gate.rules.map((r) => ({
            key: k++, metricEngine: `${r.metric}|${r.engine}`, min_score: r.min_score?.toString() ?? "",
            max_regression: r.max_regression?.toString() ?? "", min_pass_rate: r.min_pass_rate?.toString() ?? "",
          }))
        : [{ key: k++, metricEngine: "answer_relevancy|ragas", min_score: "0.7", max_regression: "0.05", min_pass_rate: "" }],
    );
  }, [open, gate]);
  const options = catalog.data?.flatMap((d) => d.engines.map((e) => ({ v: `${d.name}|${e.engine}`, l: `${d.label} (${e.engine})` }))) ?? [];
  const problems: string[] = [];
  if (!name.trim()) problems.push("Name is required");
  if (!rows.length) problems.push("Add at least one rule");
  rows.forEach((r, i) => {
    if (![r.min_score, r.max_regression, r.min_pass_rate].every(valid01)) problems.push(`Rule ${i + 1}: values must be between 0 and 1`);
    if ([r.min_score, r.max_regression, r.min_pass_rate].every((s) => s.trim() === "")) problems.push(`Rule ${i + 1}: set at least one check`);
  });
  const keys = rows.map((r) => r.metricEngine);
  if (new Set(keys).size !== keys.length) problems.push("One rule per metric and engine");
  const set = (key: number, patch: Partial<RuleRow>) => setRows(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title={gate ? `Edit ${gate.name}` : "New quality gate"} description="Per-metric rules. A gate passes only if every rule passes.">
        <div className="space-y-4">
          <Field label="Name" htmlFor="qg-name">
            <Input id="qg-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <p className="flex items-start gap-2 rounded-md bg-info-bg px-3 py-2 text-xs text-info">
            <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              <b>Min score</b>: candidate mean must reach it. <b>Max regression</b>: allowed drop vs the baseline (default 0.05); with 3+ repeats
              in both runs a drop must also exceed one standard error of judge noise. <b>Min pass rate</b>: share of cases passing the metric's
              threshold. Use <b>3 repeats</b> for gated runs so noise can be measured.
            </span>
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground">
                <th className="pb-1 font-medium">Metric</th>
                <th className="pb-1 font-medium">Min score</th>
                <th className="pb-1 font-medium">Max regression</th>
                <th className="pb-1 font-medium">Min pass rate</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={r.key}>
                  <td className="py-1 pr-2">
                    <NativeSelect aria-label={`Metric for rule ${i + 1}`} value={r.metricEngine} onChange={(e) => set(r.key, { metricEngine: e.target.value })}>
                      {options.map((o) => (
                        <option key={o.v} value={o.v}>
                          {o.l}
                        </option>
                      ))}
                    </NativeSelect>
                  </td>
                  {(["min_score", "max_regression", "min_pass_rate"] as const).map((f) => (
                    <td key={f} className="w-28 py-1 pr-2">
                      <Input
                        aria-label={`${f.replace(/_/g, " ")} for rule ${i + 1}`}
                        placeholder="—"
                        inputMode="decimal"
                        aria-invalid={!valid01(r[f])}
                        value={r[f]}
                        onChange={(e) => set(r.key, { [f]: e.target.value })}
                      />
                    </td>
                  ))}
                  <td className="py-1">
                    <Button variant="ghost" size="icon" aria-label={`Remove rule ${i + 1}`} onClick={() => setRows(rows.filter((x) => x.key !== r.key))}>
                      <X />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <Button variant="outline" size="sm" onClick={() => setRows([...rows, { key: k++, metricEngine: options.find((o) => !keys.includes(o.v))?.v ?? options[0].v, min_score: "", max_regression: "0.05", min_pass_rate: "" }])}>
            <Plus /> Add rule
          </Button>
          {problems.length > 0 && (
            <ul role="alert" className="space-y-0.5 rounded-md bg-fail-bg px-3 py-2 text-sm text-fail">
              {problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          <div className="flex justify-end gap-2 border-t pt-4">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              disabled={problems.length > 0}
              loading={m.save.isPending}
              onClick={async () => {
                try {
                  await m.save.mutateAsync({
                    ...(gate ? { id: gate.id } : {}),
                    project_id: pid,
                    name: name.trim(),
                    rules: rows.map((r) => {
                      const [metric, engine] = r.metricEngine.split("|");
                      return { metric, engine: engine as "ragas" | "deepeval", min_score: toNum(r.min_score), max_regression: toNum(r.max_regression), min_pass_rate: toNum(r.min_pass_rate) };
                    }),
                  });
                  toast.success("Gate saved");
                  onOpenChange(false);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Save gate
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function TestGate({ pid, gate, onClose }: { pid: number; gate: QualityGate | null; onClose: () => void }) {
  const runs = useRuns(pid, { page: 1, page_size: 100, status: "completed" });
  const baselines = useBaselines(pid);
  const [runId, setRunId] = useState(0);
  const [baselineId, setBaselineId] = useState(0);
  const [allowMismatch, setAllowMismatch] = useState(false);
  const [ev, setEv] = useState<GateEvaluation | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!runId && runs.data?.items.length) setRunId(runs.data.items[0].id);
  }, [runs.data, runId]);
  return (
    <Dialog open={!!gate} onOpenChange={(o) => !o && (onClose(), setEv(null))}>
      {gate && (
        <DialogContent side="right" title={`Test “${gate.name}” against a past run`} description="A preview: nothing is recorded.">
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Run" htmlFor="tg-run">
                <NativeSelect id="tg-run" value={runId} onChange={(e) => setRunId(Number(e.target.value))}>
                  {runs.data?.items.map((r) => (
                    <option key={r.id} value={r.id}>
                      #{r.id} {r.run_name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
              <Field label="Baseline (optional)" htmlFor="tg-base">
                <NativeSelect id="tg-base" value={baselineId} onChange={(e) => setBaselineId(Number(e.target.value))}>
                  <option value={0}>None: skip regression checks</option>
                  {baselines.data?.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name}
                    </option>
                  ))}
                </NativeSelect>
              </Field>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={allowMismatch} onChange={(e) => setAllowMismatch(e.target.checked)} /> Allow dataset version mismatch
            </label>
            <Button
              loading={busy}
              disabled={!runId}
              onClick={async () => {
                setBusy(true);
                try {
                  setEv(await evaluateGate(gate.id, { run_id: runId, baseline_id: baselineId || null, allow_dataset_mismatch: allowMismatch, record: false }));
                } catch (e) {
                  toast.error(errorMessage(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              <ShieldCheck /> Evaluate
            </Button>
            {ev && <GateBanner ev={ev} />}
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}

export function QualityGatesPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const gates = useGates(pid);
  const catalog = useMetricsCatalog();
  const m = useGateMutations(pid);
  const [edit, setEdit] = useState<QualityGate | undefined>();
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState<QualityGate | null>(null);
  const canEdit = project.data?.role !== "viewer";

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Regression Testing" }, { label: "Quality Gates" }]}
        title="Quality Gates"
        description="Per-metric pass/fail rules for a release. Evaluate them in the UI or from CI with the testmyllm CLI."
        actions={canEdit && <Button onClick={() => (setEdit(undefined), setOpen(true))}><Plus /> New gate</Button>}
      />
      <div className="space-y-4 p-6">
        {gates.isLoading && <TableSkeleton rows={3} />}
        {gates.isError && <ErrorState error={gates.error} onRetry={() => gates.refetch()} />}
        {gates.data?.length === 0 && (
          <EmptyState
            icon={ShieldCheck}
            title="No quality gates yet"
            description="A gate turns metric scores into a pass/fail release decision, e.g. answer correctness ≥ 0.7 and no drop larger than 0.05 vs the baseline."
            action={canEdit && <Button onClick={() => setOpen(true)}><Plus /> New gate</Button>}
          />
        )}
        {gates.data?.map((g) => (
          <Card key={g.id} className="p-4">
            <div className="mb-3 flex items-center gap-2">
              <ShieldCheck className="size-5 text-muted-foreground" aria-hidden />
              <span className="font-semibold">{g.name}</span>
              <span className="text-xs text-muted-foreground">id {g.id}</span>
              <div className="ml-auto flex gap-1">
                <Button variant="outline" size="sm" onClick={() => setTesting(g)}>
                  Test against a run
                </Button>
                {canEdit && (
                  <>
                    <Button variant="ghost" size="icon" aria-label={`Edit ${g.name}`} onClick={() => (setEdit(g), setOpen(true))}>
                      <Pencil />
                    </Button>
                    <ConfirmButton
                      variant="ghost"
                      size="icon"
                      title={`Delete gate “${g.name}”?`}
                      confirmLabel="Delete gate"
                      consequences="Recorded evaluations keep their results. CI jobs using this gate id will fail until updated."
                      onConfirm={async () => {
                        try {
                          await m.remove.mutateAsync(g.id);
                          toast.success("Gate deleted");
                        } catch (e) {
                          toast.error(errorMessage(e));
                        }
                      }}
                    >
                      <Trash2 aria-label={`Delete ${g.name}`} />
                    </ConfirmButton>
                  </>
                )}
              </div>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-muted-foreground">
                  <th className="py-1.5 font-medium">Metric</th>
                  <th className="py-1.5 text-right font-medium">Min score</th>
                  <th className="py-1.5 text-right font-medium">Max regression</th>
                  <th className="py-1.5 text-right font-medium">Min pass rate</th>
                </tr>
              </thead>
              <tbody>
                {g.rules.map((r) => (
                  <tr key={`${r.metric}${r.engine}`} className="border-b last:border-0">
                    <td className="py-1.5">
                      {metricLabel(catalog.data, r.metric)} <EngineBadge engine={r.engine} />
                    </td>
                    <td className="tabular py-1.5 text-right">{r.min_score ?? "—"}</td>
                    <td className="tabular py-1.5 text-right">{r.max_regression ?? "—"}</td>
                    <td className="tabular py-1.5 text-right">{r.min_pass_rate == null ? "—" : `${Math.round(r.min_pass_rate * 100)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      <GateEditor pid={pid} gate={edit} open={open} onOpenChange={setOpen} />
      <TestGate pid={pid} gate={testing} onClose={() => setTesting(null)} />
    </>
  );
}
