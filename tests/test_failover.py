"""Unit and integration tests for Phase 08 Provider Failover mechanism."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import CircuitBreakerError, ProviderError
from app.main import app
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.reliability.circuit_breaker import (
    CircuitBreakerManager,
    CircuitState,
    circuit_breaker_manager,
)
from app.reliability.failover import FailoverManager
from app.reliability.retry import RetryManager
from app.routing.provider_registry import ProviderRegistry
from app.routing.router import NoHealthyProviderError, Router


def create_test_response(
    content: str = "Success", model: str = "qwen2.5:3b"
) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-failover-test",
        created=1234567890,
        model=model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


@pytest.fixture(autouse=True)
def reset_global_states() -> None:
    """Ensure global circuit breaker and provider registry are cleanly reset."""
    circuit_breaker_manager.reset_all()


# --- Unit Tests for FailoverManager & Candidate Selection ---


@pytest.mark.asyncio
async def test_primary_provider_succeeds_no_failover() -> None:
    """Test 1: When primary provider succeeds, secondary provider is never called."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.return_value = create_test_response("Response from A")

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    res = await failover.execute_with_failover(req, request_id="req_test_1")

    assert res.choices[0].message.content == "Response from A"
    assert mock_retry_manager.execute_with_retry.call_count == 1
    call_target = mock_retry_manager.execute_with_retry.call_args[1]["target"]
    assert call_target.id == "provider_a"


@pytest.mark.asyncio
async def test_primary_fails_and_secondary_succeeds() -> None:
    """Test 2: When primary provider fails, secondary provider is selected and request succeeds."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    # Provider A raises ProviderError(TIMEOUT), Provider B returns success
    mock_retry_manager.execute_with_retry.side_effect = [
        ProviderError("Timeout on A", category="TIMEOUT", status_code=504),
        create_test_response("Response from B"),
    ]

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    res = await failover.execute_with_failover(req, request_id="req_test_2")

    assert res.choices[0].message.content == "Response from B"
    assert mock_retry_manager.execute_with_retry.call_count == 2
    # Verify call sequence
    first_call_target = mock_retry_manager.execute_with_retry.call_args_list[0][1]["target"]
    second_call_target = mock_retry_manager.execute_with_retry.call_args_list[1][1]["target"]
    assert first_call_target.id == "provider_a"
    assert second_call_target.id == "provider_b"


@pytest.mark.asyncio
async def test_primary_failure_excludes_primary_from_failover_loop() -> None:
    """Test 3: Provider A is excluded and not retried in same failover chain."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    router = Router(registry=registry, circuit_manager=cb_manager)

    # Exclude provider_a
    candidates = router.get_candidates(
        request=ChatCompletionRequest(
            model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
        ),
        exclude_provider_ids={"provider_a"},
    )
    assert len(candidates) == 1
    assert candidates[0].id == "provider_b"


@pytest.mark.asyncio
async def test_multiple_provider_failover_chain() -> None:
    """Test 4: Failover progresses through A -> B -> C without repeated selections."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    p_c = ProviderTarget(id="provider_c", provider="ollama", model="qwen2.5:3b", priority=3)
    registry.register(p_a)
    registry.register(p_b)
    registry.register(p_c)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=5))
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.side_effect = [
        ProviderError("Timeout A", category="TIMEOUT", status_code=504),
        ProviderError("Server error B", category="SERVER_ERROR", status_code=502),
        create_test_response("Response from C"),
    ]

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    res = await failover.execute_with_failover(req, request_id="req_test_4")

    assert res.choices[0].message.content == "Response from C"
    assert mock_retry_manager.execute_with_retry.call_count == 3
    targets_called = [
        c[1]["target"].id for c in mock_retry_manager.execute_with_retry.call_args_list
    ]
    assert targets_called == ["provider_a", "provider_b", "provider_c"]


@pytest.mark.asyncio
async def test_all_providers_fail_raises_final_error() -> None:
    """Test 5: When all eligible providers fail, the last failure is cleanly raised."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager()
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.side_effect = [
        ProviderError("504 Timeout A", category="TIMEOUT", status_code=504),
        ProviderError("502 Server error B", category="SERVER_ERROR", status_code=502),
    ]

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=2),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    with pytest.raises(ProviderError) as exc_info:
        await failover.execute_with_failover(req, request_id="req_all_fail")

    assert exc_info.value.status_code == 502
    assert exc_info.value.category == "SERVER_ERROR"


@pytest.mark.asyncio
async def test_no_failover_for_bad_request_or_auth_error() -> None:
    """Test 6 & 7: BAD_REQUEST or AUTH_ERROR fail immediately without triggering failover."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager()
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.side_effect = ProviderError(
        "Invalid parameters", category="BAD_REQUEST", status_code=400, retryable=False
    )

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    with pytest.raises(ProviderError) as exc_info:
        await failover.execute_with_failover(req, request_id="req_bad_req")

    assert exc_info.value.category == "BAD_REQUEST"
    assert exc_info.value.status_code == 400
    # Provider B was never called
    assert mock_retry_manager.execute_with_retry.call_count == 1


@pytest.mark.asyncio
async def test_open_circuit_provider_is_skipped() -> None:
    """Test 8: An OPEN circuit provider is skipped immediately in favor of a CLOSED provider."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=2))
    # Trip Provider A to OPEN
    err = ProviderError("Timeout", category="TIMEOUT")
    await cb_manager.record_failure("provider_a", err)
    await cb_manager.record_failure("provider_a", err)
    assert cb_manager.get_state("provider_a") == CircuitState.OPEN

    router = Router(registry=registry, circuit_manager=cb_manager)
    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.return_value = create_test_response("Response from B")

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    res = await failover.execute_with_failover(req, request_id="req_open_skip")

    assert res.choices[0].message.content == "Response from B"
    assert mock_retry_manager.execute_with_retry.call_count == 1
    call_target = mock_retry_manager.execute_with_retry.call_args[1]["target"]
    assert call_target.id == "provider_b"


