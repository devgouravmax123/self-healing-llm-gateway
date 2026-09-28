"""Integration tests for live Redis health tracking (run with Redis at localhost:6379)."""

import asyncio
import socket
from typing import Any

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.core.exceptions import ProviderError
from app.main import app
from app.reliability.circuit_breaker import CircuitBreakerManager, CircuitState
from app.reliability.error_classifier import ErrorCategory
from app.reliability.health_tracker import HealthTracker
from app.routing.provider_registry import provider_registry
from app.storage.circuit_storage import RedisCircuitStorage
from app.storage.health_storage import RedisHealthStorage


def is_redis_available(host: str = "localhost", port: int = 6379) -> bool:
    """Check if local Redis is running and accepting TCP connections."""
    s = socket.socket()
    s.settimeout(0.5)
    try:
        res = s.connect_ex((host, port))
        s.close()
        return res == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not is_redis_available(),
    reason="Local Redis daemon is not running on localhost:6379",
)


@pytest.fixture
async def live_redis_client() -> Any:
    """Fixture providing a connected Redis client, cleaning up test keys after use."""
    client = aioredis.from_url("redis://localhost:6379/0", decode_responses=True)
    yield client
    # Clean up test keys
    async for key in client.scan_iter("llm_gateway:health:test_health_integration_*"):
        await client.delete(key)
    async for key in client.scan_iter("llm_gateway:circuit:test_health_integration_*"):
        await client.delete(key)
    close_func = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close_func is not None:
        res = close_func()
        if hasattr(res, "__await__"):
            await res


@pytest.mark.asyncio
async def test_live_redis_health_empty_provider(live_redis_client: Any) -> None:
    """1. Empty provider on live Redis returns zero counts and null percentiles."""
    storage = RedisHealthStorage(redis_client=live_redis_client)
    tracker = HealthTracker(storage=storage)

    snapshot = await tracker.get_provider_snapshot("test_health_integration_empty")
    assert snapshot.provider_id == "test_health_integration_empty"
    assert snapshot.total_requests == 0
    assert snapshot.total_successes == 0
    assert snapshot.total_failures == 0
    assert snapshot.success_rate == 0.0
    assert snapshot.last_latency_ms is None
    assert snapshot.latency_p50_ms is None


@pytest.mark.asyncio
async def test_live_redis_health_success_and_failure(live_redis_client: Any) -> None:
    """2 & 3. Success and failure recording against live Redis."""
    storage = RedisHealthStorage(redis_client=live_redis_client)
    tracker = HealthTracker(storage=storage)
    provider_id = "test_health_integration_p1"

    # Record 1 success
    await tracker.record_attempt(provider_id, success=True, latency_ms=150.0)
    snap1 = await tracker.get_provider_snapshot(provider_id)
    assert snap1.total_requests == 1
    assert snap1.total_successes == 1
    assert snap1.total_failures == 0
    assert snap1.last_latency_ms == 150.0
    assert snap1.last_success_at is not None
    assert snap1.last_failure_at is None

    # Record 1 failure
    await tracker.record_attempt(
        provider_id, success=False, latency_ms=300.0, error_category=ErrorCategory.TIMEOUT
    )
    snap2 = await tracker.get_provider_snapshot(provider_id)
    assert snap2.total_requests == 2
    assert snap2.total_successes == 1
    assert snap2.total_failures == 1
    assert snap2.success_rate == 0.5
    assert snap2.last_latency_ms == 300.0
    assert snap2.last_failure_at is not None
    assert snap2.last_error_category == ErrorCategory.TIMEOUT


