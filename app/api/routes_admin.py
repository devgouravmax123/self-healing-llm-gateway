"""Administrative endpoints for gateway management and chaos testing (Phase 15)."""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import GatewayError
from app.reliability.chaos import FaultType, chaos_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Administration"])


class ChaosRuleRequest(BaseModel):
    """Payload for configuring a deterministic chaos injection rule."""

    provider_id: str = Field(description="Identifier of the target provider to inject faults into.")
    fault: FaultType = Field(description="Fault type to inject (TIMEOUT, SERVER_ERROR, etc.).")
    duration_seconds: float | None = Field(
        default=None, ge=1, le=3600, description="Duration in seconds before rule expires."
    )
    failure_count: int | None = Field(
        default=None, ge=1, le=1000, description="Max number of times to inject failure."
    )
    latency_seconds: float = Field(
        default=0.0, ge=0.0, le=60.0, description="Artificial latency delay in seconds."
    )


class ChaosRuleResponse(BaseModel):
    """Response model representing an active chaos rule."""

    provider_id: str
    fault: str
    duration_seconds: float | None
    failure_count: int | None
    latency_seconds: float
    injected_count: int


def verify_admin_api_key(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Verify admin authorization using the configured ADMIN_API_KEY.

    Raises:
        HTTPException(403): If chaos is disabled or in production.
        HTTPException(401): If credentials are missing or invalid.
    """
    # 1. Environment & safety guard
    if settings.environment.lower() == "production" or not settings.chaos_enabled:
        raise HTTPException(
            status_code=403,
            detail="Chaos controls are disabled or unavailable in this environment",
        )

    # 2. Check if admin_api_key is configured
    configured_admin_key = settings.admin_api_key
    if not configured_admin_key:
        logger.warning("Admin operation rejected: ADMIN_API_KEY is not configured on the server")
        raise HTTPException(
            status_code=403,
            detail="Administrative access is not configured",
        )

    # 3. Validate Authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Admin authentication required",
        )

    parts = authorization.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication scheme or malformed header",
        )

    token = parts[1].strip()
    # Constant-time comparison for security
    import hmac

    if not hmac.compare_digest(token, configured_admin_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid administrative credentials",
        )


@router.post(
    "/chaos",
    response_model=ChaosRuleResponse,
    summary="Configure Chaos Rule",
    description="Set a deterministic failure injection rule for a target provider.",
)
async def configure_chaos(
    payload: ChaosRuleRequest,
    _: Annotated[None, Depends(verify_admin_api_key)],
) -> ChaosRuleResponse:
    """Configure or update a chaos fault injection rule."""
    try:
        rule = await chaos_manager.set_rule(
            provider_id=payload.provider_id,
            fault=payload.fault,
            duration_seconds=payload.duration_seconds,
            failure_count=payload.failure_count,
            latency_seconds=payload.latency_seconds,
        )
        return ChaosRuleResponse(
            provider_id=rule.provider_id,
            fault=rule.fault.value,
            duration_seconds=rule.duration_seconds,
            failure_count=rule.failure_count,
            latency_seconds=rule.latency_seconds,
            injected_count=rule.injected_count,
        )
    except GatewayError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get(
    "/chaos",
    response_model=list[ChaosRuleResponse],
    summary="List Active Chaos Rules",
    description="Retrieve all currently active and unexpired chaos rules.",
)
async def list_chaos_rules(
    _: Annotated[None, Depends(verify_admin_api_key)],
) -> list[ChaosRuleResponse]:
    """List all active chaos rules."""
    rules = await chaos_manager.list_rules()
    return [
        ChaosRuleResponse(
            provider_id=r.provider_id,
            fault=r.fault.value,
            duration_seconds=r.duration_seconds,
            failure_count=r.failure_count,
            latency_seconds=r.latency_seconds,
            injected_count=r.injected_count,
        )
        for r in rules
    ]


@router.delete(
    "/chaos",
    summary="Clear All Chaos Rules",
    description="Remove all active chaos rules and immediately restore normal behavior.",
)
async def clear_all_chaos(
    _: Annotated[None, Depends(verify_admin_api_key)],
) -> dict[str, Any]:
    """Clear all active chaos rules."""
    cleared_count = await chaos_manager.clear_all()
    return {
        "status": "success",
        "cleared_count": cleared_count,
        "message": "All chaos rules successfully cleared",
    }


@router.delete(
    "/chaos/{provider_id}",
    summary="Clear Specific Chaos Rule",
    description="Remove the active chaos rule for a specific provider.",
)
async def clear_provider_chaos(
    provider_id: str,
    _: Annotated[None, Depends(verify_admin_api_key)],
) -> dict[str, Any]:
    """Clear active chaos rule for a single provider."""
    cleared = await chaos_manager.clear_rule(provider_id)
    return {
        "status": "success" if cleared else "not_found",
        "provider_id": provider_id,
        "cleared": cleared,
    }
