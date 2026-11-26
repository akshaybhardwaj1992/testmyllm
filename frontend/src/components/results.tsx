import { BarChart3, ChevronDown, ChevronRight, Table2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";

import { EngineBadge, Score, StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/overlay";
import { Badge, Card } from "@/components/ui/primitives";
import { useMetricsCatalog } from "@/hooks/queries";
import { cn, fmtScore, fmtUsd } from "@/lib/utils";
import type { MetricAggregate, MetricDef, TestResult } from "@/types";

const ENGINES = ["ragas", "deepeval"] as const;
const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

function useMetricLabels() {
  const catalog = useMetricsCatalog();
  return useMemo(() => {
    const m = new Map<string, MetricDef>();
    catalog.data?.forEach((d) => m.set(d.name, d));
    return m;
  }, [catalog.data]);
}

function Reason({ agg }: { agg: MetricAggregate }) {
  const [open, setOpen] = useState(false);
  if (!agg.reason || agg.status === "not_applicable") return null;
  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="inline-flex cursor-pointer items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        {agg.status === "error" ? "Error details" : "Judge reason"}
      </button>
      {open && <p className="mt-1 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">{agg.reason}</p>}
    </div>
  );
}

function EngineCell({ agg }: { agg?: MetricAggregate }) {
  if (!agg)
    return (
      <Tooltip content="Not selected for this engine, or the engine does not implement this metric.">
        <span tabIndex={0} className="text-xs text-muted-foreground">
          not run
        </span>
      </Tooltip>
    );
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <Score
          score={agg.mean}
          threshold={agg.threshold}
          higherIsBetter={agg.higher_is_better}
          status={agg.status}
          engine={agg.engine}
          engineVersion={agg.engine_version}
          judgeModel={agg.judge_model}
          n={agg.n}
          std={agg.std}
          reason={agg.reason}
        />
        <EngineBadge engine={agg.engine} version={agg.engine_version} />
      </div>
      {agg.n > 1 && agg.min != null && (
        <div className="tabular mt-1 text-[11px] text-muted-foreground">
          min {fmtScore(agg.min)} · max {fmtScore(agg.max)} · n={agg.n}
          {agg.errors ? ` · ${agg.errors} error` : ""}
        </div>
      )}
      <Reason agg={agg} />
    </div>
  );
}

/**
 * One answer's metrics. Shared metrics show Ragas and DeepEval side by side, never merged (spec §2).
 */
