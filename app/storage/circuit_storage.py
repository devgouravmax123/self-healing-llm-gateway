"""Circuit state storage abstraction supporting Redis and in-memory fallback."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from redis.asyncio.client import Redis

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """The three discrete states of a provider circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass
class CircuitSnapshot:
    """Snapshot representation of an individual provider's circuit breaker."""

    provider_id: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_failure_at: float | None = None
    active_probes: int = 0


class CircuitStateStorage(Protocol):
    """Protocol defining the async storage interface for circuit-breaker state."""

    async def acquire_permission(
        self,
        provider_id: str,
        cooldown_seconds: float,
        max_probes: int,
    ) -> tuple[bool, CircuitState, float]:
        """Try to acquire permission for a request.

        Returns:
            (allowed: bool, current_state: CircuitState, remaining_cooldown: float)
        """
        ...

    async def record_success(self, provider_id: str) -> None:
        """Record a successful execution against the provider."""
        ...

    async def record_failure(
        self,
        provider_id: str,
        failure_threshold: int,
    ) -> tuple[CircuitState, int]:
        """Record a failure against the provider.

        Returns:
            (new_state: CircuitState, consecutive_failures: int)
        """
        ...

    async def release_probe(self, provider_id: str) -> None:
        """Release an active probe slot in HALF_OPEN state if execution was aborted or ignored."""
        ...

    async def get_state(self, provider_id: str, cooldown_seconds: float) -> CircuitState:
        """Retrieve current circuit state (evaluating cooldown expiration passively)."""
        ...

    async def reset(self, provider_id: str | None = None) -> None:
        """Reset state for a provider or all providers (primarily for testing)."""
        ...


@dataclass
class InMemoryProviderRecord:
    """Internal mutable in-memory circuit record."""

    provider_id: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    opened_at: float | None = None
    last_failure_at: float | None = None
    active_probes: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class InMemoryCircuitStorage:
    """In-process thread/coroutine-safe circuit state storage (degraded fallback & unit tests)."""

    def __init__(self, time_func: Callable[[], float] | None = None) -> None:
        self._time_func = time_func or time.monotonic
        self._circuits: dict[str, InMemoryProviderRecord] = {}
        self._lock = asyncio.Lock()

    def _get_or_create(self, provider_id: str) -> InMemoryProviderRecord:
        if provider_id not in self._circuits:
            self._circuits[provider_id] = InMemoryProviderRecord(provider_id=provider_id)
        return self._circuits[provider_id]

    async def acquire_permission(
        self,
        provider_id: str,
        cooldown_seconds: float,
        max_probes: int,
    ) -> tuple[bool, CircuitState, float]:
        record = self._get_or_create(provider_id)
        current_time = self._time_func()

        async with record.lock:
            # 1. OPEN state handling
            if record.state == CircuitState.OPEN:
                opened_at = record.opened_at or current_time
                elapsed = current_time - opened_at
                if elapsed >= cooldown_seconds:
                    # Transition to HALF_OPEN and claim probe
                    record.state = CircuitState.HALF_OPEN
                    record.active_probes = 1
                    return True, CircuitState.HALF_OPEN, 0.0
                remaining = cooldown_seconds - elapsed
                return False, CircuitState.OPEN, remaining

            # 2. HALF_OPEN state handling
            if record.state == CircuitState.HALF_OPEN:
                if record.active_probes < max_probes:
                    record.active_probes += 1
                    return True, CircuitState.HALF_OPEN, 0.0
                return False, CircuitState.HALF_OPEN, 0.0

            # 3. CLOSED state handling
            return True, CircuitState.CLOSED, 0.0

    async def record_success(self, provider_id: str) -> None:
        record = self._get_or_create(provider_id)
        async with record.lock:
            if record.state == CircuitState.HALF_OPEN:
                record.state = CircuitState.CLOSED
                record.consecutive_failures = 0
                record.opened_at = None
                record.active_probes = 0
            elif record.state == CircuitState.CLOSED:
                record.consecutive_failures = 0

    async def record_failure(
        self,
        provider_id: str,
        failure_threshold: int,
    ) -> tuple[CircuitState, int]:
        record = self._get_or_create(provider_id)
        current_time = self._time_func()
        async with record.lock:
            record.last_failure_at = current_time
            if record.state == CircuitState.HALF_OPEN:
                record.state = CircuitState.OPEN
                record.opened_at = current_time
                record.active_probes = 0
                return CircuitState.OPEN, record.consecutive_failures
            else:
                record.consecutive_failures += 1
                if record.consecutive_failures >= failure_threshold:
                    record.state = CircuitState.OPEN
                    record.opened_at = current_time
                    record.active_probes = 0
                    return CircuitState.OPEN, record.consecutive_failures
                return CircuitState.CLOSED, record.consecutive_failures

    async def release_probe(self, provider_id: str) -> None:
        record = self._get_or_create(provider_id)
        async with record.lock:
            if record.state == CircuitState.HALF_OPEN:
                record.active_probes = max(0, record.active_probes - 1)

    async def get_state(self, provider_id: str, cooldown_seconds: float) -> CircuitState:
        record = self._get_or_create(provider_id)
        current_time = self._time_func()
        if record.state == CircuitState.OPEN:
            opened_at = record.opened_at or current_time
            if current_time - opened_at >= cooldown_seconds:
                return CircuitState.HALF_OPEN
        return record.state

    async def update_snapshot(
        self,
        provider_id: str,
        state: CircuitState | None = None,
        consecutive_failures: int | None = None,
        opened_at: float | None = None,
        last_failure_at: float | None = None,
        active_probes: int | None = None,
    ) -> None:
        """Update the in-memory record to mirror the latest authoritative Redis state."""
        record = self._get_or_create(provider_id)
        async with record.lock:
            if state is not None:
                record.state = state
            if consecutive_failures is not None:
                record.consecutive_failures = consecutive_failures
            if opened_at is not None:
                record.opened_at = opened_at
            elif state == CircuitState.CLOSED:
                record.opened_at = None
            if last_failure_at is not None:
                record.last_failure_at = last_failure_at
            if active_probes is not None:
                record.active_probes = active_probes

    async def reset(self, provider_id: str | None = None) -> None:
        async with self._lock:
            if provider_id is not None:
                self._circuits.pop(provider_id, None)
            else:
                self._circuits.clear()


