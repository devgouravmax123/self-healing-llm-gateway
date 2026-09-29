"""Redis-backed atomic sliding window log rate limiter."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.config import Settings, settings
from app.core.exceptions import RateLimitError
from app.observability.metrics import GatewayMetrics, gateway_metrics
from app.storage.redis import RedisManager, redis_manager

logger = logging.getLogger(__name__)

# Redis Lua Script for atomic Sliding Window Log rate limiting
# KEYS[1]: ratelimit:{tenant_id}
# ARGV[1]: current_time_ms (number)
# ARGV[2]: window_size_ms (number)
# ARGV[3]: limit (number)
# ARGV[4]: server_member_id (unique string)
# Returns: {allowed (1/0), remaining (int), retry_after_seconds (int), reset_timestamp_sec (int)}
SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

local clear_before = now - window_ms

-- 1. Remove expired entries older than the current sliding window
redis.call('ZREMRANGEBYSCORE', key, 0, clear_before)

-- 2. Count current active requests in window
local current_count = redis.call('ZCARD', key)

if current_count < limit then
    -- Request is allowed: record member with current timestamp
    redis.call('ZADD', key, now, member)
    -- Extend TTL on the sorted set
    redis.call('PEXPIRE', key, window_ms + 2000)
    local remaining = limit - current_count - 1
    local reset_sec = math.floor((now + window_ms) / 1000)
    return {1, remaining, 0, reset_sec}
else
    -- Request is rejected: find oldest entry to calculate precise retry-after
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after_sec = 1
    local reset_sec = math.floor((now + window_ms) / 1000)
    if oldest and #oldest >= 2 then
        local oldest_ts = tonumber(oldest[2])
        local wait_ms = (oldest_ts + window_ms) - now
        if wait_ms > 0 then
            retry_after_sec = math.ceil(wait_ms / 1000)
            reset_sec = math.floor((oldest_ts + window_ms) / 1000)
        end
    end
    return {0, 0, retry_after_sec, reset_sec}
end
"""


class RateLimiter:
    """Manages tenant rate limits using an atomic Redis sliding window log with fallback."""

    def __init__(
        self,
        config: Settings | None = None,
        redis_mgr: RedisManager | None = None,
        time_func: Any = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self.config = config or settings
        self.redis_mgr = redis_mgr or redis_manager
        self.time_func = time_func or time.time
        self.metrics = metrics or gateway_metrics

    async def check_rate_limit(
        self,
        tenant_id: str,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        """Check rate limit for the authenticated tenant.

        Raises:
            RateLimitError: if tenant has exceeded configured rate limit.

        Behavior:
            If Redis is down/unreachable, fails open for authenticated requests,
            logging a structured warning without failing client inference.
        """
        effective_limit = limit if limit is not None else self.config.default_tenant_rpm
        effective_window = (
            window_seconds if window_seconds is not None else self.config.rate_limit_window_seconds
        )
        window_ms = effective_window * 1000
        now_ms = int(self.time_func() * 1000)

        # Generate server-side unique member to prevent client request-ID collision / bypass
        server_member_id = f"{uuid4().hex}_{now_ms}"
        redis_key = f"ratelimit:{tenant_id}"

        redis_client = self.redis_mgr.get_client()

        if redis_client is None or not self.redis_mgr.is_connected:
            logger.warning(
                "rate_limit.redis_unavailable: Redis is unreachable. "
                "Failing open for tenant_id=%s (limit=%d, window=%ds)",
                tenant_id,
                effective_limit,
                effective_window,
            )
            return

        try:
            result: Any = await redis_client.eval(  # type: ignore[no-untyped-call]
                SLIDING_WINDOW_LUA,
                1,
                redis_key,
                str(now_ms),
                str(window_ms),
                str(effective_limit),
                server_member_id,
            )

            # result is [allowed (1/0), remaining (int), retry_after_sec (int), reset_sec (int)]

            allowed = bool(result[0])
            remaining = int(result[1])
            retry_after = int(result[2])
            reset_time = int(result[3])

            if allowed:
                logger.debug(
                    "rate_limit.allowed: tenant_id=%s, limit=%d, remaining=%d",
                    tenant_id,
                    effective_limit,
                    remaining,
                )
                return

            # Record rate limit rejection metric (Phase 14.3b)
            self.metrics.rate_limit_rejections_total.labels(reason="limit_exceeded").inc()

            logger.warning(
                "rate_limit.rejected: tenant_id=%s exceeded limit=%d (retry_after=%ds)",
                tenant_id,
                effective_limit,
                retry_after,
            )
            raise RateLimitError(
                message=f"Rate limit exceeded. Please retry after {retry_after} seconds.",
                status_code=429,
                retry_after=retry_after,
                limit=effective_limit,
                remaining=0,
                reset_time=reset_time,
                details={
                    "tenant_id": tenant_id,
                    "retry_after_seconds": retry_after,
                    "limit": effective_limit,
                    "window_seconds": effective_window,
                },
            )

        except RateLimitError:
            raise
        except (RedisError, OSError, Exception) as exc:
            logger.warning(
                "rate_limit.redis_unavailable: Error executing rate limiter for tenant_id=%s: %s. "
                "Failing open.",
                tenant_id,
                exc,
            )
            return


# Global rate limiter singleton
rate_limiter = RateLimiter()
