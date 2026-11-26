import { Columns3, PlayCircle, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { RunsTable } from "@/components/runs-table";
import { EmptyState, ErrorState, Pagination, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/overlay";
import { Card, Checkbox, Input, NativeSelect } from "@/components/ui/primitives";
import { useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";

const COLUMNS = [
  { key: "type", label: "Type" },
  { key: "pass", label: "Pass rate" },
  { key: "cost", label: "Cost" },
  { key: "date", label: "Date" },
];

export function RunsPage() {
  const pid = Number(useParams().projectId);
  const [sp, setSp] = useSearchParams();
  const page = Number(sp.get("page") ?? 1);
  const status = sp.get("status") ?? "";
  const sort = sp.get("sort") ?? "-created_at";
  const [q, setQ] = useState(sp.get("q") ?? "");
  const [hidden, setHidden] = useState<string[]>([]);

  const update = (patch: Record<string, string>) => {
    const next = new URLSearchParams(sp);
    Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
    if (!("page" in patch)) next.delete("page");
    setSp(next, { replace: true });
  };

  useEffect(() => {
    const t = setTimeout(() => {
      if ((sp.get("q") ?? "") !== q) update({ q });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const runs = useRuns(pid, { page, page_size: 20, q: sp.get("q") ?? undefined, status: status || undefined, sort });
  const filtered = !!(sp.get("q") || status);

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Runs" }]}
        title="Test Runs"
        description="Every evaluation run in this project."
        actions={
          <Button asChild>
            <Link to={`/p/${pid}/studio/llm`}>New run</Link>
          </Button>
        }
      />
      <div className="space-y-4 p-6">
        <div className="flex items-center gap-3">
          <div className="relative w-72">
            <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search run names"
              aria-label="Search run names"
              className="pl-8"
            />
          </div>
          <div className="w-44">
            <NativeSelect value={status} onChange={(e) => update({ status: e.target.value })} aria-label="Filter by status">
              <option value="">All statuses</option>
              <option value="queued,running">Active</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
              <option value="failed">Failed</option>
            </NativeSelect>
          </div>
          <div className="ml-auto">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Columns3 /> Columns
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Visible columns</DropdownMenuLabel>
                {COLUMNS.map((c) => (
                  <DropdownMenuItem
                    key={c.key}
                    onSelect={(e) => {
                      e.preventDefault();
                      setHidden((h) => (h.includes(c.key) ? h.filter((x) => x !== c.key) : [...h, c.key]));
                    }}
                  >
                    <Checkbox checked={!hidden.includes(c.key)} tabIndex={-1} aria-hidden /> {c.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <Card className="overflow-hidden">
          {runs.isLoading && <TableSkeleton rows={6} cols={6} />}
          {runs.isError && (
            <div className="p-4">
              <ErrorState error={runs.error} onRetry={() => runs.refetch()} />
            </div>
          )}
          {runs.data && runs.data.items.length === 0 && (
            <div className="p-5">
              <EmptyState
                icon={PlayCircle}
                title={filtered ? "No runs match these filters" : "No runs yet"}
                description={filtered ? "Clear the search or status filter." : "Start with an LLM response test."}
                action={
                  filtered ? (
                    <Button variant="outline" onClick={() => (setQ(""), setSp({}, { replace: true }))}>
                      Clear filters
                    </Button>
                  ) : (
                    <Button asChild>
                      <Link to={`/p/${pid}/studio/llm`}>New LLM test</Link>
                    </Button>
                  )
                }
              />
            </div>
          )}
          {!!runs.data?.items.length && (
            <>
              <RunsTable
                runs={runs.data.items}
                projectId={pid}
                sort={sort}
                onSort={(s) => update({ sort: s })}
                hidden={hidden}
              />
              <Pagination
                page={page}
                pageSize={runs.data.page_size}
                total={runs.data.total}
                onPage={(p) => update({ page: String(p) })}
              />
            </>
          )}
        </Card>
      </div>
    </>
  );
}
