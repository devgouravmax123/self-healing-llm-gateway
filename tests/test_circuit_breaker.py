from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import CircuitBreakerError, ProviderError
from app.main import app
from app.models.provider import ProviderTarget
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


class MockLiteLLMResponse:
    """Mock LiteLLM completion response."""

    def __init__(self, content: str = "Mock response", model: str = "ollama/qwen2.5:3b") -> None:
        self.id = "chatcmpl-cb-test-123"
        self.created = 1234567890
        self.model = model
        self.choices = [
            type(
                "Choice",
                (),
                {
                    "index": 0,
                    "message": type(
                        "Message",
                        (),
                        {"role": "assistant", "content": content},
                    )(),
                    "finish_reason": "stop",
                },
            )()
        ]
        self.usage = type(
            "Usage",
            (),
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )()


def create_test_response(content: str = "Success") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id="chatcmpl-test",
        created=1234567890,
        model="qwen2.5:3b",
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatCompletionMessageResponse(role="assistant", content=content),
                finish_reason="stop",
            )
        ],
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def create_test_target(provider_id: str = "test_provider") -> ProviderTarget:
    return ProviderTarget(
        id=provider_id,
        provider="ollama",
        model="qwen2.5:3b",
        enabled=True,
    )


@pytest.fixture(autouse=True)
def reset_global_circuit_breaker() -> None:
    """Ensure global circuit breaker state is cleanly reset between tests."""
    circuit_breaker_manager.reset_all()


# --- Unit Tests for CircuitBreakerManager ---


@pytest.mark.asyncio
async def test_initial_closed_state() -> None:
    """Test 1: New provider starts in CLOSED state and permission is granted."""
    cb = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    assert cb.get_state("provider_a") == CircuitState.CLOSED

    # Should not raise exception
    await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_successful_request_does_not_open_circuit() -> None:
    """Test 2: Successful provider executions keep the circuit in CLOSED state."""
    cb = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))

    for _ in range(5):
        await cb.acquire_permission("provider_a")
        await cb.record_success("provider_a")

    assert cb.get_state("provider_a") == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_failures_accumulate_and_threshold_opens_circuit() -> None:
    """Test 3 & 4: Failures accumulate up to threshold, then transition CLOSED -> OPEN."""
    cb = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=3))
    err = ProviderError("Server error", category="SERVER_ERROR", retryable=True)

    # Failure 1
    await cb.record_failure("provider_a", err)
    assert cb.get_state("provider_a") == CircuitState.CLOSED

    # Failure 2
    await cb.record_failure("provider_a", err)
    assert cb.get_state("provider_a") == CircuitState.CLOSED

    # Failure 3 -> Reaches threshold (3) -> Transitions to OPEN
    await cb.record_failure("provider_a", err)
    assert cb.get_state("provider_a") == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_blocks_permission_immediately() -> None:
    """Test 5: When OPEN, acquire_permission raises CircuitBreakerError immediately."""
    cb = CircuitBreakerManager(
        config=Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=30.0)
    )
    err = ProviderError("Timeout", category="TIMEOUT", retryable=True)

    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    assert cb.get_state("provider_a") == CircuitState.OPEN

    with pytest.raises(CircuitBreakerError) as exc_info:
        await cb.acquire_permission("provider_a")

    assert exc_info.value.provider == "provider_a"
    assert exc_info.value.status_code == 503
    assert "circuit protection" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_open_remains_open_during_cooldown() -> None:
    """Test 6: During cooldown, circuit remains OPEN and continues blocking requests."""
    current_mock_time = 1000.0

    def mock_time() -> float:
        return current_mock_time

    cb = CircuitBreakerManager(
        config=Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=30.0),
        time_func=mock_time,
    )
    err = ProviderError("Timeout", category="TIMEOUT")

    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)
    assert cb.get_state("provider_a") == CircuitState.OPEN

    # Advance time by 15s (< 30s cooldown)
    current_mock_time += 15.0

    assert cb.get_state("provider_a") == CircuitState.OPEN
    with pytest.raises(CircuitBreakerError):
        await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_cooldown_transitions_to_half_open() -> None:
    """Test 7: After cooldown expires, circuit transitions OPEN -> HALF_OPEN."""
    current_mock_time = 1000.0

    def mock_time() -> float:
        return current_mock_time

    cb = CircuitBreakerManager(
        config=Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=30.0),
        time_func=mock_time,
    )
    err = ProviderError("Timeout", category="TIMEOUT")

    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    # Advance time past cooldown (31s > 30s)
    current_mock_time += 31.0

    assert cb.get_state("provider_a") == CircuitState.HALF_OPEN
    # Permission for probe should succeed
    await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_half_open_success_transitions_to_closed() -> None:
    """Test 8: Successful probe in HALF_OPEN state transitions HALF_OPEN -> CLOSED."""
    current_mock_time = 1000.0

    def mock_time() -> float:
        return current_mock_time

    cb = CircuitBreakerManager(
        config=Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=30.0),
        time_func=mock_time,
    )
    err = ProviderError("Timeout", category="TIMEOUT")

    # Trip the circuit
    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    # Advance time to half-open
    current_mock_time += 31.0
    assert cb.get_state("provider_a") == CircuitState.HALF_OPEN

    # Acquire probe and report success
    await cb.acquire_permission("provider_a")
    await cb.record_success("provider_a")

    # State should now be CLOSED and failure count reset
    assert cb.get_state("provider_a") == CircuitState.CLOSED

    # Should permit requests normally
    await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_half_open_failure_transitions_to_open() -> None:
    """Test 9: Failed probe in HALF_OPEN state transitions HALF_OPEN -> OPEN immediately."""
    current_mock_time = 1000.0

    def mock_time() -> float:
        return current_mock_time

    cb = CircuitBreakerManager(
        config=Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=30.0),
        time_func=mock_time,
    )
    err = ProviderError("Timeout", category="TIMEOUT")

    # Trip the circuit
    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    # Advance time to half-open
    current_mock_time += 31.0
    assert cb.get_state("provider_a") == CircuitState.HALF_OPEN

    # Acquire probe and report failure
    await cb.acquire_permission("provider_a")
    await cb.record_failure("provider_a", err)

    # State should be re-tripped to OPEN
    assert cb.get_state("provider_a") == CircuitState.OPEN

    # Immediate next request before new cooldown should be blocked
    with pytest.raises(CircuitBreakerError):
        await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_non_provider_errors_do_not_trip_circuit() -> None:
    """Test 10: Client-side errors (BAD_REQUEST, AUTH_ERROR) do not increase failure counter."""
    cb = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=2))
    bad_req_err = ProviderError("Bad request", category="BAD_REQUEST", retryable=False)
    auth_err = ProviderError("Auth error", category="AUTH_ERROR", retryable=False)

    for _ in range(5):
        await cb.record_failure("provider_a", bad_req_err)
        await cb.record_failure("provider_a", auth_err)

    # Circuit must still be CLOSED
    assert cb.get_state("provider_a") == CircuitState.CLOSED
    await cb.acquire_permission("provider_a")


