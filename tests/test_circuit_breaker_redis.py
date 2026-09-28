"""Unit tests for Phase 09 Redis operational state, storage abstraction, and fallbacks."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError

from app.core.exceptions import CircuitBreakerError, ProviderError
from app.main import app
from app.reliability.circuit_breaker import (
    CircuitBreakerManager,
    CircuitState,
)
from app.storage.circuit_storage import (
    InMemoryCircuitStorage,
    RedisCircuitStorage,
)
from app.storage.redis import RedisManager


@pytest.mark.asyncio
async def test_in_memory_storage_lifecycle() -> None:
    """Test InMemoryCircuitStorage state transitions and probe acquisition."""
    storage = InMemoryCircuitStorage()

    # 1. Initially CLOSED
    allowed, state, remaining = await storage.acquire_permission(
        provider_id="prov_1", cooldown_seconds=10.0, max_probes=1
    )
    assert allowed is True
    assert state == CircuitState.CLOSED
    assert remaining == 0.0

    # 2. Record failures to trip to OPEN
    new_state, count = await storage.record_failure("prov_1", failure_threshold=2)
    assert new_state == CircuitState.CLOSED
    assert count == 1

    new_state, count = await storage.record_failure("prov_1", failure_threshold=2)
    assert new_state == CircuitState.OPEN
    assert count == 2

    # 3. Blocked while OPEN
    allowed, state, remaining = await storage.acquire_permission(
        provider_id="prov_1", cooldown_seconds=10.0, max_probes=1
    )
    assert allowed is False
    assert state == CircuitState.OPEN
    assert remaining > 0.0


@pytest.mark.asyncio
async def test_redis_circuit_storage_lua_mocked() -> None:
    """Test RedisCircuitStorage executing Lua scripts with a mocked Redis client."""
    mock_client = AsyncMock()
    # In redis-py, register_script is synchronous and returns an executable
    # script object whose __call__ is async
    mock_acquire_script = AsyncMock()
    mock_acquire_script.side_effect = lambda keys, args: [1, "CLOSED", "0"]
    mock_client.register_script = lambda script: mock_acquire_script

    storage = RedisCircuitStorage(redis_client=mock_client)
    allowed, state, remaining = await storage.acquire_permission(
        provider_id="prov_redis", cooldown_seconds=30.0, max_probes=1
    )

    assert allowed is True
    assert state == CircuitState.CLOSED
    assert remaining == 0.0


@pytest.mark.asyncio
async def test_redis_failure_transparent_fallback_to_in_memory() -> None:
    """Test that Redis network errors transparently fall back to InMemoryCircuitStorage."""
    mock_client = AsyncMock()
    # Simulate Redis connection failure on script execution
    mock_script = AsyncMock(side_effect=ConnectionError("Connection to Redis lost"))
    mock_client.register_script = lambda script: mock_script

    redis_mgr = RedisManager()
    redis_mgr._client = mock_client
    redis_mgr._is_connected = True

    cb_manager = CircuitBreakerManager(redis_mgr=redis_mgr)

    # Should not raise Redis error; falls back to in-memory store cleanly
    await cb_manager.acquire_permission("prov_fallback")
    # Record a failure
    err = ProviderError("Server error", category="SERVER_ERROR")
    await cb_manager.record_failure("prov_fallback", err)

    # In-memory store recorded the failure
    fallback_record = cb_manager._fallback_storage._get_or_create("prov_fallback")
    assert fallback_record.consecutive_failures == 1


@pytest.mark.asyncio
async def test_redis_recovery_behavior() -> None:
    """Test that when Redis reconnects, CircuitBreakerManager resumes using Redis."""
    redis_mgr = RedisManager()
    redis_mgr._client = None
    redis_mgr._is_connected = False

    cb = CircuitBreakerManager(redis_mgr=redis_mgr)
    # Active storage is currently in-memory fallback
    assert cb._get_active_storage() is cb._fallback_storage

    # Redis connects
    mock_client = AsyncMock()
    mock_client.ping.return_value = True
    redis_mgr._client = mock_client
    redis_mgr._is_connected = True

    # Active storage is now RedisCircuitStorage
    active = cb._get_active_storage()
    assert isinstance(active, RedisCircuitStorage)


@pytest.mark.asyncio
async def test_readiness_endpoint_connected_and_degraded() -> None:
    """Test /ready responses in both Redis-connected and degraded states."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Redis unavailable (degraded)
        with patch("app.storage.redis.RedisManager.ping", return_value=False):
            res = await client.get("/ready")
            assert res.status_code == 200
            data = res.json()
            assert data["ready"] is True
            assert data["status"] == "degraded"
            assert data["details"]["redis"] == "disconnected"
            assert data["details"]["mode"] == "degraded_in_memory"

        # Case 2: Redis connected (ready)
        with patch("app.storage.redis.RedisManager.ping", return_value=True):
            res = await client.get("/ready")
            assert res.status_code == 200
            data = res.json()
            assert data["ready"] is True
            assert data["status"] == "ready"
            assert data["details"]["redis"] == "connected"
            assert data["details"]["mode"] == "distributed"


