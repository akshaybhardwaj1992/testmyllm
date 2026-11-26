"""Call a user's RAG endpoint and map its JSON response to (answer, retrieved contexts).

Request template: a JSON document where the string "{{question}}" is replaced by the question
(JSON-safely; the template is parsed, never string-concatenated). POST sends it as the JSON body;
GET sends a flat object as query parameters.

Response mapping (JSON paths, dot-separated; `[n]` indexes; `[*]` maps over a list):
  answer_path        e.g. "answer", "data.output.text", "choices[0].message.content"
  contexts_path      e.g. "contexts", "sources[*]", "result.documents[*].page_content"
  context_text_path  optional, applied to each context object, e.g. "text" or "metadata.chunk"
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.redact import redact
from app.rag.ssrf import BlockedDestination, safe_request

PLACEHOLDER = "{{question}}"
_SEG = re.compile(r"^([^\[\]]*)((?:\[(?:\d+|\*)\])*)$")
_IDX = re.compile(r"\[(\d+|\*)\]")
_TEXT_KEYS = ("text", "content", "page_content", "chunk", "body", "snippet", "passage")


class ConnectorError(RuntimeError):
    """The endpoint could not be called or its response could not be mapped."""


@dataclass
class ConnectorConfig:
    endpoint_url: str
    method: str = "POST"
    auth_header_name: str = ""
    auth_value: str = ""
    request_template: str = '{"question": "{{question}}"}'
    answer_path: str = "answer"
    contexts_path: str = ""
    context_text_path: str = ""
    timeout_s: float = 30.0


@dataclass
class ConnectorResult:
    answer: str
    contexts: list[str] | None
    latency_ms: int
    status_code: int
    warnings: list[str] = field(default_factory=list)
    raw_preview: str = ""


# ------------------------------------------------------------------ template
def validate_template(template: str, method: str) -> None:
    try:
        doc = json.loads(template)
    except json.JSONDecodeError as e:
        raise ValueError(f"Request template is not valid JSON (line {e.lineno}: {e.msg})") from e
    if PLACEHOLDER not in template:
        raise ValueError("Request template must contain {{question}}")
    if method == "GET" and (not isinstance(doc, dict) or any(isinstance(v, (dict, list)) for v in doc.values())):
        raise ValueError("For GET, the template must be a flat JSON object (sent as query parameters)")


def render(template: str, question: str, variables: dict[str, str] | None = None) -> Any:
    """Fill {{question}} (and any {{name}} in `variables`, e.g. {{user}} for security identities)."""
    subs = {PLACEHOLDER: question, **{"{{" + k + "}}": v for k, v in (variables or {}).items()}}

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            if node in subs:
                return subs[node]
            for k, v in subs.items():
                node = node.replace(k, v)
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        return node

    return walk(json.loads(template))


# ------------------------------------------------------------------ JSON paths
def validate_path(path: str) -> None:
    if not path:
        return
    for seg in path.split("."):
        if not seg or not _SEG.match(seg):
            raise ValueError(f"Invalid path segment '{seg}' in '{path}'")


def extract(obj: Any, path: str) -> list[Any]:
    """All values at `path` (wildcards flatten). Missing keys yield an empty list."""
    if not path:
        return [obj]
    current = [obj]
    for seg in path.split("."):
        m = _SEG.match(seg)
        if not m:
            raise ValueError(f"Invalid path segment '{seg}'")
        key, idx = m.group(1), _IDX.findall(m.group(2))
        nxt: list[Any] = []
        for node in current:
            if key:
                if not isinstance(node, dict) or key not in node:
                    continue
                node = node[key]
            items = [node]
            for i in idx:
                expanded = []
                for it in items:
                    if not isinstance(it, list):
                        continue
                    if i == "*":
                        expanded.extend(it)
                    elif int(i) < len(it):
                        expanded.append(it[int(i)])
                items = expanded
            nxt.extend(items)
        current = nxt
    return current


def _as_text(v: Any, text_path: str) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, (int, float, bool)):
        return str(v)
    if text_path:
        found = extract(v, text_path)
        return _as_text(found[0], "") if found else None
    if isinstance(v, dict):
        for k in _TEXT_KEYS:
            if isinstance(v.get(k), str):
                return v[k].strip() or None
    return json.dumps(v, ensure_ascii=False)


def map_response(data: Any, cfg: ConnectorConfig) -> tuple[str, list[str] | None, list[str]]:
    warnings: list[str] = []
    answers = extract(data, cfg.answer_path)
    if not answers:
        raise ConnectorError(f"No value at answer path '{cfg.answer_path}'")
    answer = _as_text(answers[0], "")
    if not answer:
        raise ConnectorError(f"The value at answer path '{cfg.answer_path}' is empty")
    contexts: list[str] | None = None
    if cfg.contexts_path:
        found = extract(data, cfg.contexts_path)
        if len(found) == 1 and isinstance(found[0], list):  # path pointed at the list itself
            found = found[0]
        if not found:
            warnings.append(f"No contexts at '{cfg.contexts_path}': this answer had no retrieved context")
            contexts = []
        else:
            contexts = [t for t in (_as_text(x, cfg.context_text_path) for x in found) if t]
            if len(contexts) < len(found):
                warnings.append(f"{len(found) - len(contexts)} context item(s) had no text and were skipped")
    return answer, contexts, warnings


# ------------------------------------------------------------------ call
def _transport() -> httpx.AsyncBaseTransport | None:
    """Overridden in tests with an httpx.MockTransport."""
    return None


async def call(cfg: ConnectorConfig, question: str, variables: dict[str, str] | None = None) -> ConnectorResult:
    payload = render(cfg.request_template, question, variables)
    headers = {"Accept": "application/json", "User-Agent": "testmyllm-rag-connector/1"}
    if cfg.auth_header_name and cfg.auth_value:
        headers[cfg.auth_header_name] = cfg.auth_value
    url, content = cfg.endpoint_url, None
    if cfg.method == "GET":
        url = str(httpx.URL(cfg.endpoint_url).copy_merge_params({k: str(v) for k, v in payload.items()}))
    else:
        headers["Content-Type"] = "application/json"
        content = json.dumps(payload).encode()
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(transport=_transport(), timeout=cfg.timeout_s) as client:
            resp = await safe_request(client, cfg.method, url, headers=headers, content=content, timeout=cfg.timeout_s)
    except BlockedDestination as e:
        raise ConnectorError(str(e)) from e
    except httpx.TimeoutException as e:
        raise ConnectorError(f"Endpoint timed out after {cfg.timeout_s:.0f}s") from e
    except httpx.HTTPError as e:
        raise ConnectorError(f"Could not reach the endpoint: {type(e).__name__}") from e
    latency = int((time.perf_counter() - t0) * 1000)
    preview = redact(resp.body[:2000].decode("utf-8", errors="replace"))
    if resp.status_code >= 400:
        raise ConnectorError(f"Endpoint returned HTTP {resp.status_code}: {preview[:300]}")
    try:
        data = json.loads(resp.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConnectorError(f"Endpoint did not return JSON: {preview[:200]}") from e
    answer, contexts, warnings = map_response(data, cfg)
    return ConnectorResult(answer, contexts, latency, resp.status_code, warnings, preview)
