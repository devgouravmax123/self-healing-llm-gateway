"""Security and tenant trust boundary integration tests."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.auth import authenticator, get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.main import app
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.reliability.failover import failover_manager
from app.usage.tracker import UsageTracker


@pytest.mark.asyncio
async def test_tenant_spoofing_prevented_in_usage_attribution() -> None:
    """CRITICAL SECURITY TEST:

    Verify that when authenticated tenant = 'tenant-A' but client sends metadata
    tenant_id = 'tenant-B', usage tracker attributes record strictly to 'tenant-A'.
    """
    mock_repo = MagicMock()
    mock_repo.create_usage_record = AsyncMock(return_value=None)
    mock_calc = MagicMock()
    mock_calc.calculate_cost = MagicMock(return_value=None)

    tracker = UsageTracker(calculator=mock_calc, repository=mock_repo)

    request = ChatCompletionRequest(
        model="test-model",
        messages=[ChatMessage(role="user", content="Hello")],
        metadata={"tenant_id": "tenant-B-attacker", "feature": "custom_agent"},
    )
    target = ProviderTarget(
        id="prov_1",
        provider="ollama",
        model="qwen2.5:3b",
        api_base="http://localhost:11434",
    )

    response = ChatCompletionResponse(
        id="chatcmpl-123",
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content="Hi!"),
                finish_reason="stop",
            )
        ],
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
    )

    # Invoke record_usage passing the authenticated tenant_id 'tenant-A-legit'
    await tracker.record_usage(
        request=request,
        request_id="req_test_spoof",
        target=target,
        response=response,
        tenant_id="tenant-A-legit",
    )

    assert mock_repo.create_usage_record.called
    call_kwargs = mock_repo.create_usage_record.call_args[1]

    # Verify tenant_id stored is the authenticated tenant 'tenant-A-legit', NOT 'tenant-B-attacker'
    assert call_kwargs["tenant_id"] == "tenant-A-legit"
    # Feature is preserved from metadata
    assert call_kwargs["feature"] == "custom_agent"


def test_chat_completions_unauthenticated_returns_401() -> None:
    """Verify calling /v1/chat/completions without Authorization header returns 401."""
    # Temporarily remove default auth override
    app.dependency_overrides.pop(get_authenticated_tenant, None)
    try:
        client = TestClient(app)
        payload = {
            "model": "qwen2.5:3b",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        response = client.post("/v1/chat/completions", json=payload)
        assert response.status_code == 401
        data = response.json()
        assert "error" in data
        assert data["error"]["type"] == "AuthenticationError"
    finally:
        app.dependency_overrides[get_authenticated_tenant] = lambda: TenantContext(
            tenant_id="test_tenant_default",
            tenant_name="Default Test Org",
            api_key_id=uuid4(),
            key_prefix="gw_live_test",
            is_admin=False,
        )


def test_chat_completions_authenticated_executes_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify calling /v1/chat/completions with valid Bearer token executes pipeline."""
    # Remove default test fixture override so actual get_authenticated_tenant executes
    app.dependency_overrides.pop(get_authenticated_tenant, None)
    try:
        client = TestClient(app)

        # Mock authenticator to return valid TenantContext
        fake_ctx = TenantContext(
            tenant_id="tenant_legit",
            tenant_name="Legit Corp",
            api_key_id=uuid4(),
            key_prefix="gw_live_1234",
            is_admin=False,
        )
        monkeypatch.setattr(
            authenticator,
            "authenticate_key",
            AsyncMock(return_value=fake_ctx),
        )

        # Mock failover_manager execution
        expected_resp = ChatCompletionResponse(
            id="chatcmpl-test",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Hello there!"),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=10, total_tokens=15),
        )
        mock_execute = AsyncMock(return_value=expected_resp)
        monkeypatch.setattr(failover_manager, "execute_with_failover", mock_execute)

        payload = {
            "model": "qwen2.5:3b",
            "messages": [{"role": "user", "content": "Hello"}],
            "metadata": {"tenant_id": "spoofed_tenant"},
        }
        headers = {"Authorization": "Bearer gw_live_1234_validsecret"}
        response = client.post("/v1/chat/completions", json=payload, headers=headers)

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "Hello there!"

        # Verify execute_with_failover received tenant_id='tenant_legit'
        assert mock_execute.called
        exec_kwargs = mock_execute.call_args[1]
        assert exec_kwargs["tenant_id"] == "tenant_legit"
    finally:
        app.dependency_overrides[get_authenticated_tenant] = lambda: TenantContext(
            tenant_id="test_tenant_default",
            tenant_name="Default Test Org",
            api_key_id=uuid4(),
            key_prefix="gw_live_test",
            is_admin=False,
        )
