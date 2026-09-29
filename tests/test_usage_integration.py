"""Integration tests for durable usage tracking against live PostgreSQL."""

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.db.base import Base
from app.db.models.tenant import Tenant
from app.db.session import database_manager
from app.models.provider import ProviderTarget
from app.models.requests import ChatCompletionRequest, ChatMessage
from app.models.responses import (
    ChatCompletionChoice,
    ChatCompletionMessageResponse,
    ChatCompletionResponse,
    CompletionUsage,
)
from app.usage.tracker import usage_tracker


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


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def cleanup_db_manager() -> AsyncGenerator[None, None]:
    """Ensure database_manager connection pool is cleanly closed between tests."""
    yield
    await database_manager.close()


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
        # Pre-seed standard test tenant to satisfy foreign-key constraints
        tenant = Tenant(id="tenant_test_1", name="Test Org", status="active")
        session.add(tenant)
        await session.commit()
        yield session


class TestUsageIntegration:
    """Integration tests for live PostgreSQL usage tracking."""

    async def test_live_usage_record_persistence(self, pg_session: AsyncSession) -> None:
        """Verify tracking usage writes durable UsageRecord into live PostgreSQL."""
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        req = ChatCompletionRequest(
            model="gpt-4o",
            messages=[ChatMessage(role="user", content="Test prompt")],
            metadata={"tenant_id": "tenant_test_1", "feature": "chat_test"},
        )
        target = ProviderTarget(
            id="openai_prod",
            provider="openai",
            model="gpt-4o",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id=f"chatcmpl-{req_id}",
            model="gpt-4o",
            choices=[
                ChatCompletionChoice(
                    message=ChatCompletionMessageResponse(role="assistant", content="Answer")
                )
            ],
            usage=CompletionUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )

        await usage_tracker.record_usage(
            request=req,
            request_id=req_id,
            target=target,
            response=resp,
        )

        # Query back from DB
        sql = (
            "SELECT request_id, provider, model, input_tokens, output_tokens, estimated_cost "
            f"FROM usage_records WHERE request_id = '{req_id}'"
        )
        result = await pg_session.execute(text(sql))
        row = result.fetchone()
        assert row is not None
        assert row[0] == req_id
        assert row[1] == "openai"
        assert row[2] == "gpt-4o"
        assert row[3] == 100
        assert row[4] == 50
        assert row[5] == Decimal("0.000750")

    async def test_live_usage_null_metrics_persistence(self, pg_session: AsyncSession) -> None:
        """Verify tracking usage with None tokens writes SQL NULL without fabricating 0."""
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        req = ChatCompletionRequest(
            model="custom-cloud",
            messages=[ChatMessage(role="user", content="Test prompt")],
        )
        target = ProviderTarget(
            id="custom_target",
            provider="custom_cloud",
            model="unpriced-model",
            enabled=True,
            priority=1,
        )
        resp = ChatCompletionResponse(
            id=f"chatcmpl-{req_id}",
            model="unpriced-model",
            choices=[],
            usage=None,
        )

        await usage_tracker.record_usage(
            request=req,
            request_id=req_id,
            target=target,
            response=resp,
        )

        sql = (
            "SELECT input_tokens, output_tokens, total_tokens, estimated_cost "
            f"FROM usage_records WHERE request_id = '{req_id}'"
        )
        result = await pg_session.execute(text(sql))
        row = result.fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None
        assert row[2] is None
        assert row[3] is None
