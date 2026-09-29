"""OpenAI-compatible chat completion routes."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.core.request_context import (
    get_provider_id,
    get_request_id,
    set_feature,
    set_requested_model,
    set_tenant_id,
)
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.reliability.circuit_breaker import circuit_breaker_manager
from app.reliability.failover import failover_manager
from app.reliability.rate_limiter import rate_limiter
from app.reliability.retry import retry_manager
from app.routing.router import router as gateway_router

logger = logging.getLogger(__name__)

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
    set_tenant_id(tenant_context.tenant_id)

    # Resolve feature from request metadata (defaulting to 'chat')
    feature = "chat"
    if request.metadata and isinstance(request.metadata, dict):
        feature = request.metadata.get("feature", "chat")
    set_feature(feature)

    # Attach model name to http_request.state for middleware metric observation
    http_request.state.requested_model = request.model

    # Structured lifecycle event: request_received
    logger.info(
        "Request received: request_id=%s, tenant_id=%s, feature=%s, model=%s",
        request_id,
        tenant_context.tenant_id,
        feature,
        request.model,
        extra={
            "event": "request_received",
            "request_id": request_id,
            "tenant_id": tenant_context.tenant_id,
            "feature": feature,
            "model": request.model,
        },
    )

    from app.observability.tracing import trace_span

    with trace_span(
        "gateway.request",
        attributes={
            "request.id": request_id,
            "tenant.id": tenant_context.tenant_id,
            "feature": feature,
            "llm.request.model": request.model,
        },
    ) as root_span:
        # 1. Check rate limit for authenticated tenant (happens before router/provider/failover)
        await rate_limiter.check_rate_limit(tenant_id=tenant_context.tenant_id)

        # 2. Ensure failover_manager uses the currently bound router & retry_manager in this module
        failover_manager.router = gateway_router
        failover_manager.retry_manager = retry_manager
        failover_manager.circuit_manager = circuit_breaker_manager

        # 3. Execute with failover passing authenticated tenant_id for secure usage attribution
        t_start = time.perf_counter()
        response = await failover_manager.execute_with_failover(
            request=request,
            request_id=request_id,
            tenant_id=tenant_context.tenant_id,
        )
        latency_sec = time.perf_counter() - t_start

        # Structured lifecycle event: request_completed
        active_provider = get_provider_id() or None
        logger.info(
            "Request completed: request_id=%s, tenant_id=%s, provider=%s, model=%s, latency=%.3fs",
            request_id,
            tenant_context.tenant_id,
            active_provider,
            request.model,
            latency_sec,
            extra={
                "event": "request_completed",
                "request_id": request_id,
                "tenant_id": tenant_context.tenant_id,
                "feature": feature,
                "provider": active_provider,
                "model": request.model,
                "latency": latency_sec,
            },
        )
        try:
            if root_span is not None:
                if active_provider:
                    root_span.set_attribute("llm.provider", active_provider)
                root_span.set_attribute("llm.model", request.model)
        except Exception:
            pass

        return response
