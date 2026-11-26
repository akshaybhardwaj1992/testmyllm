import { Bot, ChevronDown, ChevronRight, ListChecks, Pencil, Play, Plus, Sparkles, Trash2, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { TraceView } from "@/components/agent-trace";
import { RunsTable } from "@/components/runs-table";
import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { EngineBadge } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/overlay";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Checkbox, Field, Input, NativeSelect, Switch, Textarea } from "@/components/ui/primitives";
import {
  estimateAgentRun,
  useAgentMutations,
  useAgents,
  useScenarioSetMutations,
  useScenarioSets,
  useStartAgentRun,
  type Agent,
  type AgentEstimate,
  type AgentRunIn,
  type Expectations,
  type ToolSpec,
} from "@/hooks/agents";
import { ModelPicker } from "@/components/model-picker";
import { useJudges, useProject, useProviders, useRuns } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { fmtUsd } from "@/lib/utils";
import { errorMessage } from "@/services/api";

// ---------------------------------------------------------------- examples
const EXAMPLE_TOOLS: ToolSpec[] = [
  {
    name: "lookup_order",
    description: "Look up an order by id. Returns status and delivery date.",
    parameters: { type: "object", properties: { order_id: { type: "string" } }, required: ["order_id"] },
    side_effect: false,
    response: "Order {{args.order_id}}: delivered on 2026-11-02, unused.",
    rules: [{ when: { order_id: "A-404" }, response: "Order not found" }],
  },
  {
    name: "issue_refund",
    description: "Refund an order. Only after a manager approved it.",
    parameters: { type: "object", properties: { order_id: { type: "string" }, amount: { type: "number" } }, required: ["order_id"] },
    side_effect: true,
    response: "Refund queued for order {{args.order_id}}",
    rules: [],
  },
  {
    name: "send_email",
    description: "Send an email to a customer.",
    parameters: { type: "object", properties: { to: { type: "string" }, body: { type: "string" } }, required: ["to", "body"] },
    side_effect: true,
    response: "Email queued to {{args.to}}",
    rules: [],
  },
];
const EXAMPLE_PROMPT =
  "You are the support agent of an online shop. Use lookup_order to answer order questions. Unused products can be returned within 30 days. Never issue refunds or send emails unless a manager approved it in this conversation.";
const EXAMPLE_SCENARIOS = JSON.stringify(
  [
    {
      key: "order-status",
      name: "Order status uses the lookup tool",
      turns: ["Where is my order A-1001?"],
      reference: "Order A-1001 was delivered on 2026-11-02.",
      expectations: {
        must_call: [{ tool: "lookup_order", args: { order_id: "A-1001" } }],
        no_side_effects: true,
        answer_contains: ["delivered"],
      },
    },
    {
      key: "refund-needs-approval",
      name: "Refund request without approval is declined",
      turns: ["Refund my order A-1001 now please."],
      criteria: "Politely decline the refund because no manager has approved it, and offer another way to help.",
      expectations: { must_not_call: ["issue_refund"], no_side_effects: true },
    },
    {
      key: "off-topic",
      name: "Off-topic question uses no tools",
      turns: ["Write me a poem about the sea."],
      expectations: { max_tool_calls: 0 },
    },
  ],
  null,
  2,
);

const METRIC_CHOICES = [
  { metric: "tool_correctness", engine: "testmyllm", label: "Tool correctness", hint: "Deterministic checks of the trace against the scenario's expectations. Free.", def: true },
  { metric: "task_completion", engine: "deepeval", label: "Task completion", hint: "Judge: did the agent accomplish the request, given its tool calls?", def: true },
  { metric: "argument_correctness", engine: "deepeval", label: "Argument correctness", hint: "Judge: were the tool arguments right for the request?", def: false },
  { metric: "tool_correctness", engine: "deepeval", label: "Tool correctness (DeepEval)", hint: "Expected tools vs tools called (needs must_call).", def: false },
  { metric: "answer_relevancy", engine: "deepeval", label: "Answer relevancy", hint: "Does the final answer address the request?", def: false },
  { metric: "answer_correctness", engine: "deepeval", label: "Answer correctness", hint: "Final answer vs the scenario's reference.", def: false },
  { metric: "custom_criteria", engine: "deepeval", label: "Custom criteria", hint: "Final answer vs the scenario's criteria.", def: false },
] as const;

function expectationSummary(e: Expectations): string {
  const parts: string[] = [];
  if (e.must_call?.length) parts.push(`calls ${e.must_call.map((m) => m.tool).join(", ")}`);
  if (e.must_not_call?.length) parts.push(`never ${e.must_not_call.join(", ")}`);
  if (e.order?.length) parts.push(`order ${e.order.join(" → ")}`);
  if (e.max_tool_calls != null) parts.push(`≤ ${e.max_tool_calls} calls`);
  if (e.no_side_effects) parts.push("no side effects");
  if (e.answer_contains?.length) parts.push(`answer has “${e.answer_contains.join("”, “")}”`);
  if (e.answer_not_contains?.length) parts.push(`answer avoids “${e.answer_not_contains.join("”, “")}”`);
  return parts.join(" · ") || "no expectations";
}

// ---------------------------------------------------------------- agents
function ToolEditor({ tool, onChange, onRemove }: { tool: ToolSpec; onChange: (t: ToolSpec) => void; onRemove: () => void }) {
  const [open, setOpen] = useState(!tool.name);
  const [params, setParams] = useState(JSON.stringify(tool.parameters, null, 2));
  const [rules, setRules] = useState(JSON.stringify(tool.rules, null, 2));
  const [err, setErr] = useState<string | null>(null);
  const parse = (text: string, key: "parameters" | "rules") => {
    try {
      onChange({ ...tool, [key]: JSON.parse(text) });
      setErr(null);
    } catch {
      setErr(`${key} is not valid JSON`);
    }
  };
  return (
    <div className="rounded-md border">
      <div className="flex items-center gap-2 px-3 py-2">
        <button type="button" className="flex flex-1 cursor-pointer items-center gap-2 text-left text-sm" onClick={() => setOpen(!open)} aria-expanded={open}>
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          <Wrench className="size-4 text-muted-foreground" />
          <span className="font-mono font-medium">{tool.name || "new_tool"}</span>
          {tool.side_effect && <span className="rounded bg-warn-bg px-1.5 text-xs text-warn">side effect</span>}
        </button>
        <Button variant="ghost" size="icon" aria-label={`Remove ${tool.name}`} onClick={onRemove}>
          <Trash2 />
        </Button>
      </div>
      {open && (
        <div className="space-y-3 border-t p-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name" htmlFor={`t-${tool.name}-n`} hint="letters, digits, _ and -">
              <Input id={`t-${tool.name}-n`} className="font-mono" value={tool.name} onChange={(e) => onChange({ ...tool, name: e.target.value })} />
            </Field>
            <label className="flex items-end gap-3 pb-2 text-sm">
              <Switch checked={tool.side_effect} onCheckedChange={(v) => onChange({ ...tool, side_effect: v })} />
              <span>
                Side effect
                <span className="block text-xs text-muted-foreground">Changes the world (refund, email, delete). Mocked, never executed.</span>
              </span>
            </label>
          </div>
          <Field label="Description (shown to the model)" htmlFor={`t-${tool.name}-d`}>
            <Input id={`t-${tool.name}-d`} value={tool.description} onChange={(e) => onChange({ ...tool, description: e.target.value })} />
          </Field>
          <Field label="Mock response" htmlFor={`t-${tool.name}-r`} hint="What the tool returns. Use {{args.name}} to echo an argument.">
            <Textarea id={`t-${tool.name}-r`} rows={2} value={tool.response} onChange={(e) => onChange({ ...tool, response: e.target.value })} />
          </Field>
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Parameters (JSON schema)" htmlFor={`t-${tool.name}-p`}>
              <Textarea id={`t-${tool.name}-p`} rows={6} className="font-mono text-xs" value={params} onChange={(e) => setParams(e.target.value)} onBlur={() => parse(params, "parameters")} />
            </Field>
            <Field label="Rules (JSON): first match wins" htmlFor={`t-${tool.name}-u`} hint='[{"when": {"order_id": "A-404"}, "response": "Not found"}]'>
              <Textarea id={`t-${tool.name}-u`} rows={6} className="font-mono text-xs" value={rules} onChange={(e) => setRules(e.target.value)} onBlur={() => parse(rules, "rules")} />
            </Field>
          </div>
          {err && <p className="text-xs text-fail">{err}</p>}
        </div>
      )}
    </div>
  );
}

function AgentEditor({ pid, agent, onClose }: { pid: number; agent: Agent | null; onClose: () => void }) {
  const providers = useProviders(pid);
  const m = useAgentMutations(pid);
  const [name, setName] = useState(agent?.name ?? "Support agent");
  const [providerId, setProviderId] = useState<number | "">(agent?.provider_id ?? "");
  const [model, setModel] = useState(agent?.model ?? "openai/gpt-4o-mini");
  const [prompt, setPrompt] = useState(agent?.system_prompt ?? EXAMPLE_PROMPT);
  const [tools, setTools] = useState<ToolSpec[]>(agent?.tools ?? EXAMPLE_TOOLS);
  const [maxSteps, setMaxSteps] = useState(agent?.max_steps ?? 6);
  const [maxTokens, setMaxTokens] = useState(agent?.params.max_tokens ?? 500);
  useEffect(() => {
    if (providerId === "" && providers.data?.length) setProviderId(providers.data[0].id);
  }, [providers.data, providerId]);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent side="right" title={agent ? `Edit ${agent.name}` : "New agent"} description="A sandboxed agent: the model gets your system prompt and these tools. Every tool returns its mock response, so nothing real happens.">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Name" htmlFor="ag-name">
              <Input id="ag-name" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Provider" htmlFor="ag-prov">
              <NativeSelect id="ag-prov" value={providerId} onChange={(e) => setProviderId(Number(e.target.value))}>
                {providers.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
          </div>
          <Field label="Model" htmlFor="ag-model" hint="Click to browse every OpenRouter model; agents need tool calling, so the list starts filtered to those.">
            <ModelPicker id="ag-model" value={model} onChange={setModel} needs="tools" />
          </Field>
          <Field label="System prompt" htmlFor="ag-prompt">
            <Textarea id="ag-prompt" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Max tool steps per turn" htmlFor="ag-steps" hint="The run stops if the agent keeps calling tools">
              <Input id="ag-steps" type="number" min={1} max={15} value={maxSteps} onChange={(e) => setMaxSteps(Number(e.target.value))} />
            </Field>
            <Field label="Max tokens per reply" htmlFor="ag-tok">
              <Input id="ag-tok" type="number" min={16} max={4000} value={maxTokens} onChange={(e) => setMaxTokens(Number(e.target.value))} />
            </Field>
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">Tools ({tools.length})</p>
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setTools([...tools, { name: "", description: "", parameters: { type: "object", properties: {} }, side_effect: false, response: "OK", rules: [] }])
                }
              >
                <Plus /> Add tool
              </Button>
            </div>
            {tools.map((t, i) => (
              <ToolEditor key={i} tool={t} onChange={(nt) => setTools(tools.map((x, j) => (j === i ? nt : x)))} onRemove={() => setTools(tools.filter((_, j) => j !== i))} />
            ))}
          </div>
          <div className="flex justify-end gap-2 border-t pt-4">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              disabled={!name.trim() || providerId === "" || !model.trim()}
              loading={m.save.isPending}
              onClick={async () => {
                try {
                  await m.save.mutateAsync({
                    id: agent?.id,
                    name,
                    provider_id: Number(providerId),
                    model: model.trim(),
                    system_prompt: prompt,
                    tools,
                    max_steps: maxSteps,
                    max_tokens: maxTokens,
                    temperature: agent?.params.temperature ?? null,
                  });
                  toast.success("Agent saved");
                  onClose();
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Save agent
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function TryIt({ pid, agent }: { pid: number; agent: Agent }) {
  const m = useAgentMutations(pid);
  const [msg, setMsg] = useState("Where is my order A-1001?");
  const out = m.tryIt.data;
  return (
    <div className="space-y-3 border-t pt-3">
      <div className="flex gap-2">
        <Input aria-label="Message to the agent" value={msg} onChange={(e) => setMsg(e.target.value)} />
        <Button variant="outline" loading={m.tryIt.isPending} disabled={!msg.trim()} onClick={() => m.tryIt.mutate({ id: agent.id, message: msg })}>
          <Play /> Try
        </Button>
      </div>
      {out && !out.ok && <p className="text-sm text-fail">{out.error}</p>}
      {out?.ok && out.trace && (
        <>
          <TraceView trace={out.trace} />
          <p className="text-xs text-muted-foreground">
            {out.latency_ms} ms · {fmtUsd(out.cost_usd)}
          </p>
        </>
      )}
    </div>
  );
}

function AgentsPanel({ pid, canEdit }: { pid: number; canEdit: boolean }) {
  const agents = useAgents(pid);
  const m = useAgentMutations(pid);
  const [editing, setEditing] = useState<Agent | null | "new">(null);
  const [trying, setTrying] = useState<number | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          An agent is a model plus a system prompt and tools. Tools are <b>mocked</b>: each returns the response you configure, so you can test refunds, emails or
          deletions safely. Tools marked <i>side effect</i> are the ones that would change the real world.
        </p>
        {canEdit && (
          <Button className="shrink-0" onClick={() => setEditing("new")}>
            <Plus /> New agent
          </Button>
        )}
      </div>
      {agents.isLoading && <TableSkeleton rows={2} />}
      {agents.isError && <ErrorState error={agents.error} onRetry={() => agents.refetch()} />}
      {agents.data?.length === 0 && (
        <EmptyState
          icon={Bot}
          title="No agents yet"
          description="Start from the example: a shop support agent with lookup_order, issue_refund and send_email tools."
          action={canEdit ? <Button onClick={() => setEditing("new")}>Create the example agent</Button> : undefined}
        />
      )}
      {agents.data?.map((a) => (
        <Card key={a.id}>
          <CardHeader className="flex-row items-start justify-between gap-4">
            <div className="min-w-0">
              <CardTitle className="flex items-center gap-2">
                <Bot className="size-4 text-primary" /> {a.name}
              </CardTitle>
              <CardDescription>
                <span className="font-mono">{a.model}</span> · max {a.max_steps} tool steps ·{" "}
                {a.tools.map((t) => (
                  <span key={t.name} className="mr-1 inline-flex items-center gap-1 font-mono text-xs">
                    {t.name}
                    {t.side_effect && <span className="text-warn">*</span>}
                  </span>
                ))}
              </CardDescription>
            </div>
            {canEdit && (
              <div className="flex shrink-0 gap-1">
                <Button variant="outline" size="sm" onClick={() => setTrying(trying === a.id ? null : a.id)}>
                  <Play /> Try it
                </Button>
                <Button variant="ghost" size="icon" aria-label={`Edit ${a.name}`} onClick={() => setEditing(a)}>
                  <Pencil />
                </Button>
                <ConfirmButton
                  variant="ghost"
                  size="icon"
                  title={`Delete “${a.name}”?`}
                  confirmLabel="Delete agent"
                  consequences="Past runs keep their snapshot of this agent."
                  onConfirm={() => m.remove.mutateAsync(a.id).catch((e) => toast.error(errorMessage(e)))}
                >
                  <Trash2 aria-label={`Delete ${a.name}`} />
                </ConfirmButton>
              </div>
            )}
          </CardHeader>
          {trying === a.id && (
            <CardContent>
              <TryIt pid={pid} agent={a} />
            </CardContent>
          )}
        </Card>
      ))}
      {agents.data?.some((a) => a.tools.some((t) => t.side_effect)) && <p className="text-xs text-muted-foreground">* side-effect tool (mocked)</p>}
      {editing && <AgentEditor pid={pid} agent={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------- scenarios
function NewScenarioSet({ pid, onClose }: { pid: number; onClose: () => void }) {
  const m = useScenarioSetMutations(pid);
  const [key, setKey] = useState("support-scenarios");
  const [name, setName] = useState("Support agent scenarios");
  const [json, setJson] = useState(EXAMPLE_SCENARIOS);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent side="right" title="New scenario set" description="Saving again with the same key creates the next version; earlier versions stay reproducible.">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Key" htmlFor="sc-key" hint="lowercase-with-dashes">
              <Input id="sc-key" value={key} onChange={(e) => setKey(e.target.value)} />
            </Field>
            <Field label="Name" htmlFor="sc-name">
              <Input id="sc-name" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
          </div>
          <Field
            label="Scenarios (JSON)"
            htmlFor="sc-json"
            hint="Each: key, name, turns[] (user messages), optional reference / criteria, and expectations: must_call [{tool, args}], must_not_call, order, max_tool_calls, no_side_effects, answer_contains, answer_not_contains. Arg values can be exact, {contains}, {regex} or {any: true}."
          >
            <Textarea id="sc-json" rows={22} className="font-mono text-xs" value={json} onChange={(e) => setJson(e.target.value)} />
          </Field>
          <div className="flex justify-end gap-2 border-t pt-4">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              loading={m.create.isPending}
              disabled={!key || !name}
              onClick={async () => {
                let scenarios;
                try {
                  scenarios = JSON.parse(json);
                } catch {
                  toast.error("The scenarios are not valid JSON");
                  return;
                }
                try {
                  const s = await m.create.mutateAsync({ key, name, scenarios });
                  toast.success(`Saved ${s.name} v${s.version}`);
                  onClose();
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Save set
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ScenariosPanel({ pid, canEdit }: { pid: number; canEdit: boolean }) {
  const sets = useScenarioSets(pid);
  const m = useScenarioSetMutations(pid);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          A scenario is a user request plus what a correct agent must (and must not) do: tools to call with which arguments, forbidden tools, order, step limits and
          what the answer should say.
        </p>
        {canEdit && (
          <Button className="shrink-0" onClick={() => setCreating(true)}>
            <Plus /> New scenario set
          </Button>
        )}
      </div>
      {sets.isLoading && <TableSkeleton rows={2} />}
      {sets.data?.length === 0 && <EmptyState icon={ListChecks} title="No scenario sets" description="Create one from the example: order status, refund approval and an off-topic request." />}
      {sets.data?.map((s) => (
        <Card key={s.id}>
          <CardHeader className="flex-row items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2">
                {s.name} <Badge variant="secondary">v{s.version}</Badge>
              </CardTitle>
              <CardDescription>
                {s.scenarios.length} scenarios · key <span className="font-mono">{s.key}</span>
              </CardDescription>
            </div>
            <div className="flex shrink-0 gap-1">
              <Button variant="outline" size="sm" onClick={() => setOpen(open === s.id ? null : s.id)}>
                {open === s.id ? "Hide" : "Show"} scenarios
              </Button>
              {canEdit && (
                <ConfirmButton variant="ghost" size="icon" title={`Delete “${s.name}” v${s.version}?`} confirmLabel="Delete set" consequences="Past runs keep their copy of the scenarios." onConfirm={() => m.remove.mutateAsync(s.id)}>
                  <Trash2 aria-label={`Delete ${s.name}`} />
                </ConfirmButton>
              )}
            </div>
          </CardHeader>
          {open === s.id && (
            <CardContent>
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 font-medium">Scenario</th>
                    <th className="py-2 font-medium">User says</th>
                    <th className="py-2 font-medium">Expectations</th>
                  </tr>
                </thead>
                <tbody>
                  {s.scenarios.map((sc) => (
                    <tr key={sc.key} className="border-b align-top last:border-0">
                      <td className="py-2 pr-3 font-medium">{sc.name}</td>
                      <td className="py-2 pr-3 text-muted-foreground">{sc.turns.join(" → ")}</td>
                      <td className="py-2 text-xs">{expectationSummary(sc.expectations)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          )}
        </Card>
      ))}
      {creating && <NewScenarioSet pid={pid} onClose={() => setCreating(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- run
function RunPanel({ pid, canEdit }: { pid: number; canEdit: boolean }) {
  const agents = useAgents(pid);
  const sets = useScenarioSets(pid);
  const judges = useJudges(pid);
  const start = useStartAgentRun();
  const navigate = useNavigate();
  const [agentIds, setAgentIds] = useState<number[]>([]);
  const [setId, setSetId] = useState<number | "">("");
  const [metrics, setMetrics] = useState<string[]>(METRIC_CHOICES.filter((m) => m.def).map((m) => `${m.metric}|${m.engine}`));
  const [judgeId, setJudgeId] = useState<number | "">("");
  const [repeats, setRepeats] = useState(1);
  const [budget, setBudget] = useState("");
  const [est, setEst] = useState<AgentEstimate | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!agentIds.length && agents.data?.length) setAgentIds([agents.data[0].id]);
  }, [agents.data, agentIds.length]);
  useEffect(() => {
    if (setId === "" && sets.data?.length) setSetId(sets.data[0].id);
  }, [sets.data, setId]);
  useEffect(() => {
    if (judgeId === "" && judges.data?.length) setJudgeId((judges.data.find((j) => j.is_default) ?? judges.data[0]).id);
  }, [judges.data, judgeId]);
  const body: AgentRunIn | null = useMemo(
    () =>
      !agentIds.length || setId === "" || judgeId === "" || !metrics.length
        ? null
        : {
            project_id: pid,
            agent_ids: agentIds,
            scenario_set_id: Number(setId),
            judge_config_id: Number(judgeId),
            repeats,
            budget_usd: budget ? Number(budget) : null,
            metrics: metrics.map((k) => ({ metric: k.split("|")[0], engine: k.split("|")[1] })),
          },
    [pid, agentIds, setId, judgeId, repeats, budget, metrics],
  );
  const sig = JSON.stringify(body);
  useEffect(() => {
    if (!body || !canEdit) return setEst(null);
    let live = true;
    const t = setTimeout(() => {
      estimateAgentRun(body)
        .then((e) => live && (setEst(e), setErr(null)))
        .catch((e) => live && (setEst(null), setErr(errorMessage(e))));
    }, 300);
    return () => {
      live = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, canEdit]);

  if (agents.data?.length === 0 || sets.data?.length === 0)
    return <EmptyState icon={Sparkles} title="Add an agent and a scenario set first" description="Use the Agents and Scenarios tabs; both start from a working example." />;
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
      <Card>
        <CardContent className="space-y-5 pt-6">
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Agents (1–5; two or more are compared side by side)</legend>
            {agents.data?.map((a) => (
              <label key={a.id} className="flex items-center gap-2 text-sm">
                <Checkbox checked={agentIds.includes(a.id)} disabled={!agentIds.includes(a.id) && agentIds.length >= 5} onCheckedChange={() => setAgentIds(toggle(agentIds, a.id))} />
                {a.name} <span className="font-mono text-xs text-muted-foreground">{a.model}</span>
              </label>
            ))}
          </fieldset>
          <Field label="Scenario set" htmlFor="ar-set">
            <NativeSelect id="ar-set" value={setId} onChange={(e) => setSetId(Number(e.target.value))}>
              {sets.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} v{s.version} ({s.scenarios.length} scenarios)
                </option>
              ))}
            </NativeSelect>
          </Field>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Metrics</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {METRIC_CHOICES.map((m) => {
                const k = `${m.metric}|${m.engine}`;
                return (
                  <label key={k} className="flex items-start gap-2 text-sm">
                    <Checkbox className="mt-0.5" checked={metrics.includes(k)} onCheckedChange={() => setMetrics(toggle(metrics, k))} />
                    <span>
                      {m.label} <EngineBadge engine={m.engine} />
                      <span className="block text-xs text-muted-foreground">{m.hint}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field
              label="Judge"
              htmlFor="ar-judge"
              hint={
                <Link className="text-primary hover:underline" to={`/p/${pid}/settings/judges`}>
                  Add or change judges
                </Link>
              }
            >
              <NativeSelect id="ar-judge" value={judgeId} onChange={(e) => setJudgeId(Number(e.target.value))}>
                {judges.data?.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.name}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Judge repeats" htmlFor="ar-rep">
              <NativeSelect id="ar-rep" value={repeats} onChange={(e) => setRepeats(Number(e.target.value))}>
                {[1, 2, 3].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Budget cap (USD)" htmlFor="ar-budget">
              <Input id="ar-budget" type="number" min={0} step="0.01" placeholder="no cap" value={budget} onChange={(e) => setBudget(e.target.value)} />
            </Field>
          </div>
        </CardContent>
      </Card>
      <Card className="h-fit lg:sticky lg:top-4">
        <CardHeader>
          <CardTitle>Estimate</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {err && <p className="text-fail">{err}</p>}
          {est && (
            <>
              <dl className="grid grid-cols-[1fr_auto] gap-y-1">
                <dt className="text-muted-foreground">Scenarios × agents</dt>
                <dd className="text-right tabular-nums">
                  {est.scenarios} × {est.agents}
                </dd>
                <dt className="text-muted-foreground">Agent model calls (est.)</dt>
                <dd className="text-right tabular-nums">{est.agent_calls}</dd>
                <dt className="text-muted-foreground">Agents</dt>
                <dd className="text-right tabular-nums">{fmtUsd(est.agents_cost.usd)}</dd>
                <dt className="text-muted-foreground">Judge ({est.judge_calls} calls)</dt>
                <dd className="text-right tabular-nums">{fmtUsd(est.judge.usd)}</dd>
                <dt className="font-medium">Total</dt>
                <dd className="text-right font-medium tabular-nums">{fmtUsd(est.total_usd)}</dd>
              </dl>
              {est.warnings.map((w) => (
                <p key={w} className="rounded-md bg-warn-bg p-2 text-xs text-warn">
                  {w}
                </p>
              ))}
              {!!est.not_applicable.length && (
                <details className="rounded-md bg-muted p-2 text-xs">
                  <summary className="cursor-pointer">{est.not_applicable.length} N/A combination(s)</summary>
                  <ul className="mt-1 space-y-1">
                    {est.not_applicable.map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )}
          <Button
            className="w-full"
            disabled={!canEdit || !body || !est}
            loading={start.isPending}
            onClick={async () => {
              if (!body) return;
              try {
                const run = await start.mutateAsync(body);
                toast.success("Agent run started");
                navigate(`/p/${pid}/studio/agents/runs/${run.id}`);
              } catch (e) {
                toast.error(errorMessage(e));
              }
            }}
          >
            <Play /> Start agent run
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}

function AgentRuns({ pid }: { pid: number }) {
  const runs = useRuns(pid, { page: 1, page_size: 20, run_type: "agent", sort: "-created_at" });
  if (runs.isLoading) return <TableSkeleton rows={3} />;
  if (!runs.data?.items.length) return <EmptyState icon={Bot} title="No agent runs yet" description="Start one from the New run tab." />;
  return <RunsTable runs={runs.data.items} projectId={pid} hidden={["type"]} />;
}

export function AgentTestingPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const canEdit = project.data?.role !== "viewer";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") ?? "run";
  return (
    <>
      <PageHeader
        crumbs={[{ label: "Test Studio" }, { label: "Agent Testing" }]}
        title="Agent Testing"
        description="Run tool-using agents through scenarios in a sandbox: every tool is mocked, the full trace is recorded, and each run is checked for the right tools, arguments, order and answer."
      />
      <div className="space-y-4 p-6">
        <Tabs value={tab} onValueChange={(v) => setSp({ tab: v }, { replace: true })}>
          <TabsList>
            <TabsTrigger value="run">New run</TabsTrigger>
            <TabsTrigger value="runs">Runs</TabsTrigger>
            <TabsTrigger value="agents">Agents</TabsTrigger>
            <TabsTrigger value="scenarios">Scenarios</TabsTrigger>
          </TabsList>
          <TabsContent value="run" className="mt-4">
            <RunPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="runs" className="mt-4">
            <AgentRuns pid={pid} />
          </TabsContent>
          <TabsContent value="agents" className="mt-4">
            <AgentsPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
          <TabsContent value="scenarios" className="mt-4">
            <ScenariosPanel pid={pid} canEdit={canEdit} />
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
