"""Unit tests for scripts/bootstrap_local_demo.py."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.auth import Authenticator, hash_api_key
from app.db.models.api_key import ApiKey
from app.db.models.tenant import Tenant
from scripts.bootstrap_local_demo import bootstrap_local_demo


@pytest.mark.asyncio
async def test_bootstrap_local_demo_flow() -> None:
    """Verify bootstrap creates tenant if needed, persists hashed key, and generates valid key."""
    mock_tenant = Tenant(id="demo_tenant", name="Local Demo Tenant", status="active")
    stored_keys: list[dict[str, str]] = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_tenant))
    )
    mock_session.commit = AsyncMock()

    mock_db_ctx = MagicMock()
    mock_db_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_db_mgr = MagicMock()
    mock_db_mgr.initialize = AsyncMock()
    mock_db_mgr.session = MagicMock(return_value=mock_db_ctx)

    async def fake_create_api_key(
        tenant_id: str, key_prefix: str, hashed_key: str, **kwargs: str
    ) -> ApiKey:
        stored_keys.append(
            {
                "tenant_id": tenant_id,
                "key_prefix": key_prefix,
                "hashed_key": hashed_key,
            }
        )
        key_record = ApiKey(
            id=uuid4(),
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            status="active",
        )
        key_record.tenant = mock_tenant
        return key_record

    with (
        patch("scripts.bootstrap_local_demo.database_manager", mock_db_mgr),
        patch(
            "scripts.bootstrap_local_demo.api_key_repository.create_api_key",
            side_effect=fake_create_api_key,
        ),
    ):
        plain_key_1 = await bootstrap_local_demo()
        plain_key_2 = await bootstrap_local_demo()

        # 1. Plaintext keys must follow standard format and be unique
        assert plain_key_1.startswith("gw_live_")
        assert plain_key_2.startswith("gw_live_")
        assert plain_key_1 != plain_key_2

        # 2. Database stores only the SHA-256 hash, never the plaintext key
        assert len(stored_keys) == 2
        assert stored_keys[0]["hashed_key"] == hash_api_key(plain_key_1)
        assert stored_keys[1]["hashed_key"] == hash_api_key(plain_key_2)
        assert plain_key_1 not in [stored_keys[0]["hashed_key"], stored_keys[1]["hashed_key"]]

        # 3. Authenticator validates the generated plaintext key against the hashed record
        mock_repo = MagicMock()
        record = ApiKey(
            id=uuid4(),
            tenant_id="demo_tenant",
            key_prefix=stored_keys[0]["key_prefix"],
            hashed_key=stored_keys[0]["hashed_key"],
            status="active",
        )
        record.tenant = mock_tenant
        mock_repo.get_by_hashed_key = AsyncMock(return_value=record)

        mock_redis = MagicMock()
        mock_redis.get_client = MagicMock(return_value=None)
        mock_redis.is_connected = False

        authenticator = Authenticator(repo=mock_repo, redis_mgr=mock_redis)
        ctx = await authenticator.authenticate_key(plain_key_1)
        assert ctx.tenant_id == "demo_tenant"


@pytest.mark.asyncio
async def test_bootstrap_local_demo_creates_missing_tenant() -> None:
    """Verify bootstrap creates and commits a new Tenant when none exists."""
    added_entities: list[Tenant] = []
    stored_keys: list[dict[str, str]] = []

    mock_session = AsyncMock()
    # Simulate missing tenant on lookup
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )
    mock_session.commit = AsyncMock()

    def fake_add(entity: Tenant) -> None:
        added_entities.append(entity)

    mock_session.add = MagicMock(side_effect=fake_add)

    mock_db_ctx = MagicMock()
    mock_db_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_db_mgr = MagicMock()
    mock_db_mgr.initialize = AsyncMock()
    mock_db_mgr.session = MagicMock(return_value=mock_db_ctx)

    async def fake_create_api_key(
        tenant_id: str, key_prefix: str, hashed_key: str, **kwargs: str
    ) -> ApiKey:
        stored_keys.append(
            {
                "tenant_id": tenant_id,
                "key_prefix": key_prefix,
                "hashed_key": hashed_key,
            }
        )
        return ApiKey(
            id=uuid4(),
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            status="active",
        )

    with (
        patch("scripts.bootstrap_local_demo.database_manager", mock_db_mgr),
        patch(
            "scripts.bootstrap_local_demo.api_key_repository.create_api_key",
            side_effect=fake_create_api_key,
        ),
    ):
        plain_key = await bootstrap_local_demo()

        # 1. Verify database_manager.initialize was called
        mock_db_mgr.initialize.assert_awaited_once()

        # 2. Verify Tenant entity was constructed and added with required fields
        assert len(added_entities) == 1
        new_tenant = added_entities[0]
        assert isinstance(new_tenant, Tenant)
        assert new_tenant.id == "demo_tenant"
        assert new_tenant.name == "Local Demo Tenant"
        assert new_tenant.status == "active"

        # 3. Verify session.commit() was called to persist the new tenant
        mock_session.commit.assert_awaited_once()

        # 4. Verify valid API key was generated and stored securely
        assert plain_key.startswith("gw_live_")
        assert len(stored_keys) == 1
        assert stored_keys[0]["tenant_id"] == "demo_tenant"
        assert stored_keys[0]["hashed_key"] == hash_api_key(plain_key)
        assert plain_key != stored_keys[0]["hashed_key"]
