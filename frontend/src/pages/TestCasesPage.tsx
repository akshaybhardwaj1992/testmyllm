import { Database } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { CasesTable } from "@/components/cases-table";
import { EmptyState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, NativeSelect } from "@/components/ui/primitives";
import { useDatasets } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";

/** Browse cases of any dataset's latest version; open one to edit it in its dataset. */
export function TestCasesPage() {
  const pid = Number(useParams().projectId);
  const datasets = useDatasets(pid);
  const navigate = useNavigate();
  const [datasetId, setDatasetId] = useState(0);
  useEffect(() => {
    if (!datasetId && datasets.data?.length) setDatasetId(datasets.data[0].id);
  }, [datasets.data, datasetId]);
  const ds = datasets.data?.find((d) => d.id === datasetId);

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Management" }, { label: "Test Cases" }]}
        title="Test Cases"
        description="Cases in the latest version of each dataset. Editing a case creates a new dataset version."
      />
      <div className="space-y-4 p-6">
        {datasets.isLoading && <TableSkeleton rows={4} />}
        {datasets.data?.length === 0 && (
          <EmptyState
            icon={Database}
            title="No test cases yet"
            description="Test cases live in datasets. Upload a CSV or JSON file to create some."
            action={
              <Button asChild>
                <Link to={`/p/${pid}/datasets`}>Go to Datasets</Link>
              </Button>
            }
          />
        )}
        {ds && (
          <>
            <div className="flex items-center gap-3">
              <label htmlFor="tc-ds" className="text-sm font-medium">
                Dataset
              </label>
              <div className="w-72">
                <NativeSelect id="tc-ds" value={datasetId} onChange={(e) => setDatasetId(Number(e.target.value))}>
                  {datasets.data?.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name} · v{d.current_version?.version_number} · {d.current_version?.row_count} cases
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <Button asChild variant="outline" size="sm">
                <Link to={`/p/${pid}/datasets/${ds.id}`}>Open dataset</Link>
              </Button>
            </div>
            <Card className="overflow-hidden">
              {ds.current_version && (
                <CasesTable versionId={ds.current_version.id} onOpen={() => navigate(`/p/${pid}/datasets/${ds.id}?v=${ds.current_version!.id}`)} />
              )}
            </Card>
          </>
        )}
      </div>
    </>
  );
}