# --- Lua Scripts for Atomic Redis State Transitions ---

LUA_ACQUIRE_PERMISSION = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local cooldown = tonumber(ARGV[2])
local max_probes = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])

local state = redis.call('HGET', key, 'state')
if not state or state == 'CLOSED' then
    return {1, 'CLOSED', 0}
end

if state == 'OPEN' then
    local opened_at = tonumber(redis.call('HGET', key, 'opened_at') or '0')
    local elapsed = now - opened_at
    if elapsed >= cooldown then
        redis.call('HSET', key, 'state', 'HALF_OPEN', 'active_probes', 1)
        if ttl_seconds > 0 then
            redis.call('EXPIRE', key, ttl_seconds)
        end
        return {1, 'HALF_OPEN', 0}
    else
        local remaining = cooldown - elapsed
        return {0, 'OPEN', tostring(remaining)}
    end
elseif state == 'HALF_OPEN' then
    local active_probes = tonumber(redis.call('HGET', key, 'active_probes') or '0')
    if active_probes < max_probes then
        redis.call('HINCRBY', key, 'active_probes', 1)
        if ttl_seconds > 0 then
            redis.call('EXPIRE', key, ttl_seconds)
        end
        return {1, 'HALF_OPEN', 0}
    else
        return {0, 'HALF_OPEN', 0}
    end
end
return {1, 'CLOSED', 0}
"""

LUA_RECORD_SUCCESS = """
local key = KEYS[1]
local state = redis.call('HGET', key, 'state')
if state == 'HALF_OPEN' then
    redis.call('HSET', key, 'state', 'CLOSED', 'consecutive_failures', 0, 'active_probes', 0)
    redis.call('HDEL', key, 'opened_at')
elseif state == 'CLOSED' or not state then
    redis.call('HSET', key, 'state', 'CLOSED', 'consecutive_failures', 0)
end
return 1
"""

LUA_RECORD_FAILURE = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local threshold = tonumber(ARGV[2])
local ttl_seconds = tonumber(ARGV[3])

local state = redis.call('HGET', key, 'state')

if state == 'HALF_OPEN' then
    redis.call(
        'HSET', key,
        'state', 'OPEN',
        'opened_at', tostring(now),
        'last_failure_at', tostring(now),
        'active_probes', 0
    )
    if ttl_seconds > 0 then
        redis.call('EXPIRE', key, ttl_seconds)
    end
    local failures = tonumber(redis.call('HGET', key, 'consecutive_failures') or '0')
    return {'OPEN', failures}
else
    local failures = redis.call('HINCRBY', key, 'consecutive_failures', 1)
    redis.call('HSET', key, 'last_failure_at', tostring(now))
    if failures >= threshold then
        redis.call('HSET', key, 'state', 'OPEN', 'opened_at', tostring(now), 'active_probes', 0)
        if ttl_seconds > 0 then
            redis.call('EXPIRE', key, ttl_seconds)
        end
        return {'OPEN', failures}
    else
        return {'CLOSED', failures}
    end
end
"""

