import { LineChart as LineIcon, Table2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EmptyState } from "@/components/states";
import { EngineBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Checkbox, NativeSelect } from "@/components/ui/primitives";
import { useMetricsCatalog } from "@/hooks/queries";
import { metricLabel } from "@/lib/metrics";
import { fmtDate } from "@/lib/utils";
import type { TrendPoint } from "@/types";

/** Validated categorical slots (index.css --chart-1..5), assigned in fixed order by metric, never by rank. */
const SERIES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];
const MAX_SERIES = SERIES.length;

type Row = { run_id: number; run_name: string; created_at: string; [metric: string]: number | string | null };

/**
 * Quality trend: one line per metric (for one engine - Ragas and DeepEval are never mixed), one point per
 * completed run, the metric's threshold drawn as a dashed reference line. Chart or table view.
 */
export function TrendChart({ points, pid, height = 260 }: { points: TrendPoint[]; pid: number; height?: number }) {
  const catalog = useMetricsCatalog();
  const navigate = useNavigate();
  const engines = useMemo(() => [...new Set(points.map((p) => p.engine))].sort(), [points]);
  const [engine, setEngine] = useState<string>("");
  const eng = engine && engines.includes(engine) ? engine : (engines[0] ?? "");
  const allMetrics = useMemo(() => [...new Set(points.filter((p) => p.engine === eng).map((p) => p.metric))], [points, eng]);
  // colour follows the metric (its position in the full list), so toggling metrics never repaints the others
  const colorOf = (m: string) => SERIES[allMetrics.indexOf(m) % SERIES.length];
  const [picked, setPicked] = useState<string[] | null>(null);
  useEffect(() => setPicked(null), [eng]);
  const metrics = (picked ?? allMetrics.slice(0, MAX_SERIES)).filter((m) => allMetrics.includes(m));
  const [view, setView] = useState<"chart" | "table">("chart");

  const rows = useMemo(() => {
    const byRun = new Map<number, Row>();
    for (const p of points) {
      if (p.engine !== eng) continue;
      const r = byRun.get(p.run_id) ?? { run_id: p.run_id, run_name: p.run_name, created_at: p.created_at };
      r[p.metric] = p.mean;
      byRun.set(p.run_id, r);
    }
    return [...byRun.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.run_id - b.run_id);
  }, [points, eng]);
  const thresholds = useMemo(() => {
    const t: Record<string, number | null> = {};
    for (const p of points) if (p.engine === eng && t[p.metric] == null) t[p.metric] = p.threshold;
    return t;
  }, [points, eng]);

  if (!points.length)
    return <EmptyState icon={LineIcon} title="No trend yet" description="Complete two or more runs (not in no-persistence mode) to see quality over time." />;
  const label = (m: string) => metricLabel(catalog.data, m);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <NativeSelect aria-label="Engine" className="w-36" value={eng} onChange={(e) => setEngine(e.target.value)}>
          {engines.map((e) => (
            <option key={e} value={e}>
              {e === "ragas" ? "Ragas" : e === "deepeval" ? "DeepEval" : e}
            </option>
          ))}
        </NativeSelect>
        {allMetrics.map((m) => (
          <label key={m} className="flex items-center gap-1.5 text-sm">
            <Checkbox
              checked={metrics.includes(m)}
              disabled={!metrics.includes(m) && metrics.length >= MAX_SERIES}
              onCheckedChange={() => setPicked(metrics.includes(m) ? metrics.filter((x) => x !== m) : [...metrics, m])}
            />
            <span className="inline-block size-2.5 rounded-full" style={{ background: colorOf(m) }} aria-hidden />
            {label(m)}
          </label>
        ))}
        <div className="ml-auto flex gap-1">
          <Button size="sm" variant={view === "chart" ? "secondary" : "ghost"} onClick={() => setView("chart")} aria-pressed={view === "chart"}>
            <LineIcon /> Chart
          </Button>
          <Button size="sm" variant={view === "table" ? "secondary" : "ghost"} onClick={() => setView("table")} aria-pressed={view === "table"}>
            <Table2 /> Table
          </Button>
        </div>
      </div>
      {view === "chart" ? (
        <div style={{ height }} role="img" aria-label={`Quality trend for ${metrics.map(label).join(", ")} (${eng}) across ${rows.length} runs`}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={rows}
              margin={{ top: 8, right: 16, bottom: 4, left: -8 }}
              onClick={(s) => {
                const id = (s?.activePayload?.[0]?.payload as Row | undefined)?.run_id;
                if (id) navigate(`/p/${pid}/runs/${id}`);
              }}
            >
              <CartesianGrid stroke="var(--border)" strokeDasharray="0" vertical={false} />
              <XAxis
                dataKey="run_id"
                tickFormatter={(id) => `#${id}`}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis domain={[0, 1]} ticks={[0, 0.25, 0.5, 0.75, 1]} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
              <Tooltip
                cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }}
                contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12, color: "var(--foreground)" }}
                labelFormatter={(id, payload) => {
                  const r = payload?.[0]?.payload as Row | undefined;
                  return r ? `#${id} · ${r.run_name} · ${fmtDate(r.created_at)}` : `#${id}`;
                }}
                formatter={(v: number, name: string) => [v == null ? "—" : v.toFixed(3), `${label(name)} (threshold ${thresholds[name] ?? "—"})`]}
              />
              {metrics.length > 1 && <Legend formatter={(m: string) => <span style={{ color: "var(--foreground)" }}>{label(m)}</span>} wrapperStyle={{ fontSize: 12 }} />}
              {metrics.map((m) =>
                thresholds[m] != null ? (
                  <ReferenceLine key={`t-${m}`} y={thresholds[m]!} stroke={colorOf(m)} strokeDasharray="4 4" strokeOpacity={0.6} ifOverflow="extendDomain" />
                ) : null,
              )}
              {metrics.map((m) => (
                <Line
                  key={m}
                  dataKey={m}
                  name={m}
                  stroke={colorOf(m)}
                  strokeWidth={2}
                  dot={{ r: 4, strokeWidth: 2, stroke: "var(--card)", fill: colorOf(m) }}
                  activeDot={{ r: 6, strokeWidth: 2, stroke: "var(--card)" }}
                  connectNulls
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-2 font-medium">Run</th>
                <th className="py-2 font-medium">Date</th>
                {metrics.map((m) => (
                  <th key={m} className="py-2 text-right font-medium">
                    {label(m)} <span className="font-normal">(≥ {thresholds[m] ?? "—"})</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.run_id} className="cursor-pointer border-b last:border-0 hover:bg-muted/40" onClick={() => navigate(`/p/${pid}/runs/${r.run_id}`)}>
                  <td className="py-1.5">
                    #{r.run_id} {r.run_name}
                  </td>
                  <td className="py-1.5 text-muted-foreground">{fmtDate(r.created_at)}</td>
                  {metrics.map((m) => (
                    <td key={m} className="py-1.5 text-right tabular-nums">
                      {typeof r[m] === "number" ? (r[m] as number).toFixed(3) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <EngineBadge engine={eng} /> Mean score per run over all valid repeats; dashed lines are each metric's threshold. Click a point to open the run.
      </p>
    </div>
  );
}