export function MetricGrid({ result }: { result: TestResult }) {
  const labels = useMetricLabels();
  const byMetric = new Map<string, Partial<Record<string, MetricAggregate>>>();
  result.aggregates.forEach((a) => {
    const row = byMetric.get(a.metric_name) ?? {};
    row[a.engine] = a;
    byMetric.set(a.metric_name, row);
  });
  const usedEngines = ENGINES.filter((e) => result.aggregates.some((a) => a.engine === e));
  if (byMetric.size === 0) {
    return <p className="px-4 py-6 text-center text-sm text-muted-foreground">Waiting for the first metric…</p>;
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <th scope="col" className="w-48 px-4 py-2 font-medium">
            Metric
          </th>
          {usedEngines.map((e) => (
            <th key={e} scope="col" className="px-4 py-2 font-medium">
              {e === "ragas" ? "Ragas" : "DeepEval"}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[...byMetric.entries()].map(([name, row]) => {
          const def = labels.get(name);
          return (
            <tr key={name} className="border-b align-top last:border-0">
              <th scope="row" className="px-4 py-3 text-left font-normal">
                <div className="font-medium">{def?.label ?? name}</div>
                <div className="text-xs text-muted-foreground">
                  {def?.higher_is_better === false ? "lower is better" : "higher is better"}
                </div>
              </th>
              {usedEngines.map((e) => (
                <td key={e} className="px-4 py-3">
                  <EngineCell agg={row[e]} />
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function AnswerCard({ result, defaultOpen = true }: { result: TestResult; defaultOpen?: boolean }) {
  const [showOutput, setShowOutput] = useState(defaultOpen);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-3 border-b bg-muted/30 px-4 py-3">
        <span className="font-semibold">{result.output_label}</span>
        <Badge variant="outline">{result.output_source === "provider" ? `generated · ${result.source_model}` : "manual"}</Badge>
        {result.source_model && result.output_source === "manual" && (
          <span className="text-xs text-muted-foreground">by {result.source_model}</span>
        )}
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          {result.latency_ms != null && <span className="tabular">{result.latency_ms} ms</span>}
          {result.cost_usd != null && <span className="tabular">{fmtUsd(result.cost_usd)}</span>}
          <StatusChip status={result.status} />
        </span>
      </div>
      {result.error_message && (
        <p role="alert" className="border-b bg-warn-bg px-4 py-2 text-sm text-warn">
          {result.error_message}
        </p>
      )}
      <div className="border-b px-4 py-2">
        <button
          type="button"
          className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          onClick={() => setShowOutput(!showOutput)}
          aria-expanded={showOutput}
        >
          {showOutput ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />} Answer
        </button>
        {showOutput && (
          <p className="mt-1.5 max-h-60 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed">
            {result.model_output ?? <span className="text-muted-foreground">Generating…</span>}
          </p>
        )}
      </div>
      <MetricGrid result={result} />
    </Card>
  );
}

/** Grouped bars per metric/engine, one bar per answer, plus a difference table (spec §10.2 compare mode). */
export function CompareView({ results }: { results: TestResult[] }) {
  const labels = useMetricLabels();
  const [asTable, setAsTable] = useState(false);
  const rows = useMemo(() => {
    const keys = new Map<string, { metric: string; engine: string; higher: boolean; threshold: number | null }>();
    results.forEach((r) =>
      r.aggregates.forEach((a) => {
        if (a.status !== "not_applicable")
          keys.set(`${a.metric_name}|${a.engine}`, {
            metric: a.metric_name,
            engine: a.engine,
            higher: a.higher_is_better,
            threshold: a.threshold ?? null,
          });
      }),
    );
    return [...keys.values()].map((k) => {
      const row: Record<string, string | number | null | boolean> = {
        name: `${labels.get(k.metric)?.label ?? k.metric} (${k.engine})`,
        higher: k.higher,
        threshold: k.threshold,
      };
      results.forEach((r) => {
        const a = r.aggregates.find((x) => x.metric_name === k.metric && x.engine === k.engine);
        row[r.output_label] = a?.mean ?? null;
      });
      return row;
    });
  }, [results, labels]);

  if (results.length < 2 || rows.length === 0) return null;
  const [a, b] = results;
  const anyScore = rows.some((r) => results.some((res) => typeof r[res.output_label] === "number"));
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">
          {a.output_label} vs {b.output_label}
        </h3>
        <Button variant="ghost" size="sm" onClick={() => setAsTable(!asTable)} aria-pressed={asTable}>
          {asTable ? <BarChart3 /> : <Table2 />} {asTable ? "View as chart" : "View as table"}
        </Button>
      </div>
      {!asTable && !anyScore && (
        <p className="rounded-md bg-warn-bg px-3 py-2 text-sm text-warn">
          No scores to chart: every metric errored. Check the error details below (often the provider key).
        </p>
      )}
      {!asTable && anyScore && (
        <div className="h-72" role="img" aria-label="Grouped bar chart of scores per metric and engine">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 8, left: -16 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} interval={0} />
              <YAxis domain={[0, 1]} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
              <RTooltip
                contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
                formatter={(v: number) => (v == null ? "error" : v.toFixed(3))}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {results.map((r, i) => (
                <Bar key={r.id} dataKey={r.output_label} fill={SERIES[i % SERIES.length]} radius={[3, 3, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <div className={cn("overflow-x-auto", !asTable && "mt-3")}>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-4 font-medium">
                Metric (engine)
              </th>
              {results.map((r) => (
                <th key={r.id} scope="col" className="py-2 pr-4 text-right font-medium">
                  {r.output_label}
                </th>
              ))}
              <th scope="col" className="py-2 text-right font-medium">
                Difference ({a.output_label} − {b.output_label})
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const va = row[a.output_label] as number | null;
              const vb = row[b.output_label] as number | null;
              const diff = va != null && vb != null ? va - vb : null;
              const better = diff == null || diff === 0 ? null : (diff > 0) === row.higher ? a.output_label : b.output_label;
              return (
                <tr key={String(row.name)} className="border-b last:border-0">
                  <td className="py-2 pr-4">{String(row.name)}</td>
                  {results.map((r) => (
                    <td key={r.id} className="tabular py-2 pr-4 text-right">
                      {row[r.output_label] == null ? <span className="text-warn">error</span> : fmtScore(row[r.output_label] as number)}
                    </td>
                  ))}
                  <td className="tabular py-2 text-right">
                    {diff == null ? "—" : `${diff > 0 ? "+" : ""}${diff.toFixed(2)}`}
                    {better && <span className="ml-2 text-xs text-muted-foreground">favours {better}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted-foreground">
        Ragas and DeepEval use different judge prompts: compare answers within an engine before comparing engines.
        All scores are 0–1, higher is better (for hallucination, bias and toxicity 1 means none detected).
      </p>
    </Card>
  );
}
