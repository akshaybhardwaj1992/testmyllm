import { Boxes, History, Play, Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { CaseEditor } from "@/components/case-editor";
import { CasesTable } from "@/components/cases-table";
import { RunDialog } from "@/components/run-dialog";
import { ConfirmButton, ErrorState, TableSkeleton } from "@/components/states";
import { UploadWizard } from "@/components/upload-wizard";
import { Button } from "@/components/ui/button";
import { Badge, Card, NativeSelect } from "@/components/ui/primitives";
import { useDataset, useDatasetMutations, useProject, useVersions } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { FieldBadges } from "@/pages/DatasetsPage";
import { NewSuiteDialog } from "@/pages/SuitesPage";
import { fmtDate } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import type { CaseFull } from "@/types";

export function DatasetDetailPage() {
  const { projectId, datasetId } = useParams();
  const pid = Number(projectId);
  const id = Number(datasetId);
  const [sp, setSp] = useSearchParams();
  const project = useProject(pid);
  const dataset = useDataset(id);
  const versions = useVersions(id);
  const dsm = useDatasetMutations(pid);
  const navigate = useNavigate();
  const [editing, setEditing] = useState<CaseFull | undefined>();
  const [editorOpen, setEditorOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [suiteOpen, setSuiteOpen] = useState(false);

  const current = dataset.data?.current_version;
  const selectedId = Number(sp.get("v") ?? current?.id ?? 0);
  const selected = versions.data?.find((v) => v.id === selectedId) ?? current ?? undefined;
  const isLatest = !!selected && selected.id === current?.id;
  const canEdit = project.data?.role !== "viewer";

  useEffect(() => {
    if (current && !sp.get("v")) setSp({ v: String(current.id) }, { replace: true });
  }, [current, sp, setSp]);

  if (dataset.isLoading)
    return (
      <div className="p-6">
        <TableSkeleton rows={6} />
      </div>
    );
  if (dataset.isError || !dataset.data)
    return (
      <div className="p-6">
        <ErrorState error={dataset.error} onRetry={() => dataset.refetch()} />
      </div>
    );
  const ds = dataset.data;
  const nextVersion = (current?.version_number ?? 0) + 1;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Datasets", to: `/p/${pid}/datasets` }, { label: ds.name }]}
        title={
          <span className="flex items-center gap-3">
            {ds.name} <Badge>v{selected?.version_number}</Badge>
            {!isLatest && <Badge variant="outline">older version · read-only</Badge>}
          </span>
        }
        description={`${selected?.row_count ?? 0} cases · ${ds.version_count} version(s)`}
        actions={
          <>
            <Button variant="outline" disabled={!selected} onClick={() => setSuiteOpen(true)}>
              <Boxes /> Create suite
            </Button>
            <Button disabled={!selected || !canEdit} onClick={() => setRunOpen(true)}>
              <Play /> Run this version
            </Button>
          </>
        }
      />
      <div className="space-y-4 p-6">
        <Card className="flex flex-wrap items-center gap-4 p-4">
          <div className="flex items-center gap-2">
            <History className="size-4 text-muted-foreground" aria-hidden />
            <label htmlFor="version" className="text-sm font-medium">
              Version
            </label>
            <div className="w-64">
              <NativeSelect id="version" value={selected?.id ?? ""} onChange={(e) => setSp({ v: e.target.value }, { replace: true })}>
                {versions.data?.map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number} · {v.row_count} cases · {v.change_note || v.source_filename || "—"}
                    {v.id === current?.id ? " (latest)" : ""}
                  </option>
                ))}
              </NativeSelect>
            </div>
          </div>
          {selected && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <span>Created {fmtDate(selected.created_at)}</span>
              <FieldBadges fields={selected.fields_present} />
            </div>
          )}
          {canEdit && (
            <div className="ml-auto flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setUploadOpen(true)}>
                <Upload /> Upload new version
              </Button>
              <Button variant="outline" size="sm" disabled={!isLatest} onClick={() => (setEditing(undefined), setEditorOpen(true))}>
                <Plus /> Add case
              </Button>
              {project.data?.role === "owner" && (
                <ConfirmButton
                  variant="ghost"
                  size="sm"
                  title={`Delete dataset "${ds.name}"?`}
                  confirmLabel="Delete dataset"
                  consequences="All versions and their cases are deleted. Datasets used by a run cannot be deleted, so past results stay reproducible."
                  onConfirm={async () => {
                    try {
                      await dsm.remove.mutateAsync(ds.id);
                      toast.success("Dataset deleted");
                      navigate(`/p/${pid}/datasets`);
                    } catch (e) {
                      toast.error(errorMessage(e));
                    }
                  }}
                >
                  <Trash2 /> Delete
                </ConfirmButton>
              )}
            </div>
          )}
        </Card>
        <Card className="overflow-hidden">
          {selected && <CasesTable versionId={selected.id} onOpen={(c) => (setEditing(c), setEditorOpen(true))} />}
        </Card>
      </div>
      {selected && (
        <>
          <CaseEditor
            datasetId={ds.id}
            versionId={selected.id}
            nextVersion={nextVersion}
            caseItem={editing}
            readOnly={!isLatest || !canEdit}
            open={editorOpen}
            onOpenChange={setEditorOpen}
            onSaved={(out) => setSp({ v: String(out.version.id) }, { replace: true })}
          />
          <RunDialog
            pid={pid}
            source={{ dataset_version_id: selected.id }}
            sourceLabel={`${ds.name} v${selected.version_number}`}
            caseCount={selected.row_count}
            fieldsPresent={selected.fields_present}
            open={runOpen}
            onOpenChange={setRunOpen}
          />
          <NewSuiteDialog
            pid={pid}
            open={suiteOpen}
            onOpenChange={setSuiteOpen}
            initialDatasetId={ds.id}
            initialVersionId={selected.id}
          />
        </>
      )}
      <UploadWizard
        pid={pid}
        dataset={ds}
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onImported={(out) => out.version && setSp({ v: String(out.version.id) }, { replace: true })}
      />
    </>
  );
}
