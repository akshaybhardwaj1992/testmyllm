"""Token/cost accounting for judge HTTP calls made by engines that don't report usage (Ragas).

Each metric evaluation runs in its own asyncio Task with its own `Usage` in a ContextVar; an httpx
response hook adds `usage` from every chat/embeddings response to whichever Usage is current.
"""
from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass

import httpx


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reported_cost: float = 0.0
    calls: int = 0
    cost_reported_for_all: bool = True

    def add(self, usage: dict) -> None:
        self.calls += 1
        self.input_tokens += int(usage.get("prompt_tokens") or 0)
        self.output_tokens += int(usage.get("completion_tokens") or 0)
        cost = usage.get("cost")
        if isinstance(cost, (int, float)):
            self.reported_cost += float(cost)
        else:
            self.cost_reported_for_all = False

    def cost(self, price_in: float | None, price_out: float | None) -> float | None:
        if self.calls and self.cost_reported_for_all:
            return self.reported_cost
        if price_in is None or price_out is None:
            return None
        return self.input_tokens * price_in + self.output_tokens * price_out


current_usage: contextvars.ContextVar[Usage | None] = contextvars.ContextVar("judge_usage", default=None)


async def _record(response: httpx.Response) -> None:
    usage = current_usage.get()
    if usage is None or response.status_code >= 400:
        return
    if not response.request.url.path.endswith(("/chat/completions", "/embeddings")):
        return
    await response.aread()
    try:
        body = json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        return
    if isinstance(body, dict) and isinstance(body.get("usage"), dict):
        usage.add(body["usage"])


def tracking_http_client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, event_hooks={"response": [_record]})
