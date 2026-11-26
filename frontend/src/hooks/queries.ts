import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api } from "@/services/api";
import type {
  ApiToken,
  ApiTokenCreated,
  Baseline,
  CaseComparison,
  GateEvaluation,
  QualityGate,
  QualityGateIn,
  RegressionReport,
  CaseEdit,
  CaseIn,
  Comparison,
  ModelConfig,
  ModelConfigIn,
  ConnectorTestIn,
  ConnectorTestOut,
  RagConnector,
  RagConnectorIn,
  CasesPage,
  CatalogModel,
  Dataset,
  DatasetVersion,
  Estimate,
  JudgeConfig,
  JudgeConfigIn,
  Member,
  MemberIn,
  MetricDef,
  Project,
  ProjectSettings,
  Provider,
  ProviderTest,
  Results,
  Review,
  ReviewIn,
  Role,
  Run,
  RunCreate,
  RunList,
  RunSummary,
  Suite,
  SuiteIn,
  UploadOut,
} from "@/types";

export const qk = {
  projects: ["projects"] as const,
  project: (id: number) => ["projects", id] as const,
  providers: (pid: number) => ["providers", pid] as const,
  judges: (pid: number) => ["judges", pid] as const,
  runs: (pid: number, params: object) => ["runs", pid, params] as const,
  run: (id: number) => ["run", id] as const,
  results: (id: number) => ["results", id] as const,
};

// ---------------------------------------------------------------- catalog
export const useMetricsCatalog = () =>
  useQuery({ queryKey: ["metrics"], queryFn: () => api<MetricDef[]>("/metrics"), staleTime: Infinity });

export const useCatalogModels = () =>
  useQuery({
    queryKey: ["catalog-models"],
    queryFn: () => api<CatalogModel[]>("/catalog/models"),
    staleTime: 60 * 60 * 1000,
  });

// ---------------------------------------------------------------- projects
export const useProjects = () => useQuery({ queryKey: qk.projects, queryFn: () => api<Project[]>("/projects") });

export const useProject = (id: number) =>
  useQuery({ queryKey: qk.project(id), queryFn: () => api<Project>(`/projects/${id}`), enabled: id > 0 });

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description: string }) => api<Project>("/projects", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
  });
}

// ---------------------------------------------------------------- providers & judges
export const useProviders = (pid: number) =>
  useQuery({ queryKey: qk.providers(pid), queryFn: () => api<Provider[]>(`/projects/${pid}/providers`) });

export function useSaveProvider(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: number; name?: string; api_key?: string }) =>
      id
        ? api<Provider>(`/providers/${id}`, { method: "PATCH", json: body })
        : api<Provider>(`/projects/${pid}/providers`, { method: "POST", json: { type: "openrouter", ...body } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.providers(pid) }),
  });
}

export function useTestProvider(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<ProviderTest>(`/providers/${id}/test`, { method: "POST" }),
    onSettled: () => qc.invalidateQueries({ queryKey: qk.providers(pid) }),
  });
}

export function useDeleteProvider(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/providers/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.providers(pid) });
      qc.invalidateQueries({ queryKey: qk.judges(pid) });
    },
  });
}

export const useJudges = (pid: number) =>
  useQuery({ queryKey: qk.judges(pid), queryFn: () => api<JudgeConfig[]>(`/projects/${pid}/judge-configs`) });

export function useSaveJudge(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: Partial<JudgeConfigIn> & { id?: number }) =>
      id
        ? api<JudgeConfig>(`/judge-configs/${id}`, { method: "PATCH", json: body })
        : api<JudgeConfig>(`/projects/${pid}/judge-configs`, { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.judges(pid) }),
  });
}

