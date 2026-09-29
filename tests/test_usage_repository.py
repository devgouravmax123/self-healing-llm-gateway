"""Unit tests for UsageRepository persistence and error handling."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.repositories.usage_repo import UsageRepository
from app.db.session import DatabaseManager


class TestUsageRepository:
    """Test suite for UsageRepository."""

    @pytest.mark.asyncio
    async def test_successful_usage_record_creation(self) -> None:
        """Verify repository creates RequestRecord and UsageRecord within session context."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # No pre-existing record
        mock_session.execute.return_value = mock_result

        mock_db_mgr = MagicMock(spec=DatabaseManager)
        mock_db_mgr.session.return_value.__aenter__.return_value = mock_session

        repo = UsageRepository(db_mgr=mock_db_mgr)
        record = await repo.create_usage_record(
            request_id="req_success_1",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_abc",
            feature="chat",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost=Decimal("0.000750"),
        )

        assert record is not None
        assert record.request_id == "req_success_1"
        assert record.provider == "openai"
        assert record.model == "gpt-4o"
        assert record.input_tokens == 100
        assert record.output_tokens == 50
        assert record.total_tokens == 150
        assert record.estimated_cost == Decimal("0.000750")

    @pytest.mark.asyncio
    async def test_database_exception_returns_none_without_raising(self) -> None:
        """Verify database errors are caught, logged, and return None gracefully."""
        mock_db_mgr = MagicMock(spec=DatabaseManager)
        mock_db_mgr.session.side_effect = Exception("PostgreSQL down")

        repo = UsageRepository(db_mgr=mock_db_mgr)
        record = await repo.create_usage_record(
            request_id="req_fail_1",
            provider="ollama",
            model="qwen2.5:3b",
            input_tokens=10,
            output_tokens=20,
        )

        assert record is None
