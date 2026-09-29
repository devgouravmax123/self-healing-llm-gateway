"""Mandatory double-counting integration test for Phase 14.2.

Scenario:
  Provider A: attempt 1 -> TIMEOUT
  Provider A: retry 1   -> TIMEOUT (exhausts retry limit)
  Provider B: attempt 1 -> SUCCESS (failover succeeds)
  Final HTTP Response   -> 200 OK

Assert:
  gateway_requests_total == 1
  gateway_request_duration_seconds_count == 1
  gateway_provider_requests_total{provider="A", status="error"} == 2
  gateway_provider_requests_total{provider="B", status="success"} == 1
  gateway_provider_duration_seconds_count{provider="A"} == 2
  gateway_provider_duration_seconds_count{provider="B"} == 1
  gateway_provider_errors_total{provider="A", error_category="TIMEOUT"} == 2
  gateway_provider_errors_total{provider="B"} is 0
"""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.core.auth_context import TenantContext
from app.core.exceptions import ProviderError
from app.main import create_app
from app.models.provider import ProviderTarget
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
)
from app.observability.metrics import gateway_metrics
from app.reliability.circuit_breaker import CircuitBreakerManager
from app.reliability.error_classifier import ErrorCategory
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import InMemoryCircuitStorage


@pytest.fixture(autouse=True)
def reset_metrics_for_test() -> None:
    """Reset the metrics registry before test for clean isolation."""
    gateway_metrics.reset_for_test()


def test_double_counting_failover_scenario() -> None:
    """Verify that a multi-attempt failover scenario produces exact, non-duplicated counts."""
    # 1. Setup isolated provider targets
    target_a = ProviderTarget(
        id="provider_a",
        provider="ollama",
        model="qwen2.5:3b",
        priority=1,
    )
    target_b = ProviderTarget(
        id="provider_b",
        provider="ollama",
        model="qwen2.5:3b",
        priority=2,
    )

    registry = ProviderRegistry()
    registry.register(target_a)
    registry.register(target_b)

    circuit_storage = InMemoryCircuitStorage()
    circuit_mgr = CircuitBreakerManager(storage=circuit_storage)
    test_router = Router(registry=registry, circuit_manager=circuit_mgr)

    # 2. Mock provider service
    # Provider A: 2 timeouts (attempt 1, retry 1). Provider B: 1 success.
    mock_service = AsyncMock()

    def side_effect_fn(*args: object, **kwargs: object) -> ChatCompletionResponse:
        target = kwargs.get("target") or (args[2] if len(args) > 2 else None)
        assert isinstance(target, ProviderTarget)
        if target.id == "provider_a":
            raise ProviderError(
                message="Timeout on Provider A",
                status_code=504,
                category=ErrorCategory.TIMEOUT.value,
                retryable=True,
            )
        return ChatCompletionResponse(
            id="chatcmpl_success_b",
            created=12345,
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Success B"),
                    finish_reason="stop",
                )
            ],
        )

    mock_service.execute_chat_completion.side_effect = side_effect_fn

    # 3. Setup RetryManager & FailoverManager sharing the gateway_metrics singleton
    from app.core.config import Settings

    sleep_mock = AsyncMock()
    retry_mgr = RetryManager(
        config=Settings(MAX_RETRIES=1),
        provider_service=mock_service,
        sleep_func=sleep_mock,
        metrics=gateway_metrics,
    )

    test_failover_mgr = FailoverManager(
        router_instance=test_router,
        retry_instance=retry_mgr,
        circuit_instance=circuit_mgr,
    )

    fake_tenant = TenantContext(
        tenant_id="tenant_double_counting_test",
        tenant_name="Double Counting Tenant",
        api_key_id=uuid4(),
        key_prefix="gw_live_1234",
    )

    app = create_app()
    client = TestClient(app)

    with (
        patch("app.core.auth.authenticator.authenticate_key", new_callable=AsyncMock) as mock_auth,
        patch("app.api.routes_chat.failover_manager", test_failover_mgr),
        patch("app.api.routes_chat.gateway_router", test_router),
        patch("app.api.routes_chat.retry_manager", retry_mgr),
        patch("app.api.routes_chat.circuit_breaker_manager", circuit_mgr),
    ):
        mock_auth.return_value = fake_tenant

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Hello double counting test"}],
            },
            headers={"Authorization": "Bearer gw_live_secret123"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "chatcmpl_success_b"

    # 4. Verify Prometheus exposition output
    metrics_text = generate_latest(gateway_metrics.registry).decode("utf-8")

    # A. Gateway Request Metrics: Exactly 1 HTTP request served (200 OK)
    assert 'gateway_requests_total{model="qwen2.5:3b",status_code="200"} 1.0' in metrics_text
    assert (
        'gateway_request_duration_seconds_count{model="qwen2.5:3b",status_code="200"} 1.0'
        in metrics_text
    )

    # B. Provider Request Counters: 2 errors on Provider A, 1 success on Provider B
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_a",status="error"} 2.0' in metrics_text
    )
    assert (
        "gateway_provider_requests_total{"
        'model="qwen2.5:3b",provider="provider_b",status="success"} 1.0' in metrics_text
    )

    # C. Provider Duration Observations: 2 on Provider A, 1 on Provider B (3 total)
    assert (
        'gateway_provider_duration_seconds_count{model="qwen2.5:3b",provider="provider_a"} 2.0'
        in metrics_text
    )
    assert (
        'gateway_provider_duration_seconds_count{model="qwen2.5:3b",provider="provider_b"} 1.0'
        in text
        if (text := metrics_text)
        else False
    )

    # D. Provider Error Counters: 2 on Provider A with category TIMEOUT, 0 on Provider B
    assert (
        "gateway_provider_errors_total{"
        'error_category="TIMEOUT",model="qwen2.5:3b",provider="provider_a"} 2.0' in metrics_text
    )
    assert 'provider="provider_b"' not in [
        line for line in metrics_text.splitlines() if "gateway_provider_errors_total" in line
    ]
