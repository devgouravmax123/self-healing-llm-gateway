"""OpenAI-compatible chat completion routes."""

from fastapi import APIRouter

from app.core.request_context import get_request_id
from app.models.requests import ChatCompletionRequest
from app.models.responses import ChatCompletionResponse
from app.reliability.circuit_breaker import circuit_breaker_manager
from app.reliability.failover import failover_manager
from app.reliability.retry import retry_manager
from app.routing.router import router as gateway_router

router = APIRouter(prefix="/v1", tags=["Chat"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Create Chat Completion",
    description="OpenAI-compatible chat completion endpoint with retry, circuit breaker, failover.",
)
async def create_chat_completion(request: ChatCompletionRequest) -> ChatCompletionResponse:
    """Handle chat completion request with multi-provider failover, circuit breaker, and retries."""
    request_id = get_request_id() or "req_chatcmpl"
    # Ensure failover_manager uses the currently bound router & retry_manager in this module
    failover_manager.router = gateway_router
    failover_manager.retry_manager = retry_manager
    failover_manager.circuit_manager = circuit_breaker_manager
    return await failover_manager.execute_with_failover(
        request=request,
        request_id=request_id,
    )
