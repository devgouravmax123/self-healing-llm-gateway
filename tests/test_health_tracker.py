"""Unit tests for Phase 10: Provider Health Tracker."""

import math
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ProviderError
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.reliability.circuit_breaker import CircuitState
from app.reliability.error_classifier import ErrorCategory
from app.reliability.health_tracker import HealthTracker, calculate_percentile
from app.reliability.retry import RetryManager
from app.storage.health_storage import InMemoryHealthStorage


# ==============================================================================
# Percentile Unit Tests
# ==============================================================================
def test_calculate_percentile_empty() -> None:
    """Empty list returns None."""
    assert calculate_percentile([], 50.0) is None
    assert calculate_percentile([], 95.0) is None
    assert calculate_percentile([], 99.0) is None


def test_calculate_percentile_single_value() -> None:
    """Single value returns that value."""
    assert calculate_percentile([42.0], 50.0) == 42.0
    assert calculate_percentile([42.0], 95.0) == 42.0
    assert calculate_percentile([42.0], 99.0) == 42.0


def test_calculate_percentile_known_distribution() -> None:
    """Known array of values produces deterministic, standard nearest-rank percentiles."""
    # 100 values: 1.0 to 100.0
    data = [float(i) for i in range(1, 101)]
    p50 = calculate_percentile(data, 50.0)
    p95 = calculate_percentile(data, 95.0)
    p99 = calculate_percentile(data, 99.0)

    assert p50 == 50.0
    assert p95 == 95.0
    assert p99 == 99.0


def test_calculate_percentile_small_list() -> None:
    """Small list with odd/even lengths."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    # 5 * 0.5 = 2.5 -> ceil = 3 -> index 2 -> 30.0
    assert calculate_percentile(data, 50.0) == 30.0
    # 5 * 0.95 = 4.75 -> ceil = 5 -> index 4 -> 50.0
    assert calculate_percentile(data, 95.0) == 50.0


# ==============================================================================
# HealthTracker Unit Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_initial_empty_state() -> None:
    """1. Initial empty state returns zero counts and null metrics."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)

    snapshot = await tracker.get_provider_snapshot("provider_empty")
    assert snapshot.provider_id == "provider_empty"
    assert snapshot.total_requests == 0
    assert snapshot.total_successes == 0
    assert snapshot.total_failures == 0
    assert snapshot.success_rate == 0.0
    assert snapshot.last_latency_ms is None
    assert snapshot.latency_p50_ms is None
    assert snapshot.latency_p95_ms is None
    assert snapshot.latency_p99_ms is None
    assert snapshot.last_success_at is None
    assert snapshot.last_failure_at is None
    assert snapshot.last_error_category is None
    assert snapshot.circuit_state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_successful_attempt() -> None:
    """2. Successful attempt records counts, latency, and success timestamp."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)
    t_before = time.time()

    await tracker.record_attempt("p1", success=True, latency_ms=120.5)

    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 1
    assert snapshot.total_successes == 1
    assert snapshot.total_failures == 0
    assert snapshot.success_rate == 1.0
    assert snapshot.last_latency_ms == 120.5
    assert snapshot.latency_p50_ms == 120.5
    assert snapshot.last_success_at is not None
    assert snapshot.last_success_at >= t_before
    assert snapshot.last_failure_at is None
    assert snapshot.last_error_category is None


@pytest.mark.asyncio
async def test_failed_attempt() -> None:
    """3. Failed attempt records counts, latency, and failure timestamp."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)
    t_before = time.time()

    await tracker.record_attempt(
        "p1", success=False, latency_ms=500.0, error_category=ErrorCategory.TIMEOUT
    )

    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 1
    assert snapshot.total_successes == 0
    assert snapshot.total_failures == 1
    assert snapshot.success_rate == 0.0
    assert snapshot.last_latency_ms == 500.0
    assert snapshot.last_failure_at is not None
    assert snapshot.last_failure_at >= t_before
    assert snapshot.last_success_at is None
    assert snapshot.last_error_category == ErrorCategory.TIMEOUT


