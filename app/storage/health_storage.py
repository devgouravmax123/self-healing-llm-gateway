"""Health storage abstraction supporting Redis and in-memory fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio.client import Redis

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)

LATENCY_WINDOW_SIZE = 50
HEALTH_TTL_SECONDS = 604800  # 7 days


@dataclass
class RawProviderHealthData:
    """Raw storage data representation for an individual provider."""

    provider_id: str
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_latency_ms: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error_category: str | None = None
    latencies: list[float] = field(default_factory=list)


class HealthStateStorage(Protocol):
    """Protocol defining the async storage interface for provider health metrics."""

    async def record_attempt(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float,
        category: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        """Record an attempt outcome for a provider atomically."""
        ...

    async def get_health_data(self, provider_id: str) -> RawProviderHealthData:
        """Retrieve raw health data for a specific provider."""
        ...

    async def reset(self, provider_id: str | None = None) -> None:
        """Reset health metrics for a provider or all providers."""
        ...


@dataclass
class InMemoryProviderHealthRecord:
    """Internal mutable in-memory provider health record."""

    provider_id: str
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_latency_ms: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_error_category: str | None = None
    latencies: list[float] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemoryHealthStorage:
    """In-process thread/coroutine-safe provider health storage (fallback & unit tests)."""

    def __init__(
        self,
        time_func: Callable[[], float] | None = None,
        max_latency_window: int = LATENCY_WINDOW_SIZE,
    ) -> None:
        self._time_func = time_func or time.time
        self._max_latency_window = max_latency_window
        self._records: dict[str, InMemoryProviderHealthRecord] = {}
        self._lock = asyncio.Lock()

    def _get_or_create(self, provider_id: str) -> InMemoryProviderHealthRecord:
        if provider_id not in self._records:
            self._records[provider_id] = InMemoryProviderHealthRecord(provider_id=provider_id)
        return self._records[provider_id]

    async def record_attempt(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float,
        category: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        record = self._get_or_create(provider_id)
        current_time = now_epoch if now_epoch is not None else self._time_func()

        async with record.lock:
            record.total_requests += 1
            record.last_latency_ms = round(latency_ms, 2)

            if success:
                record.total_successes += 1
                record.last_success_at = current_time
            else:
                record.total_failures += 1
                record.last_failure_at = current_time
                if category is not None:
                    record.last_error_category = category

            # Rolling latency window (capped at max_latency_window)
            record.latencies.insert(0, round(latency_ms, 2))
            if len(record.latencies) > self._max_latency_window:
                record.latencies = record.latencies[: self._max_latency_window]

    async def get_health_data(self, provider_id: str) -> RawProviderHealthData:
        record = self._get_or_create(provider_id)
        async with record.lock:
            return RawProviderHealthData(
                provider_id=provider_id,
                total_requests=record.total_requests,
                total_successes=record.total_successes,
                total_failures=record.total_failures,
                last_latency_ms=record.last_latency_ms,
                last_success_at=record.last_success_at,
                last_failure_at=record.last_failure_at,
                last_error_category=record.last_error_category,
                latencies=list(record.latencies),
            )

    async def reset(self, provider_id: str | None = None) -> None:
        async with self._lock:
            if provider_id is not None:
                self._records.pop(provider_id, None)
            else:
                self._records.clear()


# --- Lua Script for Atomic Redis Health Updates ---

LUA_RECORD_HEALTH = """
local hash_key = KEYS[1]
local list_key = KEYS[2]

local success = tonumber(ARGV[1])
local latency_ms = tostring(ARGV[2])
local now_epoch = tostring(ARGV[3])
local error_category = ARGV[4]
local window_size = tonumber(ARGV[5])
local ttl_seconds = tonumber(ARGV[6])

-- 1. Increment total requests
redis.call('HINCRBY', hash_key, 'total_requests', 1)

-- 2. Update last latency
redis.call('HSET', hash_key, 'last_latency_ms', latency_ms)

-- 3. Success or Failure updates
if success == 1 then
    redis.call('HINCRBY', hash_key, 'total_successes', 1)
    redis.call('HSET', hash_key, 'last_success_at', now_epoch)
else
    redis.call('HINCRBY', hash_key, 'total_failures', 1)
    redis.call('HSET', hash_key, 'last_failure_at', now_epoch)
    if error_category and error_category ~= '' then
        redis.call('HSET', hash_key, 'last_error_category', error_category)
    end
end

-- 4. Push latency sample and trim to window size
redis.call('LPUSH', list_key, latency_ms)
redis.call('LTRIM', list_key, 0, window_size - 1)

-- 5. Refresh TTLs on both keys
if ttl_seconds > 0 then
    redis.call('EXPIRE', hash_key, ttl_seconds)
    redis.call('EXPIRE', list_key, ttl_seconds)
end

return 1
"""


class RedisHealthStorage:
    """Redis-backed provider health storage using atomic Lua scripts."""

    def __init__(
        self,
        redis_client: Redis[Any],
        config: Settings | None = None,
        time_func: Callable[[], float] | None = None,
        max_latency_window: int = LATENCY_WINDOW_SIZE,
    ) -> None:
        self.client = redis_client
        self.config = config or settings
        self._time_func = time_func or time.time
        self._max_latency_window = max_latency_window
        self._record_script: Any = None

    def _hash_key(self, provider_id: str) -> str:
        return f"llm_gateway:health:{provider_id}"

    def _list_key(self, provider_id: str) -> str:
        return f"llm_gateway:health:{provider_id}:latencies"

    def _get_record_script(self) -> Any:
        if self._record_script is None:
            self._record_script = self.client.register_script(LUA_RECORD_HEALTH)
        return self._record_script

    async def record_attempt(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float,
        category: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        script = self._get_record_script()
        now = now_epoch if now_epoch is not None else self._time_func()
        err_cat = category or ""
        rounded_latency = round(latency_ms, 2)

        await script(
            keys=[self._hash_key(provider_id), self._list_key(provider_id)],
            args=[
                1 if success else 0,
                rounded_latency,
                now,
                err_cat,
                self._max_latency_window,
                HEALTH_TTL_SECONDS,
            ],
        )

    async def get_health_data(self, provider_id: str) -> RawProviderHealthData:
        hash_key = self._hash_key(provider_id)
        list_key = self._list_key(provider_id)

        # Retrieve hash and latency list
        data: dict[str, str] = await self.client.hgetall(hash_key)
        raw_latencies: list[str] = await self.client.lrange(
            list_key, 0, self._max_latency_window - 1
        )

        if not data:
            return RawProviderHealthData(provider_id=provider_id)

        latencies = [float(lat) for lat in raw_latencies if lat]

        return RawProviderHealthData(
            provider_id=provider_id,
            total_requests=int(data.get("total_requests", 0)),
            total_successes=int(data.get("total_successes", 0)),
            total_failures=int(data.get("total_failures", 0)),
            last_latency_ms=float(data["last_latency_ms"]) if "last_latency_ms" in data else None,
            last_success_at=float(data["last_success_at"]) if "last_success_at" in data else None,
            last_failure_at=float(data["last_failure_at"]) if "last_failure_at" in data else None,
            last_error_category=data.get("last_error_category"),
            latencies=latencies,
        )

    async def reset(self, provider_id: str | None = None) -> None:
        if provider_id is not None:
            await self.client.delete(self._hash_key(provider_id), self._list_key(provider_id))
        else:
            # Delete all llm_gateway:health:* keys
            keys = []
            async for k in self.client.scan_iter("llm_gateway:health:*"):
                keys.append(k)
            if keys:
                await self.client.delete(*keys)
