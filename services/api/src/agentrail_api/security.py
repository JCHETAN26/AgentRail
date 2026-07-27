"""Runtime security guards backed by Redis.

Redis is deliberately used for short-lived controls only. PostgreSQL remains
the source of truth for durable state; these keys can expire without changing a
run, policy or audit record.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from typing import cast

import redis.asyncio as redis

from agentrail_api.settings import ApiSettings
from agentrail_core.errors import RateLimitedError, ValidationFailedError

_RATE_LIMIT_SCRIPT = """
local count = redis.call("INCR", KEYS[1])
if count == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return count
"""


async def enforce_authenticated_rate_limit(
    client: redis.Redis, settings: ApiSettings, *, actor_kind: str, actor_id: str
) -> None:
    """Apply a fixed-window request cap to one authenticated actor."""
    digest = hashlib.sha256(f"{actor_kind}:{actor_id}".encode()).hexdigest()
    key = f"agentrail:rate:{settings.environment.value}:{digest}"
    result = await cast(
        Awaitable[int | bytes | str],
        client.eval(_RATE_LIMIT_SCRIPT, 1, key, str(settings.api_rate_limit_window_seconds)),
    )
    count = int(result)
    if count > settings.api_rate_limit_requests:
        raise RateLimitedError(
            "Too many requests. Retry after the current rate-limit window.",
            details={
                "limit": settings.api_rate_limit_requests,
                "window_seconds": settings.api_rate_limit_window_seconds,
            },
        )


def _github_delivery_key(settings: ApiSettings, delivery_id: str | None) -> str:
    if delivery_id is None or not delivery_id.strip():
        raise ValidationFailedError("GitHub webhooks must include X-GitHub-Delivery.")
    digest = hashlib.sha256(delivery_id.strip().encode("utf-8")).hexdigest()
    return f"agentrail:github-delivery:{settings.environment.value}:{digest}"


async def reserve_github_delivery(
    client: redis.Redis, settings: ApiSettings, *, delivery_id: str | None
) -> bool:
    """Return true exactly once for each GitHub delivery id in the replay window."""
    key = _github_delivery_key(settings, delivery_id)
    return bool(
        await client.set(
            key,
            "1",
            ex=settings.github_webhook_replay_ttl_seconds,
            nx=True,
        )
    )


async def release_github_delivery(
    client: redis.Redis, settings: ApiSettings, *, delivery_id: str | None
) -> None:
    """Forget a delivery reservation when downstream processing did not commit."""
    await client.delete(_github_delivery_key(settings, delivery_id))
