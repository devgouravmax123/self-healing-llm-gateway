"""Unit and integration tests for Phase 14.3a Reliability Metrics.

Tests:
1. Retries:
   - First provider attempt does not increment retry counter.
   - Same-provider retry increments retries_total exactly once.
   - Multiple retries increment exactly once per additional attempt.
   - Distinct retry reasons produce bounded ErrorCategory labels.
2. Failover:
   - A -> B failover after retry exhaustion increments failovers_total exactly once.
   - Labels from_provider="provider_a", to_provider="provider_b", reason="retry_exhausted".
   - Circuit-skipped provider does NOT increment failover from that provider.
3. Circuit:
   - Transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED / OPEN) update one-hot gauge accurately.
"""

from unittest.mock import AsyncMock

import pytest
from prometheus_client import generate_latest

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
)
from app.observability.metrics import GatewayMetrics, create_metrics_registry
from app.reliability.circuit_breaker import CircuitBreakerManager
from app.reliability.error_classifier import ErrorCategory
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import Router
from app.storage.circuit_storage import CircuitState, InMemoryCircuitStorage


@pytest.fixture
def custom_metrics() -> GatewayMetrics:
    """Provide an isolated GatewayMetrics instance for reliability metrics testing."""
    registry = create_metrics_registry()
    return GatewayMetrics(registry=registry)


# --- Retry Metric Tests ---


@pytest.mark.asyncio
async def test_first_attempt_does_not_increment_retry_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that a first successful provider attempt does not increment gateway_retries_total."""
    mock_service = AsyncMock()
    mock_service.execute_chat_completion.return_value = ChatCompletionResponse(
        id="chatcmpl_first_success",
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="OK")
            )
        ],
    )

    retry_mgr = RetryManager(
        provider_service=mock_service,
        metrics=custom_metrics,
    )
    target = ProviderTarget(id="prov_a", provider="ollama", model="qwen2.5:3b")
    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )

    resp = await retry_mgr.execute_with_retry(request=req, request_id="req_1", target=target)
    assert resp.id == "chatcmpl_first_success"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert "gateway_retries_total{" not in text


@pytest.mark.asyncio
async def test_single_retry_increments_retries_total_with_reason(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that 1 retry attempt on the same provider increments retries_total exactly once."""
    mock_service = AsyncMock()
    mock_service.execute_chat_completion.side_effect = [
        ProviderError(
            message="Timeout",
            status_code=504,
            category=ErrorCategory.TIMEOUT.value,
            retryable=True,
        ),
        ChatCompletionResponse(
            id="chatcmpl_retry_ok",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="Retry OK")
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
    target = ProviderTarget(id="prov_retry", provider="ollama", model="qwen2.5:3b")
    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )

    resp = await retry_mgr.execute_with_retry(request=req, request_id="req_2", target=target)
    assert resp.id == "chatcmpl_retry_ok"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_retries_total{provider="prov_retry",reason="TIMEOUT"} 1.0' in text


@pytest.mark.asyncio
async def test_multiple_retries_increment_each_additional_attempt(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that multiple retries increment retries_total once per each retry attempt."""
    mock_service = AsyncMock()
    mock_service.execute_chat_completion.side_effect = [
        ProviderError(
            message="Server Error",
            status_code=502,
            category=ErrorCategory.SERVER_ERROR.value,
            retryable=True,
        ),
        ProviderError(
            message="Rate Limited",
            status_code=429,
            category=ErrorCategory.RATE_LIMITED.value,
            retryable=True,
        ),
        ChatCompletionResponse(
            id="chatcmpl_multi_ok",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="Multi OK")
                )
            ],
        ),
    ]

    sleep_mock = AsyncMock()
    retry_mgr = RetryManager(
        config=Settings(MAX_RETRIES=2),
        provider_service=mock_service,
        sleep_func=sleep_mock,
        metrics=custom_metrics,
    )
    target = ProviderTarget(id="prov_multi", provider="ollama", model="qwen2.5:3b")
    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )

    resp = await retry_mgr.execute_with_retry(request=req, request_id="req_3", target=target)
    assert resp.id == "chatcmpl_multi_ok"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_retries_total{provider="prov_multi",reason="SERVER_ERROR"} 1.0' in text
    assert 'gateway_retries_total{provider="prov_multi",reason="RATE_LIMITED"} 1.0' in text


# --- Failover Metric Tests ---


@pytest.mark.asyncio
async def test_failover_increments_after_retry_exhaustion(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that a genuine failover from Provider A to Provider B increments failovers_total."""
    target_a = ProviderTarget(id="prov_a", provider="ollama", model="qwen2.5:3b", priority=1)
    target_b = ProviderTarget(id="prov_b", provider="ollama", model="qwen2.5:3b", priority=2)

    registry = ProviderRegistry()
    registry.register(target_a)
    registry.register(target_b)

    circuit_storage = InMemoryCircuitStorage()
    circuit_mgr = CircuitBreakerManager(storage=circuit_storage, metrics=custom_metrics)
    router = Router(registry=registry, circuit_manager=circuit_mgr)

    mock_service = AsyncMock()

    def side_effect_fn(*args: object, **kwargs: object) -> ChatCompletionResponse:
        target = kwargs.get("target") or (args[2] if len(args) > 2 else None)
        assert isinstance(target, ProviderTarget)
        if target.id == "prov_a":
            raise ProviderError(
                message="Timeout on A",
                status_code=504,
                category=ErrorCategory.TIMEOUT.value,
                retryable=True,
            )
        return ChatCompletionResponse(
            id="chatcmpl_b_ok",
            model="qwen2.5:3b",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="B OK")
                )
            ],
        )

    mock_service.execute_chat_completion.side_effect = side_effect_fn

    sleep_mock = AsyncMock()
    retry_mgr = RetryManager(
        config=Settings(MAX_RETRIES=1),
        provider_service=mock_service,
        sleep_func=sleep_mock,
        metrics=custom_metrics,
    )

    failover_mgr = FailoverManager(
        router_instance=router,
        retry_instance=retry_mgr,
        circuit_instance=circuit_mgr,
        metrics=custom_metrics,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Failover Test")]
    )
    resp = await failover_mgr.execute_with_failover(
        request=req, request_id="req_failover_1", tenant_id="tenant_1"
    )

    assert resp.id == "chatcmpl_b_ok"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert (
        "gateway_failovers_total{"
        'from_provider="prov_a",reason="retry_exhausted",to_provider="prov_b"} 1.0' in text
    )


