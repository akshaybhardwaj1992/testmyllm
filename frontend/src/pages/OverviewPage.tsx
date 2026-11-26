import { AlertTriangle, Check, KeyRound, MessageSquareText, PlayCircle, ShieldAlert, Sparkles, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { RunsTable } from "@/components/runs-table";
import { EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { EngineBadge } from "@/components/status";
import { TrendChart } from "@/components/trend-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/primitives";
import { useOverview, useTrends } from "@/hooks/insights";
import { useJudges, useMetricsCatalog, useProviders, useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { metricLabel } from "@/lib/metrics";
import { cn, fmtDate, fmtUsd } from "@/lib/utils";
import type { Overview } from "@/types";

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: React.ReactNode; tone?: "pass" | "fail" }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className={cn("tabular mt-1 font-display text-2xl font-semibold", tone === "fail" && "text-fail", tone === "pass" && "text-pass")}>{value}</div>
      {hint && <div className="mt-0.5 truncate text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

function GateBanner({ o, pid }: { o: Overview; pid: number }) {
  const g = o.latest_gate;
  if (!g) return null;
  const ok = g.status === "passed";
  return (
    <div role="status" className={cn("flex items-center gap-3 rounded-lg border px-4 py-3 text-sm", ok ? "border-pass/40 bg-pass-bg" : "border-fail/40 bg-fail-bg")}>
      {ok ? <Check className="size-5 text-pass" /> : <X className="size-5 text-fail" />}
      <span>
        <span className={cn("font-semibold", ok ? "text-pass" : "text-fail")}>
          Latest {g.kind === "security" ? "security" : "quality"} gate “{g.gate_name}” {ok ? "passed" : "failed"}
        </span>{" "}
        on <Link className="font-medium underline-offset-2 hover:underline" to={`/p/${pid}/runs/${g.run_id}`}>{g.run_name}</Link>
        <span className="text-muted-foreground"> · {fmtDate(g.created_at)}</span>
      </span>
      <Button asChild size="sm" variant="outline" className="ml-auto">
        <Link to={g.kind === "security" ? `/p/${pid}/runs/${g.run_id}` : `/p/${pid}/regression`}>Details</Link>
      </Button>
    </div>
  );
}

export function OverviewPage() {
  const pid = Number(useParams().projectId);
  const providers = useProviders(pid);
  const judges = useJudges(pid);
  const overview = useOverview(pid);
  const trends = useTrends(pid, { max_runs: 30 });
  const runs = useRuns(pid, { page: 1, page_size: 8 });
  const catalog = useMetricsCatalog();
  const needsSetup = providers.data?.length === 0 || judges.data?.length === 0;
  const o = overview.data;
  const items = runs.data?.items ?? [];

  return (
    <>
      <PageHeader
        title="Overview"
        description="Project health at a glance: latest results, gates, quality trend and the cases that fail most."
        actions={
          <Button asChild>
            <Link to={`/p/${pid}/studio/llm`}>
              <MessageSquareText /> New LLM test
            </Link>
          </Button>
        }
      />
      <div className="space-y-6 p-6">
        {needsSetup && (
          <Card className="border-primary/40">
            <CardHeader>
              <CardTitle>Finish setting up</CardTitle>
            </CardHeader>
            <CardContent className="flex gap-3">
              <Button asChild variant={providers.data?.length ? "outline" : "default"}>
                <Link to={`/p/${pid}/settings/providers`}>
                  <KeyRound /> {providers.data?.length ? "Provider added ✓" : "1. Add OpenRouter key"}
                </Link>
              </Button>
              <Button asChild variant={judges.data?.length || !providers.data?.length ? "outline" : "default"}>
                <Link to={`/p/${pid}/settings/judges`}>
                  <Sparkles /> {judges.data?.length ? "Judge configured ✓" : "2. Configure a judge model"}
                </Link>
              </Button>
            </CardContent>
          </Card>
        )}
        {overview.isError && <ErrorState error={overview.error} onRetry={() => overview.refetch()} />}
        {overview.isLoading && <TableSkeleton rows={2} cols={4} />}
        {o && (
          <>
            <GateBanner o={o} pid={pid} />
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Kpi
                label="Latest run pass rate"
                value={pct(o.latest_run?.pass_rate)}
                hint={o.latest_run ? <Link className="hover:underline" to={`/p/${pid}/runs/${o.latest_run.id}`}>{o.latest_run.run_name}</Link> : "No completed run yet"}
              />
              <Kpi label="Open gate failures" value={String(o.open_gate_failures)} tone={o.open_gate_failures ? "fail" : undefined} hint="Latest evaluation per run and gate, last 30 days" />
              <Kpi label="Spend, last 30 days" value={fmtUsd(o.spend_30d, "$0")} hint={`${o.runs_30d} run(s) · model + judge, measured`} />
              <Kpi
                label="Security: attack success rate"
                value={o.security ? pct(o.security.asr) : "—"}
                tone={o.security ? (o.security.critical || o.security.asr ? "fail" : "pass") : undefined}
                hint={
                  o.security ? (
                    <Link className="hover:underline" to={`/p/${pid}/runs/${o.security.run_id}`}>
                      {o.security.critical} critical · {o.security.breached}/{o.security.attacks} breached
                    </Link>
                  ) : (
                    <Link className="hover:underline" to={`/p/${pid}/security`}>No scan yet</Link>
                  )
                }
              />
            </div>

            {!!o.latest_run?.metrics.length && (
              <Card>
                <CardHeader>
                  <CardTitle>Latest run, per metric</CardTitle>
                  <CardDescription>Mean score and pass rate for each metric and engine in “{o.latest_run.run_name}”.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  {o.latest_run.metrics.map((m) => {
                    const ok = m.threshold == null || (m.higher_is_better ? m.mean >= m.threshold : m.mean <= m.threshold);
                    return (
                      <div key={`${m.metric}${m.engine}`} className="rounded-md border p-3">
                        <div className="flex items-center justify-between gap-2 text-sm">
                          <span className="truncate font-medium">{metricLabel(catalog.data, m.metric)}</span>
                          <EngineBadge engine={m.engine} />
                        </div>
                        <div className="mt-1 flex items-baseline gap-2">
                          <span className={cn("font-display text-xl font-semibold tabular-nums", ok ? "text-pass" : "text-fail")}>{m.mean.toFixed(2)}</span>
                          <span className="text-xs text-muted-foreground">
                            {ok ? "≥" : "<"} threshold {m.threshold ?? "—"} · {pct(m.pass_rate)} pass
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            )}
          </>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Quality trend</CardTitle>
            <CardDescription>Last 30 completed runs (security scans and no-persistence runs excluded).</CardDescription>
          </CardHeader>
          <CardContent>
            {trends.isLoading ? <TableSkeleton rows={3} /> : trends.isError ? <ErrorState error={trends.error} onRetry={() => trends.refetch()} /> : <TrendChart points={trends.data?.points ?? []} pid={pid} />}
          </CardContent>
        </Card>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Card className="min-w-0">
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle>Recent runs</CardTitle>
              <Button asChild variant="ghost" size="sm">
                <Link to={`/p/${pid}/history`}>Full history</Link>
              </Button>
            </CardHeader>
            {runs.isLoading && <TableSkeleton />}
            {runs.data && items.length === 0 && (
              <div className="p-5 pt-0">
                <EmptyState
                  icon={PlayCircle}
                  title="No runs yet"
                  description="Run an LLM response test to see results here."
                  action={
                    <Button asChild>
                      <Link to={`/p/${pid}/studio/llm`}>Open LLM Response Testing</Link>
                    </Button>
                  }
                />
              </div>
            )}
            {items.length > 0 && (
              <div className="overflow-x-auto">
                <RunsTable runs={items} projectId={pid} />
              </div>
            )}
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="size-4 text-warn" /> Top failing test cases
              </CardTitle>
              <CardDescription>Cases that failed most often across the last 20 dataset runs.</CardDescription>
            </CardHeader>
            <CardContent>
              {!o?.top_failing.length ? (
                <p className="text-sm text-muted-foreground">No repeated failures. Failing cases from dataset runs appear here.</p>
              ) : (
                <ol className="space-y-2">
                  {o.top_failing.map((f) => (
                    <li key={f.case_key} className="flex items-start gap-3 text-sm">
                      <span className="mt-0.5 shrink-0 rounded bg-fail-bg px-1.5 text-xs font-semibold tabular-nums text-fail" title={`${f.failures} failures in ${f.appearances} evaluations`}>
                        {f.failures}/{f.appearances}
                      </span>
                      <span className="flex-1">
                        <span className="line-clamp-2">{f.input}</span>
                        {f.dataset && <span className="text-xs text-muted-foreground">{f.dataset}</span>}
                      </span>
                      {f.last_failed_run_id && (
                        <Link className="shrink-0 text-xs text-primary hover:underline" to={`/p/${pid}/runs/${f.last_failed_run_id}`}>
                          #{f.last_failed_run_id}
                        </Link>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>
        {o?.security && o.security.critical > 0 && (
          <p className="flex items-center gap-2 text-sm text-fail">
            <ShieldAlert className="size-4" /> The latest security scan has {o.security.critical} critical finding(s).{" "}
            <Link className="underline" to={`/p/${pid}/runs/${o.security.run_id}`}>Review them</Link>
          </p>
        )}
      </div>
    </>
  );
}
