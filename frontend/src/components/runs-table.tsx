import { ArrowDown, ArrowUp } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { StatusChip } from "@/components/status";
import { Badge, Progress } from "@/components/ui/primitives";
import { cn, fmtDate, fmtUsd } from "@/lib/utils";
import type { Run } from "@/types";

export type SortKey = "created_at" | "run_name" | "status" | "actual_cost_usd";

/** Security scans live under the Security section so the sidebar highlights the right area. */
export const runPath = (pid: number, r: Pick<Run, "id" | "run_type">) =>
  r.run_type === "security"
    ? `/p/${pid}/security/runs/${r.id}`
    : r.run_type === "agent"
      ? `/p/${pid}/studio/agents/runs/${r.id}`
      : `/p/${pid}/runs/${r.id}`;

export function passRate(r: Run) {
  const decided = r.passed_tests + r.failed_tests + r.error_tests;
  return decided ? r.passed_tests / decided : null;
}

export function RunsTable({
  runs,
  projectId,
  sort,
  onSort,
  hidden = [],
}: {
  runs: Run[];
  projectId: number;
  sort?: string;
  onSort?: (s: string) => void;
  hidden?: string[];
}) {
  const navigate = useNavigate();
  const cols: { key: string; label: string; sort?: SortKey; className?: string }[] = [
    { key: "name", label: "Name", sort: "run_name" },
    { key: "type", label: "Type" },
    { key: "status", label: "Status", sort: "status" },
    { key: "pass", label: "Pass rate", className: "text-right" },
    { key: "cost", label: "Cost", sort: "actual_cost_usd", className: "text-right" },
    { key: "date", label: "Date", sort: "created_at" },
  ];
  const visible = cols.filter((c) => !hidden.includes(c.key));
  const dir = sort?.startsWith("-") ? "desc" : "asc";
  const sortKey = sort?.replace("-", "");

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead>
          <tr className="border-y bg-muted/40 text-left text-xs text-muted-foreground">
            {visible.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={cn("px-4 py-2 font-medium", c.className)}
                aria-sort={sortKey === c.sort ? (dir === "asc" ? "ascending" : "descending") : undefined}
              >
                {c.sort && onSort ? (
                  <button
                    type="button"
                    className="inline-flex cursor-pointer items-center gap-1 hover:text-foreground"
                    onClick={() => onSort(sortKey === c.sort && dir === "desc" ? c.sort! : `-${c.sort}`)}
                  >
                    {c.label}
                    {sortKey === c.sort &&
                      (dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                ) : (
                  c.label
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const rate = passRate(r);
            const active = r.status === "queued" || r.status === "running";
            const go = () => navigate(runPath(projectId, r));
            return (
              <tr
                key={r.id}
                tabIndex={0}
                onClick={go}
                onKeyDown={(e) => e.key === "Enter" && go()}
                className="cursor-pointer border-b last:border-0 hover:bg-accent/40 focus-visible:bg-accent/40"
              >
                {visible.map((c) => (
                  <td key={c.key} className={cn("px-4 py-2.5", c.className)}>
                    {c.key === "name" && <span className="font-medium">{r.run_name}</span>}
                    {c.key === "type" && <Badge variant="secondary">{r.run_type.toUpperCase()}</Badge>}
                    {c.key === "status" &&
                      (active ? (
                        <div className="flex w-36 items-center gap-2">
                          <StatusChip status={r.status} />
                          <Progress value={(r.completed_tests / Math.max(1, r.total_tests)) * 100} label="Run progress" />
                        </div>
                      ) : (
                        <StatusChip
                          status={r.status}
                          label={r.status_reason === "budget_cap_reached" ? "Budget cap" : undefined}
                        />
                      ))}
                    {c.key === "pass" && (
                      <span className="tabular">{rate == null ? "—" : `${Math.round(rate * 100)}%`}</span>
                    )}
                    {c.key === "cost" && <span className="tabular">{fmtUsd(r.actual_cost_usd)}</span>}
                    {c.key === "date" && <span className="text-muted-foreground">{fmtDate(r.created_at)}</span>}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