@pytest.mark.asyncio
async def test_circuit_skipped_provider_does_not_count_as_failover_source(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that a candidate skipped due to OPEN circuit is not counted in failovers_total."""
    target_a = ProviderTarget(id="prov_a_open", provider="ollama", model="qwen2.5:3b", priority=1)
    target_b = ProviderTarget(id="prov_b_ready", provider="ollama", model="qwen2.5:3b", priority=2)

    registry = ProviderRegistry()
    registry.register(target_a)
    registry.register(target_b)

    circuit_storage = InMemoryCircuitStorage()
    # Pre-trip Provider A to OPEN
    await circuit_storage.update_snapshot(provider_id="prov_a_open", state=CircuitState.OPEN)

    circuit_mgr = CircuitBreakerManager(storage=circuit_storage, metrics=custom_metrics)
    router = Router(registry=registry, circuit_manager=circuit_mgr)

    mock_service = AsyncMock()
    mock_service.execute_chat_completion.return_value = ChatCompletionResponse(
        id="chatcmpl_b_direct",
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                message=ChatCompletionMessageResponse(role="assistant", content="Direct B")
            )
        ],
    )

    retry_mgr = RetryManager(
        provider_service=mock_service,
        metrics=custom_metrics,
    )

    failover_mgr = FailoverManager(
        router_instance=router,
        retry_instance=retry_mgr,
        circuit_instance=circuit_mgr,
        metrics=custom_metrics,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Skip Test")]
    )
    resp = await failover_mgr.execute_with_failover(
        request=req, request_id="req_skip_1", tenant_id="tenant_1"
    )

    assert resp.id == "chatcmpl_b_direct"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    # No failover should be recorded because Provider A never executed
    assert "gateway_failovers_total{" not in text


# --- Circuit State Metric Tests ---


@pytest.mark.asyncio
async def test_circuit_state_one_hot_gauge_transitions(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify circuit state transitions (CLOSED -> OPEN -> HALF_OPEN) update one-hot gauge."""
    storage = InMemoryCircuitStorage()
    cfg = Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=10.0)
    cb_mgr = CircuitBreakerManager(config=cfg, storage=storage, metrics=custom_metrics)

    provider_id = "prov_cb_test"

    # Initial state: CLOSED
    cb_mgr.set_circuit_metric(provider_id, CircuitState.CLOSED)
    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert f'gateway_circuit_state{{provider="{provider_id}",state="closed"}} 1.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="open"}} 0.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="half_open"}} 0.0' in text

    # Failure 1 (threshold is 2): remains CLOSED
    await cb_mgr.record_failure(
        provider_id,
        ProviderError(
            message="Err 1",
            category=ErrorCategory.SERVER_ERROR.value,
        ),
    )
    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert f'gateway_circuit_state{{provider="{provider_id}",state="closed"}} 1.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="open"}} 0.0' in text

    # Failure 2: reaches threshold -> trips to OPEN
    await cb_mgr.record_failure(
        provider_id,
        ProviderError(
            message="Err 2",
            category=ErrorCategory.SERVER_ERROR.value,
        ),
    )
    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert f'gateway_circuit_state{{provider="{provider_id}",state="closed"}} 0.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="open"}} 1.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="half_open"}} 0.0' in text

    # Fast-forward cooldown by updating opened_at
    await storage.update_snapshot(provider_id=provider_id, opened_at=storage._time_func() - 20.0)

    # Acquire permission: transitions to HALF_OPEN
    await cb_mgr.acquire_permission(provider_id)
    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert f'gateway_circuit_state{{provider="{provider_id}",state="closed"}} 0.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="open"}} 0.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="half_open"}} 1.0' in text

    # Success in HALF_OPEN: transitions back to CLOSED
    await cb_mgr.record_success(provider_id)
    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert f'gateway_circuit_state{{provider="{provider_id}",state="closed"}} 1.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="open"}} 0.0' in text
    assert f'gateway_circuit_state{{provider="{provider_id}",state="half_open"}} 0.0' in text