@pytest.mark.asyncio
async def test_error_category_recording() -> None:
    """4. Error category is recorded and updated properly."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)

    await tracker.record_attempt(
        "p1", success=False, latency_ms=10.0, error_category=ErrorCategory.RATE_LIMITED
    )
    snap1 = await tracker.get_provider_snapshot("p1")
    assert snap1.last_error_category == ErrorCategory.RATE_LIMITED

    # Success should NOT erase last_error_category in Redis/storage
    await tracker.record_attempt("p1", success=True, latency_ms=15.0)
    snap2 = await tracker.get_provider_snapshot("p1")
    assert snap2.last_error_category == ErrorCategory.RATE_LIMITED

    # Another error updates it
    await tracker.record_attempt(
        "p1", success=False, latency_ms=20.0, error_category=ErrorCategory.SERVER_ERROR
    )
    snap3 = await tracker.get_provider_snapshot("p1")
    assert snap3.last_error_category == ErrorCategory.SERVER_ERROR


@pytest.mark.asyncio
async def test_success_rate_calculation() -> None:
    """5. Success rate is computed accurately across mixed attempts."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)

    # 3 successes, 1 failure -> 75%
    await tracker.record_attempt("p1", success=True, latency_ms=10.0)
    await tracker.record_attempt("p1", success=True, latency_ms=20.0)
    await tracker.record_attempt("p1", success=True, latency_ms=30.0)
    await tracker.record_attempt(
        "p1", success=False, latency_ms=40.0, error_category=ErrorCategory.TIMEOUT
    )

    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 4
    assert snapshot.total_successes == 3
    assert snapshot.total_failures == 1
    assert math.isclose(snapshot.success_rate, 0.75)


