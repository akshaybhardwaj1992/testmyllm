"""Minimal HTTP client for the TestMyLLM API (standard library only), authenticated with a project API token."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from testmyllm_mcp.tools import ToolError


class Client:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: float = 60):
        self.base = (base_url or os.environ.get("TESTMYLLM_URL") or "http://localhost:8080").rstrip("/") + "/api/v1"
        self.token = token or os.environ.get("TESTMYLLM_TOKEN") or ""
        self.timeout = timeout
        if not self.token:
            raise ToolError("Set TESTMYLLM_TOKEN to a project API token (Settings → Integrations → API tokens)")

    def request(self, method: str, path: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
            "Accept": "application/json", "User-Agent": "testmyllm-mcp/0.1",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - user-configured URL
                payload = resp.read()
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
                msg = f"{err.get('message')} [{err.get('code')}]"
                if err.get("details"):
                    msg += f" {json.dumps(err['details'])[:400]}"
            except (ValueError, AttributeError):
                msg = str(e.reason)
            raise ToolError(f"{method} {path} -> HTTP {e.code}: {msg}") from e
        except urllib.error.URLError as e:
            raise ToolError(f"Cannot reach TestMyLLM at {self.base}: {e.reason}") from e
        return json.loads(payload) if payload else None
