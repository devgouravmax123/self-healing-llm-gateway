"""Unit tests for the Redis sliding window log rate limiter."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import RateLimitError
from app.reliability.rate_limiter import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit() -> None:
    """Verify requests under limit pass without raising exceptions."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    # Lua script returns: [allowed=1, remaining=9, retry_after=0, reset_sec=1700000000]
    mock_client.eval = AsyncMock(return_value=[1, 9, 0, 1700000000])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr)
    # Should not raise
    await limiter.check_rate_limit(tenant_id="tenant_123", limit=10, window_seconds=60)
    assert mock_client.eval.called


@pytest.mark.asyncio
async def test_rate_limiter_rejects_exceeded_limit() -> None:
    """Verify requests exceeding limit raise RateLimitError (429) with correct retry-after."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    # Lua script returns: [allowed=0, remaining=0, retry_after=14, reset_sec=1700000014]
    mock_client.eval = AsyncMock(return_value=[0, 0, 14, 1700000014])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr)
    with pytest.raises(RateLimitError) as exc_info:
        await limiter.check_rate_limit(tenant_id="tenant_123", limit=10, window_seconds=60)

    err = exc_info.value
    assert err.status_code == 429
    assert err.retry_after == 14
    assert err.limit == 10
    assert err.remaining == 0
    assert err.reset_time == 1700000014


@pytest.mark.asyncio
async def test_rate_limiter_redis_unavailable_fails_open() -> None:
    """Verify rate limiter fails open (does not raise) when Redis is disconnected or fails."""
    # Case 1: Redis client is None / is_connected is False
    mock_redis_mgr1 = MagicMock()
    mock_redis_mgr1.is_connected = False
    mock_redis_mgr1.get_client = MagicMock(return_value=None)

    limiter1 = RateLimiter(redis_mgr=mock_redis_mgr1)
    # Should complete without error
    await limiter1.check_rate_limit(tenant_id="tenant_123")

    # Case 2: Redis eval raises an exception (e.g. connection timeout)
    mock_redis_mgr2 = MagicMock()
    mock_redis_mgr2.is_connected = True
    mock_client2 = AsyncMock()
    mock_client2.eval = AsyncMock(side_effect=RuntimeError("Redis connection dropped"))
    mock_redis_mgr2.get_client = MagicMock(return_value=mock_client2)

    limiter2 = RateLimiter(redis_mgr=mock_redis_mgr2)
    # Should fail open without raising error
    await limiter2.check_rate_limit(tenant_id="tenant_123")


@pytest.mark.asyncio
async def test_rate_limiter_uses_unique_server_member_ids() -> None:
    """Verify that each rate limit check generates a unique server-side member ID."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    mock_client.eval = AsyncMock(return_value=[1, 5, 0, 1700000000])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr)

    await limiter.check_rate_limit(tenant_id="tenant_123")
    await limiter.check_rate_limit(tenant_id="tenant_123")

    call1_args = mock_client.eval.call_args_list[0][0]
    call2_args = mock_client.eval.call_args_list[1][0]

    # ARGV[4] is the server member ID passed to eval
    member1 = call1_args[6]
    member2 = call2_args[6]

    assert member1 != member2
