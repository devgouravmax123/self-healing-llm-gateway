"""Unit and integration tests for Phase 14.2 Provider Metrics."""

from unittest.mock import AsyncMock

import pytest
from prometheus_client import generate_latest

from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
)
from app.observability.metrics import GatewayMetrics, create_metrics_registry
from app.reliability.error_classifier import ErrorCategory
from app.reliability.retry import RetryManager


@pytest.fixture
def custom_metrics() -> GatewayMetrics:
    """Provide an isolated GatewayMetrics instance for provider tests."""
    registry = create_metrics_registry()
    return GatewayMetrics(registry=registry)


@pytest.mark.asyncio
async def test_provider_metrics_single_success(custom_metrics: GatewayMetrics) -> None:
    """Verify that a single successful provider attempt increments provider metrics."""
    mock_service = AsyncMock()
    mock_service.execute_chat_completion.return_value = ChatCompletionResponse(
        id="chatcmpl_success",
        created=12345,
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content="OK"),
                finish_reason="stop",
            )
        ],
    )

    retry_mgr = RetryManager(
        provider_service=mock_service,
        metrics=custom_metrics,
    )

    target = ProviderTarget(
        id="provider_a",
        provider="ollama",
        model="qwen2.5:3b",
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hi")],
    )

    response = await retry_mgr.execute_with_retry(
        request=request,
        request_id="req_test_prov_success",
        target=target,
    )

    assert response.id == "chatcmpl_success"

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # 1 provider request success
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_a",status="success"} 1.0' in text
    )
    # 1 provider duration observation
    assert (
        'gateway_provider_duration_seconds_count{model="qwen2.5:3b",provider="provider_a"} 1.0'
        in text
    )
    # 0 provider error samples
    assert "gateway_provider_errors_total{" not in text


@pytest.mark.asyncio
async def test_provider_metrics_single_failure(custom_metrics: GatewayMetrics) -> None:
    """Verify that a non-retryable provider failure increments attempts, duration, and errors."""
    mock_service = AsyncMock()
    mock_service.execute_chat_completion.side_effect = ProviderError(
        message="Bad request to model",
        status_code=400,
        category=ErrorCategory.BAD_REQUEST.value,
        retryable=False,
    )

    retry_mgr = RetryManager(
        provider_service=mock_service,
        metrics=custom_metrics,
    )

    target = ProviderTarget(
        id="provider_b",
        provider="ollama",
        model="qwen2.5:3b",
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hi")],
    )

    with pytest.raises(ProviderError) as exc_info:
        await retry_mgr.execute_with_retry(
            request=request,
            request_id="req_test_prov_fail",
            target=target,
        )

    assert exc_info.value.status_code == 400

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # 1 provider request error
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_b",status="error"} 1.0' in text
    )
    # 1 provider duration observation
    assert (
        'gateway_provider_duration_seconds_count{model="qwen2.5:3b",provider="provider_b"} 1.0'
        in text
    )
    # 1 provider error category BAD_REQUEST
    assert (
        "gateway_provider_errors_total{"
        'error_category="BAD_REQUEST",model="qwen2.5:3b",provider="provider_b"} 1.0' in text
    )


@pytest.mark.asyncio
async def test_provider_metrics_retry_accounting(custom_metrics: GatewayMetrics) -> None:
    """Verify retries on same provider increment attempt count, duration, and errors."""
    mock_service = AsyncMock()
    # First attempt: timeout (retryable). Second attempt: success.
    mock_service.execute_chat_completion.side_effect = [
        ProviderError(
            message="Gateway timeout",
            status_code=504,
            category=ErrorCategory.TIMEOUT.value,
            retryable=True,
        ),
        ChatCompletionResponse(
            id="chatcmpl_retry_success",
            created=12345,
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Retry OK"),
                    finish_reason="stop",
                )
            ],
        ),
    ]

    sleep_mock = AsyncMock()

    retry_mgr = RetryManager(
        provider_service=mock_service,
        sleep_func=sleep_mock,
        metrics=custom_metrics,
    )

    target = ProviderTarget(
        id="provider_retry",
        provider="ollama",
        model="qwen2.5:3b",
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hi")],
    )

    response = await retry_mgr.execute_with_retry(
        request=request,
        request_id="req_test_retry",
        target=target,
    )

    assert response.id == "chatcmpl_retry_success"
    assert sleep_mock.call_count == 1

    text = generate_latest(custom_metrics.registry).decode("utf-8")

    # 1 error attempt + 1 success attempt = 2 total requests on provider_retry
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_retry",status="error"} 1.0' in text
    )
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_retry",status="success"} 1.0' in text
    )

    # 2 provider duration observations
    assert (
        'gateway_provider_duration_seconds_count{model="qwen2.5:3b",provider="provider_retry"} 2.0'
        in text
    )

    # 1 TIMEOUT error recorded
    assert (
        "gateway_provider_errors_total{"
        'error_category="TIMEOUT",model="qwen2.5:3b",provider="provider_retry"} 1.0' in text
    )
