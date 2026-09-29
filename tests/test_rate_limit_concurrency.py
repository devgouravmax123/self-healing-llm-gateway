"""Concurrency and stress tests for the Redis rate limiter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import RateLimitError
from app.reliability.rate_limiter import RateLimiter
from app.storage.redis import redis_manager


@pytest.mark.asyncio
async def test_rate_limiter_concurrency_simulated() -> None:
    """Verify that concurrent requests enforce rate limits without race conditions."""
    limit = 10
    window_seconds = 60

    stored_entries: list[tuple[int, str]] = []
    lock = asyncio.Lock()

    async def mock_eval(
        script: str,
        numkeys: int,
        key: str,
        now_ms_str: str,
        window_ms_str: str,
        limit_str: str,
        member: str,
    ) -> list[int]:
        async with lock:
            now_ms = int(now_ms_str)
            w_ms = int(window_ms_str)
            lim = int(limit_str)

            # 1. Remove expired
            cutoff = now_ms - w_ms
            nonlocal stored_entries
            stored_entries = [e for e in stored_entries if e[0] > cutoff]

            if len(stored_entries) < lim:
                stored_entries.append((now_ms, member))
                remaining = lim - len(stored_entries)
                return [1, remaining, 0, (now_ms + w_ms) // 1000]
            else:
                oldest_ts = stored_entries[0][0] if stored_entries else now_ms
                retry_after_sec = max(1, ((oldest_ts + w_ms) - now_ms + 999) // 1000)
                return [0, 0, retry_after_sec, (oldest_ts + w_ms) // 1000]

    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = MagicMock()
    mock_client.eval = AsyncMock(side_effect=mock_eval)
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr)

    total_concurrent_requests = 35
    allowed_count = 0
    rejected_count = 0

    async def send_request() -> None:
        nonlocal allowed_count, rejected_count
        try:
            await limiter.check_rate_limit(
                tenant_id="tenant_concurrent",
                limit=limit,
                window_seconds=window_seconds,
            )
            allowed_count += 1
        except RateLimitError:
            rejected_count += 1

    # Launch 35 concurrent requests
    await asyncio.gather(*(send_request() for _ in range(total_concurrent_requests)))

    assert allowed_count == limit
    assert rejected_count == total_concurrent_requests - limit
    assert allowed_count + rejected_count == total_concurrent_requests


@pytest.mark.asyncio
async def test_live_redis_rate_limiting_concurrency() -> None:
    """Live integration test with real Redis (skipped if Redis is offline)."""
    initialized_here = False
    if not redis_manager.is_connected:
        await redis_manager.initialize()
        initialized_here = redis_manager.is_connected

    if not redis_manager.is_connected:
        pytest.skip("Redis server is not available at REDIS_URL")

    try:
        limiter = RateLimiter(redis_mgr=redis_manager)
        tenant_id = "tenant_test_live_concurrency"
        limit = 5
        window_sec = 2

        # Clean up test key first
        client = redis_manager.get_client()
        if client is not None:
            await client.delete(f"ratelimit:{tenant_id}")

        allowed_count = 0
        rejected_count = 0

        async def make_call() -> None:
            nonlocal allowed_count, rejected_count
            try:
                await limiter.check_rate_limit(
                    tenant_id=tenant_id,
                    limit=limit,
                    window_seconds=window_sec,
                )
                allowed_count += 1
            except RateLimitError:
                rejected_count += 1

        await asyncio.gather(*(make_call() for _ in range(15)))

        assert allowed_count == limit
        assert rejected_count == 10
    finally:
        if initialized_here:
            await redis_manager.close()
