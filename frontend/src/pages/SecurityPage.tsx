import { useParams, useSearchParams } from "react-router-dom";

import { RunsTable } from "@/components/runs-table";
import { GatesPanel, LibraryPanel, ScanPanel, TargetsPanel } from "@/components/security";
import { EmptyState, TableSkeleton } from "@/components/states";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { useProject, useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { Radar } from "lucide-react";

function RecentScans({ pid }: { pid: number }) {
  const runs = useRuns(pid, { page: 1, page_size: 20, run_type: "security", sort: "-created_at" });
  if (runs.isLoading) return <TableSkeleton rows={3} />;
  if (!runs.data?.items.length) return <EmptyState icon={Radar} title="No scans yet" description="Start one from the New scan tab." />;
  return <RunsTable runs={runs.data.items} projectId={pid} hidden={["type"]} />;
}

export function SecurityPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const canEdit = project.data?.role !== "viewer";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "scan";
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Security Testing" }]}
        title="AI Security Testing"
        description="Attack your own LLM app with prompt injection, jailbreaks and data-extraction probes, measure attack success rates and gate releases on them."
      />
      <div className="space-y-4 p-6">
        <Tabs value={tab} onValueChange={(v) => setSp({ tab: v }, { replace: true })}>
          <TabsList>
            <TabsTrigger value="scan">New scan</TabsTrigger>
            <TabsTrigger value="scans">Scans</TabsTrigger>
            <TabsTrigger value="targets">Targets</TabsTrigger>
            <TabsTrigger value="library">Attack library</TabsTrigger>
            <TabsTrigger value="gates">Security gates</TabsTrigger>
          </TabsList>
          <TabsContent value="scan" className="mt-4">
            <ScanPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="scans" className="mt-4">
            <RecentScans pid={pid} />
          </TabsContent>
          <TabsContent value="targets" className="mt-4">
            <TargetsPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="library" className="mt-4">
            <LibraryPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="gates" className="mt-4">
            <GatesPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
