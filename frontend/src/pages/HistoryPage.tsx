import { Download, History, Search } from "lucide-react";
import { useParams, useSearchParams } from "react-router-dom";

import { RunsTable } from "@/components/runs-table";
import { EmptyState, ErrorState, Pagination, TableSkeleton } from "@/components/states";
import { TrendChart } from "@/components/trend-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, NativeSelect } from "@/components/ui/primitives";
import { historyExportUrl, useTrends } from "@/hooks/insights";
import { useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";

const TYPES = [
  ["", "All types"],
  ["llm", "LLM response"],
  ["rag", "RAG"],
  ["benchmark", "Model comparison"],
  ["security", "Security scan"],
] as const;
const STATUSES = [
  ["", "Any status"],
  ["completed", "Completed"],
  ["running,queued", "In progress"],
  ["cancelled", "Cancelled"],
  ["failed", "Failed"],
] as const;

/** Evaluation history (spec §3, §10.2): every run with filters, a quality trend and CSV export. */
export function HistoryPage() {
  const pid = Number(useParams().projectId);
  const [sp, setSp] = useSearchParams();
  const f = {
    run_type: sp.get("type") ?? "",
    status: sp.get("status") ?? "",
    q: sp.get("q") ?? "",
    date_from: sp.get("from") ?? "",
    date_to: sp.get("to") ?? "",
  };
  const page = Number(sp.get("page") ?? 1);
  const sort = sp.get("sort") ?? "-created_at";
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v);
    else next.delete(k);
    if (k !== "page") next.delete("page");
    setSp(next, { replace: true });
  };
  const runs = useRuns(pid, { page, page_size: 25, sort, ...f });
  const days = f.date_from ? Math.max(1, Math.ceil((Date.now() - new Date(f.date_from).getTime()) / 86_400_000)) : 365;
  const trendType = f.run_type && f.run_type !== "security" ? f.run_type : undefined;
  const trends = useTrends(pid, { days, max_runs: 60, run_type: trendType });

  return (
    <>
      <PageHeader
        crumbs={[{ label: "History" }]}
        title="Evaluation history"
        description="Every run in this project, with filters, the quality trend and a CSV export (one row per run, with per-metric means)."
        actions={
          <Button asChild variant="outline">
            <a href={historyExportUrl(pid, f)} download>
              <Download /> Export CSV
            </a>
          </Button>
        }
      />
      <div className="space-y-6 p-6">
        <Card className="p-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Search" htmlFor="h-q">
              <div className="relative">
                <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
                <Input id="h-q" className="pl-8" placeholder="Run name" value={f.q} onChange={(e) => set("q", e.target.value)} />
              </div>
            </Field>
            <Field label="Type" htmlFor="h-type">
              <NativeSelect id="h-type" value={f.run_type} onChange={(e) => set("type", e.target.value)}>
                {TYPES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Status" htmlFor="h-status">
              <NativeSelect id="h-status" value={f.status} onChange={(e) => set("status", e.target.value)}>
                {STATUSES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="From" htmlFor="h-from">
              <Input id="h-from" type="date" value={f.date_from} max={f.date_to || undefined} onChange={(e) => set("from", e.target.value)} />
            </Field>
            <Field label="To" htmlFor="h-to">
              <Input id="h-to" type="date" value={f.date_to} min={f.date_from || undefined} onChange={(e) => set("to", e.target.value)} />
            </Field>
          </div>
        </Card>

        {f.run_type !== "security" && (
          <Card>
            <CardHeader>
              <CardTitle>Quality trend</CardTitle>
              <CardDescription>
                Completed {trendType ? TYPES.find(([v]) => v === trendType)?.[1] : ""} runs {f.date_from ? `since ${f.date_from}` : "in the last year"} (up to 60; no-persistence runs excluded).
              </CardDescription>
            </CardHeader>
            <CardContent>{trends.isLoading ? <TableSkeleton rows={3} /> : <TrendChart points={trends.data?.points ?? []} pid={pid} />}</CardContent>
          </Card>
        )}

        <Card>
          {runs.isLoading && <TableSkeleton rows={6} />}
          {runs.isError && (
            <div className="p-4">
              <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
            </div>
          )}
          {runs.data?.items.length === 0 && (
            <div className="p-5">
              <EmptyState icon={History} title="No runs match" description="Change the filters or date range." />
            </div>
          )}
          {!!runs.data?.items.length && (
            <>
              <RunsTable runs={runs.data.items} projectId={pid} sort={sort} onSort={(s) => set("sort", s)} />
              <div className="border-t p-3">
                <Pagination page={page} pageSize={25} total={runs.data.total} onPage={(p) => set("page", String(p))} />
              </div>
            </>
          )}
        </Card>
      </div>
    </>
  );
}
