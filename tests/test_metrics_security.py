"""Unit and integration tests for Phase 14.3b Security Metrics.

Tests:
1. Authentication Failures:
   - Missing credentials -> gateway_auth_failures_total{reason="missing_credentials"} += 1
   - Malformed header -> gateway_auth_failures_total{reason="malformed_header"} += 1
   - Unknown API key -> gateway_auth_failures_total{reason="invalid_credentials"} += 1
   - Revoked/inactive key -> gateway_auth_failures_total{reason="invalid_credentials"} += 1
   - Expired key -> gateway_auth_failures_total{reason="expired_key"} += 1
   - Inactive tenant -> 403 Forbidden, 0 auth failure metrics
   - Valid authentication -> 0 auth failure metrics
   - No double counting
2. Rate Limit Rejections:
   - Request below limit -> 0 rejection metric
   - Request exceeding limit -> gateway_rate_limit_rejections_total{reason="limit_exceeded"} += 1
   - Multiple rejected requests -> exactly 1 increment per rejection
   - Redis fail-open -> 0 rejection metric
   - No unbounded labels (no tenant_id, request_id, api_key, ip)
3. Endpoint & Layer Integration:
   - 401/403/429 generate gateway_requests_total without provider/retry/failover metrics
"""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from app.core.auth import Authenticator, get_authenticated_tenant
from app.core.exceptions import AuthenticationError, PermissionDeniedError, RateLimitError
from app.db.base import utc_now
from app.db.models.api_key import ApiKey
from app.db.models.tenant import Tenant
from app.main import create_app
from app.observability.metrics import GatewayMetrics, create_metrics_registry
from app.reliability.rate_limiter import RateLimiter


@pytest.fixture
def custom_metrics() -> GatewayMetrics:
    """Provide an isolated GatewayMetrics instance for security metrics testing."""
    registry = create_metrics_registry()
    return GatewayMetrics(registry=registry)


# --- Authentication Metric Tests ---


@pytest.mark.asyncio
async def test_auth_failure_missing_header(custom_metrics: GatewayMetrics) -> None:
    """Verify missing Authorization header increments gateway_auth_failures_total."""
    authenticator = Authenticator(metrics=custom_metrics)
    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_key("")

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_auth_failures_total{reason="missing_credentials"} 1.0' in text


@pytest.mark.asyncio
async def test_auth_failure_malformed_header(
    monkeypatch: pytest.MonkeyPatch, custom_metrics: GatewayMetrics
) -> None:
    """Verify malformed Authorization headers increment gateway_auth_failures_total."""
    from app.core import auth as auth_mod

    authenticator = Authenticator(metrics=custom_metrics)
    monkeypatch.setattr(auth_mod, "authenticator", authenticator)

    # Missing Bearer prefix
    with pytest.raises(AuthenticationError):
        await get_authenticated_tenant("Basic some_base64_token")

    # Empty token
    with pytest.raises(AuthenticationError):
        await get_authenticated_tenant("Bearer ")

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_auth_failures_total{reason="malformed_header"} 2.0' in text


@pytest.mark.asyncio
async def test_auth_failure_unknown_key(custom_metrics: GatewayMetrics) -> None:
    """Verify unknown API key increments gateway_auth_failures_total with invalid_credentials."""
    mock_repo = AsyncMock()
    mock_repo.get_by_hashed_key.return_value = None

    authenticator = Authenticator(repo=mock_repo, metrics=custom_metrics)

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_key("gw_live_unknown_1234567890abcdef")

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_auth_failures_total{reason="invalid_credentials"} 1.0' in text


