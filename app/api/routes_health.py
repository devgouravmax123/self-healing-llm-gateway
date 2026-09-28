"""Health and readiness routes."""

from fastapi import APIRouter

from app.core.config import settings
from app.models.responses import HealthResponse, ReadyResponse

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
    """Return readiness status indicating if the gateway is ready to accept traffic."""
    return ReadyResponse(
        status="ready",
        ready=True,
        details={"api": "ready"},
    )
