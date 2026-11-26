import { AlertTriangle, Bot, ChevronDown, ChevronRight, OctagonX, Syringe, User, Wrench } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

export type TraceStep = {
  type: "user" | "assistant" | "tool_call" | "stop";
  content?: string;
  thinking?: boolean;
  name?: string;
  args?: Record<string, unknown>;
  output?: string;
  side_effect?: boolean;
  injected?: boolean;
  unknown?: boolean;
  reason?: string;
};
export type Trace = { steps: TraceStep[]; stopped: string; tool_calls: number };

function ToolCall({ s, n }: { s: TraceStep; n: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={cn("rounded-md border bg-card", s.side_effect && "border-warn/50", s.unknown && "border-fail/50")}>
      <button type="button" className="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-sm" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <Wrench className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="font-mono font-medium">{s.name}</span>
        <span className="truncate font-mono text-xs text-muted-foreground">({JSON.stringify(s.args ?? {})})</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <span className="text-xs text-muted-foreground">#{n}</span>
          {s.side_effect && (
            <span className="rounded bg-warn-bg px-1.5 text-xs font-medium text-warn" title="Would change something in the real world. Mocked: nothing was executed.">
              side effect
            </span>
          )}
          {s.injected && (
            <span className="inline-flex items-center gap-1 rounded bg-fail-bg px-1.5 text-xs font-medium text-fail" title="This tool result was replaced by an attack payload">
              <Syringe className="size-3" /> injected
            </span>
          )}
          {s.unknown && <span className="rounded bg-fail-bg px-1.5 text-xs font-medium text-fail">unknown tool</span>}
        </span>
      </button>
      {open && (
        <div className="space-y-2 border-t px-3 py-2 text-xs">
          <div>
            <p className="mb-0.5 font-medium text-muted-foreground">Arguments</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 font-mono">{JSON.stringify(s.args ?? {}, null, 2)}</pre>
          </div>
          <div>
            <p className="mb-0.5 font-medium text-muted-foreground">Mock result returned to the agent</p>
            <pre className="whitespace-pre-wrap rounded bg-muted p-2 font-mono">{s.output}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

/** Agent trace as a timeline: user turns, tool calls (mocked, never executed), assistant replies. */
export function TraceView({ trace }: { trace: Trace }) {
  let n = 0;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {trace.tool_calls} tool call(s) · {trace.stopped === "max_steps" ? "stopped at the step limit" : "finished with an answer"} · tools are mocked: nothing real was
        executed.
      </p>
      <ol className="space-y-2 border-l-2 border-border pl-4">
        {trace.steps.map((s, i) => (
          <li key={i} className="relative">
            <span className="absolute -left-[23px] top-2.5 size-2.5 rounded-full border-2 border-card bg-border" aria-hidden />
            {s.type === "user" && (
              <div className="flex gap-2 text-sm">
                <User className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-label="User" />
                <p className="whitespace-pre-wrap">{s.content}</p>
              </div>
            )}
            {s.type === "assistant" && (
              <div className={cn("flex gap-2 rounded-md p-2 text-sm", s.thinking ? "text-muted-foreground" : "bg-muted/50")}>
                <Bot className="mt-0.5 size-4 shrink-0 text-primary" aria-label="Agent" />
                <p className="whitespace-pre-wrap">{s.content || "—"}</p>
              </div>
            )}
            {s.type === "tool_call" && <ToolCall s={s} n={++n} />}
            {s.type === "stop" && (
              <p className="flex items-center gap-2 text-sm text-warn">
                <OctagonX className="size-4" /> {s.reason}
              </p>
            )}
          </li>
        ))}
      </ol>
      {trace.steps.some((s) => s.side_effect) && (
        <p className="flex items-center gap-2 text-xs text-warn">
          <AlertTriangle className="size-3.5" /> The agent requested a side-effect action. In a real deployment this would have changed data or contacted someone.
        </p>
      )}
    </div>
  );
}
