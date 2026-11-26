import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { Trace } from "@/components/agent-trace";
import { api } from "@/services/api";
import type { Run } from "@/types";

/** Mirrors backend app/schemas/agents.py (decision D31). */
export type MockRule = { when: Record<string, unknown>; response: string };
export type ToolSpec = {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  side_effect: boolean;
  response: string;
  rules: MockRule[];
};
export type Agent = {
  id: number;
  project_id: number;
  name: string;
  provider_id: number;
  model: string;
  system_prompt: string;
  tools: ToolSpec[];
  max_steps: number;
  params: { max_tokens?: number; temperature?: number | null };
  created_at: string;
};
export type AgentIn = {
  name: string;
  provider_id: number;
  model: string;
  system_prompt: string;
  tools: ToolSpec[];
  max_steps: number;
  max_tokens: number;
  temperature: number | null;
};
export type Expectations = {
  must_call?: { tool: string; args?: Record<string, unknown> | null }[];
  must_not_call?: string[];
  order?: string[];
  max_tool_calls?: number | null;
  no_side_effects?: boolean;
  answer_contains?: string[];
  answer_not_contains?: string[];
};
export type Scenario = { key: string; name: string; turns: string[]; reference?: string; criteria?: string; tags?: string[]; expectations: Expectations };
export type ScenarioSet = { id: number; project_id: number; key: string; name: string; version: number; description: string; scenarios: Scenario[]; created_at: string };
export type AgentRunIn = {
  project_id: number;
  agent_ids: number[];
  scenario_set_id: number;
  metrics: { metric: string; engine: string; threshold?: number | null }[];
  judge_config_id: number;
  repeats: number;
  budget_usd?: number | null;
  run_name?: string | null;
};
type Cost = { usd: number | null; input_tokens: number; output_tokens: number };
export type AgentEstimate = {
  scenarios: number;
  agents: number;
  agent_calls: number;
  agents_cost: Cost;
  judge: Cost;
  total_usd: number | null;
  judge_calls: number;
  not_applicable: string[];
  warnings: string[];
};
export type AgentTryOut = { ok: boolean; final: string | null; trace: Trace | null; cost_usd: number | null; latency_ms: number | null; error: string | null };

export const useAgents = (pid: number) => useQuery({ queryKey: ["agents", pid], queryFn: () => api<Agent[]>(`/projects/${pid}/agents`) });

export function useAgentMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["agents", pid] });
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: AgentIn & { id?: number }) =>
        id ? api<Agent>(`/agents/${id}`, { method: "PUT", json: body }) : api<Agent>(`/projects/${pid}/agents`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/agents/${id}`, { method: "DELETE" }), onSuccess: done }),
    tryIt: useMutation({ mutationFn: ({ id, message }: { id: number; message: string }) => api<AgentTryOut>(`/agents/${id}/try`, { method: "POST", json: { message } }) }),
  };
}

export const useScenarioSets = (pid: number) =>
  useQuery({ queryKey: ["scenario-sets", pid], queryFn: () => api<ScenarioSet[]>(`/projects/${pid}/agent-scenario-sets`) });

export function useScenarioSetMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["scenario-sets", pid] });
  return {
    create: useMutation({
      mutationFn: (body: { key: string; name: string; description?: string; scenarios: Scenario[] }) =>
        api<ScenarioSet>(`/projects/${pid}/agent-scenario-sets`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/agent-scenario-sets/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

export const estimateAgentRun = (body: AgentRunIn) => api<AgentEstimate>("/agent-runs/estimate", { method: "POST", json: body });

export function useStartAgentRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentRunIn) => api<Run>("/agent-runs", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}
