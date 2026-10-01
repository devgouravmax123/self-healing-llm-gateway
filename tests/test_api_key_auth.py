"""Unit tests for API key generation, hashing, and authentication logic."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.auth import (
    Authenticator,
    generate_api_key,
    get_authenticated_tenant,
    hash_api_key,
    require_admin_auth,
    verify_hash,
)
from app.core.auth_context import TenantContext
from app.core.exceptions import AuthenticationError, GatewayError, PermissionDeniedError
from app.db.base import utc_now
from app.db.models.api_key import ApiKey
from app.db.models.tenant import Tenant


def test_generate_api_key_format_and_entropy() -> None:
    """Verify generated API keys follow format gw_<env>_<prefix>_<secret> and have unique hashes."""
    key1, prefix1, hash1 = generate_api_key(environment="live")
    key2, prefix2, hash2 = generate_api_key(environment="live")

    assert key1.startswith("gw_live_")
    assert key2.startswith("gw_live_")
    assert prefix1 in key1
    assert prefix2 in key2
    assert key1 != key2
    assert hash1 != hash2
    assert len(hash1) == 64  # SHA-256 hex string


def test_hash_api_key_deterministic_and_constant_time_verify() -> None:
    """Verify hash_api_key produces deterministic outputs and verify_hash passes."""
    sample_key = "gw_live_testprefix_1234567890abcdef"
    h1 = hash_api_key(sample_key)
    h2 = hash_api_key(sample_key)
    assert h1 == h2
    assert verify_hash(sample_key, h1) is True
    assert verify_hash("gw_live_wrongkey", h1) is False


@pytest.mark.asyncio
async def test_authenticator_valid_key() -> None:
    """Verify Authenticator returns correct TenantContext for active key and tenant."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_12345678",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    authenticator = Authenticator(repo=mock_repo)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert isinstance(ctx, TenantContext)
    assert ctx.tenant_id == "tenant_alpha"
    assert ctx.tenant_name == "Alpha Corp"
    assert ctx.api_key_id == api_key_id
    assert ctx.is_admin is False


@pytest.mark.asyncio
async def test_authenticator_admin_key() -> None:
    """Verify Authenticator correctly sets is_admin=True for admin keys."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_admin", name="Admin Org", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_admin",
        key_prefix="gw_live_admin123",
        hashed_key="dummy_hash",
        status="active",
        is_admin=True,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    authenticator = Authenticator(repo=mock_repo)
    ctx = await authenticator.authenticate_key("gw_live_admin123_secret")

    assert ctx.is_admin is True
    # Verify require_admin_auth dependency passes
    admin_ctx = await require_admin_auth(ctx)
    assert admin_ctx.is_admin is True


@pytest.mark.asyncio
async def test_require_admin_auth_fails_for_normal_key() -> None:
    """Verify require_admin_auth raises 403 PermissionDeniedError for non-admin context."""
    normal_ctx = TenantContext(
        tenant_id="tenant_user",
        tenant_name="User Org",
        api_key_id=uuid4(),
        key_prefix="gw_live_norm",
        is_admin=False,
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        await require_admin_auth(normal_ctx)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticator_missing_or_invalid_key() -> None:
    """Verify missing, empty, or un-found key raises 401 AuthenticationError."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(return_value=None)
    authenticator = Authenticator(repo=mock_repo)

    with pytest.raises(AuthenticationError) as exc1:
        await authenticator.authenticate_key("")
    assert exc1.value.status_code == 401

    with pytest.raises(AuthenticationError) as exc2:
        await authenticator.authenticate_key("gw_live_unknown_key")
    assert exc2.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticator_revoked_key() -> None:
    """Verify revoked key raises 401 AuthenticationError."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    mock_key_record = ApiKey(
        id=uuid4(),
        tenant_id="tenant_alpha",
        key_prefix="gw_live_revoked",
        hashed_key="dummy_hash",
        status="revoked",
        is_admin=False,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    authenticator = Authenticator(repo=mock_repo)
    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate_key("gw_live_revoked_key")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticator_expired_key() -> None:
    """Verify expired key raises 401 AuthenticationError."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    past_time = utc_now() - timedelta(hours=1)
    mock_key_record = ApiKey(
        id=uuid4(),
        tenant_id="tenant_alpha",
        key_prefix="gw_live_expired",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=past_time,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    authenticator = Authenticator(repo=mock_repo)
    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate_key("gw_live_expired_key")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticator_inactive_tenant() -> None:
    """Verify key with inactive tenant raises 403 PermissionDeniedError."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_inactive", name="Inactive Org", status="suspended")
    mock_key_record = ApiKey(
        id=uuid4(),
        tenant_id="tenant_inactive",
        key_prefix="gw_live_activekey",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    authenticator = Authenticator(repo=mock_repo)
    with pytest.raises(PermissionDeniedError) as exc_info:
        await authenticator.authenticate_key("gw_live_activekey_secret")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticator_db_failure_raises_503() -> None:
    """Verify unexpected database error raises 503 GatewayError when no valid cache exists."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(side_effect=RuntimeError("DB Connection Lost"))
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=None)
    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)

    with pytest.raises(GatewayError) as exc_info:
        await authenticator.authenticate_key("gw_live_key")
    assert exc_info.value.status_code == 503
    assert exc_info.value.message == "Authentication service temporarily unavailable"


