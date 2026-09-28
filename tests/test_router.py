"""Unit tests for Phase 04 Provider Registry and Basic Routing."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import NoHealthyProviderError, Router
from tests.test_provider_integration import MockLiteLLMResponse


def test_provider_target_model_validation() -> None:
    """Verify ProviderTarget model instantiation and default fields."""
    target = ProviderTarget(
        id="ollama_test",
        provider="ollama",
        model="qwen2.5:3b",
        api_base="http://localhost:11434",
        enabled=True,
    )
    assert target.id == "ollama_test"
    assert target.provider == "ollama"
    assert target.model == "qwen2.5:3b"
    assert target.api_base == "http://localhost:11434"
    assert target.enabled is True

    # Test default enabled is True
    target_default = ProviderTarget(id="p1", provider="ollama", model="m1")
    assert target_default.enabled is True
    assert target_default.api_base is None


def test_provider_registry_register_and_get() -> None:
    """Verify registering and retrieving providers from ProviderRegistry."""
    registry = ProviderRegistry()
    target1 = ProviderTarget(id="p1", provider="ollama", model="qwen2.5:3b")
    target2 = ProviderTarget(id="p2", provider="ollama", model="llama3:8b", enabled=False)

    registry.register(target1)
    registry.register(target2)

    assert registry.get("p1") == target1
    assert registry.get("p2") == target2
    assert registry.get("nonexistent") is None


def test_provider_registry_list_all_and_enabled() -> None:
    """Verify list_all and list_enabled methods filter disabled providers."""
    registry = ProviderRegistry()
    p1 = ProviderTarget(id="p1", provider="ollama", model="m1", enabled=True)
    p2 = ProviderTarget(id="p2", provider="ollama", model="m2", enabled=False)
    p3 = ProviderTarget(id="p3", provider="ollama", model="m3", enabled=True)

    registry.register(p1)
    registry.register(p2)
    registry.register(p3)

    assert len(registry.list_all()) == 3
    enabled = registry.list_enabled()
    assert len(enabled) == 2
    assert p1 in enabled
    assert p3 in enabled
    assert p2 not in enabled


def test_provider_registry_from_settings() -> None:
    """Verify registry initialization from Settings."""
    custom_settings = Settings(
        LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://localhost:11434",
        OLLAMA_MODEL="qwen2.5:3b",
    )
    registry = ProviderRegistry.from_settings(custom_settings)
    enabled = registry.list_enabled()
    assert len(enabled) == 1
    assert enabled[0].id == "ollama_default"
    assert enabled[0].provider == "ollama"
    assert enabled[0].model == "qwen2.5:3b"
    assert enabled[0].api_base == "http://localhost:11434"


def test_router_select_provider_matching_model() -> None:
    """Verify router selects the provider target matching the requested model."""
    registry = ProviderRegistry()
    p1 = ProviderTarget(id="p1", provider="ollama", model="qwen2.5:3b")
    p2 = ProviderTarget(id="p2", provider="ollama", model="llama3:8b")
    registry.register(p1)
    registry.register(p2)

    router = Router(registry=registry)

    # Direct match
    req1 = ChatCompletionRequest(
        model="llama3:8b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    selected1 = router.select_provider(req1)
    assert selected1.id == "p2"

    # With prefix 'ollama/'
    req2 = ChatCompletionRequest(
        model="ollama/qwen2.5:3b",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    selected2 = router.select_provider(req2)
    assert selected2.id == "p1"


def test_router_select_provider_fallback_to_default() -> None:
    """Verify router falls back to first enabled provider for generic/unmatched model."""
    registry = ProviderRegistry()
    p1 = ProviderTarget(id="default_ollama", provider="ollama", model="qwen2.5:3b")
    registry.register(p1)

    router = Router(registry=registry)
    req = ChatCompletionRequest(
        model="local-model-a",
        messages=[ChatMessage(role="user", content="Hello")],
    )
    selected = router.select_provider(req)
    assert selected.id == "default_ollama"


def test_router_raises_when_no_enabled_providers() -> None:
    """Verify router raises NoHealthyProviderError when registry has no enabled providers."""
    registry = ProviderRegistry()
    p_disabled = ProviderTarget(id="p1", provider="ollama", model="m1", enabled=False)
    registry.register(p_disabled)

    router = Router(registry=registry)
    req = ChatCompletionRequest(
        model="m1",
        messages=[ChatMessage(role="user", content="Hello")],
    )

    with pytest.raises(NoHealthyProviderError) as exc_info:
        router.select_provider(req)

    assert exc_info.value.status_code == 503
    assert "No enabled LLM providers" in exc_info.value.message


@pytest.mark.asyncio
async def test_chat_completions_endpoint_uses_router() -> None:
    """Verify POST /v1/chat/completions executes cleanly through router with mocked LiteLLM."""
    mock_resp = MockLiteLLMResponse(
        content="Routed response",
        model="ollama/qwen2.5:3b",
    )

    with patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion:
        mock_acompletion.return_value = mock_resp

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "What is router in gateway?"}],
            }
            response = await client.post("/v1/chat/completions", json=payload)
            assert response.status_code == 200
            data = response.json()
            assert data["model"] == "qwen2.5:3b"
            assert data["choices"][0]["message"]["content"] == "Routed response"
            assert "X-Request-ID" in response.headers


@pytest.mark.asyncio
async def test_chat_completions_endpoint_no_providers_returns_503() -> None:
    """Verify POST /v1/chat/completions returns 503 when no providers are available."""
    empty_registry = ProviderRegistry()
    test_router = Router(registry=empty_registry)

    with patch("app.api.routes_chat.gateway_router", test_router):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = {
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Hello"}],
            }
            response = await client.post(
                "/v1/chat/completions",
                json=payload,
                headers={"X-Request-ID": "req_503_test"},
            )
            assert response.status_code == 503
            data = response.json()
            assert "error" in data
            assert data["error"]["type"] == "NoHealthyProviderError"
            assert response.headers["X-Request-ID"] == "req_503_test"
