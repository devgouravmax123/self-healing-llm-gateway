"""OpenAI-compatible chat completion routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.core.request_context import get_request_id, set_requested_model
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.reliability.circuit_breaker import circuit_breaker_manager
from app.reliability.failover import failover_manager
from app.reliability.rate_limiter import rate_limiter
from app.reliability.retry import retry_manager
from app.routing.router import router as gateway_router

router = APIRouter(prefix="/v1", tags=["Chat"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Create Chat Completion",
    description="OpenAI-compatible chat completion endpoint with retry, circuit breaker, failover.",
)
async def create_chat_completion(
    request: ChatCompletionRequest,
    tenant_context: Annotated[TenantContext, Depends(get_authenticated_tenant)],
    http_request: Request,
) -> ChatCompletionResponse:
    """Handle chat completion request with authentication, rate limiting, and failover."""
    request_id = get_request_id() or "req_chatcmpl"
    set_requested_model(request.model)

    # Attach model name to http_request.state for middleware metric observation
    http_request.state.requested_model = request.model

    # 1. Check rate limit for authenticated tenant (happens before router/provider/failover)
    await rate_limiter.check_rate_limit(tenant_id=tenant_context.tenant_id)

    # 2. Ensure failover_manager uses the currently bound router & retry_manager in this module
    failover_manager.router = gateway_router
    failover_manager.retry_manager = retry_manager
    failover_manager.circuit_manager = circuit_breaker_manager

    # 3. Execute with failover passing authenticated tenant_id for secure usage attribution
    return await failover_manager.execute_with_failover(
        request=request,
        request_id=request_id,
        tenant_id=tenant_context.tenant_id,
    )