@pytest.mark.asyncio
async def test_authenticator_cache_miss_populates_redis() -> None:
    """Test 1: Redis miss -> PostgreSQL lookup succeeds -> Redis cache is populated."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_12345678",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=None)  # Cache miss
    mock_client.set = AsyncMock()
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert ctx.tenant_id == "tenant_alpha"
    mock_repo.get_by_hashed_key.assert_awaited_once()
    mock_client.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_authenticator_cache_hit_bypasses_postgres() -> None:
    """Test 2: Redis cache hit -> PostgreSQL repository is not called."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock()
    api_key_id = uuid4()

    import json

    cached_payload = json.dumps(
        {
            "tenant_id": "tenant_alpha",
            "tenant_name": "Alpha Corp",
            "tenant_status": "active",
            "api_key_id": str(api_key_id),
            "key_prefix": "gw_live_12345678",
            "is_admin": False,
            "status": "active",
            "expires_at": None,
        }
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=cached_payload)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert ctx.tenant_id == "tenant_alpha"
    assert ctx.api_key_id == api_key_id
    mock_repo.get_by_hashed_key.assert_not_called()


@pytest.mark.asyncio
async def test_authenticator_db_unavailable_with_valid_cache() -> None:
    """Test 3: PostgreSQL connection fails, but valid cache exists -> Auth succeeds."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(side_effect=RuntimeError("Postgres Down"))
    api_key_id = uuid4()

    import json

    cached_payload = json.dumps(
        {
            "tenant_id": "tenant_cached",
            "tenant_name": "Cached Corp",
            "tenant_status": "active",
            "api_key_id": str(api_key_id),
            "key_prefix": "gw_live_cached123",
            "is_admin": True,
            "status": "active",
            "expires_at": None,
        }
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=cached_payload)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_cached123_secret")

    assert ctx.tenant_id == "tenant_cached"
    assert ctx.is_admin is True
    mock_repo.get_by_hashed_key.assert_not_called()


@pytest.mark.asyncio
async def test_authenticator_db_unavailable_cache_miss_raises_503() -> None:
    """Test 4: Cache miss + PostgreSQL down -> HTTP 503."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(side_effect=RuntimeError("Postgres Down"))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=None)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    with pytest.raises(GatewayError) as exc_info:
        await authenticator.authenticate_key("gw_live_cold_key")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_authenticator_redis_unavailable_postgres_healthy() -> None:
    """Test 5: Redis GET fails -> Fallback to PostgreSQL -> Auth succeeds."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_12345678",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=RuntimeError("Redis Connection Refused"))
    mock_client.set = AsyncMock(side_effect=RuntimeError("Redis Connection Refused"))
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert ctx.tenant_id == "tenant_alpha"
    mock_repo.get_by_hashed_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_authenticator_expired_cached_key() -> None:
    """Test 6: Redis contains expired metadata -> 401 AuthenticationError."""
    import json

    past_time = (utc_now() - timedelta(minutes=10)).isoformat()
    cached_payload = json.dumps(
        {
            "tenant_id": "tenant_alpha",
            "tenant_name": "Alpha Corp",
            "tenant_status": "active",
            "api_key_id": str(uuid4()),
            "key_prefix": "gw_live_exp",
            "is_admin": False,
            "status": "active",
            "expires_at": past_time,
        }
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=cached_payload)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(redis_mgr=mock_redis_mgr)
    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate_key("gw_live_exp_key")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticator_revoked_cached_key() -> None:
    """Test 7: Redis contains status != active -> 401 AuthenticationError."""
    import json

    cached_payload = json.dumps(
        {
            "tenant_id": "tenant_alpha",
            "tenant_name": "Alpha Corp",
            "tenant_status": "active",
            "api_key_id": str(uuid4()),
            "key_prefix": "gw_live_rev",
            "is_admin": False,
            "status": "revoked",
            "expires_at": None,
        }
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=cached_payload)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(redis_mgr=mock_redis_mgr)
    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate_key("gw_live_rev_key")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_authenticator_inactive_cached_tenant() -> None:
    """Test 8: Redis contains tenant_status != active -> 403 PermissionDeniedError."""
    import json

    cached_payload = json.dumps(
        {
            "tenant_id": "tenant_susp",
            "tenant_name": "Suspended Corp",
            "tenant_status": "suspended",
            "api_key_id": str(uuid4()),
            "key_prefix": "gw_live_susp",
            "is_admin": False,
            "status": "active",
            "expires_at": None,
        }
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=cached_payload)
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(redis_mgr=mock_redis_mgr)
    with pytest.raises(PermissionDeniedError) as exc_info:
        await authenticator.authenticate_key("gw_live_susp_key")

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_authenticator_malformed_cache_fallback_to_db() -> None:
    """Test 9: Malformed cache payload -> Fallback to PostgreSQL -> Auth succeeds."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_12345678",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value="not valid json {{{")
    mock_client.set = AsyncMock()
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert ctx.tenant_id == "tenant_alpha"
    mock_repo.get_by_hashed_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_authenticator_malformed_cache_db_unavailable_raises_503() -> None:
    """Test 10: Malformed cache payload + PostgreSQL down -> 503."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(side_effect=RuntimeError("Postgres Down"))

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value="corrupt data")
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    with pytest.raises(GatewayError) as exc_info:
        await authenticator.authenticate_key("gw_live_corrupt_key")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_authenticator_cache_set_failure_does_not_fail_request() -> None:
    """Test 11: PostgreSQL succeeds, but Redis SET fails -> Request still succeeds."""
    mock_repo = MagicMock()
    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    api_key_id = uuid4()
    mock_key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_12345678",
        hashed_key="dummy_hash",
        status="active",
        is_admin=False,
        expires_at=None,
    )
    mock_key_record.tenant = mock_tenant
    mock_repo.get_by_hashed_key = AsyncMock(return_value=mock_key_record)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=None)
    mock_client.set = AsyncMock(side_effect=RuntimeError("Redis SET Failed"))
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    ctx = await authenticator.authenticate_key("gw_live_12345678_secret")

    assert ctx.tenant_id == "tenant_alpha"


@pytest.mark.asyncio
async def test_authenticator_invalidate_cache() -> None:
    """Test 12: Invalidate cache explicitly deletes cache key from Redis."""
    mock_client = AsyncMock()
    mock_client.delete = AsyncMock()
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(redis_mgr=mock_redis_mgr)
    await authenticator.invalidate_cache("sample_hash_123")

    mock_client.delete.assert_awaited_once_with("api_key_cache:sample_hash_123")


@pytest.mark.asyncio
async def test_get_authenticated_tenant_header_parsing() -> None:
    """Verify get_authenticated_tenant dependency handles various header formats."""
    # Missing header
    with pytest.raises(AuthenticationError):
        await get_authenticated_tenant(None)

    # Malformed header (not Bearer)
    with pytest.raises(AuthenticationError):
        await get_authenticated_tenant("Basic dXNlcjpwYXNz")

    # Missing token part
    with pytest.raises(AuthenticationError):
        await get_authenticated_tenant("Bearer ")


@pytest.mark.asyncio
async def test_successful_revocation_invalidates_cache() -> None:
    """Test 1: Successful PostgreSQL revocation invalidates Redis cache entry."""
    api_key_id = uuid4()
    plain_key = "gw_live_revocation_test_key"
    hashed = hash_api_key(plain_key)

    mock_repo = MagicMock()
    # Repository revocation returns the hashed_key on DB commit success
    mock_repo.revoke_api_key = AsyncMock(return_value=hashed)

    mock_client = AsyncMock()
    mock_client.delete = AsyncMock()
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    success = await authenticator.revoke_api_key(api_key_id)

    assert success is True
    mock_repo.revoke_api_key.assert_awaited_once_with(api_key_id)
    mock_client.delete.assert_awaited_once_with(f"api_key_cache:{hashed}")


@pytest.mark.asyncio
async def test_postgres_failure_does_not_invalidate_cache() -> None:
    """Test 2: PostgreSQL revocation failure does NOT invoke Redis cache invalidation."""
    api_key_id = uuid4()

    mock_repo = MagicMock()
    mock_repo.revoke_api_key = AsyncMock(side_effect=RuntimeError("PostgreSQL commit failed"))

    mock_client = AsyncMock()
    mock_client.delete = AsyncMock()
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)

    with pytest.raises(RuntimeError, match="PostgreSQL commit failed"):
        await authenticator.revoke_api_key(api_key_id)

    mock_repo.revoke_api_key.assert_awaited_once_with(api_key_id)
    mock_client.delete.assert_not_called()


@pytest.mark.asyncio
async def test_redis_failure_does_not_undo_revocation() -> None:
    """Test 3: Redis invalidation error is contained and does not fail revocation."""
    api_key_id = uuid4()
    plain_key = "gw_live_redis_fail_test_key"
    hashed = hash_api_key(plain_key)

    mock_repo = MagicMock()
    mock_repo.revoke_api_key = AsyncMock(return_value=hashed)

    mock_client = AsyncMock()
    mock_client.delete = AsyncMock(side_effect=RuntimeError("Redis connection lost during DELETE"))
    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)
    success = await authenticator.revoke_api_key(api_key_id)

    # Revocation still succeeds despite Redis error
    assert success is True
    mock_repo.revoke_api_key.assert_awaited_once_with(api_key_id)
    mock_client.delete.assert_awaited_once_with(f"api_key_cache:{hashed}")


@pytest.mark.asyncio
async def test_revoked_credential_rejected_after_real_revocation_flow() -> None:
    """Test 4: Revocation purges cache; subsequent authentication hits DB and is rejected (401)."""
    api_key_id = uuid4()
    plain_key = "gw_live_active_then_revoked_key"
    hashed = hash_api_key(plain_key)

    # In-memory Redis simulation
    redis_store: dict[str, str] = {}

    mock_client = AsyncMock()

    async def fake_get(k: str) -> str | None:
        return redis_store.get(k)

    async def fake_set(k: str, v: str, ex: int | None = None) -> None:
        redis_store[k] = v

    async def fake_delete(k: str) -> None:
        redis_store.pop(k, None)

    mock_client.get = AsyncMock(side_effect=fake_get)
    mock_client.set = AsyncMock(side_effect=fake_set)
    mock_client.delete = AsyncMock(side_effect=fake_delete)

    mock_redis_mgr = MagicMock()
    mock_redis_mgr.get_client = MagicMock(return_value=mock_client)

    mock_tenant = Tenant(id="tenant_alpha", name="Alpha Corp", status="active")
    key_record = ApiKey(
        id=api_key_id,
        tenant_id="tenant_alpha",
        key_prefix="gw_live_active",
        hashed_key=hashed,
        status="active",
        is_admin=False,
        expires_at=None,
    )
    key_record.tenant = mock_tenant

    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(return_value=key_record)

    async def fake_revoke(target_id: UUID) -> str | None:
        if target_id == api_key_id:
            key_record.status = "revoked"
            key_record.revoked_at = utc_now()
            return key_record.hashed_key
        return None

    mock_repo.revoke_api_key = AsyncMock(side_effect=fake_revoke)

    authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis_mgr)

    # 1. First authentication populates cache
    ctx = await authenticator.authenticate_key(plain_key)
    assert ctx.tenant_id == "tenant_alpha"
    assert f"api_key_cache:{hashed}" in redis_store

    # 2. Execute real revocation mutation via Authenticator
    revocation_res = await authenticator.revoke_api_key(api_key_id)
    assert revocation_res is True
    # Cache key must be gone
    assert f"api_key_cache:{hashed}" not in redis_store
    assert key_record.status == "revoked"

    # 3. Subsequent authentication attempts must be rejected with 401 AuthenticationError
    with pytest.raises(AuthenticationError) as exc_info:
        await authenticator.authenticate_key(plain_key)
    assert exc_info.value.status_code == 401
