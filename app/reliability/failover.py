"""Failover orchestrator for resilient multi-provider LLM execution."""

import logging
import time
from datetime import datetime

from app.core.config import Settings, settings
from app.core.exceptions import CircuitBreakerError, ProviderError
from app.core.request_context import set_provider_id
from app.db.base import utc_now
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.observability.metrics import GatewayMetrics, gateway_metrics
from app.reliability.circuit_breaker import CircuitBreakerManager, circuit_breaker_manager
from app.reliability.error_classifier import ErrorCategory
from app.reliability.retry import RetryManager, retry_manager
from app.routing.router import NoHealthyProviderError, Router
from app.routing.router import router as default_router
from app.usage.tracker import UsageTracker, usage_tracker

logger = logging.getLogger(__name__)

# Categories of provider errors that permit attempting failover to a different provider
FAILOVER_ELIGIBLE_CATEGORIES: set[ErrorCategory] = {
    ErrorCategory.TIMEOUT,
    ErrorCategory.CONNECTION_ERROR,
    ErrorCategory.RATE_LIMITED,
    ErrorCategory.SERVER_ERROR,
    ErrorCategory.UPSTREAM_ERROR,
}


class FailoverManager:
    """Orchestrates multi-provider failover with circuit breaker, retry, and usage integration."""

    def __init__(
        self,
        config: Settings | None = None,
        router_instance: Router | None = None,
        retry_instance: RetryManager | None = None,
        circuit_instance: CircuitBreakerManager | None = None,
        usage_tracker_instance: UsageTracker | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self.config = config or settings
        self.router = router_instance or default_router
        self.retry_manager = retry_instance or retry_manager
        self.circuit_manager = circuit_instance or circuit_breaker_manager
        self.usage_tracker = usage_tracker_instance or usage_tracker
        self.metrics = metrics or gateway_metrics

    def is_failover_eligible(self, error: Exception) -> bool:
        """Determine if an error is eligible to trigger failover to another provider."""
        # CircuitBreakerError (e.g. OPEN or probe busy) is eligible for failover to another provider
        if isinstance(error, CircuitBreakerError):
            return True

        if isinstance(error, ProviderError) and error.category:
            try:
                cat = ErrorCategory(error.category)
                return cat in FAILOVER_ELIGIBLE_CATEGORIES
            except ValueError:
                return False

        return False

    async def execute_with_failover(
        self,
        request: ChatCompletionRequest,
        request_id: str,
        tenant_id: str | None = None,
        started_at: datetime | None = None,
    ) -> ChatCompletionResponse:
        """Execute chat completion with automatic provider failover.

        Execution flow per attempted provider:
        1. Select eligible ProviderTarget (excluding already attempted & OPEN circuits).
        2. Acquire permission from the circuit breaker for that provider.
        3. Execute provider call bounded by RetryManager (retries on the SAME provider).
        4. If provider execution succeeds:
           - Record success in the provider's circuit breaker.
           - Return the successful OpenAI-compatible response.
        5. If provider execution fails:
           - Record 1 failure event in that provider's circuit breaker.
           - Check failover eligibility (client errors like BAD_REQUEST/AUTH_ERROR halt).
           - Exclude failed provider and select next candidate if under limit.
        6. If all candidates exhausted, raise final error (or NoHealthyProviderError).
        """
        t_start = time.perf_counter()
        req_started_at = started_at or utc_now()
        attempted_provider_ids: set[str] = set()
        max_providers = self.config.max_failover_providers
        last_exception: Exception | None = None
        last_executed_provider_id: str | None = None

        from app.observability.tracing import trace_span

        while len(attempted_provider_ids) < max_providers:
            # 1. Select next eligible provider target
            try:
                candidates = self.router.get_candidates(
                    request=request,
                    exclude_provider_ids=attempted_provider_ids,
                    check_circuit=False,
                )
                with trace_span(
                    "routing.select",
                    attributes={
                        "model": request.model,
                        "candidate_count": len(candidates),
                    },
                ) as select_span:
                    target: ProviderTarget = self.router.select_provider(
                        request=request,
                        exclude_provider_ids=attempted_provider_ids,
                        check_circuit=False,
                    )
                    try:
                        if select_span is not None:
                            select_span.set_attribute("selected_provider", target.id)
                            select_span.set_attribute("llm.provider", target.id)
                            select_span.set_attribute("llm.model", target.model)
                    except Exception:
                        pass
            except NoHealthyProviderError as exc:
                logger.warning(
                    "No further eligible providers available for request_id=%s (attempted: %s)",
                    request_id,
                    attempted_provider_ids,
                )
                if last_exception is not None:
                    # Re-raise the concrete underlying failure if one occurred during failover
                    raise last_exception from exc
                raise exc

            # Mark provider as attempted for this request
            attempted_provider_ids.add(target.id)
            set_provider_id(target.id)
            current_failover_step = len(attempted_provider_ids)

            # Structured lifecycle event: provider_selected
            logger.info(
                "Provider selected: provider=%s, model=%s, request_id=%s (provider %d/%d)",
                target.id,
                target.model,
                request_id,
                current_failover_step,
                max_providers,
                extra={
                    "event": "provider_selected",
                    "request_id": request_id,
                    "provider": target.id,
                    "model": target.model,
                },
            )

            # 2. Acquire permission from circuit breaker
            try:
                await self.circuit_manager.acquire_permission(target.id)
            except CircuitBreakerError as cb_exc:
                logger.warning(
                    "Circuit breaker blocked provider '%s' for request_id=%s: %s",
                    target.id,
                    request_id,
                    cb_exc.message,
                )
                last_exception = cb_exc
                # Try next eligible provider immediately without calling LiteLLM or RetryManager
                # Note: Circuit-skipped providers did not execute this request and are NOT
                # counted as failover sources.
                continue

            # If we previously executed a provider that exhausted retries/failed:
            if last_executed_provider_id is not None and last_executed_provider_id != target.id:
                self.metrics.failovers_total.labels(
                    from_provider=last_executed_provider_id,
                    to_provider=target.id,
                    reason="retry_exhausted",
                ).inc()

                # OpenTelemetry span for failover transition
                with trace_span(
                    "failover.transition",
                    attributes={
                        "from_provider": last_executed_provider_id,
                        "to_provider": target.id,
                        "reason": "retry_exhausted",
                    },
                ):
                    pass

                # Structured lifecycle event: failover_started
                logger.info(
                    "Failover started: from_provider=%s, to_provider=%s, request_id=%s",
                    last_executed_provider_id,
                    target.id,
                    request_id,
                    extra={
                        "event": "failover_started",
                        "request_id": request_id,
                        "provider": target.id,
                        "model": target.model,
                    },
                )

            # Mark this target as the last provider that physically attempted execution
            last_executed_provider_id = target.id

            # 3. Execute with retries on this target
            try:
                response = await self.retry_manager.execute_with_retry(
                    request=request,
                    request_id=request_id,
                    target=target,
                )

                # 4. Success -> notify circuit breaker, record usage, and return response
                await self.circuit_manager.record_success(target.id)
                logger.info(
                    "Request succeeded via provider '%s' for request_id=%s",
                    target.id,
                    request_id,
                )

                # Calculate authoritative elapsed latency and completion timestamp
                latency_sec = time.perf_counter() - t_start
                completed_at = utc_now()
                latency_ms = latency_sec * 1000.0

                # Record usage safely (guaranteed non-blocking / non-failing)
                try:
                    await self.usage_tracker.record_usage(
                        request=request,
                        request_id=request_id,
                        target=target,
                        response=response,
                        tenant_id=tenant_id,
                        started_at=req_started_at,
                        latency_ms=latency_ms,
                        completed_at=completed_at,
                    )
                except Exception as tracker_exc:
                    logger.warning(
                        "Usage tracking invocation failed for request_id=%s: %s",
                        request_id,
                        tracker_exc,
                    )

                return response

            except Exception as exc:
                last_exception = exc
                # Record exactly one circuit failure event for this provider execution
                await self.circuit_manager.record_failure(target.id, exc)

                # Check failover eligibility
                if not self.is_failover_eligible(exc):
                    logger.info(
                        "Error is not failover-eligible (%s). Halting failover for request_id=%s",
                        type(exc).__name__,
                        request_id,
                    )
                    raise exc

                logger.warning(
                    "Provider '%s' exhausted retry policy and failed for request_id=%s. "
                    "Initiating failover...",
                    target.id,
                    request_id,
                )

        # Reached max failover provider limit
        logger.error(
            "Max failover provider limit reached (%d providers attempted: %s) for request_id=%s",
            max_providers,
            attempted_provider_ids,
            request_id,
        )
        if last_exception is not None:
            raise last_exception
        raise NoHealthyProviderError(
            f"All {max_providers} attempted LLM providers failed to serve the request."
        )


# Global failover manager instance
failover_manager = FailoverManager()
