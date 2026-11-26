import { Boxes, Play, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";

import { CasesTable } from "@/components/cases-table";
import { RunDialog } from "@/components/run-dialog";
import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, Tooltip } from "@/components/ui/overlay";
import { Badge, Card, Checkbox, Field, Input, NativeSelect, Textarea } from "@/components/ui/primitives";
import { useCases, useDatasets, useProject, useSuiteMutations, useSuites, useVersions } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { fmtDate } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { Suite } from "@/types";

export function NewSuiteDialog({
  pid,
  open,
  onOpenChange,
  initialDatasetId,
  initialVersionId,
}: {
  pid: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initialDatasetId?: number;
  initialVersionId?: number;
}) {
  const datasets = useDatasets(pid);
  const m = useSuiteMutations(pid);
  const [datasetId, setDatasetId] = useState(initialDatasetId ?? 0);
  const versions = useVersions(datasetId);
  const [versionId, setVersionId] = useState(initialVersionId ?? 0);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [selection, setSelection] = useState<"all" | "tags" | "manual">("all");
  const [tags, setTags] = useState<string[]>([]);
  const [caseIds, setCaseIds] = useState<number[]>([]);
  const tagSource = useCases(versionId, { page: 1, page_size: 1 });

  useEffect(() => {
    if (!datasetId && datasets.data?.length) setDatasetId(datasets.data[0].id);
  }, [datasets.data, datasetId]);
  useEffect(() => {
    const ds = datasets.data?.find((d) => d.id === datasetId);
    if (ds && (!versionId || !versions.data?.some((v) => v.id === versionId))) setVersionId(ds.current_version?.id ?? 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId, datasets.data, versions.data]);
  useEffect(() => (setTags([]), setCaseIds([])), [versionId]);

  const valid = name.trim() && versionId && (selection !== "tags" || tags.length) && (selection !== "manual" || caseIds.length);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent side="right" title="New test suite" description="A reusable selection of cases from one pinned dataset version.">
        <div className="space-y-4">
          <Field label="Name" htmlFor="s-name" error={name.trim() ? undefined : undefined}>
            <Input id="s-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Refund policy regression" />
          </Field>
          <Field label="Description (optional)" htmlFor="s-desc">
            <Textarea id="s-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Dataset" htmlFor="s-ds">
              <NativeSelect id="s-ds" value={datasetId} onChange={(e) => (setDatasetId(Number(e.target.value)), setVersionId(0))}>
                {datasets.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Version" htmlFor="s-v" hint="The suite stays pinned to this version.">
              <NativeSelect id="s-v" value={versionId} onChange={(e) => setVersionId(Number(e.target.value))}>
                {versions.data?.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} · {v.row_count} cases
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Cases</legend>
            <div role="radiogroup" aria-label="Case selection" className="flex w-fit rounded-md border bg-card p-0.5">
              {(
                [
                  ["all", "All cases"],
                  ["tags", "By tag"],
                  ["manual", "Pick cases"],
                ] as const
              ).map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  aria-checked={selection === v}
                  onClick={() => setSelection(v)}
                  className={`h-7 cursor-pointer rounded px-3 text-sm ${selection === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            {selection === "tags" && (
              <div className="flex flex-wrap gap-3 pt-1">
                {tagSource.data?.tags.length ? (
                  tagSource.data.tags.map((t) => (
                    <label key={t} className="flex items-center gap-2 text-sm">
                      <Checkbox checked={tags.includes(t)} onCheckedChange={(v) => setTags(v === true ? [...tags, t] : tags.filter((x) => x !== t))} />
                      {t}
                    </label>
                  ))
                ) : (
                  <p className="text-sm text-muted-foreground">This version has no tags.</p>
                )}
              </div>
            )}
            {selection === "manual" && versionId > 0 && (
              <div className="overflow-hidden rounded-lg border">
                <CasesTable versionId={versionId} selectable selected={caseIds} onSelectedChange={setCaseIds} pageSize={15} />
              </div>
            )}
          </fieldset>
          <div className="flex justify-end gap-2 border-t pt-4">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              disabled={!valid}
              loading={m.create.isPending}
              onClick={async () => {
                try {
                  const s = await m.create.mutateAsync({
                    project_id: pid, name: name.trim(), description, dataset_version_id: versionId, selection,
                    tags: selection === "tags" ? tags : [], case_ids: selection === "manual" ? caseIds : [],
                  });
                  toast.success(`Suite "${s.name}" created with ${s.case_count} case(s)`);
                  onOpenChange(false);
                  setName("");
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Create suite
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function SuitesPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const suites = useSuites(pid);
  const datasets = useDatasets(pid);
  const m = useSuiteMutations(pid);
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState<Suite | null>(null);
  const versions = useVersions(running?.dataset_id ?? 0);
  const runVersion = versions.data?.find((v) => v.id === running?.dataset_version_id);
  const canEdit = project.data?.role !== "viewer";
  const noData = datasets.data?.length === 0;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Management" }, { label: "Test Suites" }]}
        title="Test Suites"
        description="Reusable collections of cases pinned to a dataset version. Run them repeatedly to compare models or releases."
        actions={
          canEdit && (
            <Button disabled={noData} onClick={() => setOpen(true)}>
              <Plus /> New suite
            </Button>
          )
        }
      />
      <div className="p-6">
        <Card className="overflow-hidden">
          {suites.isLoading && <TableSkeleton rows={4} cols={5} />}
          {suites.isError && (
            <div className="p-4">
              <ErrorState error={suites.error} onRetry={() => suites.refetch()} />
            </div>
          )}
          {suites.data?.length === 0 && (
            <div className="p-5">
              <EmptyState
                icon={Boxes}
                title="No suites yet"
                description={noData ? <>Upload a dataset first in <Link className="text-primary underline" to={`/p/${pid}/datasets`}>Datasets</Link>.</> : "Create a suite from a dataset version: all cases, by tag, or hand-picked."}
                action={!noData && canEdit && <Button onClick={() => setOpen(true)}><Plus /> New suite</Button>}
              />
            </div>
          )}
          {!!suites.data?.length && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-sm">
                <thead>
                  <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Name</th>
                    <th className="px-4 py-2 font-medium">Dataset</th>
                    <th className="px-4 py-2 text-right font-medium">Cases</th>
                    <th className="px-4 py-2 font-medium">Selection</th>
                    <th className="px-4 py-2 font-medium">Created</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {suites.data.map((s) => (
                    <tr key={s.id} className="border-b last:border-0">
                      <td className="px-4 py-2.5">
                        <div className="font-medium">{s.name}</div>
                        {s.description && <div className="line-clamp-1 text-xs text-muted-foreground">{s.description}</div>}
                      </td>
                      <td className="px-4 py-2.5">
                        <Link className="hover:underline" to={`/p/${pid}/datasets/${s.dataset_id}?v=${s.dataset_version_id}`}>
                          {s.dataset_name}
                        </Link>{" "}
                        <Badge>v{s.version_number}</Badge>
                        {!s.is_latest_version && (
                          <Tooltip content="A newer dataset version exists. The suite stays pinned so runs remain comparable.">
                            <span tabIndex={0} className="ml-1.5 text-xs text-warn">
                              newer version available
                            </span>
                          </Tooltip>
                        )}
                      </td>
                      <td className="tabular px-4 py-2.5 text-right">{s.case_count}</td>
                      <td className="px-4 py-2.5 text-muted-foreground">
                        {s.selection === "tags" ? `tags: ${s.tags.join(", ")}` : s.selection === "manual" ? "hand-picked" : "all cases"}
                      </td>
                      <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(s.created_at)}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex justify-end gap-1">
                          {canEdit && (
                            <Button size="sm" onClick={() => setRunning(s)}>
                              <Play /> Run
                            </Button>
                          )}
                          {canEdit && (
                            <ConfirmButton
                              variant="ghost"
                              size="icon"
                              title={`Delete suite "${s.name}"?`}
                              confirmLabel="Delete suite"
                              consequences="The suite definition is removed. Past runs keep their results and configuration snapshot."
                              onConfirm={async () => {
                                try {
                                  await m.remove.mutateAsync(s.id);
                                  toast.success("Suite deleted");
                                } catch (e) {
                                  toast.error(errorMessage(e));
                                }
                              }}
                            >
                              <Trash2 aria-label={`Delete ${s.name}`} />
                            </ConfirmButton>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      <NewSuiteDialog pid={pid} open={open} onOpenChange={setOpen} />
      {running && runVersion && (
        <RunDialog
          pid={pid}
          source={{ suite_id: running.id }}
          sourceLabel={`suite "${running.name}"`}
          caseCount={running.case_count}
          fieldsPresent={runVersion.fields_present}
          open={!!running}
          onOpenChange={(o) => !o && setRunning(null)}
        />
      )}
    </>
  );
}
