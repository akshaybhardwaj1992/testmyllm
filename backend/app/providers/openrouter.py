"""OpenRouter provider: model-under-test generation, connection test and model pricing.

Reuses the prototype's call shape (OpenAI SDK against https://openrouter.ai/api/v1).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from openai import AsyncOpenAI

from app.core.config import get_settings

_PRICING_TTL_S = 3600
_pricing_cache: tuple[float, dict[str, tuple[float, float]]] | None = None
# id -> {"name", "kind": chat|embedding, "tools": bool, "context": int|None}; filled with the pricing
_model_meta: dict[str, dict] = {}


@dataclass
class Generation:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int
    # function calls requested by the model: [{"id", "name", "arguments" (JSON string)}]
    tool_calls: list[dict] | None = None


def _base_url() -> str:
    return get_settings().openrouter_base_url.rstrip("/")


async def generate(api_key: str, model: str, prompt: str, params: dict | None = None) -> Generation:
    return await chat(api_key, model, [{"role": "user", "content": prompt}], params)


async def chat(api_key: str, model: str, messages: list[dict], params: dict | None = None) -> Generation:
    """One chat completion with a full message list (system prompt, multi-turn history)."""
    params = dict(params or {})
    params.setdefault("max_tokens", 1000)
    client = AsyncOpenAI(
        api_key=api_key, base_url=_base_url(), max_retries=4, timeout=get_settings().provider_timeout_s
    )
    t0 = time.perf_counter()
    try:
        r = await client.chat.completions.create(model=model, messages=messages, **params)
    finally:
        await client.close()
    latency = int((time.perf_counter() - t0) * 1000)
    usage = r.usage
    in_tok = usage.prompt_tokens if usage else None
    out_tok = usage.completion_tokens if usage else None
    cost = getattr(usage, "cost", None) if usage else None
    if cost is None and in_tok is not None:
        price = model_price(model)
        if price:
            cost = in_tok * price[0] + (out_tok or 0) * price[1]
    msg = r.choices[0].message
    calls = [{"id": c.id, "name": c.function.name, "arguments": c.function.arguments or "{}"}
             for c in (getattr(msg, "tool_calls", None) or []) if getattr(c, "function", None)]
    return Generation(msg.content or "", in_tok, out_tok, float(cost) if cost is not None else None, latency,
                      calls or None)


def test_key(api_key: str) -> tuple[bool, str]:
    """Validate a key without spending credits (GET /key)."""
    try:
        r = httpx.get(f"{_base_url()}/key", headers={"Authorization": f"Bearer {api_key}"}, timeout=15)
    except httpx.HTTPError as e:
        return False, f"Could not reach OpenRouter: {type(e).__name__}"
    if r.status_code == 200:
        data = r.json().get("data", {})
        label = data.get("label") or "key"
        limit = data.get("limit_remaining")
        extra = f", {limit:.2f} USD credit remaining" if isinstance(limit, (int, float)) else ""
        return True, f"Connected ({label}{extra})"
    if r.status_code in (401, 403):
        return False, "OpenRouter rejected the key"
    return False, f"OpenRouter returned HTTP {r.status_code}"


def _load_pricing() -> dict[str, tuple[float, float]]:
    global _pricing_cache
    now = time.time()
    if _pricing_cache and now - _pricing_cache[0] < _PRICING_TTL_S:
        return _pricing_cache[1]
    prices: dict[str, tuple[float, float]] = {}
    try:
        for path in ("/models", "/embeddings/models"):
            r = httpx.get(f"{_base_url()}{path}", timeout=15)
            if r.status_code != 200:
                continue
            for m in r.json().get("data", []):
                _model_meta[m.get("id", "")] = {
                    "name": m.get("name") or m.get("id", ""),
                    "kind": "embedding" if path.startswith("/embeddings") else "chat",
                    "tools": "tools" in (m.get("supported_parameters") or []),
                    "context": m.get("context_length"),
                }
                p = m.get("pricing") or {}
                try:
                    prices[m["id"]] = (float(p.get("prompt", 0)), float(p.get("completion", 0)))
                except (TypeError, ValueError, KeyError):
                    continue
    except httpx.HTTPError:
        if _pricing_cache:
            return _pricing_cache[1]
        return {}
    _pricing_cache = (now, prices)
    return prices


def model_meta(model: str) -> dict:
    _load_pricing()
    return _model_meta.get(model, {})


def model_price(model: str) -> tuple[float, float] | None:
    """(USD per input token, USD per output token) or None if unknown."""
    return _load_pricing().get(model)


def set_pricing_for_tests(prices: dict[str, tuple[float, float]]) -> None:
    global _pricing_cache
    _pricing_cache = (time.time() + 10**9, prices)


FAMILY_KEYWORDS = {
    "anthropic": ("claude", "anthropic"),
    "openai": ("gpt", "chatgpt", "openai", "o1", "o3", "o4"),
    "google": ("gemini", "gemma", "google"),
    "meta-llama": ("llama", "meta"),
    "mistralai": ("mistral", "mixtral"),
    "deepseek": ("deepseek",),
    "x-ai": ("grok",),
    "qwen": ("qwen",),
}


def model_family(model_or_label: str | None) -> str | None:
    """'openai/gpt-4o-mini' -> 'openai'; 'Claude' -> 'anthropic'. None if unknown."""
    if not model_or_label:
        return None
    s = model_or_label.strip().lower()
    if "/" in s:
        vendor = s.split("/", 1)[0]
        return vendor
    for fam, words in FAMILY_KEYWORDS.items():
        if any(s == w or s.startswith(w + "-") or s.startswith(w + " ") or w in s.split() for w in words):
            return fam
    return None
