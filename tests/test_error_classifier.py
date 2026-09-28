"""Unit tests for Phase 05 Error Classification."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from litellm.exceptions import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadGatewayError,
    BadRequestError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)

from app.main import app
from app.reliability.error_classifier import (
    ErrorCategory,
    ErrorClassifier,
    error_classifier,
)


def test_classify_timeout() -> None:
    """Verify Timeout exception classifies to ErrorCategory.TIMEOUT."""
    exc = Timeout("Request timed out", model="ollama/qwen2.5:3b", llm_provider="ollama")
    classified = error_classifier.classify(exc, provider="ollama")

    assert classified.category == ErrorCategory.TIMEOUT
    assert classified.retryable is True
    assert classified.http_status == 504
    assert classified.provider == "ollama"
    assert "timed out" in classified.message


def test_classify_timeout_standard_error() -> None:
    """Verify standard python TimeoutError classifies to TIMEOUT."""
    exc = TimeoutError("Connection timed out")
    classified = error_classifier.classify(exc, provider="ollama")

    assert classified.category == ErrorCategory.TIMEOUT
    assert classified.retryable is True
    assert classified.http_status == 504


def test_classify_rate_limited() -> None:
    """Verify RateLimitError classifies to ErrorCategory.RATE_LIMITED."""
    exc = RateLimitError("Rate limit reached", model="ollama/qwen2.5:3b", llm_provider="ollama")
    classified = error_classifier.classify(exc, provider="ollama")

    assert classified.category == ErrorCategory.RATE_LIMITED
    assert classified.retryable is True
    assert classified.http_status == 429
    assert "rate limit" in classified.message


def test_classify_server_error_500() -> None:
    """Verify InternalServerError or HTTP 500 classifies to ErrorCategory.SERVER_ERROR."""
    exc = InternalServerError(
        "Internal server error", model="ollama/qwen2.5:3b", llm_provider="ollama"
    )
    classified = error_classifier.classify(exc, provider="ollama", status_code=500)

    assert classified.category == ErrorCategory.SERVER_ERROR
    assert classified.retryable is True
    assert classified.http_status == 502
    assert "internal server error" in classified.message


def test_classify_upstream_error_502_503_504() -> None:
    """Verify BadGatewayError / ServiceUnavailableError classify to UPSTREAM_ERROR."""
    # 502
    exc_502 = BadGatewayError("Bad gateway", model="ollama/qwen2.5:3b", llm_provider="ollama")
    c_502 = error_classifier.classify(exc_502, provider="ollama")
    assert c_502.category == ErrorCategory.UPSTREAM_ERROR
    assert c_502.retryable is True
    assert c_502.http_status == 502

    # 503
    exc_503 = ServiceUnavailableError(
        "Service unavailable", model="ollama/qwen2.5:3b", llm_provider="ollama"
    )
    c_503 = error_classifier.classify(exc_503, provider="ollama")
    assert c_503.category == ErrorCategory.UPSTREAM_ERROR
    assert c_503.retryable is True
    assert c_503.http_status == 502

    # Generic APIError with 504 status code
    exc_504 = APIError(
        status_code=504,
        message="Gateway timeout from upstream",
        llm_provider="ollama",
        model="ollama/qwen2.5:3b",
    )
    c_504 = error_classifier.classify(exc_504, provider="ollama", status_code=504)
    assert c_504.category == ErrorCategory.UPSTREAM_ERROR
    assert c_504.retryable is True
    assert c_504.http_status == 502


def test_classify_connection_error() -> None:
    """Verify APIConnectionError and ConnectionError classify to CONNECTION_ERROR."""
    exc1 = APIConnectionError("Failed to connect", llm_provider="ollama", model="ollama/qwen2.5:3b")
    c1 = error_classifier.classify(exc1, provider="ollama")
    assert c1.category == ErrorCategory.CONNECTION_ERROR
    assert c1.retryable is True
    assert c1.http_status == 503

    exc2 = ConnectionRefusedError("Connection refused")
    c2 = error_classifier.classify(exc2, provider="ollama")
    assert c2.category == ErrorCategory.CONNECTION_ERROR
    assert c2.retryable is True
    assert c2.http_status == 503


def test_classify_auth_error() -> None:
    """Verify AuthenticationError and PermissionDeniedError classify to AUTH_ERROR."""
    exc1 = AuthenticationError("Invalid API key", model="model-a", llm_provider="custom")
    c1 = error_classifier.classify(exc1, provider="custom")
    assert c1.category == ErrorCategory.AUTH_ERROR
    assert c1.retryable is False
    assert c1.http_status == 502

    mock_request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    mock_raw_response = httpx.Response(status_code=403, request=mock_request)
    exc2 = PermissionDeniedError(
        "Permission denied",
        model="model-a",
        llm_provider="custom",
        response=mock_raw_response,
    )
    c2 = error_classifier.classify(exc2, provider="custom", status_code=403)
    assert c2.category == ErrorCategory.AUTH_ERROR
    assert c2.retryable is False


def test_classify_bad_request() -> None:
    """Verify BadRequestError / 400 classify to BAD_REQUEST."""
    exc = BadRequestError("Invalid model parameter", model="model-a", llm_provider="custom")
    c = error_classifier.classify(exc, provider="custom", status_code=400)
    assert c.category == ErrorCategory.BAD_REQUEST
    assert c.retryable is False
    assert c.http_status == 400
    assert "Invalid request" in c.message


def test_classify_unknown_error() -> None:
    """Verify unhandled general exceptions classify to UNKNOWN."""
    exc = RuntimeError("Something completely unexpected happened")
    c = error_classifier.classify(exc, provider="ollama")
    assert c.category == ErrorCategory.UNKNOWN
    assert c.retryable is False
    assert c.http_status == 500
    assert c.original_type == "RuntimeError"


def test_error_classifier_custom_instance() -> None:
    """Verify standalone instance of ErrorClassifier works properly."""
    classifier = ErrorClassifier()
    exc = ValueError("Arbitrary error")
    result = classifier.classify(exc)
    assert result.category == ErrorCategory.UNKNOWN
    assert result.original_type == "ValueError"


@pytest.mark.asyncio
async def test_chat_completions_classified_error_propagation() -> None:
    """Verify POST /v1/chat/completions propagates classified error details in response."""
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = RateLimitError(
            "Rate limit exceeded", model="ollama/qwen2.5:3b", llm_provider="ollama"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Hello"}],
            }
            response = await client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"X-Request-ID": "req_classifier_test"},
            )
            assert response.status_code == 429
            data = response.json()
            assert data["error"]["details"]["error_category"] == "RATE_LIMITED"
            assert data["error"]["details"]["retryable"] is True
            assert response.headers["X-Request-ID"] == "req_classifier_test"
