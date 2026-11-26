"""Per-provider concurrency limit shared by all worker processes (spec §5.5).

A Redis sorted set of slot tokens scored by acquisition time. Tokens older than the unit timeout are
purged on every attempt, so a slot leaked by a killed worker heals itself.

The limiter protects the provider from bursts; it must never fail an evaluation. If Redis is slow or
unavailable the slot is granted without limiting and a warning is logged.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager

from app.core.config import get_settings

log = logging.getLogger(__name__)
MAX_WAIT_S = 300  # give up waiting for a slot after this long and proceed (logged)


@asynccontextmanager
async def provider_slot(provider_id: int | str):
    s = get_settings()
    if s.celery_eager:
        yield
        return
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError

    # ints are provider ids; strings name other upstreams (e.g. "connector:3")
    key = f"testlab:slots:{provider_id if isinstance(provider_id, str) else f'provider:{provider_id}'}"
    token = uuid.uuid4().hex
    ttl = s.unit_timeout_s + 60
    r = aioredis.from_url(s.redis_url, socket_timeout=10, socket_connect_timeout=5)
    acquired = False
    try:
        started = time.monotonic()
        while True:
            try:
                now = time.time()
                await r.zremrangebyscore(key, 0, now - ttl)
                await r.zadd(key, {token: now})
                rank = await r.zrank(key, token)
                if rank is not None and rank < s.provider_max_concurrency:
                    acquired = True
                    break
                await r.zrem(key, token)
            except (RedisError, OSError) as e:
                log.warning("provider limiter unavailable (%s); continuing without limiting", type(e).__name__)
                break
            if time.monotonic() - started > MAX_WAIT_S:
                log.warning("waited %ss for a provider slot; continuing without limiting", MAX_WAIT_S)
                break
            await asyncio.sleep(0.3 + random.random() * 0.7)
        yield
    finally:
        if acquired:
            try:
                await r.zrem(key, token)
            except (RedisError, OSError):
                pass  # the token expires after `ttl` anyway
        try:
            await r.aclose()
        except (RedisError, OSError):
            pass


async def rate_limit(key: str, rpm: int) -> None:
    """Wait until fewer than `rpm` calls were started for `key` in the last 60 s (all workers).
    Fail-open like provider_slot: a limiter problem never fails a scan."""
    s = get_settings()
    if s.celery_eager or rpm <= 0:
        return
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError

    rkey = f"testlab:rate:{key}"
    r = aioredis.from_url(s.redis_url, socket_timeout=10, socket_connect_timeout=5)
    started = time.monotonic()
    try:
        while True:
            try:
                now = time.time()
                await r.zremrangebyscore(rkey, 0, now - 60)
                if await r.zcard(rkey) < rpm:
                    await r.zadd(rkey, {uuid.uuid4().hex: now})
                    await r.expire(rkey, 120)
                    return
            except (RedisError, OSError) as e:
                log.warning("rate limiter unavailable (%s); continuing", type(e).__name__)
                return
            if time.monotonic() - started > MAX_WAIT_S:
                log.warning("waited %ss for the rate limit on %s; continuing", MAX_WAIT_S, key)
                return
            await asyncio.sleep(1 + random.random())
    finally:
        try:
            await r.aclose()
        except (RedisError, OSError):
            pass