@pytest.mark.asyncio
async def test_all_providers_open_returns_503() -> None:
    """Test 9: When all providers have OPEN circuits, 503 is returned without provider calls."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=1))
    err = ProviderError("Timeout", category="TIMEOUT")
    await cb_manager.record_failure("provider_a", err)
    await cb_manager.record_failure("provider_b", err)
    assert cb_manager.get_state("provider_a") == CircuitState.OPEN
    assert cb_manager.get_state("provider_b") == CircuitState.OPEN

    router = Router(registry=registry, circuit_manager=cb_manager)
    mock_retry_manager = AsyncMock(spec=RetryManager)

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    with pytest.raises((NoHealthyProviderError, CircuitBreakerError)) as exc_info:
        await failover.execute_with_failover(req, request_id="req_all_open")

    assert exc_info.value.status_code == 503
    mock_retry_manager.execute_with_retry.assert_not_called()


@pytest.mark.asyncio
async def test_model_compatibility_prevents_incompatible_failover() -> None:
    """Test 10: Failover respects specific model constraints without invalid fallback."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="custom-llama:70b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="mistral:7b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager()
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.side_effect = ProviderError(
        "Timeout on A", category="TIMEOUT", status_code=504
    )

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    # Request explicitly requests custom-llama:70b
    req = ChatCompletionRequest(
        model="custom-llama:70b",
        messages=[ChatMessage(role="user", content="Hi")],
    )

    with pytest.raises(ProviderError):
        await failover.execute_with_failover(req, request_id="req_compat_test")

    # Only Provider A was called; Provider B does not serve custom-llama:70b
    assert mock_retry_manager.execute_with_retry.call_count == 1
    assert mock_retry_manager.execute_with_retry.call_args[1]["target"].id == "provider_a"


@pytest.mark.asyncio
async def test_circuit_accounting_records_one_failure_per_exhausted_provider() -> None:
    """Test 12: An exhausted provider execution contributes exactly 1 circuit failure event."""
    registry = ProviderRegistry()
    p_a = ProviderTarget(id="provider_a", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="provider_b", provider="ollama", model="qwen2.5:3b", priority=2)
    registry.register(p_a)
    registry.register(p_b)

    cb_manager = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    router = Router(registry=registry, circuit_manager=cb_manager)

    mock_retry_manager = AsyncMock(spec=RetryManager)
    mock_retry_manager.execute_with_retry.side_effect = [
        ProviderError("Timeout on A", category="TIMEOUT", status_code=504),
        create_test_response("Response from B"),
    ]

    failover = FailoverManager(
        config=Settings(MAX_FAILOVER_PROVIDERS=3),
        router_instance=router,
        retry_instance=mock_retry_manager,
        circuit_instance=cb_manager,
    )

    req = ChatCompletionRequest(
        model="qwen2.5:3b", messages=[ChatMessage(role="user", content="Hi")]
    )
    await failover.execute_with_failover(req, request_id="req_circuit_accounting")

    # Provider A recorded 1 failure
    circuit_a = cb_manager._get_or_create_circuit("provider_a")
    assert circuit_a.consecutive_failures == 1

    # Provider B recorded success -> 0 failures
    circuit_b = cb_manager._get_or_create_circuit("provider_b")
    assert circuit_b.consecutive_failures == 0


# --- HTTP Endpoint End-to-End Tests ---


@pytest.mark.asyncio
async def test_chat_completions_endpoint_end_to_end_failover() -> None:
    """Test 14 & 15: POST /v1/chat/completions succeeds via failover and preserves headers."""
    # Set up multi-provider registry
    test_registry = ProviderRegistry()
    p_a = ProviderTarget(id="ollama_primary", provider="ollama", model="qwen2.5:3b", priority=1)
    p_b = ProviderTarget(id="ollama_secondary", provider="ollama", model="qwen2.5:3b", priority=2)
    test_registry.register(p_a)
    test_registry.register(p_b)

    custom_router = Router(registry=test_registry, circuit_manager=circuit_breaker_manager)
    mock_response_b = create_test_response(
        "Failover successfully recovered via secondary provider!"
    )

    with (
        patch("app.api.routes_chat.gateway_router", custom_router),
        patch(
            "app.api.routes_chat.retry_manager.execute_with_retry", new_callable=AsyncMock
        ) as mock_retry,
    ):
        mock_retry.side_effect = [
            ProviderError(
                "Primary connection failed", category="CONNECTION_ERROR", status_code=503
            ),
            mock_response_b,
        ]

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            custom_id = "req_failover_e2e_777"
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen2.5:3b",
                    "messages": [{"role": "user", "content": "Explain failover."}],
                },
                headers={"X-Request-ID": custom_id},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "chatcmpl-failover-test"
            assert (
                data["choices"][0]["message"]["content"]
                == "Failover successfully recovered via secondary provider!"
            )
            assert response.headers["X-Request-ID"] == custom_id
            assert mock_retry.call_count == 2
