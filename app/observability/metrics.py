"""Prometheus metrics registry and foundation container.

Provides an isolated CollectorRegistry container to prevent duplicate registration
and global state leakage across test executions and application lifecycles.
"""

from __future__ import annotations

import logging

from prometheus_client import CollectorRegistry

logger = logging.getLogger(__name__)


def create_metrics_registry() -> CollectorRegistry:
    """Create a new, isolated Prometheus CollectorRegistry.

    Using custom registries avoids polluting the process-wide default `REGISTRY`,
    preventing `ValueError: Duplicated timeseries in CollectorRegistry` during
    repeated application factory calls or isolated test executions.
    """
    return CollectorRegistry(auto_describe=True)


class GatewayMetrics:
    """Encapsulates Prometheus metrics and their bound CollectorRegistry.

    Phase 14.1 establishes this container and registry architecture.
    Individual business metrics (requests, latency, retries, etc.) will be attached
    in subsequent Phase 14 sub-phases without modifying this core registry isolation.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry: CollectorRegistry = (
            registry if registry is not None else create_metrics_registry()
        )

    def reset_for_test(self, new_registry: CollectorRegistry | None = None) -> None:
        """Reset internal metrics registry for clean test isolation."""
        self.registry = new_registry if new_registry is not None else create_metrics_registry()


# Singleton application metrics instance for default app lifetime
gateway_metrics = GatewayMetrics()


def get_metrics_registry() -> CollectorRegistry:
    """Dependency helper returning the current gateway metrics registry."""
    return gateway_metrics.registry
