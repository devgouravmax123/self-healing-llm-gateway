"""Unit tests for API key generation, hashing, and authentication logic."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

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
async def test_authenticator_db_failure_raises_500() -> None:
    """Verify unexpected database error raises 500 GatewayError without leaking SQL."""
    mock_repo = MagicMock()
    mock_repo.get_by_hashed_key = AsyncMock(side_effect=RuntimeError("DB Connection Lost"))
    authenticator = Authenticator(repo=mock_repo)

    with pytest.raises(GatewayError) as exc_info:
        await authenticator.authenticate_key("gw_live_key")
    assert exc_info.value.status_code == 500


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
