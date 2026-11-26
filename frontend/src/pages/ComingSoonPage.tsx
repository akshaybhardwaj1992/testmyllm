import { Construction } from "lucide-react";

import { EmptyState } from "@/components/states";
import type { NavItem } from "@/layouts/nav";
import { PageHeader } from "@/layouts/AppLayout";

const MILESTONES: Record<string, string> = {
  M2: "Datasets & test management: versioned uploads, suites, batch runs, human review and exports.",
  M3: "RAG evaluation: offline datasets and live connectors with failure-origin diagnosis.",
  M4: "Model comparison: quality, latency and cost across models on one dataset.",
  M5: "Regression testing: baselines, per-metric quality gates, CLI and GitHub Action.",
  M6: "Overview dashboard, evaluation history and the MCP server.",
};

export function ComingSoonPage({ item }: { item: NavItem }) {
  return (
    <>
      <PageHeader crumbs={[{ label: item.label }]} title={item.label} />
      <div className="p-6">
        <EmptyState
          icon={Construction}
          title={`Arrives in milestone ${item.milestone}`}
          description={MILESTONES[item.milestone ?? ""] ?? "Planned for a later milestone."}
        />
      </div>
    </>
  );
}
