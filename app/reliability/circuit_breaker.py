"""Circuit Breaker reliability component for upstream LLM providers."""

import logging
from collections.abc import Callable
from typing import Any

from redis.exceptions import RedisError

from app.core.config import Settings, settings
from app.core.exceptions import CircuitBreakerError, ProviderError
from app.observability.metrics import GatewayMetrics, gateway_metrics
from app.reliability.error_classifier import ErrorCategory
from app.storage.circuit_storage import (
    CircuitState,
    CircuitStateStorage,
    InMemoryCircuitStorage,
    RedisCircuitStorage,
)
from app.storage.redis import RedisManager, redis_manager

logger = logging.getLogger(__name__)

# Non-provider categories that represent client errors or non-health issues
NON_CIRCUIT_FAILURE_CATEGORIES: set[ErrorCategory] = {
    ErrorCategory.AUTH_ERROR,
    ErrorCategory.BAD_REQUEST,
}


class CircuitBreakerManager:
    """Manages provider-specific circuit breakers using distributed storage with local fallback."""

    def __init__(
        self,
        config: Settings | None = None,
        storage: CircuitStateStorage | None = None,
        redis_mgr: RedisManager | None = None,
        time_func: Callable[[], float] | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self.config = config or settings
        self.redis_mgr = redis_mgr or redis_manager
        self._fallback_storage = InMemoryCircuitStorage(time_func=time_func)
        self._explicit_storage = storage
        self._cached_redis_storage: RedisCircuitStorage | None = None
        self._time_func = time_func
        self.metrics = metrics or gateway_metrics

    def set_circuit_metric(self, provider_id: str, state: CircuitState) -> None:
        """Update the one-hot Prometheus circuit state gauge for a provider."""
        self.metrics.circuit_state.labels(
            provider=provider_id,
            state="closed",
        ).set(1.0 if state == CircuitState.CLOSED else 0.0)
        self.metrics.circuit_state.labels(
            provider=provider_id,
            state="open",
        ).set(1.0 if state == CircuitState.OPEN else 0.0)
        self.metrics.circuit_state.labels(
            provider=provider_id,
            state="half_open",
        ).set(1.0 if state == CircuitState.HALF_OPEN else 0.0)

    def _get_active_storage(self) -> CircuitStateStorage:
        """Resolve the active storage backend (Redis if available, else in-memory fallback)."""
        if self._explicit_storage is not None:
            return self._explicit_storage

        redis_client = self.redis_mgr.get_client()
        if redis_client is not None and self.redis_mgr.is_connected:
            if (
                self._cached_redis_storage is None
                or self._cached_redis_storage.client is not redis_client
            ):
                self._cached_redis_storage = RedisCircuitStorage(
                    redis_client=redis_client,
                    config=self.config,
                    time_func=self._time_func,
                )
            return self._cached_redis_storage

        return self._fallback_storage

    async def _execute_with_fallback(
        self,
        action_name: str,
        redis_op: Callable[[CircuitStateStorage], Any],
    ) -> Any:
        """Execute a circuit operation against the active storage.

        Falls back to local memory on Redis errors.
        """
        active = self._get_active_storage()
        try:
            return await redis_op(active)
        except (RedisError, OSError, Exception) as exc:
            if active is not self._fallback_storage:
                logger.warning(
                    "Redis error during circuit breaker operation '%s': %s. "
                    "Failing back to in-memory circuit storage (process-local).",
                    action_name,
                    exc,
                )
                return await redis_op(self._fallback_storage)
            raise exc

    async def acquire_permission(self, provider_id: str) -> None:
        """Check if a request is allowed to execute against the specified provider.

        Raises:
            CircuitBreakerError: If the circuit is OPEN or HALF_OPEN probe capacity is exhausted.
        """
        cooldown = self.config.circuit_cooldown_seconds
        max_probes = self.config.circuit_half_open_max_probes

        async def _op(storage: CircuitStateStorage) -> tuple[bool, CircuitState, float]:
            return await storage.acquire_permission(
                provider_id=provider_id,
                cooldown_seconds=cooldown,
                max_probes=max_probes,
            )

        allowed, state, remaining = await self._execute_with_fallback("acquire_permission", _op)

        # Mirror state to local fallback store for fail-safe resilience
        current_time = self._fallback_storage._time_func()
        if not allowed and state == CircuitState.OPEN:
            calc_opened_at = current_time - (self.config.circuit_cooldown_seconds - remaining)
            await self._fallback_storage.update_snapshot(
                provider_id=provider_id,
                state=CircuitState.OPEN,
                opened_at=calc_opened_at,
            )
            self.set_circuit_metric(provider_id, CircuitState.OPEN)
        elif allowed and state == CircuitState.HALF_OPEN:
            await self._fallback_storage.update_snapshot(
                provider_id=provider_id,
                state=CircuitState.HALF_OPEN,
                active_probes=1,
            )
            self.set_circuit_metric(provider_id, CircuitState.HALF_OPEN)
        elif allowed and state == CircuitState.CLOSED:
            await self._fallback_storage.update_snapshot(
                provider_id=provider_id,
                state=CircuitState.CLOSED,
            )
            self.set_circuit_metric(provider_id, CircuitState.CLOSED)

        if not allowed:
            if state == CircuitState.OPEN:
                logger.warning(
                    "Request blocked for provider '%s': Circuit is OPEN "
                    "(%.2fs remaining in cooldown)",
                    provider_id,
                    remaining,
                )
                raise CircuitBreakerError(
                    provider=provider_id,
                    message=(
                        f"Provider '{provider_id}' is temporarily unavailable due to "
                        f"circuit protection. Circuit is OPEN. "
                        f"Cooldown remaining: {remaining:.1f}s."
                    ),
                    details={
                        "circuit_state": CircuitState.OPEN.value,
                        "cooldown_remaining_seconds": round(remaining, 2),
                    },
                )
            elif state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Request blocked for provider '%s': Circuit is HALF_OPEN and "
                    "probe limit reached (%d max active)",
                    provider_id,
                    max_probes,
                )
                raise CircuitBreakerError(
                    provider=provider_id,
                    message=(
                        f"Provider '{provider_id}' is recovering in HALF_OPEN state. "
                        "Probe request is already in-flight."
                    ),
                    details={
                        "circuit_state": CircuitState.HALF_OPEN.value,
                        "max_probes": max_probes,
                    },
                )

        if state == CircuitState.HALF_OPEN:
            logger.info(
                "Granted HALF_OPEN probe permission for provider '%s' (max probes: %d)",
                provider_id,
                max_probes,
            )
        else:
            logger.debug("Granted permission for provider '%s' (Circuit CLOSED)", provider_id)

    async def record_success(self, provider_id: str) -> None:
        """Record a successful execution against the provider."""

        async def _op(storage: CircuitStateStorage) -> None:
            await storage.record_success(provider_id=provider_id)

        await self._execute_with_fallback("record_success", _op)
        # Mirror success to fallback store
        await self._fallback_storage.update_snapshot(
            provider_id=provider_id,
            state=CircuitState.CLOSED,
            consecutive_failures=0,
            active_probes=0,
        )
        self.set_circuit_metric(provider_id, CircuitState.CLOSED)
        logger.info("Recorded success for provider '%s'", provider_id)

    async def record_failure(self, provider_id: str, error: Exception | ProviderError) -> None:
        """Record an execution failure against the provider."""
        # Check if error category should count toward circuit breaker
        if isinstance(error, ProviderError) and error.category:
            try:
                cat = ErrorCategory(error.category)
                if cat in NON_CIRCUIT_FAILURE_CATEGORIES:
                    logger.debug(
                        "Ignoring error category '%s' for circuit breaker on provider '%s'",
                        cat,
                        provider_id,
                    )

                    # Release probe slot if this was during HALF_OPEN
                    async def _release_op(storage: CircuitStateStorage) -> None:
                        await storage.release_probe(provider_id=provider_id)

                    await self._execute_with_fallback("release_probe", _release_op)
                    await self._fallback_storage.release_probe(provider_id=provider_id)
                    return
            except ValueError:
                pass

        threshold = self.config.circuit_failure_threshold

        async def _fail_op(storage: CircuitStateStorage) -> tuple[CircuitState, int]:
            return await storage.record_failure(
                provider_id=provider_id,
                failure_threshold=threshold,
            )

        new_state, failures = await self._execute_with_fallback("record_failure", _fail_op)
        current_time = self._fallback_storage._time_func()
        if new_state == CircuitState.OPEN:
            await self._fallback_storage.update_snapshot(
                provider_id=provider_id,
                state=CircuitState.OPEN,
                consecutive_failures=failures,
                opened_at=current_time,
                last_failure_at=current_time,
                active_probes=0,
            )
            self.set_circuit_metric(provider_id, CircuitState.OPEN)

            # Structured lifecycle event: circuit_opened
            logger.warning(
                "Provider '%s' circuit state changed to OPEN (failures: %d/%d)",
                provider_id,
                failures,
                threshold,
                extra={
                    "event": "circuit_opened",
                    "provider": provider_id,
                },
            )
        else:
            await self._fallback_storage.update_snapshot(
                provider_id=provider_id,
                consecutive_failures=failures,
                last_failure_at=current_time,
            )
            self.set_circuit_metric(provider_id, CircuitState.CLOSED)
            logger.warning(
                "Provider '%s' recorded failure (%d/%d consecutive failures)",
                provider_id,
                failures,
                threshold,
            )

    async def get_state_async(self, provider_id: str) -> CircuitState:
        """Async method to inspect current circuit state."""
        cooldown = self.config.circuit_cooldown_seconds

        async def _op(storage: CircuitStateStorage) -> CircuitState:
            return await storage.get_state(provider_id=provider_id, cooldown_seconds=cooldown)

        res: CircuitState = await self._execute_with_fallback("get_state", _op)
        return res

    def get_state(self, provider_id: str) -> CircuitState:
        """Synchronous inspection method for router candidate filtering."""
        # Check fallback storage or active in-memory record directly
        if self._explicit_storage and isinstance(self._explicit_storage, InMemoryCircuitStorage):
            record = self._explicit_storage._circuits.get(provider_id)
            if record:
                if record.state == CircuitState.OPEN:
                    opened_at = record.opened_at or 0.0
                    if (
                        self._fallback_storage._time_func() - opened_at
                        >= self.config.circuit_cooldown_seconds
                    ):
                        return CircuitState.HALF_OPEN
                return record.state
        record = self._fallback_storage._circuits.get(provider_id)
        if record:
            if record.state == CircuitState.OPEN:
                opened_at = record.opened_at or 0.0
                if (
                    self._fallback_storage._time_func() - opened_at
                    >= self.config.circuit_cooldown_seconds
                ):
                    return CircuitState.HALF_OPEN
            return record.state
        return CircuitState.CLOSED

    def _get_or_create_circuit(self, provider_id: str) -> Any:
        """Backwards-compatibility helper for legacy unit test inspection."""
        return self._fallback_storage._get_or_create(provider_id)

    def reset_all(self) -> None:
        """Synchronous reset for test suite isolation."""
        self._fallback_storage._circuits.clear()


# Global circuit breaker manager instance
circuit_breaker_manager = CircuitBreakerManager()

__all__ = [
    "CircuitBreakerManager",
    "CircuitState",
    "circuit_breaker_manager",
]
