"""Redis-backed task delivery.

Redis is *only* a delivery mechanism. It is explicitly not the source of truth
for job state: a job row is committed to PostgreSQL before its identifier is
published here, and the worker re-reads the row before doing any work. Losing
the Redis database therefore delays jobs but never loses them.
"""

from __future__ import annotations

import redis.asyncio as redis
from redis.exceptions import RedisError

from agentrail_core.errors import DependencyUnavailableError
from agentrail_core.settings import QueueSettings


def create_redis_client(settings: QueueSettings) -> redis.Redis:
    client: redis.Redis = redis.Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=5,
        # Must exceed the blocking pop timeout, or every idle poll looks like a
        # socket failure.
        socket_timeout=settings.queue_block_timeout_seconds + 5,
        health_check_interval=30,
    )
    return client


async def check_redis(client: redis.Redis) -> None:
    """Raise :class:`DependencyUnavailableError` if Redis is not usable."""
    try:
        await client.ping()
    except RedisError as exc:  # pragma: no cover - exercised in integration tests
        raise DependencyUnavailableError(
            "Redis is not reachable", details={"dependency": "redis"}
        ) from exc


async def publish_job(client: redis.Redis, queue_key: str, job_id: str) -> None:
    """Append a job identifier to the pending queue.

    Delivery is at-least-once: this may be retried after a partial failure, and
    the consumer must therefore be idempotent.
    """
    await client.rpush(queue_key, job_id)  # type: ignore[misc]


async def consume_job(
    client: redis.Redis, queue_key: str, *, block_timeout_seconds: int
) -> str | None:
    """Block for the next job identifier, returning ``None`` on timeout."""
    result = await client.blpop([queue_key], timeout=block_timeout_seconds)  # type: ignore[misc]
    if result is None:
        return None
    _key, job_id = result
    return str(job_id)


async def queue_depth(client: redis.Redis, queue_key: str) -> int:
    return int(await client.llen(queue_key))  # type: ignore[misc]
