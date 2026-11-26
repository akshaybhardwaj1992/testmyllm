import { BarChart3, Search, Table2, Trophy } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip as RTooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { EmptyState, ErrorState, Pagination, TableSkeleton } from "@/components/states";
import { EngineBadge, Score, StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/overlay";
import { Card, Checkbox, Input } from "@/components/ui/primitives";
import { isActive, useCaseComparison, useComparison, useMetricsCatalog } from "@/hooks/queries";
import { metricLabel } from "@/lib/metrics";
import { cn, fmtScore, fmtUsd } from "@/lib/utils";
import type { ModelSummary, Run } from "@/types";

const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];
const tooltipStyle = { background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 };
const axisTick = { fontSize: 11, fill: "var(--muted-foreground)" };

function ChartCard({ title, subtitle, chart, table }: { title: string; subtitle?: string; chart: React.ReactNode; table: React.ReactNode }) {
  const [asTable, setAsTable] = useState(false);
  return (
    <Card className="p-4">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <h3 className="font-semibold">{title}</h3>
          {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        <Button variant="ghost" size="sm" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
          {asTable ? <BarChart3 /> : <Table2 />} {asTable ? "Chart" : "Table"}
        </Button>
      </div>
      {asTable ? table : chart}
    </Card>
  );
}

function best<T>(items: T[], key: (t: T) => number | null | undefined, lowerIsBetter = false): T | undefined {
  const vals = items.filter((i) => key(i) != null);
  if (vals.length < 2) return undefined;
  return vals.reduce((a, b) => ((lowerIsBetter ? key(b)! < key(a)! : key(b)! > key(a)!) ? b : a));
}

export function ComparisonView({ run }: { run: Run }) {
  const catalog = useMetricsCatalog();
  const sig = `${run.status}:${run.completed_tests}`;
  const cmp = useComparison(run.id, sig);
  const models = cmp.data?.models ?? [];
  const cols = cmp.data?.metric_columns ?? [];
  const color = (label: string) => SERIES[Math.max(0, models.findIndex((m) => m.label === label)) % SERIES.length];

  const quality = useMemo(
    () =>
      cols.map((c) => {
        const row: Record<string, string | number | null> = { name: `${metricLabel(catalog.data, c.metric)} (${c.engine})` };
        models.forEach((m) => {
          row[m.label] = m.metrics.find((x) => x.metric_name === c.metric && x.engine === c.engine)?.mean ?? null;
        });
        return row;
      }),
    [cols, models, catalog.data],
  );

  if (cmp.isLoading) return <TableSkeleton rows={5} cols={6} />;
  if (cmp.isError) return <ErrorState error={cmp.error} onRetry={() => cmp.refetch()} />;
  if (!models.length) return <EmptyState icon={BarChart3} title="No models to compare yet" />;

  const bestQuality = best(models, (m) => m.quality);
  const bestCost = best(models, (m) => m.cost_per_case_usd, true);
  const bestLatency = best(models, (m) => m.latency_p50_ms, true);
  const Win = ({ on, what }: { on: boolean; what: string }) =>
    on ? (
      <Tooltip content={`Best ${what}`}>
        <Trophy className="ml-1 inline size-3.5 text-warn" aria-label={`best ${what}`} />
      </Tooltip>
    ) : null;

  return (
    <div className="space-y-4">
      {isActive(run) && <p className="text-sm text-muted-foreground">Figures update as answers complete.</p>}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[900px] text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="px-4 py-2 font-medium">Model</th>
              <th className="px-4 py-2 text-right font-medium">
                <Tooltip content={cmp.data?.quality_note}>
                  <span tabIndex={0} className="underline decoration-dotted">
                    Quality
                  </span>
                </Tooltip>
              </th>
              {cols.map((c) => (
                <th key={`${c.metric}|${c.engine}`} className="px-4 py-2 text-right font-medium">
                  {metricLabel(catalog.data, c.metric)}
                  <div className="font-normal">
                    <EngineBadge engine={c.engine} />
                  </div>
                </th>
              ))}
              <th className="px-4 py-2 text-right font-medium">Pass / fail / err</th>
              <th className="px-4 py-2 text-right font-medium">Latency p50 / p95</th>
              <th className="px-4 py-2 text-right font-medium">Avg output tokens</th>
              <th className="px-4 py-2 text-right font-medium">Cost / case</th>
              <th className="px-4 py-2 text-right font-medium">Model + judge cost</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.label} className="border-b last:border-0">
                <td className="px-4 py-2.5">
                  <span className="mr-2 inline-block size-2.5 rounded-full" style={{ background: color(m.label) }} aria-hidden />
                  <span className="font-medium">{m.label}</span>
                  <div className="font-mono text-[11px] text-muted-foreground">{m.model}</div>
                </td>
                <td className="tabular px-4 py-2.5 text-right font-semibold">
                  {fmtScore(m.quality)}
                  <Win on={bestQuality === m} what="quality" />
                </td>
                {cols.map((c) => {
                  const x = m.metrics.find((y) => y.metric_name === c.metric && y.engine === c.engine);
                  return (
                    <td key={`${c.metric}|${c.engine}`} className="tabular px-4 py-2.5 text-right">
                      {fmtScore(x?.mean)}
                      {x?.pass_rate != null && <div className="text-[11px] text-muted-foreground">{Math.round(x.pass_rate * 100)}% pass</div>}
                    </td>
                  );
                })}
                <td className="tabular px-4 py-2.5 text-right">
                  <span className="text-pass">{m.passed}</span> / <span className="text-fail">{m.failed}</span> /{" "}
                  <span className="text-warn">{m.errors}</span>
                </td>
                <td className="tabular px-4 py-2.5 text-right">
                  {m.latency_p50_ms ?? "—"} / {m.latency_p95_ms ?? "—"} ms
                  <Win on={bestLatency === m} what="latency" />
                </td>
                <td className="tabular px-4 py-2.5 text-right">{m.avg_output_tokens == null ? "—" : Math.round(m.avg_output_tokens)}</td>
                <td className="tabular px-4 py-2.5 text-right">
                  {fmtUsd(m.cost_per_case_usd)}
                  <Win on={bestCost === m} what="cost" />
                </td>
                <td className="tabular px-4 py-2.5 text-right text-muted-foreground">
                  {fmtUsd(m.model_cost_usd)} + {fmtUsd(m.judge_cost_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="border-t px-4 py-2 text-xs text-muted-foreground">
          {cmp.data?.quality_note} Same questions, same judge and metrics for every model. Cost = generation cost reported by the provider;
          “unknown” if any call had no price.
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <ChartCard
          title="Quality per metric"
          subtitle="Mean score, 0–1, higher is better"
          chart={
            <div className="h-72" role="img" aria-label="Grouped bar chart of mean score per metric for each model">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={quality} margin={{ top: 8, right: 8, bottom: 24, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="name" tick={{ ...axisTick, fontSize: 10 }} interval={0} angle={-12} textAnchor="end" height={48} />
                  <YAxis domain={[0, 1]} tick={axisTick} />
                  <RTooltip contentStyle={tooltipStyle} formatter={(v: number) => (v == null ? "—" : v.toFixed(3))} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {models.map((m) => (
                    <Bar key={m.label} dataKey={m.label} fill={color(m.label)} radius={[3, 3, 0, 0]} />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            </div>
          }
          table={<QualityTable rows={quality} models={models} />}
        />
        <ChartCard
          title="Cost vs quality"
          subtitle="Up and to the left is better"
          chart={
            <div className="h-72" role="img" aria-label="Scatter plot of cost per case against quality for each model">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                  <XAxis type="number" dataKey="cost" name="Cost / case" tick={axisTick} domain={[0, "auto"]} tickFormatter={(v) => fmtUsd(Number(v))} />
                  <YAxis type="number" dataKey="quality" name="Quality" domain={[0, 1]} tick={axisTick} />
                  <ZAxis range={[160, 160]} />
                  <RTooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: number, n: string) => (n === "Cost / case" ? fmtUsd(v) : v.toFixed(3))}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {models
                    .filter((m) => m.cost_per_case_usd != null && m.quality != null)
                    .map((m) => (
                      <Scatter key={m.label} name={m.label} data={[{ cost: m.cost_per_case_usd, quality: m.quality }]} fill={color(m.label)} />
                    ))}
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          }
          table={
            <SimpleTable
              head={["Model", "Cost / case", "Quality"]}
              rows={models.map((m) => [m.label, fmtUsd(m.cost_per_case_usd), fmtScore(m.quality)])}
            />
          }
        />
        <ChartCard
          title="Latency"
          subtitle="Generation time per answer (ms)"
          chart={
            <div className="h-64" role="img" aria-label="Bar chart of p50 and p95 latency per model">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={models.map((m) => ({ name: m.label, p50: m.latency_p50_ms, p95: m.latency_p95_ms }))} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="name" tick={axisTick} />
                  <YAxis tick={axisTick} />
                  <RTooltip contentStyle={tooltipStyle} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="p50" name="p50" radius={[3, 3, 0, 0]}>
                    {models.map((m) => (
                      <Cell key={m.label} fill={color(m.label)} />
                    ))}
                  </Bar>
                  <Bar dataKey="p95" name="p95" radius={[3, 3, 0, 0]} fillOpacity={0.45}>
                    {models.map((m) => (
                      <Cell key={m.label} fill={color(m.label)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          }
          table={
            <SimpleTable
              head={["Model", "p50 (ms)", "p95 (ms)"]}
              rows={models.map((m) => [m.label, String(m.latency_p50_ms ?? "—"), String(m.latency_p95_ms ?? "—")])}
            />
          }
        />
        <ChartCard
          title="Output length"
          subtitle="Average output tokens per answer"
          chart={
            <div className="h-64" role="img" aria-label="Bar chart of average output tokens per model">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={models.map((m) => ({ name: m.label, tokens: m.avg_output_tokens }))} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="name" tick={axisTick} />
                  <YAxis tick={axisTick} />
                  <RTooltip contentStyle={tooltipStyle} formatter={(v: number) => Math.round(v)} />
                  <Bar dataKey="tokens" name="Avg output tokens" radius={[3, 3, 0, 0]}>
                    {models.map((m) => (
                      <Cell key={m.label} fill={color(m.label)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          }
          table={
            <SimpleTable
              head={["Model", "Avg output tokens", "Total output tokens"]}
              rows={models.map((m) => [m.label, m.avg_output_tokens == null ? "—" : String(Math.round(m.avg_output_tokens)), String(m.output_tokens)])}
            />
          }
        />
      </div>

      <CaseDrilldown run={run} models={models} color={color} />
    </div>
  );
}

function SimpleTable({ head, rows }: { head: string[]; rows: string[][] }) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          {head.map((h, i) => (
            <th key={h} className={cn("py-2 pr-3 font-medium", i > 0 && "text-right")}>
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r[0]} className="border-b last:border-0">
            {r.map((c, i) => (
              <td key={i} className={cn("tabular py-2 pr-3", i > 0 && "text-right")}>
                {c}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function QualityTable({ rows, models }: { rows: Record<string, string | number | null>[]; models: ModelSummary[] }) {
  return (
    <SimpleTable
      head={["Metric", ...models.map((m) => m.label)]}
      rows={rows.map((r) => [String(r.name), ...models.map((m) => fmtScore(r[m.label] as number | null))])}
    />
  );
}

function CaseDrilldown({ run, models, color }: { run: Run; models: ModelSummary[]; color: (l: string) => string }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [disagree, setDisagree] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => (setDebounced(q), setPage(1)), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [disagree]);
  const data = useCaseComparison(run.id, `${run.status}:${run.completed_tests}`, {
    page,
    page_size: 10,
    q: debounced || undefined,
    disagree_only: disagree || undefined,
  });
  const labels = data.data?.labels ?? models.map((m) => m.label);

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b p-3">
        <h3 className="font-semibold">Per-case comparison</h3>
        <div className="relative ml-auto w-64">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search questions" aria-label="Search cases" className="pl-8" />
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox checked={disagree} onCheckedChange={(v) => setDisagree(v === true)} /> Only where models disagree (pass vs fail)
        </label>
      </div>
      {data.isLoading && <TableSkeleton rows={4} cols={3} />}
      {data.isError && (
        <div className="p-4">
          <ErrorState error={data.error} onRetry={() => data.refetch()} />
        </div>
      )}
      {data.data?.items.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">No cases match.</p>}
      <div className="divide-y">
        {data.data?.items.map((row) => (
          <div key={row.case.id} className="space-y-3 p-4">
            <div>
              <p className="font-medium">{row.case.input}</p>
              {row.case.expected_output && <p className="text-sm text-muted-foreground">Expected: {row.case.expected_output}</p>}
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(1, labels.length)}, minmax(0, 1fr))` }}>
              {labels.map((l) => {
                const o = row.outputs[l];
                return (
                  <div key={l} className="space-y-2 rounded-md border p-3" style={{ borderTopColor: color(l), borderTopWidth: 3 }}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold">{l}</span>
                      {o && <StatusChip status={o.status} />}
                    </div>
                    {!o ? (
                      <p className="text-sm text-muted-foreground">No result</p>
                    ) : (
                      <>
                        <p className="whitespace-pre-wrap text-sm">{o.model_output ?? <span className="text-muted-foreground">{o.error_message ?? "Pending…"}</span>}</p>
                        <div className="flex flex-wrap gap-x-3 gap-y-1 border-t pt-2">
                          {o.aggregates.map((a) => (
                            <span key={`${a.metric_name}${a.engine}`} className="flex items-center gap-1 text-xs">
                              <span className="text-muted-foreground">
                                {a.metric_name.replace(/_/g, " ")} <span className="font-mono text-[10px]">({a.engine})</span>
                              </span>
                              <Score compact score={a.mean} threshold={a.threshold} higherIsBetter={a.higher_is_better} status={a.status} engine={a.engine} engineVersion={a.engine_version} reason={a.reason} />
                            </span>
                          ))}
                        </div>
                        <p className="tabular text-[11px] text-muted-foreground">
                          {o.latency_ms != null ? `${o.latency_ms} ms` : ""} {o.output_tokens != null ? `· ${o.output_tokens} tokens` : ""}{" "}
                          {o.cost_usd != null ? `· ${fmtUsd(o.cost_usd)}` : ""}
                        </p>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {data.data && data.data.total > 10 && <Pagination page={page} pageSize={10} total={data.data.total} onPage={setPage} />}
    </Card>
  );
}
