"""Integration tests for live PostgreSQL connection, transaction rollback, and Alembic migrations.

Skipped automatically when PostgreSQL is not running/available.
"""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command
from app.core.config import settings
from app.db.base import Base
from app.db.models import ProviderEvent, RequestRecord, Tenant, UsageRecord


async def _is_postgres_available() -> bool:
    """Check if the configured PostgreSQL database is accessible."""
    try:
        engine = create_async_engine(settings.database_url, connect_args={"timeout": 2.0})
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


# Skip all tests in this module if PostgreSQL is unreachable
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pg_engine() -> AsyncGenerator[AsyncEngine, None]:
    if not await _is_postgres_available():
        pytest.skip("PostgreSQL is not reachable at " + settings.database_url)
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def pg_session(pg_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


class TestPostgreSQLIntegration:
    """Integration test suite against live PostgreSQL."""

    async def test_live_crud_and_relationships(self, pg_session: AsyncSession) -> None:
        """Verify inserting Tenant, Request, Usage, and Event records with live foreign keys."""
        tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Acme Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        req_id = f"req_{uuid.uuid4().hex[:12]}"
        req = RequestRecord(
            request_id=req_id,
            tenant_id=tenant_id,
            feature="chat",
            requested_model="qwen2.5:3b",
            final_provider="ollama",
            final_model="qwen2.5:3b",
            status="success",
            latency_ms=150.0,
            attempt_count=1,
        )
        pg_session.add(req)
        await pg_session.commit()

        usage = UsageRecord(
            request_id=req_id,
            tenant_id=tenant_id,
            provider="ollama",
            model="qwen2.5:3b",
            input_tokens=15,
            output_tokens=30,
            total_tokens=45,
            estimated_cost=Decimal("0.000200"),
        )
        pg_session.add(usage)

        event = ProviderEvent(
            request_id=req_id,
            provider_id="ollama",
            event_type="provider_success",
            latency_ms=150.0,
            event_metadata={"http_status": 200},
        )
        pg_session.add(event)
        await pg_session.commit()

        # Query back and verify relationships
        result = await pg_session.execute(
            text(f"SELECT request_id, final_provider FROM requests WHERE request_id = '{req_id}'")
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == req_id
        assert row[1] == "ollama"

    async def test_tenant_delete_restricted_unloaded(self, pg_session: AsyncSession) -> None:
        """Verify deleting tenant with dependent unloaded records raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        tenant_id = f"tenant_rest_unloaded_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Restricted Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        req_pk = uuid.uuid4()
        req = RequestRecord(
            id=req_pk,
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            requested_model="qwen2.5:3b",
            status="success",
        )
        pg_session.add(req)
        await pg_session.commit()

        # Attempt to delete tenant directly without loading children
        await pg_session.delete(tenant)
        with pytest.raises(IntegrityError):
            await pg_session.commit()
        await pg_session.rollback()

        # Verify dependent record remains intact
        check_req = await pg_session.get(RequestRecord, req_pk)
        assert check_req is not None
        assert check_req.tenant_id == tenant_id

    async def test_tenant_delete_restricted_loaded_requests(self, pg_session: AsyncSession) -> None:
        """Verify deleting tenant with dependent LOADED requests raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        tenant_id = f"tenant_rest_req_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Restricted Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        req_pk = uuid.uuid4()
        req = RequestRecord(
            id=req_pk,
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            requested_model="qwen2.5:3b",
            status="success",
        )
        pg_session.add(req)
        await pg_session.commit()

        # Explicitly load requests relationship into session
        await pg_session.refresh(tenant, ["requests"])
        assert len(tenant.requests) == 1

        # Attempt to delete tenant with loaded collection
        await pg_session.delete(tenant)
        with pytest.raises(IntegrityError):
            await pg_session.commit()
        await pg_session.rollback()

        # Verify dependent record remains intact and was not nullified
        check_req = await pg_session.get(RequestRecord, req_pk)
        assert check_req is not None
        assert check_req.tenant_id == tenant_id

    async def test_tenant_delete_restricted_loaded_usage_records(
        self, pg_session: AsyncSession
    ) -> None:
        """Verify deleting tenant with dependent LOADED usage_records raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        tenant_id = f"tenant_rest_usage_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Restricted Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        req_id = f"req_{uuid.uuid4().hex[:12]}"
        req = RequestRecord(
            request_id=req_id,
            tenant_id=tenant_id,
            requested_model="qwen2.5:3b",
            status="success",
        )
        pg_session.add(req)
        await pg_session.commit()

        usage_pk = uuid.uuid4()
        usage = UsageRecord(
            id=usage_pk,
            request_id=req_id,
            tenant_id=tenant_id,
            provider="ollama",
            model="qwen2.5:3b",
            input_tokens=10,
            output_tokens=20,
            total_tokens=30,
        )
        pg_session.add(usage)
        await pg_session.commit()

        # Explicitly load usage_records relationship into session
        await pg_session.refresh(tenant, ["usage_records"])
        assert len(tenant.usage_records) == 1

        # Attempt to delete tenant with loaded collection
        await pg_session.delete(tenant)
        with pytest.raises(IntegrityError):
            await pg_session.commit()
        await pg_session.rollback()

        # Verify dependent record remains intact and was not nullified
        check_usage = await pg_session.get(UsageRecord, usage_pk)
        assert check_usage is not None
        assert check_usage.tenant_id == tenant_id

    async def test_tenant_delete_restricted_loaded_api_keys(self, pg_session: AsyncSession) -> None:
        """Verify deleting tenant with dependent LOADED api_keys raises IntegrityError."""
        from sqlalchemy.exc import IntegrityError

        from app.core.auth import generate_api_key
        from app.db.models import ApiKey

        tenant_id = f"tenant_rest_key_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Restricted Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        _, key_prefix, hashed_key = generate_api_key(environment="live")
        api_key_pk = uuid.uuid4()
        api_key = ApiKey(
            id=api_key_pk,
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            name="Restricted Key",
            is_admin=False,
            status="active",
        )
        pg_session.add(api_key)
        await pg_session.commit()

        # Explicitly load api_keys relationship into session
        await pg_session.refresh(tenant, ["api_keys"])
        assert len(tenant.api_keys) == 1

        # Attempt to delete tenant with loaded collection
        await pg_session.delete(tenant)
        with pytest.raises(IntegrityError):
            await pg_session.commit()
        await pg_session.rollback()

        # Verify dependent record remains intact
        check_key = await pg_session.get(ApiKey, api_key_pk)
        assert check_key is not None
        assert check_key.tenant_id == tenant_id

    async def test_api_key_persistence_and_lookup(self, pg_session: AsyncSession) -> None:
        """Verify persisting and querying ApiKey entity against live PostgreSQL."""
        from app.core.auth import generate_api_key
        from app.db.models import ApiKey

        tenant_id = f"tenant_key_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Key Corp", status="active")
        pg_session.add(tenant)
        await pg_session.commit()

        full_key, key_prefix, hashed_key = generate_api_key(environment="live")

        api_key = ApiKey(
            tenant_id=tenant_id,
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            name="Test Prod Key",
            is_admin=True,
            status="active",
        )
        pg_session.add(api_key)
        await pg_session.commit()

        # Query back by hashed_key
        query = text(
            "SELECT tenant_id, key_prefix, is_admin FROM api_keys "
            f"WHERE hashed_key = '{hashed_key}'"
        )
        result = await pg_session.execute(query)
        row = result.fetchone()
        assert row is not None
        assert row[0] == tenant_id
        assert row[1] == key_prefix
        assert row[2] is True


class TestAlembicMigrationsIntegration:
    """Test running Alembic upgrade and downgrade against live PostgreSQL."""

    async def test_alembic_upgrade_and_downgrade(self) -> None:
        if not await _is_postgres_available():
            pytest.skip("PostgreSQL is not reachable for Alembic migration testing")

        # Clean slate: drop all tables before testing migration chain
        engine = create_async_engine(settings.database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
        await engine.dispose()

        alembic_cfg = Config("alembic.ini")
        # Upgrade to head
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        # Downgrade to base
        await asyncio.to_thread(command.downgrade, alembic_cfg, "base")
        # Re-upgrade to head
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        # Cleanly downgrade back to base
        await asyncio.to_thread(command.downgrade, alembic_cfg, "base")