export function useDeleteJudge(pid: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<void>(`/judge-configs/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.judges(pid) }),
  });
}

// ---------------------------------------------------------------- runs
export type RunListParams = {
  page: number;
  page_size: number;
  q?: string;
  status?: string;
  sort?: string;
  run_type?: string;
  date_from?: string;
  date_to?: string;
};

export function useRuns(pid: number, params: RunListParams) {
  const search = new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  );
  return useQuery({
    queryKey: qk.runs(pid, params),
    queryFn: () => api<RunList>(`/projects/${pid}/test-runs?${search}`),
    placeholderData: (prev) => prev,
    refetchInterval: (q) =>
      q.state.data?.items.some((r) => r.status === "queued" || r.status === "running") ? 5000 : false,
  });
}

export const isActive = (run?: Pick<Run, "status">) => run?.status === "queued" || run?.status === "running";

/**
 * Poll every 2.5 s while a run is active; back off to 10 s when progress has not changed for 20 s
 * (spec §5.9). Stops when the run is terminal.
 */
function useBackoff() {
  const last = useRef<{ sig: string; at: number }>({ sig: "", at: Date.now() });
  return (sig: string, active: boolean): number | false => {
    if (!active) return false;
    if (sig !== last.current.sig) last.current = { sig, at: Date.now() };
    return Date.now() - last.current.at > 20_000 ? 10_000 : 2_500;
  };
}

export function useRun(id: number) {
  const backoff = useBackoff();
  return useQuery({
    queryKey: qk.run(id),
    queryFn: () => api<Run>(`/test-runs/${id}`),
    enabled: id > 0,
    // keep tracking progress in a background tab (cheap: one small request, backing off to 10 s)
    refetchIntervalInBackground: true,
    refetchInterval: (q) => {
      const r = q.state.data;
      return backoff(`${r?.status}:${r?.completed_tests}:${r?.actual_cost_usd}`, isActive(r));
    },
  });
}

export type ResultParams = {
  page?: number;
  page_size?: number;
  status?: string;
  label?: string;
  q?: string;
  metric?: string;
  engine?: string;
  metric_status?: string;
  failure_origin?: string;
};

const toSearch = (params: object) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "" && v !== null)
      .map(([k, v]) => [k, String(v)]),
  ).toString();

export function useRunResults(id: number, active: boolean, progressSig: string, params: ResultParams = {}) {
  return useQuery({
    queryKey: [...qk.results(id), progressSig, params],
    queryFn: () => api<Results>(`/test-runs/${id}/results?${toSearch({ page_size: 200, ...params })}`),
    enabled: id > 0,
    placeholderData: (prev) => prev,
    refetchInterval: active ? 4000 : false,
  });
}

export function useRunSummary(id: number, progressSig: string) {
  return useQuery({
    queryKey: ["summary", id, progressSig],
    queryFn: () => api<RunSummary>(`/test-runs/${id}/summary`),
    enabled: id > 0,
    placeholderData: (prev) => prev,
  });
}

export function useReview(runId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ metricResultId, ...body }: ReviewIn & { metricResultId: number }) =>
      api<Review>(`/metric-results/${metricResultId}/review`, { method: "POST", json: body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.results(runId) });
      qc.invalidateQueries({ queryKey: ["summary", runId] });
    },
  });
}

export const exportUrl = (runId: number, format: "csv" | "json" | "html" | "pdf") =>
  `/api/v1/test-runs/${runId}/export?format=${format}`;

// ---------------------------------------------------------------- datasets
export const useDatasets = (pid: number) =>
  useQuery({ queryKey: ["datasets", pid], queryFn: () => api<Dataset[]>(`/projects/${pid}/datasets`) });

export const useDataset = (id: number) =>
  useQuery({ queryKey: ["dataset", id], queryFn: () => api<Dataset>(`/datasets/${id}`), enabled: id > 0 });

export const useVersions = (datasetId: number) =>
  useQuery({
    queryKey: ["versions", datasetId],
    queryFn: () => api<DatasetVersion[]>(`/datasets/${datasetId}/versions`),
    enabled: datasetId > 0,
  });

export type CaseParams = { page: number; page_size: number; q?: string; tag?: string };

export const useCases = (versionId: number, params: CaseParams) =>
  useQuery({
    queryKey: ["cases", versionId, params],
    queryFn: () => api<CasesPage>(`/dataset-versions/${versionId}/cases?${toSearch(params)}`),
    enabled: versionId > 0,
    placeholderData: (prev) => prev,
  });

export async function uploadDataset(form: {
  file: File;
  projectId: number;
  name?: string;
  datasetId?: number;
  mapping?: Record<string, string | null>;
  metadataColumns?: string[];
  dryRun: boolean;
  skipInvalid?: boolean;
}) {
  const fd = new FormData();
  fd.append("file", form.file);
  fd.append("project_id", String(form.projectId));
  if (form.name) fd.append("name", form.name);
  if (form.datasetId) fd.append("dataset_id", String(form.datasetId));
  if (form.mapping) fd.append("mapping", JSON.stringify(form.mapping));
  if (form.metadataColumns) fd.append("metadata_columns", JSON.stringify(form.metadataColumns));
  fd.append("dry_run", String(form.dryRun));
  fd.append("skip_invalid", String(!!form.skipInvalid));
  return api<UploadOut>("/datasets/upload", { method: "POST", body: fd });
}

function invalidateDataset(qc: ReturnType<typeof useQueryClient>, datasetId?: number) {
  qc.invalidateQueries({ queryKey: ["datasets"] });
  if (datasetId) {
    qc.invalidateQueries({ queryKey: ["dataset", datasetId] });
    qc.invalidateQueries({ queryKey: ["versions", datasetId] });
  }
  qc.invalidateQueries({ queryKey: ["cases"] });
}

export function useCaseMutations(datasetId: number) {
  const qc = useQueryClient();
  const done = () => invalidateDataset(qc, datasetId);
  return {
    create: useMutation({
      mutationFn: (body: CaseIn & { dataset_id: number; base_version_id: number }) =>
        api<CaseEdit>("/test-cases", { method: "POST", json: body }),
      onSuccess: done,
    }),
    update: useMutation({
      mutationFn: ({ id, ...body }: CaseIn & { id: number }) => api<CaseEdit>(`/test-cases/${id}`, { method: "PATCH", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<CaseEdit>(`/test-cases/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

export function useDatasetMutations(pid: number) {
  const qc = useQueryClient();
  return {
    rename: useMutation({
      mutationFn: ({ id, name }: { id: number; name: string }) =>
        api<Dataset>(`/datasets/${id}`, { method: "PATCH", json: { name } }),
      onSuccess: (d) => invalidateDataset(qc, d.id),
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>(`/datasets/${id}`, { method: "DELETE" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["datasets", pid] }),
    }),
  };
}

// ---------------------------------------------------------------- suites
export const useSuites = (pid: number) =>
  useQuery({ queryKey: ["suites", pid], queryFn: () => api<Suite[]>(`/projects/${pid}/test-suites`) });

export function useSuiteMutations(pid: number) {
  const qc = useQueryClient();
  return {
    create: useMutation({
      mutationFn: (body: SuiteIn) => api<Suite>("/test-suites", { method: "POST", json: body }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["suites", pid] }),
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>(`/test-suites/${id}`, { method: "DELETE" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["suites", pid] }),
    }),
  };
}

// ---------------------------------------------------------------- model comparison (M4)
export const useModelConfigs = (pid: number) =>
  useQuery({ queryKey: ["model-configs", pid], queryFn: () => api<ModelConfig[]>(`/projects/${pid}/model-configs`) });

export function useModelConfigMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["model-configs", pid] });
  return {
    create: useMutation({
      mutationFn: (body: ModelConfigIn) => api<ModelConfig>(`/projects/${pid}/model-configs`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>(`/model-configs/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

export const useComparison = (runId: number, progressSig: string) =>
  useQuery({
    queryKey: ["comparison", runId, progressSig],
    queryFn: () => api<Comparison>(`/test-runs/${runId}/comparison`),
    placeholderData: (prev) => prev,
  });

export const useCaseComparison = (
  runId: number,
  progressSig: string,
  params: { page: number; page_size: number; q?: string; disagree_only?: boolean },
) =>
  useQuery({
    queryKey: ["case-comparison", runId, progressSig, params],
    queryFn: () => api<CaseComparison>(`/test-runs/${runId}/cases?${toSearch(params)}`),
    placeholderData: (prev) => prev,
  });

export function useRerun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: number) => api<Run>(`/test-runs/${runId}/rerun`, { method: "POST" }),
    onSuccess: (run) => qc.invalidateQueries({ queryKey: ["runs", run.project_id] }),
  });
}

// ---------------------------------------------------------------- regression, gates, tokens (M5)
export const useBaselines = (pid: number) =>
  useQuery({ queryKey: ["baselines", pid], queryFn: () => api<Baseline[]>(`/projects/${pid}/baselines`) });

export function useBaselineMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["baselines", pid] });
  return {
    create: useMutation({
      mutationFn: (body: { run_id: number; name: string }) =>
        api<Baseline>("/baselines", { method: "POST", json: { project_id: pid, ...body } }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/baselines/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

export const useGates = (pid: number) =>
  useQuery({ queryKey: ["gates", pid], queryFn: () => api<QualityGate[]>(`/projects/${pid}/quality-gates`) });

export function useGateMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["gates", pid] });
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: QualityGateIn & { id?: number }) =>
        id ? api<QualityGate>(`/quality-gates/${id}`, { method: "PUT", json: body }) : api<QualityGate>("/quality-gates", { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/quality-gates/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

export const fetchRegression = (candidateId: number, baselineId: number, allowMismatch: boolean) =>
  api<RegressionReport>(`/regressions/${candidateId}?baseline_id=${baselineId}&allow_dataset_mismatch=${allowMismatch}`);

export const evaluateGate = (gateId: number, body: { run_id: number; baseline_id: number | null; allow_dataset_mismatch: boolean; record: boolean }) =>
  api<GateEvaluation>(`/quality-gates/${gateId}/evaluate`, { method: "POST", json: body });

export const useGateEvaluations = (runId: number) =>
  useQuery({ queryKey: ["gate-evals", runId], queryFn: () => api<GateEvaluation[]>(`/test-runs/${runId}/gate-evaluations`), enabled: runId > 0 });

export const useApiTokens = (pid: number, enabled: boolean) =>
  useQuery({ queryKey: ["tokens", pid], queryFn: () => api<ApiToken[]>(`/projects/${pid}/api-tokens`), enabled });

export function useTokenMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["tokens", pid] });
  return {
    create: useMutation({
      mutationFn: (body: { name: string; role: "editor" | "viewer" }) =>
        api<ApiTokenCreated>(`/projects/${pid}/api-tokens`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    revoke: useMutation({ mutationFn: (id: number) => api<void>(`/api-tokens/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

// ---------------------------------------------------------------- RAG connectors
export const useConnectors = (pid: number) =>
  useQuery({ queryKey: ["connectors", pid], queryFn: () => api<RagConnector[]>(`/projects/${pid}/rag-connectors`) });

export function useConnectorMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["connectors", pid] });
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: RagConnectorIn & { id?: number }) =>
        id
          ? api<RagConnector>(`/rag-connectors/${id}`, { method: "PUT", json: body })
          : api<RagConnector>(`/projects/${pid}/rag-connectors`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: number) => api<void>(`/rag-connectors/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

export const testConnector = (body: ConnectorTestIn) =>
  api<ConnectorTestOut>("/rag-connectors/test", { method: "POST", json: body });

// ---------------------------------------------------------------- project settings & members
export const useMembers = (pid: number) =>
  useQuery({ queryKey: ["members", pid], queryFn: () => api<Member[]>(`/projects/${pid}/members`) });

export function useProjectMutations(pid: number) {
  const qc = useQueryClient();
  const members = () => qc.invalidateQueries({ queryKey: ["members", pid] });
  return {
    update: useMutation({
      mutationFn: (body: { name?: string; description?: string; settings?: ProjectSettings }) =>
        api<Project>(`/projects/${pid}`, { method: "PATCH", json: body }),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: qk.projects });
        qc.invalidateQueries({ queryKey: qk.project(pid) });
      },
    }),
    remove: useMutation({
      mutationFn: () => api<void>(`/projects/${pid}`, { method: "DELETE" }),
      onSuccess: () => qc.invalidateQueries({ queryKey: qk.projects }),
    }),
    addMember: useMutation({
      mutationFn: (body: MemberIn) => api<Member>(`/projects/${pid}/members`, { method: "POST", json: body }),
      onSuccess: members,
    }),
    setRole: useMutation({
      mutationFn: ({ userId, role }: { userId: number; role: Role }) =>
        api<Member>(`/projects/${pid}/members/${userId}`, { method: "PATCH", json: { role } }),
      onSuccess: members,
    }),
    removeMember: useMutation({
      mutationFn: (userId: number) => api<void>(`/projects/${pid}/members/${userId}`, { method: "DELETE" }),
      onSuccess: members,
    }),
  };
}

export const estimateRun = (body: RunCreate) => api<Estimate>("/test-runs/estimate", { method: "POST", json: body });

export function useCreateRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RunCreate) => api<Run>("/test-runs", { method: "POST", json: body }),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["runs", run.project_id] });
      qc.invalidateQueries({ queryKey: qk.project(run.project_id) });
    },
  });
}

export function useCancelRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<Run>(`/test-runs/${id}/cancel`, { method: "POST" }),
    onSuccess: (run) => {
      qc.setQueryData(qk.run(run.id), run);
      qc.invalidateQueries({ queryKey: ["runs", run.project_id] });
    },
  });
}
