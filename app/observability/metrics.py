"""Prometheus metrics registry and foundation container.

Provides an isolated CollectorRegistry container to prevent duplicate registration
and global state leakage across test executions and application lifecycles.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter, Histogram

logger = logging.getLogger(__name__)

# Standard latency buckets for LLM gateway requests and provider executions (seconds)
LATENCY_BUCKETS: Sequence[float] = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
)


def create_metrics_registry() -> CollectorRegistry:
    """Create a new, isolated Prometheus CollectorRegistry.

    Using custom registries avoids polluting the process-wide default `REGISTRY`,
    preventing `ValueError: Duplicated timeseries in CollectorRegistry` during
    repeated application factory calls or isolated test executions.
    """
    return CollectorRegistry(auto_describe=True)


class GatewayMetrics:
    """Encapsulates Prometheus metrics and their bound CollectorRegistry.

    Provides Phase 14.2 Request and Provider metric families registered against
    an isolated registry instance.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry: CollectorRegistry = (
            registry if registry is not None else create_metrics_registry()
        )
        self._init_metrics()

    def _init_metrics(self) -> None:
        """Initialize and register Phase 14.2 metric collectors against self.registry."""
        # 1. Gateway Request Counter
        self.requests_total = Counter(
            "gateway_requests_total",
            "Total number of HTTP chat-completion requests processed by the gateway.",
            labelnames=["model", "status_code"],
            registry=self.registry,
        )

        # 2. Gateway Request Duration Histogram
        self.request_duration_seconds = Histogram(
            "gateway_request_duration_seconds",
            "End-to-end wall-clock duration of chat-completion requests in seconds.",
            labelnames=["model", "status_code"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 3. Provider Request Counter
        self.provider_requests_total = Counter(
            "gateway_provider_requests_total",
            "Total number of physical execution attempts dispatched to upstream LLM providers.",
            labelnames=["provider", "model", "status"],
            registry=self.registry,
        )

        # 4. Provider Duration Histogram
        self.provider_duration_seconds = Histogram(
            "gateway_provider_duration_seconds",
            "Wall-clock execution duration of one physical provider attempt in seconds.",
            labelnames=["provider", "model"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )

        # 5. Provider Errors Counter
        self.provider_errors_total = Counter(
            "gateway_provider_errors_total",
            "Total number of failed physical provider attempts categorized by error category.",
            labelnames=["provider", "model", "error_category"],
            registry=self.registry,
        )

    def reset_for_test(self, new_registry: CollectorRegistry | None = None) -> None:
        """Reset internal metrics registry and reinitialize metrics for clean test isolation."""
        self.registry = new_registry if new_registry is not None else create_metrics_registry()
        self._init_metrics()


# Singleton application metrics instance for default app lifetime
gateway_metrics = GatewayMetrics()


def get_metrics_registry() -> CollectorRegistry:
    """Dependency helper returning the current gateway metrics registry."""
    return gateway_metrics.registry
