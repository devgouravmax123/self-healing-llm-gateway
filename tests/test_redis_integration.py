"""Integration tests for live local Redis (run when Redis daemon is reachable at localhost:6379)."""

import asyncio
import socket
from typing import Any
from urllib.parse import urlparse

import pytest
import redis.asyncio as aioredis

from app.core.config import Settings, settings
from app.reliability.circuit_breaker import CircuitState
from app.storage.circuit_storage import RedisCircuitStorage


def is_redis_available(redis_url: str | None = None) -> bool:
    """Check if configured Redis is running and accepting TCP connections."""
    target_url = redis_url or settings.redis_url
    parsed = urlparse(target_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379

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
    reason=f"Redis daemon is not reachable at {settings.redis_url}",
)


@pytest.fixture
async def live_redis_client() -> Any:
    """Fixture providing a connected Redis client, cleaning up test keys after use."""
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    yield client
    # Clean up test keys
    async for key in client.scan_iter("llm_gateway:circuit:test_integration_*"):
        await client.delete(key)
    close_func = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close_func is not None:
        res = close_func()
        if hasattr(res, "__await__"):
            await res


@pytest.mark.asyncio
async def test_live_redis_circuit_lifecycle(live_redis_client: Any) -> None:
    """Test full circuit lifecycle (CLOSED -> OPEN -> HALF_OPEN -> CLOSED) on live Redis."""
    provider_id = "test_integration_prov_1"
    config = Settings(CIRCUIT_FAILURE_THRESHOLD=2, CIRCUIT_COOLDOWN_SECONDS=1.0)
    storage = RedisCircuitStorage(redis_client=live_redis_client, config=config)

    # 1. Initially CLOSED
    allowed, state, remaining = await storage.acquire_permission(
        provider_id, cooldown_seconds=1.0, max_probes=1
    )
    assert allowed is True
    assert state == CircuitState.CLOSED

    # 2. Record 2 failures -> trip to OPEN
    s1, f1 = await storage.record_failure(provider_id, failure_threshold=2)
    assert s1 == CircuitState.CLOSED
    assert f1 == 1

    s2, f2 = await storage.record_failure(provider_id, failure_threshold=2)
    assert s2 == CircuitState.OPEN
    assert f2 == 2

    # 3. Blocked while OPEN
    allowed_blocked, state_open, _ = await storage.acquire_permission(
        provider_id, cooldown_seconds=1.0, max_probes=1
    )
    assert allowed_blocked is False
    assert state_open == CircuitState.OPEN

    # 4. Wait for cooldown expiration (1.0s) and transition to HALF_OPEN
    await asyncio.sleep(1.1)

    allowed_probe, probe_state, _ = await storage.acquire_permission(
        provider_id,
        cooldown_seconds=1.0,
        max_probes=1,
    )
    assert allowed_probe is True
    assert probe_state == CircuitState.HALF_OPEN

    # 5. Successful probe recovers the circuit back to CLOSED
    await storage.record_success(provider_id)
    state_after_success = await storage.get_state(
        provider_id,
        cooldown_seconds=1.0,
    )
    assert state_after_success == CircuitState.CLOSED
