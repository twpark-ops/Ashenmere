"""Tests for the Redis-backed sliding-window rate limiter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentburg_server.services import rate_limiter

# ---------------------------------------------------------------------------
# Unit tests — rate limiter logic with mock Redis
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Ensure a clean module state for each test."""
    original_client = rate_limiter._client
    original_pool = rate_limiter._pool
    yield
    rate_limiter._client = original_client
    rate_limiter._pool = original_pool


class TestCheckRateLimit:
    """Core sliding-window rate limiter logic."""

    async def test_allows_request_under_limit(self):
        """Requests under the limit should be allowed."""
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
        mock_pipe.zcard = MagicMock(return_value=mock_pipe)
        mock_pipe.zadd = MagicMock(return_value=mock_pipe)
        mock_pipe.expire = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(return_value=[0, 3, True, True])  # count=3

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        rate_limiter._client = mock_client

        result = await rate_limiter.check_rate_limit("test:key", limit=10, window=60)
        assert result is True

    async def test_blocks_request_over_limit(self):
        """Requests at or over the limit should be blocked."""
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
        mock_pipe.zcard = MagicMock(return_value=mock_pipe)
        mock_pipe.zadd = MagicMock(return_value=mock_pipe)
        mock_pipe.expire = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(return_value=[0, 10, True, True])  # count=10, limit=10

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        rate_limiter._client = mock_client

        result = await rate_limiter.check_rate_limit("test:key", limit=10, window=60)
        assert result is False

    async def test_allows_when_count_exactly_one_below_limit(self):
        """Count == limit-1 should still be allowed."""
        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
        mock_pipe.zcard = MagicMock(return_value=mock_pipe)
        mock_pipe.zadd = MagicMock(return_value=mock_pipe)
        mock_pipe.expire = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(return_value=[0, 4, True, True])  # count=4, limit=5

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        rate_limiter._client = mock_client

        result = await rate_limiter.check_rate_limit("test:key", limit=5, window=1)
        assert result is True


class TestGracefulDegradation:
    """Rate limiter should allow all traffic when Redis is unavailable."""

    async def test_allows_when_client_is_none(self):
        """No Redis client → allow request."""
        rate_limiter._client = None

        result = await rate_limiter.check_rate_limit("test:key", limit=1, window=1)
        assert result is True

    async def test_allows_on_connection_error(self):
        """Redis connection error → allow request."""
        import redis.asyncio as aioredis

        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
        mock_pipe.zcard = MagicMock(return_value=mock_pipe)
        mock_pipe.zadd = MagicMock(return_value=mock_pipe)
        mock_pipe.expire = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(side_effect=aioredis.ConnectionError("connection refused"))

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        rate_limiter._client = mock_client

        result = await rate_limiter.check_rate_limit("test:key", limit=1, window=1)
        assert result is True

    async def test_allows_on_timeout_error(self):
        """Redis timeout → allow request."""
        import redis.asyncio as aioredis

        mock_pipe = MagicMock()
        mock_pipe.zremrangebyscore = MagicMock(return_value=mock_pipe)
        mock_pipe.zcard = MagicMock(return_value=mock_pipe)
        mock_pipe.zadd = MagicMock(return_value=mock_pipe)
        mock_pipe.expire = MagicMock(return_value=mock_pipe)
        mock_pipe.execute = AsyncMock(side_effect=aioredis.TimeoutError())

        mock_client = MagicMock()
        mock_client.pipeline = MagicMock(return_value=mock_pipe)

        rate_limiter._client = mock_client

        result = await rate_limiter.check_rate_limit("test:key", limit=1, window=1)
        assert result is True


class TestConnectClose:
    """Lifecycle management of the Redis pool."""

    @patch("agentburg_server.services.rate_limiter.aioredis")
    async def test_connect_creates_pool_and_client(self, mock_aioredis):
        """connect() should create a pool and Redis client."""
        mock_pool = MagicMock()
        mock_aioredis.ConnectionPool.from_url.return_value = mock_pool

        mock_redis_instance = AsyncMock()
        mock_aioredis.Redis.return_value = mock_redis_instance

        rate_limiter._client = None
        rate_limiter._pool = None

        await rate_limiter.connect()

        mock_aioredis.ConnectionPool.from_url.assert_called_once()
        mock_aioredis.Redis.assert_called_once_with(connection_pool=mock_pool)
        mock_redis_instance.ping.assert_awaited_once()

    async def test_close_cleans_up(self):
        """close() should shut down client and pool."""
        mock_client = AsyncMock()
        mock_pool = AsyncMock()

        rate_limiter._client = mock_client
        rate_limiter._pool = mock_pool

        await rate_limiter.close()

        mock_client.aclose.assert_awaited_once()
        mock_pool.aclose.assert_awaited_once()
        assert rate_limiter._client is None
        assert rate_limiter._pool is None


# ---------------------------------------------------------------------------
# Integration-level: HTTP middleware rate limiting
# ---------------------------------------------------------------------------


class TestHTTPRateLimitMiddleware:
    """Test the rate limit middleware via the FastAPI test client."""

    async def test_returns_429_when_rate_limited(self, test_client):
        """HTTP requests should receive 429 when over the limit."""
        with patch(
            "agentburg_server.services.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = await test_client.get("/api/v1/world/state")
            assert resp.status_code == 429
            assert resp.json()["detail"] == "Too many requests"

    async def test_allows_when_under_limit(self, test_client):
        """HTTP requests should pass through when under the limit."""
        with patch(
            "agentburg_server.services.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=True,
        ):
            resp = await test_client.get("/health")
            # /health is exempt from rate limiting, should always succeed
            assert resp.status_code == 200

    async def test_health_exempt_from_rate_limiting(self, test_client):
        """Health endpoint should never be rate-limited."""
        with patch(
            "agentburg_server.services.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = await test_client.get("/health")
            assert resp.status_code == 200

    async def test_metrics_exempt_from_rate_limiting(self, test_client):
        """Metrics endpoint should never be rate-limited."""
        with patch(
            "agentburg_server.services.rate_limiter.check_rate_limit",
            new_callable=AsyncMock,
            return_value=False,
        ):
            resp = await test_client.get("/metrics")
            assert resp.status_code == 200