@pytest.mark.asyncio
async def test_provider_isolation() -> None:
    """Test 11: One OPEN provider does not impact another CLOSED provider."""
    cb = CircuitBreakerManager(config=Settings(CIRCUIT_FAILURE_THRESHOLD=2))
    err = ProviderError("Timeout", category="TIMEOUT")

    # Trip provider_a
    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    assert cb.get_state("provider_a") == CircuitState.OPEN
    assert cb.get_state("provider_b") == CircuitState.CLOSED

    # provider_a is blocked
    with pytest.raises(CircuitBreakerError):
        await cb.acquire_permission("provider_a")

    # provider_b is allowed
    await cb.acquire_permission("provider_b")


@pytest.mark.asyncio
async def test_half_open_concurrency_probe_limit() -> None:
    """Test 13: HALF_OPEN limits concurrency to configured max probes."""
    current_mock_time = 1000.0

    def mock_time() -> float:
        return current_mock_time

    cb = CircuitBreakerManager(
        config=Settings(
            CIRCUIT_FAILURE_THRESHOLD=2,
            CIRCUIT_COOLDOWN_SECONDS=30.0,
            CIRCUIT_HALF_OPEN_MAX_PROBES=1,
        ),
        time_func=mock_time,
    )
    err = ProviderError("Timeout", category="TIMEOUT")

    # Trip to OPEN
    await cb.record_failure("provider_a", err)
    await cb.record_failure("provider_a", err)

    # Advance time to half-open
    current_mock_time += 31.0
    assert cb.get_state("provider_a") == CircuitState.HALF_OPEN

    # First probe claims the slot
    await cb.acquire_permission("provider_a")

    # Second concurrent probe must be rejected while first probe is in-flight
    with pytest.raises(CircuitBreakerError) as exc_info:
        await cb.acquire_permission("provider_a")

    assert "recovering in HALF_OPEN" in exc_info.value.message


# --- HTTP Endpoint and Integration Tests ---


@pytest.mark.asyncio
async def test_chat_completions_circuit_open_blocks_and_preserves_request_id() -> None:
    """Test 14 & 5: When circuit is OPEN, chat completions returns 503 CircuitBreakerError."""
    target_id = "ollama_default"

    # Trip global circuit breaker
    err = ProviderError("Service unavailable", category="UPSTREAM_ERROR")
    for _ in range(5):
        await circuit_breaker_manager.record_failure(target_id, err)

    assert circuit_breaker_manager.get_state(target_id) == CircuitState.OPEN

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        custom_id = "req_circuit_breaker_test_888"
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen2.5:3b",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
            headers={"X-Request-ID": custom_id},
        )

        assert response.status_code == 503
        data = response.json()
        assert data["error"]["type"] == "CircuitBreakerError"
        assert "circuit protection" in data["error"]["message"].lower()
        assert response.headers["X-Request-ID"] == custom_id


@pytest.mark.asyncio
async def test_retry_manager_and_circuit_breaker_interaction() -> None:
    """Test 12: In HTTP route, retries on a request record as one failure on final failure."""
    from litellm.exceptions import Timeout

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (
            patch("litellm.acompletion", new_callable=AsyncMock) as mock_acompletion,
            patch("app.reliability.retry.RetryManager.calculate_backoff", return_value=0.001),
        ):
            # All attempts fail with Timeout
            mock_acompletion.side_effect = Timeout(
                "Request timed out", model="ollama/qwen2.5:3b", llm_provider="ollama"
            )

            # Request 1 fails (exhausts 3 attempts = 1 initial + 2 retries)
            res1 = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen2.5:3b",
                    "messages": [{"role": "user", "content": "Hello!"}],
                },
            )
            assert res1.status_code == 504

            # Verify circuit state recorded exactly 1 failure event for the request
            circuit = circuit_breaker_manager._get_or_create_circuit("ollama_default")
            assert circuit.consecutive_failures == 1
            assert circuit.state == CircuitState.CLOSED