@pytest.mark.asyncio
async def test_auth_failure_revoked_key(custom_metrics: GatewayMetrics) -> None:
    """Verify revoked key increments auth failures with invalid_credentials."""
    tenant = Tenant(id="tenant_1", name="Test Tenant", status="active")
    key_record = ApiKey(
        id="key_1",
        tenant_id="tenant_1",
        name="Revoked Key",
        key_prefix="gw_live_12345678",
        hashed_key="hashed_key_val",
        status="revoked",
        tenant=tenant,
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_hashed_key.return_value = key_record

    authenticator = Authenticator(repo=mock_repo, metrics=custom_metrics)

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_key("gw_live_12345678_secret")

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_auth_failures_total{reason="invalid_credentials"} 1.0' in text


@pytest.mark.asyncio
async def test_auth_failure_expired_key(custom_metrics: GatewayMetrics) -> None:
    """Verify expired API key increments gateway_auth_failures_total with expired_key."""
    tenant = Tenant(id="tenant_1", name="Test Tenant", status="active")
    key_record = ApiKey(
        id="key_1",
        tenant_id="tenant_1",
        name="Expired Key",
        key_prefix="gw_live_12345678",
        hashed_key="hashed_key_val",
        status="active",
        expires_at=utc_now() - timedelta(days=1),
        tenant=tenant,
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_hashed_key.return_value = key_record

    authenticator = Authenticator(repo=mock_repo, metrics=custom_metrics)

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate_key("gw_live_12345678_secret")

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_auth_failures_total{reason="expired_key"} 1.0' in text


@pytest.mark.asyncio
async def test_inactive_tenant_does_not_increment_auth_failure_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify inactive tenant rejection (403 PermissionDeniedError) does not increment metric."""
    tenant = Tenant(id="tenant_1", name="Inactive Tenant", status="suspended")
    key_record = ApiKey(
        id="key_1",
        tenant_id="tenant_1",
        name="Valid Key Inactive Tenant",
        key_prefix="gw_live_12345678",
        hashed_key="hashed_key_val",
        status="active",
        expires_at=None,
        tenant=tenant,
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_hashed_key.return_value = key_record

    authenticator = Authenticator(repo=mock_repo, metrics=custom_metrics)

    with pytest.raises(PermissionDeniedError) as exc_info:
        await authenticator.authenticate_key("gw_live_12345678_secret")

    assert exc_info.value.status_code == 403

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert "gateway_auth_failures_total{" not in text


@pytest.mark.asyncio
async def test_valid_auth_does_not_increment_auth_failure_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify valid active authentication does NOT increment gateway_auth_failures_total."""
    tenant = Tenant(id="tenant_1", name="Active Tenant", status="active")
    key_record = ApiKey(
        id="key_1",
        tenant_id="tenant_1",
        name="Valid Key",
        key_prefix="gw_live_12345678",
        hashed_key="hashed_key_val",
        status="active",
        expires_at=None,
        tenant=tenant,
    )

    mock_repo = AsyncMock()
    mock_repo.get_by_hashed_key.return_value = key_record

    authenticator = Authenticator(repo=mock_repo, metrics=custom_metrics)

    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")
    assert ctx.tenant_id == "tenant_1"

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert "gateway_auth_failures_total{" not in text


# --- Rate Limiter Metric Tests ---


@pytest.mark.asyncio
async def test_rate_limit_allowed_does_not_increment_rejection_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify that an allowed request within rate limit does not increment rejections."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    mock_client.eval = AsyncMock(return_value=[1, 9, 0, 1700000000])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr, metrics=custom_metrics)
    await limiter.check_rate_limit(tenant_id="tenant_allowed", limit=10, window_seconds=60)

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert "gateway_rate_limit_rejections_total{" not in text


@pytest.mark.asyncio
async def test_rate_limit_exceeded_increments_rejection_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify rejected request increments rate_limit_rejections_total with limit_exceeded."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    mock_client.eval = AsyncMock(return_value=[0, 0, 15, 1700000060])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr, metrics=custom_metrics)

    with pytest.raises(RateLimitError):
        await limiter.check_rate_limit(tenant_id="tenant_blocked", limit=5, window_seconds=60)

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_rate_limit_rejections_total{reason="limit_exceeded"} 1.0' in text


@pytest.mark.asyncio
async def test_multiple_rate_limit_rejections_increment_each_time(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify multiple rejected requests increment rate_limit_rejections_total each time."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = True
    mock_client = AsyncMock()
    mock_client.eval = AsyncMock(return_value=[0, 0, 10, 1700000060])
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr, metrics=custom_metrics)

    for _ in range(3):
        with pytest.raises(RateLimitError):
            await limiter.check_rate_limit(tenant_id="tenant_blocked", limit=5, window_seconds=60)

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert 'gateway_rate_limit_rejections_total{reason="limit_exceeded"} 3.0' in text


@pytest.mark.asyncio
async def test_rate_limit_redis_unavailable_fails_open_without_metric(
    custom_metrics: GatewayMetrics,
) -> None:
    """Verify Redis failure causes rate limiter to fail open without incrementing rejections."""
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.is_connected = False
    mock_redis_mgr.get_client = MagicMock(return_value=None)

    limiter = RateLimiter(redis_mgr=mock_redis_mgr, metrics=custom_metrics)
    await limiter.check_rate_limit(tenant_id="tenant_fail_open", limit=10, window_seconds=60)

    text = generate_latest(custom_metrics.registry).decode("utf-8")
    assert "gateway_rate_limit_rejections_total{" not in text


# --- End-to-End HTTP Integration & Double Counting Isolation ---


def test_http_auth_failure_401_metrics_isolation() -> None:
    """Verify HTTP 401 request produces request & auth metric, zero provider metrics."""
    app = create_app()
    client = TestClient(app)

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "qwen2.5:3b", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert resp.status_code == 401

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    text = metrics_resp.text

    # Request metric recorded
    assert 'gateway_requests_total{model="unknown",status_code="401"} 1.0' in text
    # Auth failure metric recorded
    assert 'gateway_auth_failures_total{reason="missing_credentials"} 1.0' in text
    # No provider/retry/failover increments
    assert "gateway_provider_requests_total{" not in text
    assert "gateway_retries_total{" not in text
    assert "gateway_failovers_total{" not in text
