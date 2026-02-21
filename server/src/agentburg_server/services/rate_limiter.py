"""Redis-backed sliding-window rate limiter.

Uses a sorted set per key with timestamps as scores.
Gracefully degrades (allows all traffic) when Redis is unavailable.
"""

import logging
import time

import redis.asyncio as aioredis

from agentburg_server.config import settings

logger = logging.getLogger(__name__)

# Module-level connection pool — initialised in connect() / closed in close()
_pool: aioredis.ConnectionPool | None = None
_client: aioredis.Redis | None = None


async def connect() -> None:
    """Create the async Redis connection pool."""
    global _pool, _client  # noqa: PLW0603
    _pool = aioredis.ConnectionPool.from_url(
        settings.redis_url,
        max_connections=20,
        decode_responses=True,
    )
    _client = aioredis.Redis(connection_pool=_pool)
    # Verify connectivity
    try:
        await _client.ping()
        logger.info("Redis rate-limiter connected (%s)", settings.redis_url)
    except Exception:
        logger.warning("Redis not reachable — rate limiting will be disabled until reconnected")


async def close() -> None:
    """Shut down the Redis connection pool."""
    global _pool, _client  # noqa: PLW0603
    if _client:
        await _client.aclose()
        _client = None
    if _pool:
        await _pool.aclose()
        _pool = None
    logger.info("Redis rate-limiter connection closed")


async def check_rate_limit(key: str, limit: int, window: int) -> bool:
    """Check whether *key* is within the rate limit.

    Uses a sliding-window counter backed by a Redis sorted set.

    Args:
        key: Unique identifier (e.g. ``rl:http:<ip>`` or ``rl:ws:<agent_id>``).
        limit: Maximum number of requests allowed in *window* seconds.
        window: Window size in seconds.

    Returns:
        ``True`` if the request is allowed, ``False`` if rate-limited.
    """
    if _client is None:
        return True  # Graceful degradation

    now = time.time()
    window_start = now - window

    try:
        pipe = _client.pipeline(transaction=True)
        # Remove expired entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Count remaining entries in the window
        pipe.zcard(key)
        # Add current request
        pipe.zadd(key, {f"{now}": now})
        # Set TTL so keys auto-expire
        pipe.expire(key, window + 1)
        results = await pipe.execute()

        current_count: int = results[1]
        return current_count < limit
    except (aioredis.ConnectionError, aioredis.TimeoutError, OSError):
        logger.warning("Redis unavailable — allowing request (graceful degradation)")
        return True
    except Exception:
        logger.exception("Unexpected Redis error in rate limiter")
        return True