@pytest.mark.asyncio
async def test_half_open_single_probe_atomicity() -> None:
    """Test that only 1 probe is granted in HALF_OPEN state."""
    storage = InMemoryCircuitStorage()
    cooldown = 1.0

    # Trip circuit to OPEN
    await storage.record_failure("prov_probe", failure_threshold=1)
    state = await storage.get_state("prov_probe", cooldown_seconds=cooldown)
    assert state == CircuitState.OPEN

    # Simulate cooldown passing by shifting opened_at
    record = storage._get_or_create("prov_probe")
    record.opened_at = record.opened_at - 2.0 if record.opened_at else 0.0

    # Probe 1 requests permission -> granted (transitions to HALF_OPEN)
    allowed1, state1, _ = await storage.acquire_permission("prov_probe", cooldown, max_probes=1)
    assert allowed1 is True
    assert state1 == CircuitState.HALF_OPEN
    assert record.active_probes == 1

    # Probe 2 concurrently requests permission -> rejected
    allowed2, state2, _ = await storage.acquire_permission("prov_probe", cooldown, max_probes=1)
    assert allowed2 is False
    assert state2 == CircuitState.HALF_OPEN

    # Releasing probe slot allows subsequent probe
    await storage.release_probe("prov_probe")
    assert record.active_probes == 0

    allowed3, state3, _ = await storage.acquire_permission("prov_probe", cooldown, max_probes=1)
    assert allowed3 is True
    assert state3 == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_redis_open_state_preserved_on_redis_failure() -> None:
    """Test 5a: If Redis had a provider in OPEN state, fallback retains OPEN and blocks traffic."""
    mock_client = AsyncMock()
    # Mock failure script to trip circuit to OPEN
    mock_fail_script = AsyncMock(return_value=["OPEN", 5])
    # Mock acquire script to block when called on Redis
    mock_acquire_script = AsyncMock(return_value=[0, "OPEN", "25.0"])

    def script_dispatcher(script_str: str) -> AsyncMock:
        if "HINCRBY" in script_str:
            return mock_fail_script
        return mock_acquire_script

    mock_client.register_script = script_dispatcher

    redis_mgr = RedisManager()
    redis_mgr._client = mock_client
    redis_mgr._is_connected = True

    cb_manager = CircuitBreakerManager(redis_mgr=redis_mgr)

    # 1. While Redis is healthy, record failures to trip provider_x to OPEN
    err = ProviderError("Server error", category="SERVER_ERROR")
    await cb_manager.record_failure("provider_x", err)

    # Verify fallback mirror captured the OPEN state
    assert cb_manager.get_state("provider_x") == CircuitState.OPEN

    # 2. Simulate Redis suddenly crashing / disconnecting
    redis_mgr._is_connected = False
    redis_mgr._client = None

    # 3. Fallback must still see provider_x as OPEN and block traffic
    assert cb_manager.get_state("provider_x") == CircuitState.OPEN
    with pytest.raises(CircuitBreakerError) as exc_info:
        await cb_manager.acquire_permission("provider_x")

    assert exc_info.value.provider == "provider_x"
    assert "circuit protection" in exc_info.value.message.lower()


@pytest.mark.asyncio
async def test_redis_cooldown_preserved_on_fallback() -> None:
    """Test 5c: Cooldown timing is preserved during fallback without resetting to CLOSED."""
    current_time = 1000.0

    def mock_time() -> float:
        return current_time

    mock_client = AsyncMock()
    mock_fail_script = AsyncMock(return_value=["OPEN", 5])
    mock_acquire_script = AsyncMock(return_value=[0, "OPEN", "20.0"])

    mock_client.register_script = lambda s: (
        mock_fail_script if "HINCRBY" in s else mock_acquire_script
    )

    redis_mgr = RedisManager()
    redis_mgr._client = mock_client
    redis_mgr._is_connected = True

    cb = CircuitBreakerManager(
        redis_mgr=redis_mgr,
        time_func=mock_time,
    )

    # Trip provider to OPEN at t=1000
    err = ProviderError("Timeout", category="TIMEOUT")
    await cb.record_failure("provider_cooldown", err)

    # Redis crashes
    redis_mgr._is_connected = False
    redis_mgr._client = None

    # At t=1010 (< 30s cooldown), circuit is still OPEN and blocked
    current_time = 1010.0
    assert cb.get_state("provider_cooldown") == CircuitState.OPEN
    with pytest.raises(CircuitBreakerError):
        await cb.acquire_permission("provider_cooldown")

    # At t=1031 (> 30s cooldown), circuit transitions to HALF_OPEN and permits single probe
    current_time = 1031.0
    assert cb.get_state("provider_cooldown") == CircuitState.HALF_OPEN
    await cb.acquire_permission("provider_cooldown")
