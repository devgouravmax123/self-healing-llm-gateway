"""Tests for Phase 03 LiteLLM and Ollama provider integration."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from litellm.exceptions import ServiceUnavailableError, Timeout

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.main import app
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.providers.litellm_client import LiteLLMService


class MockLiteLLMChoice:
    """Mock LiteLLM choice object."""

    def __init__(
        self,
        content: str = "Hello there!",
        role: str = "assistant",
        finish_reason: str = "stop",
    ) -> None:
        self.message = type("MockMsg", (), {"role": role, "content": content})()
        self.finish_reason = finish_reason
        self.index = 0


class MockLiteLLMUsage:
    """Mock LiteLLM usage object."""

    def __init__(
        self,
        prompt_tokens: int = 15,
        completion_tokens: int = 25,
        total_tokens: int = 40,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class MockLiteLLMResponse:
    """Mock LiteLLM ModelResponse."""

    def __init__(
        self,
        content: str = "Hello from mock provider!",
        model: str = "ollama/qwen2.5:3b",
        response_id: str = "chatcmpl-test-id",
    ) -> None:
        self.id = response_id
        self.model = model
        self.choices = [MockLiteLLMChoice(content=content)]
        self.usage = MockLiteLLMUsage()
        self.created = 1700000000


@pytest.mark.asyncio
async def test_litellm_service_success() -> None:
    """Verify LiteLLMService successfully maps request to litellm call and converts response."""
    test_settings = Settings(
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="qwen2.5:3b",
    )
    service = LiteLLMService(config=test_settings)

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hi!")],
        temperature=0.7,
        max_tokens=50,
    )

    mock_resp = MockLiteLLMResponse(content="Response from Qwen", model="ollama/qwen2.5:3b")

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_resp

        result = await service.execute_chat_completion(request, request_id="req_test_123")

        mock_acompletion.assert_called_once_with(
            model="ollama/qwen2.5:3b",
            messages=[{"role": "user", "content": "Hi!"}],
            api_base="http://localhost:11434",
            temperature=0.7,
            max_tokens=50,
            timeout=30.0,
        )

        assert result.id == "chatcmpl-test-id"
        assert result.model == "qwen2.5:3b"
        assert len(result.choices) == 1
        assert result.choices[0].message.role == "assistant"
        assert result.choices[0].message.content == "Response from Qwen"
        assert result.choices[0].finish_reason == "stop"
        assert result.usage is not None
        assert result.usage.prompt_tokens == 15
        assert result.usage.completion_tokens == 25
        assert result.usage.total_tokens == 40


@pytest.mark.asyncio
async def test_litellm_service_provider_error() -> None:
    """Verify LiteLLMService raises ProviderError on provider failures."""
    test_settings = Settings(
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="qwen2.5:3b",
    )
    service = LiteLLMService(config=test_settings)

    request = ChatCompletionRequest(
        model="qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hi!")],
    )

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = Timeout(
            "Request timed out",
            model="ollama/qwen2.5:3b",
            llm_provider="ollama",
        )

        with pytest.raises(ProviderError) as exc_info:
            await service.execute_chat_completion(request, request_id="req_test_timeout")

        assert exc_info.value.status_code == 504
        assert exc_info.value.category == "TIMEOUT"
        assert exc_info.value.retryable is True
        assert "timed out" in exc_info.value.message


@pytest.mark.asyncio
async def test_chat_completions_api_with_mocked_provider() -> None:
    """Verify POST /v1/chat/completions returns OpenAI-compatible structure with mocked provider."""
    mock_resp = MockLiteLLMResponse(
        content="Real LLM response simulated",
        model="ollama/qwen2.5:3b",
    )

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_resp

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "qwen2.5:3b",
                "messages": [
                    {"role": "user", "content": "Explain quantum computing in one sentence."}
                ],
                "temperature": 0.5,
            }
            response = await client.post("/v1/chat/completions", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["object"] == "chat.completion"
            assert data["model"] == "qwen2.5:3b"
            assert len(data["choices"]) == 1
            assert data["choices"][0]["message"]["content"] == "Real LLM response simulated"
            assert data["usage"]["total_tokens"] == 40
            assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_chat_completions_api_provider_failure_returns_clean_error() -> None:
    """Verify POST /v1/chat/completions returns clean 502 error on provider failure."""
    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.side_effect = ServiceUnavailableError(
            "Ollama server is down", model="ollama/qwen2.5:3b", llm_provider="ollama"
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
                headers={"X-Request-ID": "req_failure_test"},
            )
            assert response.status_code == 502
            data = response.json()
            assert "error" in data
            assert "message" in data["error"]
            assert data["error"]["type"] == "ProviderError"
            assert response.headers["X-Request-ID"] == "req_failure_test"
