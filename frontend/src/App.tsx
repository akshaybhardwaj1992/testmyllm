import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { useAuth } from "@/hooks/auth";
import { ProjectLayout } from "@/layouts/AppLayout";
import { ALL_ITEMS } from "@/layouts/nav";
import { ComingSoonPage } from "@/pages/ComingSoonPage";
import { DatasetDetailPage } from "@/pages/DatasetDetailPage";
import { DatasetsPage } from "@/pages/DatasetsPage";
import { ProjectSettingsPage } from "@/pages/ProjectSettingsPage";
import { SuitesPage } from "@/pages/SuitesPage";
import { TestCasesPage } from "@/pages/TestCasesPage";
import { IntegrationsPage } from "@/pages/IntegrationsPage";
import { JudgesPage } from "@/pages/JudgesPage";
import { QualityGatesPage } from "@/pages/QualityGatesPage";
import { RegressionPage } from "@/pages/RegressionPage";
import { LLMTestingPage } from "@/pages/LLMTestingPage";
import { LoginPage } from "@/pages/LoginPage";
import { ModelComparisonPage } from "@/pages/ModelComparisonPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { ProvidersPage } from "@/pages/ProvidersPage";
import { RAGPage } from "@/pages/RAGPage";
import { RunDetailPage } from "@/pages/RunDetailPage";
import { RunsPage } from "@/pages/RunsPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { SecurityPage } from "@/pages/SecurityPage";
import { AgentTestingPage } from "@/pages/AgentTestingPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const loc = useLocation();
  if (user === undefined) {
    return (
      <div className="flex h-screen items-center justify-center" aria-busy>
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </div>
    );
  }
  if (user === null) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return <>{children}</>;
}

function Home() {
  let last: string | null = null;
  try {
    last = localStorage.getItem("lastProject");
  } catch {
    /* ignore */
  }
  return <Navigate to={last && last !== "NaN" ? `/p/${last}/overview` : "/projects"} replace />;
}

const pending = ALL_ITEMS.filter((i) => i.milestone);

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/projects"
        element={
          <RequireAuth>
            <ProjectsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/p/:projectId"
        element={
          <RequireAuth>
            <ProjectLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="overview" replace />} />
        <Route path="overview" element={<OverviewPage />} />
        <Route path="studio/llm" element={<LLMTestingPage />} />
        <Route path="studio/rag" element={<RAGPage />} />
        <Route path="studio/compare" element={<ModelComparisonPage />} />
        <Route path="regression" element={<RegressionPage />} />
        <Route path="gates" element={<QualityGatesPage />} />
        <Route path="security" element={<SecurityPage />} />
        <Route path="security/runs/:runId" element={<RunDetailPage />} />
        <Route path="studio/agents" element={<AgentTestingPage />} />
        <Route path="studio/agents/runs/:runId" element={<RunDetailPage />} />
        <Route path="settings/integrations" element={<IntegrationsPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="history" element={<HistoryPage />} />
        <Route path="runs/:runId" element={<RunDetailPage />} />
        <Route path="settings/providers" element={<ProvidersPage />} />
        <Route path="settings/judges" element={<JudgesPage />} />
        <Route path="settings/project" element={<ProjectSettingsPage />} />
        <Route path="datasets" element={<DatasetsPage />} />
        <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
        <Route path="cases" element={<TestCasesPage />} />
        <Route path="suites" element={<SuitesPage />} />
        {pending.map((i) => (
          <Route key={i.path} path={i.path} element={<ComingSoonPage item={i} />} />
        ))}
        <Route path="*" element={<Navigate to="overview" replace />} />
      </Route>
      <Route
        path="*"
        element={
          <RequireAuth>
            <Home />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
