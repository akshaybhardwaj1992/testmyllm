"""Sandboxed agent loop (D31): the model under test gets the agent's system prompt and tool definitions
(OpenAI function-calling format, via OpenRouter); every tool call is answered by the tool's MOCK response,
so nothing real ever happens. The full trace is recorded for assertions, judge metrics and the UI.

Tool spec (stored on the agent config):
  {"name": "lookup_order", "description": "...", "parameters": {JSON schema}, "side_effect": false,
   "response": "Order {{args.order_id}} was delivered on 2026-11-02",          # default mock reply
   "rules": [{"when": {"order_id": "A-1002"}, "response": "Order not found"}]}  # first matching rule wins
`side_effect: true` marks tools that would change the world (refunds, emails, deletes): calling them is
recorded, never executed, and security scans treat an attacker-induced call as a breach (OWASP LLM06).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from app.agents.assertions import arg_matches
from app.providers import openrouter

_TMPL = re.compile(r"\{\{\s*args\.([A-Za-z0-9_]+)\s*\}\}")
DEFAULT_MAX_STEPS = 6


@dataclass
class AgentRun:
    final: str
    steps: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # [{"name", "args", "output", "side_effect"}]
    cost_usd: float = 0.0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    stopped: str = "answered"  # answered | max_steps

    def trace(self) -> dict:
        return {"steps": self.steps, "stopped": self.stopped, "tool_calls": len(self.calls)}


def tool_schema(tools: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": t["name"], "description": t.get("description") or "",
        "parameters": t.get("parameters") or {"type": "object", "properties": {}},
    }} for t in tools]


def mock_output(tool: dict, args: dict) -> str:
    resp = tool.get("response") or "OK"
    for rule in tool.get("rules") or []:
        when = rule.get("when") or {}
        if all(arg_matches(v, args.get(k)) for k, v in when.items()):
            resp = rule.get("response", resp)
            break
    return _TMPL.sub(lambda m: str(args.get(m.group(1), "")), resp)


def _parse_args(raw: str) -> dict:
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"_value": v}
    except json.JSONDecodeError:
        return {"_raw": (raw or "")[:500]}


async def run_agent(agent: dict, turns: list[str], api_key: str, *, system_prompt: str | None = None,
                    inject_tool_output: str | None = None, before_call=None) -> AgentRun:
    """Run the agent over the user turns. `inject_tool_output` replaces the mock output of the first
    non-side-effect tool call (indirect injection through tool results). `before_call` is awaited before
    every model call (rate limiting / provider slots)."""
    tools = {t["name"]: t for t in agent.get("tools") or []}
    params = {"max_tokens": int((agent.get("params") or {}).get("max_tokens") or 500)}
    temp = (agent.get("params") or {}).get("temperature")
    if temp is not None:
        params["temperature"] = temp
    if tools:
        params["tools"] = tool_schema(list(tools.values()))
    max_steps = int(agent.get("max_steps") or DEFAULT_MAX_STEPS)
    prompt = agent.get("system_prompt") if system_prompt is None else system_prompt
    messages: list[dict] = [{"role": "system", "content": prompt}] if prompt else []
    run = AgentRun(final="")
    injected = inject_tool_output is None
    for turn in turns:
        messages.append({"role": "user", "content": turn})
        run.steps.append({"type": "user", "content": turn})
        for step in range(max_steps + 1):
            if before_call:
                await before_call()
            gen = await openrouter.chat(api_key, agent["model"], messages, params)
            run.cost_usd += gen.cost_usd or 0.0
            run.latency_ms += gen.latency_ms
            run.input_tokens += gen.input_tokens or 0
            run.output_tokens += gen.output_tokens or 0
            if not gen.tool_calls:
                run.final = gen.text
                messages.append({"role": "assistant", "content": gen.text})
                run.steps.append({"type": "assistant", "content": gen.text})
                break
            if step == max_steps:  # the model still wants tools after the step budget
                run.stopped = "max_steps"
                run.final = gen.text or ""
                run.steps.append({"type": "stop", "reason": f"Stopped after {max_steps} tool steps"})
                break
            if gen.text:
                run.steps.append({"type": "assistant", "content": gen.text, "thinking": True})
            calls = [{**c, "id": c.get("id") or f"call_{uuid.uuid4().hex[:12]}"} for c in gen.tool_calls]
            messages.append({"role": "assistant", "content": gen.text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in calls]})
            for c in calls:
                args = _parse_args(c["arguments"])
                tool = tools.get(c["name"])
                rec = {"type": "tool_call", "name": c["name"], "args": args,
                       "side_effect": bool(tool and tool.get("side_effect"))}
                if tool is None:
                    out = f"Error: unknown tool '{c['name']}'"
                    rec["unknown"] = True
                elif not injected and not tool.get("side_effect"):
                    out, injected, rec["injected"] = inject_tool_output, True, True
                else:
                    out = mock_output(tool, args)
                rec["output"] = out
                run.steps.append(rec)
                run.calls.append({"name": c["name"], "args": args, "output": out, "side_effect": rec["side_effect"]})
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
        if run.stopped == "max_steps":
            break
    return run
