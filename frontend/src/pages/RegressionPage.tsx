import { AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Flag, GitCompareArrows, Minus, Trash2, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { EngineBadge, StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, CardContent, CardHeader, CardTitle, Checkbox, Field, NativeSelect } from "@/components/ui/primitives";
import { evaluateGate, fetchRegression, useBaselineMutations, useBaselines, useGates, useMetricsCatalog, useProject, useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { metricLabel } from "@/lib/metrics";
import { cn, fmtDate, fmtScore } from "@/lib/utils";
import { ApiError, errorMessage } from "@/services/api";
import type { GateEvaluation, RegressionCase, RegressionReport } from "@/types";

const STATUS_META: Record<string, { label: string; cls: string; Icon: typeof Minus }> = {
  regressed: { label: "Regressed", cls: "bg-fail-bg text-fail", Icon: ArrowDownRight },
  improved: { label: "Improved", cls: "bg-pass-bg text-pass", Icon: ArrowUpRight },
  unchanged: { label: "Within tolerance", cls: "bg-na-bg text-na", Icon: Minus },
  no_data: { label: "No paired scores", cls: "bg-warn-bg text-warn", Icon: AlertTriangle },
  no_baseline: { label: "Not in baseline", cls: "bg-na-bg text-na", Icon: Minus },
};

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

export function GateBanner({ ev }: { ev: GateEvaluation }) {
  const g = ev.gate;
  return (
    <Card className={cn("border-2 p-4", g.passed ? "border-pass/50" : "border-fail/50")}>
      <div className="mb-3 flex items-center gap-2">
        {g.passed ? <CheckCircle2 className="size-5 text-pass" aria-hidden /> : <XCircle className="size-5 text-fail" aria-hidden />}
        <h3 className="font-semibold">
          Gate “{g.gate_name}”: {g.passed ? "passed" : "failed"}
        </h3>
        {ev.id ? <Badge variant="outline">recorded #{ev.id}</Badge> : <Badge variant="outline">preview, not recorded</Badge>}
      </div>
      <table className="w-full text-sm">
        <tbody>
          {g.rules.map((r) => (
            <tr key={`${r.metric}${r.engine}`} className="border-t align-top">
              <td className="w-56 py-2 pr-3">
                <span className="font-medium">{r.metric.replace(/_/g, " ")}</span> <EngineBadge engine={r.engine} />
              </td>
              <td className="w-20 py-2 pr-3">
                <StatusChip status={r.passed ? "passed" : "failed"} />
              </td>
              <td className="py-2">
                <ul className="space-y-0.5">
                  {r.checks.map((c) => (
                    <li key={c.check} className={cn("text-xs", c.skipped ? "text-muted-foreground" : c.passed ? "text-pass" : "text-fail")}>
                      {c.skipped ? "○" : c.passed ? "✓" : "✗"} {c.check.replace(/_/g, " ")}: {c.detail}
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function CaseDiff({ c, onClose }: { c: RegressionCase | null; onClose: () => void }) {
  return (
    <Dialog open={!!c} onOpenChange={(o) => !o && onClose()}>
      {c && (
        <DialogContent side="right" title="Baseline vs candidate" description={c.input}>
          <div className="space-y-4 text-sm">
            <div className="grid grid-cols-2 gap-3">
              {(
                [
                  ["Baseline", c.baseline_status, c.baseline_output],
                  ["Candidate", c.candidate_status, c.candidate_output],
                ] as const
              ).map(([title, status, out]) => (
                <div key={title} className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center justify-between">
                    <span className="font-semibold">{title}</span>
                    <StatusChip status={status as "passed"} />
                  </div>
                  <p className="whitespace-pre-wrap">{out ?? "—"}</p>
                </div>
              ))}
            </div>
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Score change per metric</h4>
              <ul className="mt-1 space-y-1">
                {Object.entries(c.metric_deltas).map(([k, d]) => (
                  <li key={k} className="flex justify-between border-b py-1 last:border-0">
                    <span>{k.replace("|", " · ").replace(/_/g, " ")}</span>
                    <span className={cn("tabular font-medium", d < 0 ? "text-fail" : d > 0 ? "text-pass" : "")}>
                      {d > 0 ? "+" : ""}
                      {d.toFixed(3)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </DialogContent>
      )}
    </Dialog>
  );
}

function CaseList({ title, cases, tone, onOpen }: { title: string; cases: RegressionCase[]; tone: "fail" | "pass"; onOpen: (c: RegressionCase) => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className={tone === "fail" ? "text-fail" : "text-pass"}>
          {title} ({cases.length})
        </CardTitle>
      </CardHeader>
      <CardContent>
        {cases.length === 0 ? (
          <p className="text-sm text-muted-foreground">None.</p>
        ) : (
          <ul className="divide-y">
            {cases.map((c) => (
              <li key={`${c.case_key}${c.label}`}>
                <button type="button" onClick={() => onOpen(c)} className="flex w-full cursor-pointer items-center gap-3 py-2 text-left hover:bg-accent/40">
                  <span className="line-clamp-1 flex-1 text-sm">{c.input}</span>
                  <StatusChip status={c.baseline_status as "passed"} />
                  <span className="text-muted-foreground">→</span>
                  <StatusChip status={c.candidate_status as "passed"} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ReportView({ report }: { report: RegressionReport }) {
  const catalog = useMetricsCatalog();
  const [open, setOpen] = useState<RegressionCase | null>(null);
  return (
    <div className="space-y-4">
      {report.warnings.map((w) => (
        <p key={w} role="status" className="flex items-center gap-2 rounded-md bg-warn-bg px-3 py-2 text-sm text-warn">
          <AlertTriangle className="size-4 shrink-0" aria-hidden /> {w}
        </p>
      ))}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-4 py-2 font-medium">Metric</th>
              <th className="px-4 py-2 text-right font-medium">
                <Tooltip content="Cases scored in both runs (errors and N/A on either side are excluded)">
                  <span tabIndex={0} className="underline decoration-dotted">
                    Paired
                  </span>
                </Tooltip>
              </th>
              <th className="px-4 py-2 text-right font-medium">Baseline</th>
              <th className="px-4 py-2 text-right font-medium">Candidate</th>
              <th className="px-4 py-2 text-right font-medium">Δ</th>
              <th className="px-4 py-2 text-right font-medium">Tolerance</th>
              <th className="px-4 py-2 text-right font-medium">
                <Tooltip content="Standard error of judge noise for the difference; only when both runs have 3+ repeats. A drop must exceed both the tolerance and this to count.">
                  <span tabIndex={0} className="underline decoration-dotted">
                    SE
                  </span>
                </Tooltip>
              </th>
              <th className="px-4 py-2 text-right font-medium">Pass rate</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="border-l px-4 py-2 text-right font-medium">
                <Tooltip content="Whole candidate run, including cases without a baseline score. This is what a gate's min score and min pass rate check.">
                  <span tabIndex={0} className="underline decoration-dotted">
                    Candidate, all cases
                  </span>
                </Tooltip>
              </th>
            </tr>
          </thead>
          <tbody>
            {report.metrics.map((m) => {
              const s = STATUS_META[m.status];
              return (
                <tr key={`${m.metric_name}${m.engine}`} className="border-b last:border-0">
                  <td className="px-4 py-2.5">
                    {metricLabel(catalog.data, m.metric_name)} <EngineBadge engine={m.engine} />
                  </td>
                  <td className="tabular px-4 py-2.5 text-right text-muted-foreground">{m.paired}</td>
                  <td className="tabular px-4 py-2.5 text-right">{fmtScore(m.baseline_mean)}</td>
                  <td className="tabular px-4 py-2.5 text-right">{fmtScore(m.candidate_mean_paired)}</td>
                  <td className={cn("tabular px-4 py-2.5 text-right font-semibold", (m.delta ?? 0) < 0 ? "text-fail" : (m.delta ?? 0) > 0 ? "text-pass" : "")}>
                    {m.delta == null ? "—" : `${m.delta > 0 ? "+" : ""}${m.delta.toFixed(3)}`}
                  </td>
                  <td className="tabular px-4 py-2.5 text-right text-muted-foreground">−{m.tolerance.toFixed(2)}</td>
                  <td className="tabular px-4 py-2.5 text-right text-muted-foreground">{m.standard_error == null ? "—" : m.standard_error.toFixed(3)}</td>
                  <td className="tabular px-4 py-2.5 text-right">
                    {pct(m.baseline_pass_rate)} → {pct(m.candidate_pass_rate_paired)}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", s.cls)}>
                      <s.Icon className="size-3.5" aria-hidden /> {s.label}
                    </span>
                  </td>
                  <td className="tabular border-l px-4 py-2.5 text-right text-muted-foreground">
                    {fmtScore(m.candidate_mean)} · {pct(m.candidate_pass_rate)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          {report.paired_cases} paired case(s) · {report.unchanged_count} unchanged. {report.note}
        </p>
      </Card>
      <div className="grid grid-cols-2 gap-4">
        <CaseList title="Regressed cases" cases={report.regressed} tone="fail" onOpen={setOpen} />
        <CaseList title="Improved cases" cases={report.improved} tone="pass" onOpen={setOpen} />
      </div>
      <CaseDiff c={open} onClose={() => setOpen(null)} />
    </div>
  );
}

export function RegressionPage() {
  const pid = Number(useParams().projectId);
  const [sp] = useSearchParams();
  const project = useProject(pid);
  const baselines = useBaselines(pid);
  const bm = useBaselineMutations(pid);
  const gates = useGates(pid);
  const runs = useRuns(pid, { page: 1, page_size: 100, status: "completed" });
  const [baselineId, setBaselineId] = useState(Number(sp.get("baseline") ?? 0));
  const [candidateId, setCandidateId] = useState(Number(sp.get("candidate") ?? 0));
  const [gateId, setGateId] = useState(0);
  const [allowMismatch, setAllowMismatch] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [report, setReport] = useState<RegressionReport | null>(null);
  const [evaluation, setEvaluation] = useState<GateEvaluation | null>(null);
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const canEdit = project.data?.role !== "viewer";

  useEffect(() => {
    if (!baselineId && baselines.data?.length) setBaselineId(baselines.data[0].id);
  }, [baselines.data, baselineId]);
  const baseline = baselines.data?.find((b) => b.id === baselineId);
  const candidates = useMemo(
    () =>
      (runs.data?.items ?? []).filter((r) => {
        if (r.id === baseline?.run_id) return false;
        const vid = (r.config_snapshot as { dataset?: { version_id: number } | null }).dataset?.version_id;
        return vid != null && (showAll || vid === baseline?.dataset_version_id);
      }),
    [runs.data, baseline, showAll],
  );
  useEffect(() => {
    if (!candidates.some((c) => c.id === candidateId)) setCandidateId(candidates[0]?.id ?? 0);
  }, [candidates, candidateId]);

  const compare = async (record: boolean) => {
    if (!baselineId || !candidateId) return;
    setBusy(true);
    setMismatch(null);
    try {
      if (gateId) {
        const ev = await evaluateGate(gateId, { run_id: candidateId, baseline_id: baselineId, allow_dataset_mismatch: allowMismatch, record });
        setEvaluation(ev);
        setReport(ev.report);
        if (record) toast.success(`Gate ${ev.status}: evaluation recorded`);
      } else {
        setEvaluation(null);
        setReport(await fetchRegression(candidateId, baselineId, allowMismatch));
      }
    } catch (e) {
      if (e instanceof ApiError && e.code === "dataset_version_mismatch") setMismatch(e.message);
      else toast.error(errorMessage(e));
      setReport(null);
      setEvaluation(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Regression Testing" }, { label: "Baselines & Compare" }]}
        title="Baselines & Compare"
        description="Compare a candidate run to a saved baseline on the same dataset version, per metric, with tolerance for judge noise."
      />
      <div className="space-y-5 p-6">
        <Card>
          <CardHeader>
            <CardTitle>Baselines</CardTitle>
          </CardHeader>
          <CardContent>
            {baselines.isLoading && <TableSkeleton rows={2} cols={4} />}
            {baselines.isError && <ErrorState error={baselines.error} onRetry={() => baselines.refetch()} />}
            {baselines.data?.length === 0 && (
              <EmptyState
                icon={Flag}
                title="No baselines yet"
                description="Open a completed dataset run and choose “Save as baseline” to pin it as the reference."
                action={<Button asChild variant="outline"><Link to={`/p/${pid}/runs`}>Go to Test Runs</Link></Button>}
              />
            )}
            {!!baselines.data?.length && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 font-medium">Name</th>
                    <th className="py-2 font-medium">Dataset version</th>
                    <th className="py-2 font-medium">Run</th>
                    <th className="py-2 font-medium">Saved</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {baselines.data.map((b) => (
                    <tr key={b.id} className="border-b last:border-0">
                      <td className="py-2 font-medium">{b.name}</td>
                      <td className="py-2">
                        {b.dataset_name} <Badge>v{b.version_number}</Badge>
                      </td>
                      <td className="py-2">
                        <Link className="text-primary hover:underline" to={`/p/${pid}/runs/${b.run_id}`}>
                          #{b.run_id} {b.run_name}
                        </Link>
                      </td>
                      <td className="py-2 text-muted-foreground">{fmtDate(b.created_at)}</td>
                      <td className="py-2 text-right">
                        {canEdit && (
                          <ConfirmButton
                            variant="ghost"
                            size="icon"
                            title={`Delete baseline “${b.name}”?`}
                            confirmLabel="Delete baseline"
                            consequences="The run itself is kept. CI jobs that reference this baseline id will fail until updated."
                            onConfirm={async () => {
                              try {
                                await bm.remove.mutateAsync(b.id);
                                toast.success("Baseline deleted");
                              } catch (e) {
                                toast.error(errorMessage(e));
                              }
                            }}
                          >
                            <Trash2 aria-label={`Delete ${b.name}`} />
                          </ConfirmButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

        {!!baselines.data?.length && (
          <Card>
            <CardHeader>
              <CardTitle>Compare</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <Field label="Baseline" htmlFor="rg-base">
                  <NativeSelect id="rg-base" value={baselineId} onChange={(e) => (setBaselineId(Number(e.target.value)), setReport(null))}>
                    {baselines.data.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name} · {b.dataset_name} v{b.version_number}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Candidate run" htmlFor="rg-cand" hint={showAll ? "All completed runs" : "Completed runs on the baseline's dataset version"}>
                  <NativeSelect id="rg-cand" value={candidateId} onChange={(e) => (setCandidateId(Number(e.target.value)), setReport(null))}>
                    {candidates.length === 0 && <option value={0}>No matching runs</option>}
                    {candidates.map((r) => (
                      <option key={r.id} value={r.id}>
                        #{r.id} {r.run_name}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Quality gate (optional)" htmlFor="rg-gate">
                  <NativeSelect id="rg-gate" value={gateId} onChange={(e) => setGateId(Number(e.target.value))}>
                    <option value={0}>No gate: regression report only</option>
                    {gates.data?.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name} ({g.rules.length} rules)
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={showAll} onCheckedChange={(v) => setShowAll(v === true)} /> Show runs on other dataset versions
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox checked={allowMismatch} onCheckedChange={(v) => setAllowMismatch(v === true)} /> Allow dataset version mismatch
                </label>
                <div className="ml-auto flex gap-2">
                  <Button variant={gateId ? "outline" : "default"} disabled={!candidateId} loading={busy} onClick={() => compare(false)}>
                    <GitCompareArrows /> {gateId ? "Preview" : "Compare"}
                  </Button>
                  {gateId > 0 && canEdit && (
                    <Button disabled={!candidateId} loading={busy} onClick={() => compare(true)}>
                      Evaluate gate & record
                    </Button>
                  )}
                </div>
              </div>
              {mismatch && (
                <div role="alert" className="flex items-start gap-2 rounded-md border border-fail/40 bg-fail-bg p-3 text-sm text-fail">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
                  <div>
                    <p className="font-medium">Different dataset versions</p>
                    <p>{mismatch}</p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {evaluation && <GateBanner ev={evaluation} />}
        {report && <ReportView report={report} />}
      </div>
    </>
  );
}
