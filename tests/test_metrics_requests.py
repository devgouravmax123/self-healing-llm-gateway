from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.core.auth import get_authenticated_tenant
from app.core.auth_context import TenantContext
from app.main import create_app
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
)
from app.observability.metrics import gateway_metrics


@pytest.fixture(autouse=True)
def reset_metrics_for_test() -> None:
    """Reset the metrics registry before each test for clean isolation."""
    gateway_metrics.reset_for_test()


def test_request_metrics_chat_completion_success() -> None:
    """Verify that a successful /v1/chat/completions increments request metrics exactly once."""
    app = create_app()
    client = TestClient(app)

    mock_response = ChatCompletionResponse(
        id="chatcmpl_test",
        created=123456789,
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ],
    )

    fake_tenant = TenantContext(
        tenant_id="tenant_req_metric_test",
        tenant_name="Req Metric Test Tenant",
        api_key_id=uuid4(),
        key_prefix="gw_live_1234",
    )

    with (
        patch("app.core.auth.authenticator.authenticate_key", new_callable=AsyncMock) as mock_auth,
        patch(
            "app.reliability.failover.failover_manager.execute_with_failover",
            new_callable=AsyncMock,
        ) as mock_failover,
    ):
        mock_auth.return_value = fake_tenant
        mock_failover.return_value = mock_response

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Hi"}],
            },
            headers={"Authorization": "Bearer gw_live_testkey_secret123"},
        )

        assert response.status_code == 200

    metrics_text = generate_latest(gateway_metrics.registry).decode("utf-8")

    # Verify gateway_requests_total
    assert 'gateway_requests_total{model="qwen2.5:3b",status_code="200"} 1.0' in metrics_text

    # Verify gateway_request_duration_seconds count
    assert (
        'gateway_request_duration_seconds_count{model="qwen2.5:3b",status_code="200"} 1.0'
        in metrics_text
    )


def test_request_metrics_non_chat_endpoints_not_counted() -> None:
    """Verify that non-chat endpoints (/health, /ready, etc.) do not increment requests_total."""
    app = create_app()
    client = TestClient(app)

    client.get("/health")
    client.get("/ready")
    client.get("/metrics")
    client.get("/")

    metrics_text = generate_latest(gateway_metrics.registry).decode("utf-8")

    # Metric family header will exist, but no sample lines for requests should exist
    assert "gateway_requests_total{" not in metrics_text
    assert "gateway_request_duration_seconds_count{" not in metrics_text


def test_request_metrics_unauthenticated_request() -> None:
    """Verify that an unauthenticated 401 request increments requests_total with status_code 401."""
    app = create_app()
    client = TestClient(app)

    # Missing authorization header
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "qwen2.5:3b",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )

    assert response.status_code == 401

    metrics_text = generate_latest(gateway_metrics.registry).decode("utf-8")

    # Model is unknown because authentication failed before route handler
    assert 'gateway_requests_total{model="unknown",status_code="401"} 1.0' in metrics_text
    assert (
        'gateway_request_duration_seconds_count{model="unknown",status_code="401"} 1.0'
        in metrics_text
    )


def test_request_metrics_validation_failure() -> None:
    """Verify that validation failure (e.g. 422 Unprocessable) increments requests_total."""
    app = create_app()
    # Apply default auth override for create_app() test instance
    app.dependency_overrides[get_authenticated_tenant] = lambda: TenantContext(
        tenant_id="test_tenant_validation",
        tenant_name="Validation Org",
        api_key_id=uuid4(),
        key_prefix="gw_live_test",
        is_admin=False,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={"invalid_payload": True},
        headers={"Authorization": "Bearer gw_live_any_key"},
    )

    # FastAPI returns 422 for pydantic validation errors
    assert response.status_code in (400, 422)
    status_str = str(response.status_code)

    metrics_text = generate_latest(gateway_metrics.registry).decode("utf-8")
    expected_sample = f'gateway_requests_total{{model="unknown",status_code="{status_str}"}} 1.0'
    assert expected_sample in metrics_text
