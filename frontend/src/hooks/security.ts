import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/services/api";
import type {
  AttackSet,
  AttackSetIn,
  DeepTeamEstimate,
  DeepTeamIn,
  Enhancer,
  GateEvaluation,
  Run,
  SecurityEstimate,
  SecurityGate,
  SecurityGateIn,
  SecurityReport,
  SecurityRunIn,
  SecurityTarget,
  SecurityTargetIn,
  TargetProbe,
} from "@/types";

/** Category metadata mirrored from the backend (app/security/__init__.py). OWASP Top 10 for LLM Apps 2025. */
export const CATEGORIES: Record<string, { label: string; owasp: string; group: "injection" | "disclosure"; hint: string }> = {
  prompt_injection: { label: "Prompt injection", owasp: "LLM01", group: "injection", hint: "Direct attempts to override the instructions." },
  jailbreak: { label: "Jailbreak", owasp: "LLM01", group: "injection", hint: "Role-play, 'developer mode' and emotional pretexts." },
  indirect_injection: { label: "Indirect injection", owasp: "LLM01", group: "injection", hint: "Instructions hidden in retrieved documents." },
  system_prompt_leakage: { label: "System prompt leakage", owasp: "LLM07", group: "disclosure", hint: "Extracting hidden instructions." },
  sensitive_disclosure: { label: "Sensitive information disclosure", owasp: "LLM02", group: "disclosure", hint: "Secrets and personal data." },
  cross_user_leakage: { label: "Cross-user data leakage", owasp: "LLM02", group: "disclosure", hint: "One user seeing another user's data." },
  excessive_agency: { label: "Excessive agency", owasp: "LLM06", group: "disclosure", hint: "An agent taking actions nobody authorized (agent targets)." },
};

export const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-fail text-white",
  high: "bg-fail-bg text-fail",
  medium: "bg-warn-bg text-warn",
  low: "bg-na-bg text-na",
};

export const useSecurityTargets = (pid: number) =>
  useQuery({ queryKey: ["sec-targets", pid], queryFn: () => api<SecurityTarget[]>(`/projects/${pid}/security-targets`) });

export function useTargetMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["sec-targets", pid] });
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: SecurityTargetIn & { id?: number }) =>
        id
          ? api<SecurityTarget>(`/security-targets/${id}`, { method: "PUT", json: body })
          : api<SecurityTarget>(`/projects/${pid}/security-targets`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/security-targets/${id}`, { method: "DELETE" }), onSuccess: done }),
    probe: useMutation({ mutationFn: (id: number) => api<TargetProbe>(`/security-targets/${id}/probe`, { method: "POST" }) }),
  };
}

export const useAttackSets = (pid: number) =>
  useQuery({
    queryKey: ["attack-sets", pid],
    queryFn: () => api<AttackSet[]>(`/projects/${pid}/attack-sets`),
    // poll while a DeepTeam expansion is running in the worker
    refetchInterval: (q) => (q.state.data?.some((s) => s.status === "generating") ? 2500 : false),
  });

export const useEnhancers = () =>
  useQuery({ queryKey: ["deepteam-enhancers"], queryFn: () => api<Enhancer[]>("/security/deepteam/enhancers"), staleTime: Infinity });

export const estimateDeepTeam = (setId: number, body: DeepTeamIn) =>
  api<DeepTeamEstimate>(`/attack-sets/${setId}/deepteam/estimate`, { method: "POST", json: body });

export function useAttackSetMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["attack-sets", pid] });
  return {
    create: useMutation({
      mutationFn: (body: AttackSetIn) => api<AttackSet>(`/projects/${pid}/attack-sets`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/attack-sets/${id}`, { method: "DELETE" }), onSuccess: done }),
    deepteam: useMutation({
      mutationFn: ({ setId, ...body }: DeepTeamIn & { setId: number }) =>
        api<AttackSet>(`/attack-sets/${setId}/deepteam`, { method: "POST", json: body }),
      onSuccess: done,
    }),
  };
}

export const estimateScan = (body: SecurityRunIn) => api<SecurityEstimate>("/security-runs/estimate", { method: "POST", json: body });

export function useStartScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SecurityRunIn) => api<Run>("/security-runs", { method: "POST", json: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runs"] }),
  });
}

export const useSecurityReport = (runId: number, progressSig: string, enabled = true) =>
  useQuery({
    queryKey: ["sec-report", runId, progressSig],
    queryFn: () => api<SecurityReport>(`/security-runs/${runId}/report`),
    enabled: enabled && runId > 0,
    placeholderData: (prev) => prev,
  });

export const useSecurityGates = (pid: number) =>
  useQuery({ queryKey: ["sec-gates", pid], queryFn: () => api<SecurityGate[]>(`/projects/${pid}/security-gates`) });

export function useSecurityGateMutations(pid: number) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["sec-gates", pid] });
  return {
    save: useMutation({
      mutationFn: ({ id, ...body }: SecurityGateIn & { id?: number }) =>
        id
          ? api<SecurityGate>(`/security-gates/${id}`, { method: "PUT", json: body })
          : api<SecurityGate>(`/projects/${pid}/security-gates`, { method: "POST", json: body }),
      onSuccess: done,
    }),
    remove: useMutation({ mutationFn: (id: number) => api<void>(`/security-gates/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

export const evaluateSecurityGate = (gateId: number, runId: number, record: boolean) =>
  api<GateEvaluation>(`/security-gates/${gateId}/evaluate`, { method: "POST", json: { run_id: runId, record } });
