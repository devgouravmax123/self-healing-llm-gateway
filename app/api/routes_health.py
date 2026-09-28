"""Health and readiness routes."""

from fastapi import APIRouter

from app.core.config import settings
from app.models.responses import HealthResponse, ProviderHealthSnapshot, ReadyResponse
from app.reliability.circuit_breaker import circuit_breaker_manager
from app.reliability.health_tracker import health_tracker
from app.routing.provider_registry import provider_registry
from app.storage.redis import redis_manager

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, summary="Gateway Health Check")
async def get_health() -> HealthResponse:
    """Return the basic health status of the gateway."""
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get("/ready", response_model=ReadyResponse, summary="Gateway Readiness Check")
async def get_ready() -> ReadyResponse:
    """Return readiness status indicating if the gateway is ready to accept traffic.

    If Redis is available, readiness reports 'ready' and 'connected'.
    If Redis is unreachable, the gateway reports 'ready' with status 'degraded'
    because in-memory circuit breaker fallback allows traffic to continue safely.
    """
    redis_ok = await redis_manager.ping()
    if redis_ok:
        return ReadyResponse(
            status="ready",
            ready=True,
            details={"api": "ready", "redis": "connected", "mode": "distributed"},
        )
    return ReadyResponse(
        status="degraded",
        ready=True,
        details={
            "api": "ready",
            "redis": "disconnected",
            "mode": "degraded_in_memory",
        },
    )


@router.get(
    "/health/providers",
    response_model=dict[str, ProviderHealthSnapshot],
    summary="Provider Health Snapshots",
)
async def get_provider_health() -> dict[str, ProviderHealthSnapshot]:
    """Return health metrics and circuit states for all configured providers.

    Observational only: Does not trigger provider attempts or alter routing.
    """
    snapshots: dict[str, ProviderHealthSnapshot] = {}
    for provider in provider_registry.list_all():
        circuit_state_enum = await circuit_breaker_manager.get_state_async(provider.id)
        snapshot = await health_tracker.get_provider_snapshot(
            provider.id, circuit_state=circuit_state_enum.value
        )
        snapshots[provider.id] = snapshot
    return snapshots