@pytest.mark.asyncio
async def test_timestamps_update() -> None:
    """6 & 7. Last success and failure timestamps update independently."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)

    await tracker.record_attempt("p1", success=True, latency_ms=10.0)
    snap1 = await tracker.get_provider_snapshot("p1")
    assert snap1.last_success_at is not None
    assert snap1.last_failure_at is None

    await tracker.record_attempt(
        "p1", success=False, latency_ms=20.0, error_category=ErrorCategory.TIMEOUT
    )
    snap2 = await tracker.get_provider_snapshot("p1")
    assert snap2.last_success_at == snap1.last_success_at
    assert snap2.last_failure_at is not None


@pytest.mark.asyncio
async def test_last_latency_and_rolling_window() -> None:
    """8 & 9. Latency window stores at most 50 samples in rolling order."""
    storage = InMemoryHealthStorage(max_latency_window=50)
    tracker = HealthTracker(storage=storage)

    # Record 60 attempts with latencies 1..60
    for i in range(1, 61):
        await tracker.record_attempt("p1", success=True, latency_ms=float(i))

    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 60
    assert snapshot.last_latency_ms == 60.0

    # Stored samples should be the last 50: 11..60
    data = await storage.get_health_data("p1")
    stored_latencies = data.latencies
    assert len(stored_latencies) == 50
    assert stored_latencies[0] == 60.0  # Most recent is at head
    assert stored_latencies[-1] == 11.0


@pytest.mark.asyncio
async def test_percentiles_calculation_on_window() -> None:
    """10, 11, 12. Percentiles p50, p95, p99 on 50 samples."""
    storage = InMemoryHealthStorage(max_latency_window=50)
    tracker = HealthTracker(storage=storage)

    # Record 50 samples from 2.0 to 100.0 (step 2.0)
    for i in range(1, 51):
        await tracker.record_attempt("p1", success=True, latency_ms=float(i * 2))

    snapshot = await tracker.get_provider_snapshot("p1")
    # 50 samples sorted: 2, 4, ..., 100
    # p50 = 50 * 0.5 = 25 -> item 25 = 50.0
    # p95 = 50 * 0.95 = 47.5 -> item 48 = 96.0
    # p99 = 50 * 0.99 = 49.5 -> item 50 = 100.0
    assert snapshot.latency_p50_ms == 50.0
    assert snapshot.latency_p95_ms == 96.0
    assert snapshot.latency_p99_ms == 100.0


@pytest.mark.asyncio
async def test_zero_request_handling() -> None:
    """13. Zero request handling is clean and safe."""
    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)

    snapshot = await tracker.get_provider_snapshot("unseen")
    assert snapshot.total_requests == 0
    assert snapshot.success_rate == 0.0
    assert snapshot.latency_p50_ms is None


@pytest.mark.asyncio
async def test_storage_failure_resilience() -> None:
    """14. Storage failure in record_attempt falls back to local in-memory storage."""
    failing_storage = MagicMock()
    failing_storage.record_attempt = AsyncMock(side_effect=RuntimeError("Redis down"))
    failing_storage.get_health_data = AsyncMock(side_effect=RuntimeError("Redis down"))

    tracker = HealthTracker(storage=failing_storage)

    # record_attempt handles the exception gracefully by falling back to in-memory storage
    await tracker.record_attempt("p1", success=True, latency_ms=10.0)

    # get_provider_snapshot also falls back to in-memory storage where the attempt was recorded
    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 1
    assert snapshot.total_successes == 1
    assert snapshot.provider_id == "p1"
    assert snapshot.last_latency_ms == 10.0


@pytest.mark.asyncio
async def test_health_recording_failure_does_not_mask_provider_errors() -> None:
    """15. Failure in health tracking inside RetryManager does not mask original ProviderError."""
    mock_service = MagicMock()
    mock_service.execute_chat_completion = AsyncMock(
        side_effect=ProviderError(
            message="Upstream gateway timeout",
            status_code=504,
            category=ErrorCategory.TIMEOUT,
            retryable=False,  # Non-retryable to test immediate raise
        )
    )

    failing_tracker = MagicMock()
    failing_tracker.record_attempt = AsyncMock(side_effect=Exception("Health Redis crashed"))

    retry_mgr = RetryManager(
        provider_service=mock_service,
        tracker=failing_tracker,
    )

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello")],
        model="test-model",
    )
    target = ProviderTarget(id="p1", provider="mock", model="m1")

    with pytest.raises(ProviderError) as exc_info:
        await retry_mgr.execute_with_retry(req, request_id="req-123", target=target)

    # Verify original error was preserved exactly
    assert exc_info.value.category == ErrorCategory.TIMEOUT
    assert exc_info.value.status_code == 504
    assert "Upstream gateway timeout" in exc_info.value.message

    # Verify health tracker was attempted
    failing_tracker.record_attempt.assert_called_once()


@pytest.mark.asyncio
async def test_retry_manager_records_every_attempt() -> None:
    """Verify RetryManager records health for EVERY physical attempt (retries)."""
    # 2 timeouts then 1 success
    responses = [
        ProviderError(
            message="Timeout 1",
            status_code=504,
            category=ErrorCategory.TIMEOUT,
            retryable=True,
        ),
        ProviderError(
            message="Timeout 2",
            status_code=504,
            category=ErrorCategory.TIMEOUT,
            retryable=True,
        ),
        ChatCompletionResponse(
            id="resp-1",
            created=123456789,
            model="m1",
            choices=[
                ChatCompletionChoice(
                    index=0,
                    message=ChatCompletionMessageResponse(role="assistant", content="Hi"),
                    finish_reason="stop",
                )
            ],
            usage=CompletionUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10),
        ),
    ]

    mock_service = MagicMock()
    mock_service.execute_chat_completion = AsyncMock(side_effect=responses)

    storage = InMemoryHealthStorage()
    tracker = HealthTracker(storage=storage)
    sleep_mock = AsyncMock()

    retry_mgr = RetryManager(
        provider_service=mock_service,
        tracker=tracker,
        sleep_func=sleep_mock,
    )

    req = ChatCompletionRequest(
        messages=[ChatMessage(role="user", content="Hello")],
        model="test-model",
    )
    target = ProviderTarget(id="p1", provider="mock", model="m1")

    resp = await retry_mgr.execute_with_retry(req, request_id="req-1", target=target)
    assert resp.id == "resp-1"

    # Should have 3 attempts in health storage: 2 failures + 1 success
    snapshot = await tracker.get_provider_snapshot("p1")
    assert snapshot.total_requests == 3
    assert snapshot.total_failures == 2
    assert snapshot.total_successes == 1
    assert math.isclose(snapshot.success_rate, 1 / 3, abs_tol=1e-3)
    assert snapshot.last_error_category == ErrorCategory.TIMEOUT