LUA_RELEASE_PROBE = """
local key = KEYS[1]
local state = redis.call('HGET', key, 'state')
if state == 'HALF_OPEN' then
    local active_probes = tonumber(redis.call('HGET', key, 'active_probes') or '0')
    if active_probes > 0 then
        redis.call('HINCRBY', key, 'active_probes', -1)
    end
end
return 1
"""


class RedisCircuitStorage:
    """Redis-backed circuit state storage with atomic Lua scripts."""

    def __init__(
        self,
        redis_client: Redis[Any],
        config: Settings | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self.client = redis_client
        self.config = config or settings
        self._time_func = time_func or time.time
        self._acquire_script: Any = None
        self._success_script: Any = None
        self._failure_script: Any = None
        self._release_script: Any = None

    def _key(self, provider_id: str) -> str:
        return f"llm_gateway:circuit:{provider_id}"

    def _get_ttl(self, cooldown_seconds: float) -> int:
        """Calculate deliberate TTL for OPEN/HALF_OPEN keys: 3 * cooldown (min 300s)."""
        return int(max(cooldown_seconds * 3, 300))

    def _get_acquire_script(self) -> Any:
        if self._acquire_script is None:
            self._acquire_script = self.client.register_script(LUA_ACQUIRE_PERMISSION)
        return self._acquire_script

    def _get_success_script(self) -> Any:
        if self._success_script is None:
            self._success_script = self.client.register_script(LUA_RECORD_SUCCESS)
        return self._success_script

    def _get_failure_script(self) -> Any:
        if self._failure_script is None:
            self._failure_script = self.client.register_script(LUA_RECORD_FAILURE)
        return self._failure_script

    def _get_release_script(self) -> Any:
        if self._release_script is None:
            self._release_script = self.client.register_script(LUA_RELEASE_PROBE)
        return self._release_script

    async def acquire_permission(
        self,
        provider_id: str,
        cooldown_seconds: float,
        max_probes: int,
    ) -> tuple[bool, CircuitState, float]:
        script = self._get_acquire_script()
        now = self._time_func()
        ttl = self._get_ttl(cooldown_seconds)
        # Lua returns {allowed (1/0), state_str, remaining_cooldown_str}
        result = await script(
            keys=[self._key(provider_id)],
            args=[now, cooldown_seconds, max_probes, ttl],
        )
        allowed = bool(result[0])
        state = CircuitState(result[1])
        remaining = float(result[2]) if result[2] else 0.0
        return allowed, state, remaining

    async def record_success(self, provider_id: str) -> None:
        script = self._get_success_script()
        await script(keys=[self._key(provider_id)])

    async def record_failure(
        self,
        provider_id: str,
        failure_threshold: int,
    ) -> tuple[CircuitState, int]:
        script = self._get_failure_script()
        now = self._time_func()
        ttl = self._get_ttl(self.config.circuit_cooldown_seconds)
        result = await script(
            keys=[self._key(provider_id)],
            args=[now, failure_threshold, ttl],
        )
        new_state = CircuitState(result[0])
        consecutive_failures = int(result[1])
        return new_state, consecutive_failures

    async def release_probe(self, provider_id: str) -> None:
        script = self._get_release_script()
        await script(keys=[self._key(provider_id)])

    async def get_state(self, provider_id: str, cooldown_seconds: float) -> CircuitState:
        data = await self.client.hgetall(self._key(provider_id))
        if not data:
            return CircuitState.CLOSED
        raw_state = data.get("state", "CLOSED")
        if raw_state == "OPEN":
            opened_at = float(data.get("opened_at", 0.0))
            now = self._time_func()
            if now - opened_at >= cooldown_seconds:
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN
        return CircuitState(raw_state)

    async def reset(self, provider_id: str | None = None) -> None:
        if provider_id is not None:
            await self.client.delete(self._key(provider_id))
        else:
            # Delete all llm_gateway:circuit:* keys
            keys = []
            async for k in self.client.scan_iter("llm_gateway:circuit:*"):
                keys.append(k)
            if keys:
                await self.client.delete(*keys)
