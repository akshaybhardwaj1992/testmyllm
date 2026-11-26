import { Database, Search, Upload } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { UploadWizard } from "@/components/upload-wizard";
import { Button } from "@/components/ui/button";
import { Badge, Card, Input } from "@/components/ui/primitives";
import { useDatasets, useProject } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { fmtDate } from "@/lib/utils";

export const FIELD_LABEL: Record<string, string> = {
  reference: "reference",
  response: "responses",
  context: "context",
  criteria: "criteria",
};

export function FieldBadges({ fields }: { fields: string[] }) {
  if (!fields.length) return <span className="text-xs text-muted-foreground">question only</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {fields.map((f) => (
        <Badge key={f} variant="secondary">
          {FIELD_LABEL[f] ?? f}
        </Badge>
      ))}
    </span>
  );
}

export function DatasetsPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const datasets = useDatasets(pid);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const canEdit = project.data?.role !== "viewer";
  const items = (datasets.data ?? []).filter((d) => d.name.toLowerCase().includes(q.trim().toLowerCase()));

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Management" }, { label: "Datasets" }]}
        title="Datasets"
        description="Versioned collections of test cases. Uploads and edits create new versions; runs pin the version they used."
        actions={
          canEdit && (
            <Button onClick={() => setOpen(true)}>
              <Upload /> Upload dataset
            </Button>
          )
        }
      />
      <div className="space-y-4 p-6">
        <div className="relative w-72">
          <Search className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search datasets" aria-label="Search datasets" className="pl-8" />
        </div>
        <Card className="overflow-hidden">
          {datasets.isLoading && <TableSkeleton rows={4} cols={5} />}
          {datasets.isError && (
            <div className="p-4">
              <ErrorState error={datasets.error} onRetry={() => datasets.refetch()} />
            </div>
          )}
          {datasets.data && items.length === 0 && (
            <div className="p-5">
              <EmptyState
                icon={Database}
                title={q ? "No datasets match" : "No datasets yet"}
                description={q ? "Clear the search." : "Upload a CSV or JSON file of questions, reference answers, recorded responses and context."}
                action={
                  !q && canEdit && (
                    <Button onClick={() => setOpen(true)}>
                      <Upload /> Upload dataset
                    </Button>
                  )
                }
              />
            </div>
          )}
          {items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-sm">
                <thead>
                  <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
                    <th className="px-4 py-2 font-medium">Name</th>
                    <th className="px-4 py-2 font-medium">Version</th>
                    <th className="px-4 py-2 text-right font-medium">Cases</th>
                    <th className="px-4 py-2 font-medium">Fields</th>
                    <th className="px-4 py-2 font-medium">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => {
                    const go = () => navigate(`/p/${pid}/datasets/${d.id}`);
                    return (
                      <tr key={d.id} tabIndex={0} onClick={go} onKeyDown={(e) => e.key === "Enter" && go()} className="cursor-pointer border-b last:border-0 hover:bg-accent/40">
                        <td className="px-4 py-2.5 font-medium">{d.name}</td>
                        <td className="px-4 py-2.5">
                          <Badge>v{d.current_version?.version_number ?? "—"}</Badge>
                          {d.version_count > 1 && <span className="ml-2 text-xs text-muted-foreground">{d.version_count} versions</span>}
                        </td>
                        <td className="tabular px-4 py-2.5 text-right">{d.current_version?.row_count ?? 0}</td>
                        <td className="px-4 py-2.5">
                          <FieldBadges fields={d.current_version?.fields_present ?? []} />
                        </td>
                        <td className="px-4 py-2.5 text-muted-foreground">{fmtDate(d.current_version?.created_at ?? d.created_at)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      <UploadWizard pid={pid} open={open} onOpenChange={setOpen} onImported={(out) => out.dataset && navigate(`/p/${pid}/datasets/${out.dataset.id}`)} />
    </>
  );
}
