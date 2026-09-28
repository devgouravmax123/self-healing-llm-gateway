"""Health and readiness routes."""

from fastapi import APIRouter

from app.core.config import settings
from app.models.responses import HealthResponse, ReadyResponse
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
