"""SSRF protection for user-configured RAG endpoints (spec §11).

- Only http/https, no credentials in the URL, standard ports unless allowed.
- Every hostname is resolved and EVERY resolved address must be public (no private, loopback,
  link-local, multicast, reserved, CGNAT or unspecified ranges; IPv4-mapped IPv6 is unwrapped).
- Redirects are followed manually (max 3) and each hop is re-validated.
- Optional allow-list of hosts (RAG_HOST_ALLOWLIST); private networks only with RAG_ALLOW_PRIVATE=true
  (local development, e.g. a RAG service in the same compose network).
- Bodies are streamed and capped (MAX_RESPONSE_BYTES); timeouts on connect/read.

Residual risk (decision D21): DNS can change between our check and httpx's own resolution
(rebinding). The window is small and redirects are re-checked; pin-by-IP is a later hardening.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import get_settings

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class BlockedDestination(ValueError):
    """The URL points somewhere a connector may not call."""


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


# Indirection so tests can fake DNS.
resolver: Callable[[str, int], list[str]] = _resolve


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
    )


def _allowlist() -> list[str]:
    raw = get_settings().rag_host_allowlist
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def _host_allowed(host: str, allow: list[str]) -> bool:
    return any(host == a or (a.startswith(".") and host.endswith(a)) for a in allow)


def check_url(url: str) -> None:
    """Raise BlockedDestination unless `url` is safe to call."""
    s = get_settings()
    try:
        parts = urlsplit(url)
    except ValueError as e:
        raise BlockedDestination("Invalid URL") from e
    if parts.scheme not in ("http", "https"):
        raise BlockedDestination("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise BlockedDestination("Put credentials in the auth header, not in the URL")
    host = (parts.hostname or "").lower()
    if not host:
        raise BlockedDestination("URL has no host")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as e:
        raise BlockedDestination("Invalid port") from e
    allow = _allowlist()
    if allow and not _host_allowed(host, allow):
        raise BlockedDestination(f"Host '{host}' is not in the connector allow-list")
    if s.rag_allow_private:
        return
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addrs = [host]  # IP literal: check it as written, no DNS involved
    except ValueError:
        try:
            addrs = resolver(host, port)
        except (socket.gaierror, UnicodeError) as e:
            raise BlockedDestination(f"Cannot resolve host '{host}'") from e
    if not addrs:
        raise BlockedDestination(f"Cannot resolve host '{host}'")
    bad = [a for a in addrs if not _is_public(a)]
    if bad:
        raise BlockedDestination(
            f"'{host}' resolves to a private or reserved address ({bad[0]}). "
            "Connectors may only call public endpoints (set RAG_ALLOW_PRIVATE=true for local development)."
        )


@dataclass
class SafeResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    final_url: str


async def safe_request(
    client: httpx.AsyncClient, method: str, url: str, *, headers: dict[str, str], content: bytes | None,
    timeout: float,
) -> SafeResponse:
    """Request with per-hop SSRF checks, manual redirects and a body size cap."""
    current, m, body = url, method, content
    for _hop in range(MAX_REDIRECTS + 1):
        check_url(current)
        req = client.build_request(m, current, headers=headers, content=body, timeout=timeout)
        resp = await client.send(req, stream=True, follow_redirects=False)
        try:
            if resp.is_redirect and resp.headers.get("location"):
                current = urljoin(current, resp.headers["location"])
                if resp.status_code in (301, 302, 303):  # browsers switch to GET; so do we
                    m, body = "GET", None
                continue
            chunks, size = [], 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise BlockedDestination(f"Response is larger than {MAX_RESPONSE_BYTES // 1024 // 1024} MB")
                chunks.append(chunk)
            return SafeResponse(resp.status_code, dict(resp.headers), b"".join(chunks), current)
        finally:
            await resp.aclose()
    raise BlockedDestination(f"More than {MAX_REDIRECTS} redirects")