@pytest.mark.asyncio
async def test_live_redis_health_rolling_window_and_percentiles(live_redis_client: Any) -> None:
    """4, 5, 6. Latency samples, rolling window (max 50), and percentile snapshot."""
    storage = RedisHealthStorage(redis_client=live_redis_client, max_latency_window=50)
    tracker = HealthTracker(storage=storage)
    provider_id = "test_health_integration_p2"

    # Record 60 samples with latencies 1..60
    for i in range(1, 61):
        await tracker.record_attempt(provider_id, success=True, latency_ms=float(i))

    snapshot = await tracker.get_provider_snapshot(provider_id)
    assert snapshot.total_requests == 60
    assert snapshot.last_latency_ms == 60.0

    # Check that Redis list has exactly 50 items (window size)
    list_key = f"llm_gateway:health:{provider_id}:latencies"
    length = await live_redis_client.llen(list_key)
    assert length == 50

    # Percentiles of 11..60 (50 items)
    # 50 items sorted: 11, 12, ..., 60
    # p50 = item 25 = 35.0
    # p95 = item 48 = 58.0
    # p99 = item 50 = 60.0
    assert snapshot.latency_p50_ms == 35.0
    assert snapshot.latency_p95_ms == 58.0
    assert snapshot.latency_p99_ms == 60.0


@pytest.mark.asyncio
async def test_live_redis_multiple_providers(live_redis_client: Any) -> None:
    """7. Multiple providers maintain isolated state in Redis."""
    storage = RedisHealthStorage(redis_client=live_redis_client)
    tracker = HealthTracker(storage=storage)
    p_a = "test_health_integration_a"
    p_b = "test_health_integration_b"

    await tracker.record_attempt(p_a, success=True, latency_ms=50.0)
    await tracker.record_attempt(p_b, success=False, latency_ms=100.0)

    snap_a = await tracker.get_provider_snapshot(p_a)
    snap_b = await tracker.get_provider_snapshot(p_b)

    assert snap_a.total_successes == 1
    assert snap_a.total_failures == 0
    assert snap_b.total_successes == 0
    assert snap_b.total_failures == 1


@pytest.mark.asyncio
async def test_live_redis_concurrent_updates(live_redis_client: Any) -> None:
    """8. Atomic Lua script handles concurrent updates safely without race conditions."""
    storage = RedisHealthStorage(redis_client=live_redis_client)
    tracker = HealthTracker(storage=storage)
    provider_id = "test_health_integration_concurrent"

    num_tasks = 20

    async def worker(idx: int) -> None:
        await tracker.record_attempt(
            provider_id,
            success=(idx % 2 == 0),
            latency_ms=float(10 + idx),
        )

    await asyncio.gather(*(worker(i) for i in range(num_tasks)))

    snapshot = await tracker.get_provider_snapshot(provider_id)
    assert snapshot.total_requests == num_tasks
    assert snapshot.total_successes == num_tasks // 2
    assert snapshot.total_failures == num_tasks // 2
    assert snapshot.success_rate == 0.5


@pytest.mark.asyncio
async def test_live_redis_api_snapshot_and_circuit_state(live_redis_client: Any) -> None:
    """9 & 10. GET /health/providers returns Redis-backed snapshot with matching circuit state."""
    # Setup circuit storage with live Redis
    config = Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=60.0)
    circuit_storage = RedisCircuitStorage(redis_client=live_redis_client, config=config)
    circuit_mgr = CircuitBreakerManager(storage=circuit_storage, config=config)

    health_storage = RedisHealthStorage(redis_client=live_redis_client)
    tracker = HealthTracker(storage=health_storage)

    # Let's take the first registered provider
    providers = provider_registry.list_all()
    assert len(providers) > 0
    test_prov = providers[0]

    # Trip the circuit for test_prov
    prov_err = ProviderError(
        message="Server Error", status_code=500, category=ErrorCategory.SERVER_ERROR
    )
    await circuit_mgr.record_failure(test_prov.id, prov_err)
    await circuit_mgr.record_failure(test_prov.id, prov_err)
    assert await circuit_mgr.get_state_async(test_prov.id) == CircuitState.OPEN

    # Record some health attempts
    await tracker.record_attempt(test_prov.id, success=False, latency_ms=250.0)

    # Query snapshot via tracker with circuit state
    c_state = await circuit_mgr.get_state_async(test_prov.id)
    snap = await tracker.get_provider_snapshot(test_prov.id, circuit_state=c_state.value)
    assert snap.provider_id == test_prov.id
    assert snap.circuit_state == CircuitState.OPEN.value
    assert snap.total_requests >= 1
    assert snap.total_failures >= 1

    # Also test via HTTP API endpoint
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert test_prov.id in data
        assert data[test_prov.id]["provider_id"] == test_prov.id
