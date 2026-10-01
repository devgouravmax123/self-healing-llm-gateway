"""Provider health tracking service for operational measurement."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from redis.exceptions import RedisError

from app.core.config import Settings, settings
from app.models.responses import ProviderHealthSnapshot
from app.reliability.circuit_breaker import CircuitBreakerManager, circuit_breaker_manager
from app.storage.health_storage import (
    HealthStateStorage,
    InMemoryHealthStorage,
    RawProviderHealthData,
    RedisHealthStorage,
)
from app.storage.redis import RedisManager, redis_manager

logger = logging.getLogger(__name__)


def calculate_percentile(samples: list[float], percentile: float) -> float | None:
    """Calculate a given percentile (0 to 100) from a list of float samples.

    Uses standard nearest-rank method:
        index = ceil(P / 100 * N) - 1
    without external dependencies.
    """
    if not samples:
        return None
    if len(samples) == 1:
        return round(samples[0], 2)

    import math

    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    rank = math.ceil((percentile / 100.0) * n) - 1
    rank_idx = max(0, min(rank, n - 1))
    return round(sorted_samples[rank_idx], 2)


class HealthTracker:
    """Tracks per-attempt provider performance metrics using distributed storage with fallback."""

    def __init__(
        self,
        config: Settings | None = None,
        storage: HealthStateStorage | None = None,
        redis_mgr: RedisManager | None = None,
        circuit_mgr: CircuitBreakerManager | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or settings
        self.redis_mgr = redis_mgr or redis_manager
        self.circuit_mgr = circuit_mgr or circuit_breaker_manager
        self._fallback_storage = InMemoryHealthStorage(time_func=time_func)
        self._explicit_storage = storage
        self._cached_redis_storage: RedisHealthStorage | None = None
        self._time_func = time_func

    def _get_active_storage(self) -> HealthStateStorage:
        """Resolve active health storage backend (Redis if available, else in-memory fallback)."""
        if self._explicit_storage is not None:
            return self._explicit_storage

        redis_client = self.redis_mgr.get_client()
        if redis_client is not None and self.redis_mgr.is_connected:
            if (
                self._cached_redis_storage is None
                or self._cached_redis_storage.client is not redis_client
            ):
                self._cached_redis_storage = RedisHealthStorage(
                    redis_client=redis_client,
                    config=self.config,
                    time_func=self._time_func,
                )
            return self._cached_redis_storage

        return self._fallback_storage

    async def _execute_with_fallback(
        self,
        action_name: str,
        op: Callable[[HealthStateStorage], Any],
    ) -> Any:
        """Execute a health operation against active storage with transparent in-memory fallback."""
        probe = getattr(self.redis_mgr, "probe_recovery", None)
        if probe and not getattr(self.redis_mgr, "is_connected", False):
            res = probe()
            if hasattr(res, "__await__"):
                await res

        active = self._get_active_storage()
        try:
            return await op(active)
        except (RedisError, OSError, Exception) as exc:
            if active is not self._fallback_storage:
                mark_disc = getattr(self.redis_mgr, "mark_disconnected", None)
                if mark_disc:
                    mark_disc()
                logger.warning(
                    "Redis error during health tracker operation '%s': %s. "
                    "Failing back to in-memory health storage (process-local).",
                    action_name,
                    exc,
                )
                return await op(self._fallback_storage)
            raise exc

    async def record_attempt(
        self,
        provider_id: str,
        success: bool,
        latency_ms: float,
        error_category: str | None = None,
        category: str | None = None,
        now_epoch: float | None = None,
    ) -> None:
        """Record an attempt outcome for a provider.

        Catches all errors internally so a health storage issue NEVER breaks an LLM response.
        """
        err_category = error_category or category
        try:

            async def _op(storage: HealthStateStorage) -> None:
                await storage.record_attempt(
                    provider_id=provider_id,
                    success=success,
                    latency_ms=latency_ms,
                    category=err_category,
                    now_epoch=now_epoch,
                )

            await self._execute_with_fallback("record_attempt", _op)
            logger.debug(
                "Recorded health attempt for provider '%s': success=%s, "
                "latency=%.2fms, category=%s",
                provider_id,
                success,
                latency_ms,
                err_category,
            )
        except Exception as exc:
            logger.warning(
                "Failed to record health attempt for provider '%s': %s (safely ignored)",
                provider_id,
                exc,
            )

    async def get_provider_snapshot(
        self,
        provider_id: str,
        circuit_state: str | None = None,
    ) -> ProviderHealthSnapshot:
        """Construct a full health metrics snapshot for a specific provider."""

        async def _op(storage: HealthStateStorage) -> RawProviderHealthData:
            return await storage.get_health_data(provider_id=provider_id)

        try:
            raw_data: RawProviderHealthData = await self._execute_with_fallback(
                "get_health_data", _op
            )
        except Exception as exc:
            logger.warning("Failed to retrieve health data for provider '%s': %s", provider_id, exc)
            raw_data = RawProviderHealthData(provider_id=provider_id)

        # Calculate success rate safely
        if raw_data.total_requests > 0:
            success_rate = round(raw_data.total_successes / raw_data.total_requests, 4)
        else:
            success_rate = 0.0

        # Calculate percentiles from latency samples
        p50 = calculate_percentile(raw_data.latencies, 50.0)
        p95 = calculate_percentile(raw_data.latencies, 95.0)
        p99 = calculate_percentile(raw_data.latencies, 99.0)

        # Retrieve current circuit state if not passed explicitly
        if circuit_state is None:
            c_state_enum = self.circuit_mgr.get_state(provider_id)
            resolved_circuit_state = (
                c_state_enum.value if hasattr(c_state_enum, "value") else str(c_state_enum)
            )
        else:
            resolved_circuit_state = circuit_state

        return ProviderHealthSnapshot(
            provider_id=provider_id,
            total_requests=raw_data.total_requests,
            total_successes=raw_data.total_successes,
            total_failures=raw_data.total_failures,
            success_rate=success_rate,
            last_latency_ms=raw_data.last_latency_ms,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_p99_ms=p99,
            last_success_at=raw_data.last_success_at,
            last_failure_at=raw_data.last_failure_at,
            last_error_category=raw_data.last_error_category,
            circuit_state=resolved_circuit_state,
        )

    async def reset(self, provider_id: str | None = None) -> None:
        """Reset health metrics (primarily for test suite isolation)."""

        async def _op(storage: HealthStateStorage) -> None:
            await storage.reset(provider_id=provider_id)

        await self._execute_with_fallback("reset", _op)


# Global health tracker instance
health_tracker = HealthTracker()

__all__ = [
    "HealthTracker",
    "calculate_percentile",
    "health_tracker",
]
