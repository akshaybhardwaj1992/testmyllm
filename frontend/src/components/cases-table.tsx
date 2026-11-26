import { ListChecks, Search } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, Pagination, TableSkeleton } from "@/components/states";
import { Badge, Checkbox, Input, NativeSelect } from "@/components/ui/primitives";
import { useCases } from "@/hooks/queries";
import { cn } from "@/lib/utils";
import type { CaseFull } from "@/types";

/** Server-paginated case list for one dataset version, with search, tag filter and optional selection. */
export function CasesTable({
  versionId,
  onOpen,
  selectable,
  selected,
  onSelectedChange,
  pageSize = 25,
}: {
  versionId: number;
  onOpen?: (c: CaseFull) => void;
  selectable?: boolean;
  selected?: number[];
  onSelectedChange?: (ids: number[]) => void;
  pageSize?: number;
}) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [tag, setTag] = useState("");
  useEffect(() => {
    const t = setTimeout(() => (setDebounced(q), setPage(1)), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [versionId, tag]);
  const cases = useCases(versionId, { page, page_size: pageSize, q: debounced || undefined, tag: tag || undefined });
  const items = cases.data?.items ?? [];
  const sel = new Set(selected ?? []);
  const toggle = (id: number) => onSelectedChange?.(sel.has(id) ? [...sel].filter((x) => x !== id) : [...sel, id]);
  const allOnPage = items.length > 0 && items.every((c) => sel.has(c.id));

  return (
    <div>
      <div className="flex items-center gap-3 border-b p-3">
        <div className="relative w-72">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search input or reference" aria-label="Search cases" className="pl-8" />
        </div>
        <div className="w-44">
          <NativeSelect value={tag} onChange={(e) => setTag(e.target.value)} aria-label="Filter by tag">
            <option value="">All tags</option>
            {cases.data?.tags.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </NativeSelect>
        </div>
        {selectable && <span className="ml-auto text-sm text-muted-foreground">{sel.size} selected</span>}
      </div>
      {cases.isLoading && <TableSkeleton rows={6} cols={5} />}
      {cases.isError && (
        <div className="p-4">
          <ErrorState error={cases.error} onRetry={() => cases.refetch()} />
        </div>
      )}
      {cases.data && items.length === 0 && (
        <div className="p-5">
          <EmptyState icon={ListChecks} title="No cases match" description="Clear the search or tag filter." />
        </div>
      )}
      {items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-sm">
            <thead>
              <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                {selectable && (
                  <th className="w-10 px-3 py-2">
                    <Checkbox
                      aria-label="Select all on this page"
                      checked={allOnPage}
                      onCheckedChange={(v) =>
                        onSelectedChange?.(v === true ? [...new Set([...sel, ...items.map((c) => c.id)])] : [...sel].filter((id) => !items.some((c) => c.id === id)))
                      }
                    />
                  </th>
                )}
                <th className="w-12 px-3 py-2 font-medium">#</th>
                <th className="px-3 py-2 font-medium">Input</th>
                <th className="px-3 py-2 font-medium">Reference</th>
                <th className="px-3 py-2 font-medium">Recorded response</th>
                <th className="px-3 py-2 font-medium">Context</th>
                <th className="px-3 py-2 font-medium">Tags</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => {
                const act = () => (selectable ? toggle(c.id) : onOpen?.(c));
                return (
                  <tr
                    key={c.id}
                    tabIndex={0}
                    onClick={act}
                    onKeyDown={(e) => e.key === "Enter" && act()}
                    className={cn("cursor-pointer border-b align-top last:border-0 hover:bg-accent/40", sel.has(c.id) && "bg-accent/30")}
                  >
                    {selectable && (
                      <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
                        <Checkbox aria-label={`Select case ${c.position + 1}`} checked={sel.has(c.id)} onCheckedChange={() => toggle(c.id)} />
                      </td>
                    )}
                    <td className="tabular px-3 py-2 text-muted-foreground">{c.position + 1}</td>
                    <td className="max-w-80 px-3 py-2">
                      <span className="line-clamp-2">{c.input}</span>
                    </td>
                    <td className="max-w-56 px-3 py-2 text-muted-foreground">
                      <span className="line-clamp-2">{c.expected_output ?? "—"}</span>
                    </td>
                    <td className="max-w-56 px-3 py-2 text-muted-foreground">
                      <span className="line-clamp-2">{c.recorded_output ?? "—"}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 text-muted-foreground">
                      {c.provided_context?.length ? `${c.provided_context.length} chunk(s)` : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <span className="flex flex-wrap gap-1">
                        {c.tags.map((t) => (
                          <Badge key={t} variant="outline">
                            {t}
                          </Badge>
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {cases.data && cases.data.total > pageSize && (
        <Pagination page={page} pageSize={pageSize} total={cases.data.total} onPage={setPage} />
      )}
    </div>
  );
}
