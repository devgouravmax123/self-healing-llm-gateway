"""Chaos and fault injection module for resilient LLM provider testing (Phase 15)."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    Timeout,
)

from app.core.config import Settings, settings
from app.core.exceptions import GatewayError
from app.core.request_context import get_request_id, get_tenant_id
from app.observability.tracing import trace_span
from app.routing.provider_registry import ProviderRegistry, provider_registry

logger = logging.getLogger(__name__)


class FaultType(StrEnum):
    """Supported deterministic chaos fault types."""

    TIMEOUT = "TIMEOUT"
    SERVER_ERROR = "SERVER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    LATENCY = "LATENCY"
    NETWORK_ERROR = "NETWORK_ERROR"


@dataclass
class ChaosRule:
    """In-memory specification for a deterministic chaos injection rule."""

    provider_id: str
    fault: FaultType
    duration_seconds: float | None = None
    failure_count: int | None = None
    latency_seconds: float = 0.0
    created_at: float = field(default_factory=time.time)
    injected_count: int = 0

    def is_expired(self, current_time: float) -> bool:
        """Check if rule has expired based on duration or failure count limits."""
        if self.duration_seconds is not None and self.duration_seconds > 0:
            if current_time >= (self.created_at + self.duration_seconds):
                return True
        if self.failure_count is not None and self.failure_count > 0:
            if self.injected_count >= self.failure_count:
                return True
        return False


class ChaosManager:
    """Thread-safe and async-safe in-memory manager for deterministic chaos fault injection."""

    def __init__(
        self,
        config: Settings | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self.config = config or settings
        self.registry = registry or provider_registry
        self._rules: dict[str, ChaosRule] = {}
        self._lock = asyncio.Lock()

    def is_active(self) -> bool:
        """Check if chaos functionality is safely enabled in non-production environments."""
        if self.config.environment.lower() == "production":
            return False
        return bool(self.config.chaos_enabled)

    async def set_rule(
        self,
        provider_id: str,
        fault: FaultType,
        duration_seconds: float | None = None,
        failure_count: int | None = None,
        latency_seconds: float = 0.0,
    ) -> ChaosRule:
        """Register or update a deterministic chaos rule for a valid provider."""
        if not self.is_active():
            raise GatewayError(
                message="Chaos testing is disabled or unavailable in this environment",
                status_code=403,
            )

        # Validate provider existence in registry to prevent arbitrary targets / SSRF
        known_providers = {p.id for p in self.registry.list_all()}
        # Also allow default fallback patterns if dynamically registered
        if provider_id not in known_providers and not provider_id.startswith("ollama_"):
            raise GatewayError(
                message=f"Unknown or unconfigured provider_id '{provider_id}'",
                status_code=400,
                details={"provider_id": provider_id},
            )

        # Bound latency
        if latency_seconds < 0.0 or latency_seconds > 60.0:
            raise GatewayError(
                message="Latency must be between 0.0 and 60.0 seconds",
                status_code=400,
            )

        # Bound duration and failure count
        if duration_seconds is not None and (duration_seconds <= 0 or duration_seconds > 3600):
            raise GatewayError(
                message="Duration must be between 1 and 3600 seconds",
                status_code=400,
            )
        if failure_count is not None and (failure_count <= 0 or failure_count > 1000):
            raise GatewayError(
                message="Failure count must be between 1 and 1000",
                status_code=400,
            )

        rule = ChaosRule(
            provider_id=provider_id,
            fault=fault,
            duration_seconds=duration_seconds,
            failure_count=failure_count,
            latency_seconds=latency_seconds,
        )

        async with self._lock:
            self._rules[provider_id] = rule

        logger.info(
            "Chaos rule configured: provider=%s, fault=%s, duration=%s, "
            "failure_count=%s, latency=%.2fs",
            provider_id,
            fault.value,
            duration_seconds,
            failure_count,
            latency_seconds,
            extra={
                "event": "chaos_configured",
                "provider": provider_id,
                "fault": fault.value,
                "duration_seconds": duration_seconds,
                "failure_count": failure_count,
                "latency_seconds": latency_seconds,
            },
        )
        return rule

    async def get_rule(self, provider_id: str) -> ChaosRule | None:
        """Get the active rule for a provider if not expired."""
        if not self.is_active():
            return None

        async with self._lock:
            rule = self._rules.get(provider_id)
            if rule is None:
                return None
            if rule.is_expired(time.time()):
                del self._rules[provider_id]
                return None
            return rule

    async def list_rules(self) -> list[ChaosRule]:
        """List all active unexpired chaos rules."""
        if not self.is_active():
            return []

        now = time.time()
        active_rules: list[ChaosRule] = []
        async with self._lock:
            expired_keys: list[str] = []
            for pid, rule in self._rules.items():
                if rule.is_expired(now):
                    expired_keys.append(pid)
                else:
                    active_rules.append(rule)
            for k in expired_keys:
                del self._rules[k]
        return active_rules

    async def clear_rule(self, provider_id: str) -> bool:
        """Clear a specific chaos rule."""
        async with self._lock:
            removed = self._rules.pop(provider_id, None) is not None

        if removed:
            logger.info(
                "Chaos rule cleared for provider=%s",
                provider_id,
                extra={"event": "chaos_cleared", "provider": provider_id},
            )
        return removed

    async def clear_all(self) -> int:
        """Clear all active chaos rules."""
        async with self._lock:
            count = len(self._rules)
            self._rules.clear()

        if count > 0:
            logger.info(
                "All chaos rules cleared (count=%d)",
                count,
                extra={"event": "chaos_cleared", "cleared_count": count},
            )
        return count

    async def maybe_inject_fault(
        self,
        provider_id: str,
        model: str,
        request_id: str | None = None,
    ) -> None:
        """Evaluate active rules and deterministically inject configured fault if matched."""
        if not self.is_active():
            return

        now = time.time()
        rule: ChaosRule | None = None

        async with self._lock:
            candidate = self._rules.get(provider_id)
            if candidate is not None:
                if candidate.is_expired(now):
                    del self._rules[provider_id]
                else:
                    candidate.injected_count += 1
                    rule = candidate
                    if candidate.is_expired(now):
                        del self._rules[provider_id]

        if rule is None:
            return

        effective_req_id = request_id or get_request_id() or "unknown"
        tenant_id = get_tenant_id() or "unknown"

        # Structured lifecycle event: chaos_injected
        logger.warning(
            "Chaos injected: provider=%s, model=%s, fault=%s, request_id=%s, count=%d",
            provider_id,
            model,
            rule.fault.value,
            effective_req_id,
            rule.injected_count,
            extra={
                "event": "chaos_injected",
                "provider": provider_id,
                "model": model,
                "fault": rule.fault.value,
                "request_id": effective_req_id,
                "tenant_id": tenant_id,
                "injected_count": rule.injected_count,
            },
        )

        # OpenTelemetry span attribute enrichment
        with trace_span(
            "chaos.inject",
            attributes={
                "chaos.injected": True,
                "chaos.fault": rule.fault.value,
                "llm.provider": provider_id,
                "llm.model": model,
            },
        ):
            # 1. LATENCY injection (async delay)
            if rule.fault == FaultType.LATENCY or rule.latency_seconds > 0:
                delay = rule.latency_seconds if rule.latency_seconds > 0 else 1.0
                await asyncio.sleep(delay)
                if rule.fault == FaultType.LATENCY:
                    return

            # 2. TIMEOUT exception injection
            if rule.fault == FaultType.TIMEOUT:
                raise Timeout(
                    message=f"Simulated timeout for provider '{provider_id}'",
                    model=model,
                    llm_provider=provider_id,
                )

            # 3. SERVER_ERROR (HTTP 500) exception injection
            if rule.fault == FaultType.SERVER_ERROR:
                raise InternalServerError(
                    message=(
                        f"Simulated upstream 500 internal server error for provider '{provider_id}'"
                    ),
                    model=model,
                    llm_provider=provider_id,
                )

            # 4. RATE_LIMITED (HTTP 429) exception injection
            if rule.fault == FaultType.RATE_LIMITED:
                raise RateLimitError(
                    message=f"Simulated rate limit exceeded (429) for provider '{provider_id}'",
                    model=model,
                    llm_provider=provider_id,
                )

            # 5. NETWORK_ERROR (Connection failure) exception injection
            if rule.fault == FaultType.NETWORK_ERROR:
                raise APIConnectionError(
                    message=f"Simulated connection failure for provider '{provider_id}'",
                    model=model,
                    llm_provider=provider_id,
                )


# Global default chaos manager instance
chaos_manager = ChaosManager()
