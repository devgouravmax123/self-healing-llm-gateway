"""Circuit Breaker reliability component for upstream LLM providers."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import Settings, settings
from app.core.exceptions import CircuitBreakerError, ProviderError
from app.reliability.error_classifier import ErrorCategory

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    """The three discrete states of a provider circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


# Non-provider categories that represent client errors or non-health issues
NON_CIRCUIT_FAILURE_CATEGORIES: set[ErrorCategory] = {
    ErrorCategory.AUTH_ERROR,
    ErrorCategory.BAD_REQUEST,
}


@dataclass
class ProviderCircuitState:
    """State machine representation for an individual provider's circuit breaker."""

    provider_id: str
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: float | None = None
    last_failure_at: float | None = None
    active_probes: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class CircuitBreakerManager:
    """Manages provider-specific circuit breakers to prevent cascading failures."""

    def __init__(
        self,
        config: Settings | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        self.config = config or settings
        self._time_func = time_func or time.monotonic
        self._circuits: dict[str, ProviderCircuitState] = {}
        self._global_lock = asyncio.Lock()

    def _get_or_create_circuit(self, provider_id: str) -> ProviderCircuitState:
        """Get or initialize the state machine for a specific provider."""
        if provider_id not in self._circuits:
            self._circuits[provider_id] = ProviderCircuitState(provider_id=provider_id)
        return self._circuits[provider_id]

    async def acquire_permission(self, provider_id: str) -> None:
        """Check if a request is allowed to execute against the specified provider.

        Raises:
            CircuitBreakerError: If the circuit is OPEN or HALF_OPEN probe capacity is exhausted.
        """
        circuit = self._get_or_create_circuit(provider_id)
        current_time = self._time_func()

        async with circuit.lock:
            # 1. If currently OPEN, check if cooldown period has elapsed
            if circuit.state == CircuitState.OPEN:
                cooldown = self.config.circuit_cooldown_seconds
                opened_at = circuit.opened_at or current_time
                if current_time - opened_at >= cooldown:
                    # Transition OPEN -> HALF_OPEN
                    logger.info(
                        "Circuit for provider '%s' cooldown expired (%.2fs >= %.2fs). "
                        "Transitioning OPEN -> HALF_OPEN",
                        provider_id,
                        current_time - opened_at,
                        cooldown,
                    )
                    circuit.state = CircuitState.HALF_OPEN
                    circuit.active_probes = 0
                    circuit.consecutive_successes = 0
                else:
                    remaining = cooldown - (current_time - opened_at)
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

            # 2. If HALF_OPEN, enforce single / limited probe concurrency
            if circuit.state == CircuitState.HALF_OPEN:
                max_probes = self.config.circuit_half_open_max_probes
                if circuit.active_probes >= max_probes:
                    logger.warning(
                        "Request blocked for provider '%s': Circuit is HALF_OPEN and "
                        "probe limit reached (%d/%d active)",
                        provider_id,
                        circuit.active_probes,
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
                            "active_probes": circuit.active_probes,
                            "max_probes": max_probes,
                        },
                    )
                # Claim probe slot
                circuit.active_probes += 1
                logger.info(
                    "Granted HALF_OPEN probe permission for provider '%s' (active probes: %d/%d)",
                    provider_id,
                    circuit.active_probes,
                    max_probes,
                )
                return

            # 3. If CLOSED, request is permitted
            logger.debug("Granted permission for provider '%s' (Circuit CLOSED)", provider_id)

    async def record_success(self, provider_id: str) -> None:
        """Record a successful execution against the provider."""
        circuit = self._get_or_create_circuit(provider_id)
        async with circuit.lock:
            if circuit.state == CircuitState.HALF_OPEN:
                circuit.active_probes = max(0, circuit.active_probes - 1)
                circuit.consecutive_successes += 1
                logger.info(
                    "HALF_OPEN probe succeeded for provider '%s'. "
                    "Transitioning HALF_OPEN -> CLOSED",
                    provider_id,
                )
                # Successful probe recovers the circuit
                circuit.state = CircuitState.CLOSED
                circuit.consecutive_failures = 0
                circuit.consecutive_successes = 0
                circuit.opened_at = None
            elif circuit.state == CircuitState.CLOSED:
                # Reset consecutive failure count on normal success
                if circuit.consecutive_failures > 0:
                    logger.debug(
                        "Resetting failure count (%d -> 0) for provider '%s' on success",
                        circuit.consecutive_failures,
                        provider_id,
                    )
                    circuit.consecutive_failures = 0

    async def record_failure(self, provider_id: str, error: Exception | ProviderError) -> None:
        """Record an execution failure against the provider."""
        circuit = self._get_or_create_circuit(provider_id)
        current_time = self._time_func()

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
                    # If this was during HALF_OPEN, release the probe slot
                    async with circuit.lock:
                        if circuit.state == CircuitState.HALF_OPEN:
                            circuit.active_probes = max(0, circuit.active_probes - 1)
                    return
            except ValueError:
                pass

        async with circuit.lock:
            circuit.last_failure_at = current_time

            if circuit.state == CircuitState.HALF_OPEN:
                circuit.active_probes = max(0, circuit.active_probes - 1)
                circuit.state = CircuitState.OPEN
                circuit.opened_at = current_time
                logger.warning(
                    "HALF_OPEN probe failed for provider '%s'. "
                    "Transitioning HALF_OPEN -> OPEN (reopened at %.2f)",
                    provider_id,
                    current_time,
                )
            elif circuit.state == CircuitState.CLOSED:
                circuit.consecutive_failures += 1
                threshold = self.config.circuit_failure_threshold
                logger.warning(
                    "Provider '%s' recorded failure (%d/%d consecutive failures)",
                    provider_id,
                    circuit.consecutive_failures,
                    threshold,
                )
                if circuit.consecutive_failures >= threshold:
                    circuit.state = CircuitState.OPEN
                    circuit.opened_at = current_time
                    logger.error(
                        "Failure threshold reached (%d/%d) for provider '%s'. "
                        "Transitioning CLOSED -> OPEN",
                        circuit.consecutive_failures,
                        threshold,
                        provider_id,
                    )

    def get_state(self, provider_id: str) -> CircuitState:
        """Get the current state for a provider (evaluating cooldown passively if needed)."""
        circuit = self._get_or_create_circuit(provider_id)
        current_time = self._time_func()
        if circuit.state == CircuitState.OPEN:
            cooldown = self.config.circuit_cooldown_seconds
            opened_at = circuit.opened_at or current_time
            if current_time - opened_at >= cooldown:
                return CircuitState.HALF_OPEN
        return circuit.state

    def reset_all(self) -> None:
        """Reset all circuit breaker states (primarily for testing)."""
        self._circuits.clear()


# Global circuit breaker manager instance
circuit_breaker_manager = CircuitBreakerManager()
