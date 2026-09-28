"""Unit tests for Phase 06 Timeout & Retry mechanism."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.main import app
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.reliability.retry import RetryManager
from tests.test_provider_integration import MockLiteLLMResponse


@pytest.mark.asyncio
async def test_retryable_error_succeeds_on_second_attempt() -> None:
    """Verify that a retryable error triggers a retry and succeeds on the 2nd attempt."""
    mock_sleep = AsyncMock()
    mock_provider_service = AsyncMock()

    # Attempt 1 fails with a retryable TIMEOUT error, Attempt 2 succeeds
    mock_provider_service.execute_chat_completion.side_effect = [
        ProviderError(
            message="Upstream LLM provider request timed out.",
            status_code=504,
            provider="ollama",
            category="TIMEOUT",
            retryable=True,
        ),
        MockLiteLLMResponse(content="Success on retry", model="ollama/qwen2.5:3b"),
    ]

    custom_settings = Settings(MAX_RETRIES=2, RETRY_BASE_DELAY=0.1, RETRY_JITTER=False)
    manager = RetryManager(
        config=custom_settings,
        provider_service=mock_provider_service,
        sleep_func=mock_sleep,
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    target = ProviderTarget(id="ollama_target", provider="ollama", model="qwen2.5:3b")

    result = await manager.execute_with_retry(request, request_id="req_retry_1", target=target)

    assert result.choices[0].message.content == "Success on retry"
    assert mock_provider_service.execute_chat_completion.call_count == 2
    mock_sleep.assert_called_once_with(0.1)


@pytest.mark.asyncio
async def test_retryable_error_exhausts_max_retries() -> None:
    """Verify that when a provider consistently fails, max retries are respected."""
    mock_sleep = AsyncMock()
    mock_provider_service = AsyncMock()

    mock_provider_service.execute_chat_completion.side_effect = ProviderError(
        message="Upstream LLM provider rate limit exceeded.",
        status_code=429,
        provider="ollama",
        category="RATE_LIMITED",
        retryable=True,
    )

    custom_settings = Settings(MAX_RETRIES=2, RETRY_BASE_DELAY=0.1, RETRY_JITTER=False)
    manager = RetryManager(
        config=custom_settings,
        provider_service=mock_provider_service,
        sleep_func=mock_sleep,
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    target = ProviderTarget(id="ollama_target", provider="ollama", model="qwen2.5:3b")

    with pytest.raises(ProviderError) as exc_info:
        await manager.execute_with_retry(request, request_id="req_retry_fail", target=target)

    assert exc_info.value.category == "RATE_LIMITED"
    assert exc_info.value.retryable is True
    assert exc_info.value.status_code == 429
    # Total attempts: initial attempt + 2 retries = 3
    assert mock_provider_service.execute_chat_completion.call_count == 3
    assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_non_retryable_error_does_not_retry() -> None:
    """Verify that a non-retryable error (e.g. BAD_REQUEST, AUTH_ERROR) immediately halts."""
    mock_sleep = AsyncMock()
    mock_provider_service = AsyncMock()

    mock_provider_service.execute_chat_completion.side_effect = ProviderError(
        message="Invalid request sent to LLM provider.",
        status_code=400,
        provider="ollama",
        category="BAD_REQUEST",
        retryable=False,
    )

    custom_settings = Settings(MAX_RETRIES=3, RETRY_BASE_DELAY=0.1)
    manager = RetryManager(
        config=custom_settings,
        provider_service=mock_provider_service,
        sleep_func=mock_sleep,
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    target = ProviderTarget(id="ollama_target", provider="ollama", model="qwen2.5:3b")

    with pytest.raises(ProviderError) as exc_info:
        await manager.execute_with_retry(request, request_id="req_non_retry", target=target)

    assert exc_info.value.category == "BAD_REQUEST"
    assert exc_info.value.retryable is False
    assert exc_info.value.status_code == 400
    assert mock_provider_service.execute_chat_completion.call_count == 1
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category,status_code",
    [
        ("TIMEOUT", 504),
        ("RATE_LIMITED", 429),
        ("SERVER_ERROR", 502),
        ("UPSTREAM_ERROR", 502),
        ("CONNECTION_ERROR", 503),
    ],
)
async def test_retryable_categories_trigger_retry(category: str, status_code: int) -> None:
    """Verify all defined retryable categories trigger retries."""
    mock_sleep = AsyncMock()
    mock_provider_service = AsyncMock()

    mock_provider_service.execute_chat_completion.side_effect = [
        ProviderError(
            message=f"Error {category}",
            status_code=status_code,
            provider="ollama",
            category=category,
            retryable=True,
        ),
        MockLiteLLMResponse(content="Success after error", model="ollama/qwen2.5:3b"),
    ]

    custom_settings = Settings(MAX_RETRIES=1, RETRY_BASE_DELAY=0.1, RETRY_JITTER=False)
    manager = RetryManager(
        config=custom_settings,
        provider_service=mock_provider_service,
        sleep_func=mock_sleep,
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    target = ProviderTarget(id="ollama_target", provider="ollama", model="qwen2.5:3b")

    result = await manager.execute_with_retry(request, request_id="req_cat_test", target=target)
    assert result.choices[0].message.content == "Success after error"
    assert mock_provider_service.execute_chat_completion.call_count == 2
    mock_sleep.assert_called_once()


def test_exponential_backoff_calculation_without_jitter() -> None:
    """Verify exponential backoff calculation without jitter."""
    custom_settings = Settings(
        RETRY_BASE_DELAY=0.5,
        RETRY_MAX_DELAY=5.0,
        RETRY_JITTER=False,
    )
    manager = RetryManager(config=custom_settings)

    # Attempt 0: 0.5 * (2^0) = 0.5
    assert manager.calculate_backoff(0) == 0.5
    # Attempt 1: 0.5 * (2^1) = 1.0
    assert manager.calculate_backoff(1) == 1.0
    # Attempt 2: 0.5 * (2^2) = 2.0
    assert manager.calculate_backoff(2) == 2.0
    # Attempt 3: 0.5 * (2^3) = 4.0
    assert manager.calculate_backoff(3) == 4.0
    # Attempt 4: 0.5 * (2^4) = 8.0 -> capped at 5.0
    assert manager.calculate_backoff(4) == 5.0


def test_exponential_backoff_with_jitter_bounds() -> None:
    """Verify backoff calculation with jitter stays within expected bounds."""
    custom_settings = Settings(
        RETRY_BASE_DELAY=1.0,
        RETRY_MAX_DELAY=10.0,
        RETRY_JITTER=True,
    )
    manager = RetryManager(config=custom_settings)

    # For attempt 0 (base = 1.0), with jitter (0.5 to 1.5), delay is between 0.5 and 1.5
    for _ in range(50):
        delay = manager.calculate_backoff(0)
        assert 0.5 <= delay <= 1.5

    # For attempt 5 (base = 32.0, capped at 10.0), delay is capped at 10.0
    for _ in range(50):
        delay = manager.calculate_backoff(5)
        assert 0.0 <= delay <= 10.0


@pytest.mark.asyncio
async def test_first_attempt_success_no_retries() -> None:
    """Verify that a successful first attempt does not invoke sleep or further calls."""
    mock_sleep = AsyncMock()
    mock_provider_service = AsyncMock()
    mock_provider_service.execute_chat_completion.return_value = MockLiteLLMResponse(
        content="Instant success", model="ollama/qwen2.5:3b"
    )

    custom_settings = Settings(MAX_RETRIES=3, RETRY_BASE_DELAY=0.5)
    manager = RetryManager(
        config=custom_settings,
        provider_service=mock_provider_service,
        sleep_func=mock_sleep,
    )

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    target = ProviderTarget(id="ollama_target", provider="ollama", model="qwen2.5:3b")

    result = await manager.execute_with_retry(request, request_id="req_fast", target=target)
    assert result.choices[0].message.content == "Instant success"
    assert mock_provider_service.execute_chat_completion.call_count == 1
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_chat_completions_endpoint_retries_and_preserves_request_id() -> None:
    """Verify POST /v1/chat/completions preserves X-Request-ID and succeeds on retry."""
    from litellm.exceptions import Timeout

    mock_resp = MockLiteLLMResponse(
        content="Success after route retry",
        model="ollama/qwen2.5:3b",
    )

    with (
        patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion,
        patch("app.api.routes_chat.retry_manager._sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        # First call raises Timeout, second succeeds
        mock_acompletion.side_effect = [
            Timeout("Request timed out", model="ollama/qwen2.5:3b", llm_provider="ollama"),
            mock_resp,
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Explain retry in one sentence."}],
            }
            custom_id = "req_custom_retry_trace_id_999"
            response = await client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"X-Request-ID": custom_id},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["choices"][0]["message"]["content"] == "Success after route retry"
            assert response.headers["X-Request-ID"] == custom_id
            assert mock_acompletion.call_count == 2
            mock_sleep.assert_called_once()
