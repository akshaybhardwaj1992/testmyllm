import { useQuery } from "@tanstack/react-query";

import { api } from "@/services/api";
import type { Overview, Trends } from "@/types";

export const useOverview = (pid: number) =>
  useQuery({ queryKey: ["overview", pid], queryFn: () => api<Overview>(`/projects/${pid}/overview`), refetchInterval: 30_000 });

export type TrendParams = { days?: number; max_runs?: number; run_type?: string };

export const useTrends = (pid: number, params: TrendParams = {}) => {
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => [k, String(v)]));
  return useQuery({ queryKey: ["trends", pid, params], queryFn: () => api<Trends>(`/projects/${pid}/trends?${qs}`) });
};

export type HistoryFilters = { status?: string; run_type?: string; q?: string; date_from?: string; date_to?: string };

export const historyExportUrl = (pid: number, f: HistoryFilters) => {
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v).map(([k, v]) => [k, String(v)]));
  return `/api/v1/projects/${pid}/test-runs/export?${qs}`;
};
