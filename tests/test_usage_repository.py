"""Unit tests for UsageRepository persistence and error handling."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models.request import RequestRecord
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

        # Verify default RequestRecord creation has started_at & completed_at fallback populated
        added_objs = [call[0][0] for call in mock_session.add.call_args_list]
        req_records = [obj for obj in added_objs if isinstance(obj, RequestRecord)]
        assert len(req_records) == 1
        assert req_records[0].started_at is not None
        assert req_records[0].completed_at is not None
        assert req_records[0].latency_ms is None

    @pytest.mark.asyncio
    async def test_explicit_started_at_completed_at_and_latency_ms_persisted(self) -> None:
        """Verify explicit timing fields are persisted to RequestRecord."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        mock_db_mgr = MagicMock(spec=DatabaseManager)
        mock_db_mgr.session.return_value.__aenter__.return_value = mock_session

        repo = UsageRepository(db_mgr=mock_db_mgr)
        t_started = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        t_completed = datetime(2026, 10, 1, 12, 0, 1, tzinfo=UTC)
        record = await repo.create_usage_record(
            request_id="req_timing_1",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_abc",
            feature="chat",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost=Decimal("0.000750"),
            started_at=t_started,
            latency_ms=123.45,
            completed_at=t_completed,
        )

        assert record is not None
        added_objs = [call[0][0] for call in mock_session.add.call_args_list]
        req_records = [obj for obj in added_objs if isinstance(obj, RequestRecord)]
        assert len(req_records) == 1
        assert req_records[0].started_at == t_started
        assert req_records[0].completed_at == t_completed
        assert req_records[0].latency_ms == 123.45

    @pytest.mark.asyncio
    async def test_existing_request_record_updated_if_unset_and_not_overwritten(self) -> None:
        """Verify existing RequestRecord populates timing when unset without overwrite."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_result_req = MagicMock()
        t_existing_start = datetime(2026, 10, 1, 11, 59, 0, tzinfo=UTC)
        existing_req = RequestRecord(
            request_id="req_exist_1",
            tenant_id="tenant_abc",
            feature="chat",
            requested_model="gpt-4o",
            final_provider="openai",
            final_model="gpt-4o",
            status="success",
            started_at=t_existing_start,
            completed_at=None,
            latency_ms=None,
        )
        mock_result_req.scalar_one_or_none.return_value = existing_req

        mock_result_usage = MagicMock()
        mock_result_usage.scalar_one_or_none.return_value = None

        mock_session.execute.side_effect = [mock_result_req, mock_result_usage]

        mock_db_mgr = MagicMock(spec=DatabaseManager)
        mock_db_mgr.session.return_value.__aenter__.return_value = mock_session

        repo = UsageRepository(db_mgr=mock_db_mgr)
        t_new_start = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        t_completed = datetime(2026, 10, 1, 12, 0, 2, tzinfo=UTC)
        await repo.create_usage_record(
            request_id="req_exist_1",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_abc",
            started_at=t_new_start,
            latency_ms=250.0,
            completed_at=t_completed,
        )

        # Existing started_at must NOT be overwritten
        assert existing_req.started_at == t_existing_start
        assert existing_req.completed_at == t_completed
        assert existing_req.latency_ms == 250.0

    @pytest.mark.asyncio
    async def test_existing_request_record_populates_unset_started_at(self) -> None:
        """Verify existing RequestRecord populates started_at if currently None."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_result_req = MagicMock()
        existing_req = RequestRecord(
            request_id="req_exist_2",
            tenant_id="tenant_abc",
            feature="chat",
            requested_model="gpt-4o",
            final_provider="openai",
            final_model="gpt-4o",
            status="success",
            started_at=None,
            completed_at=None,
            latency_ms=None,
        )
        mock_result_req.scalar_one_or_none.return_value = existing_req

        mock_result_usage = MagicMock()
        mock_result_usage.scalar_one_or_none.return_value = None

        mock_session.execute.side_effect = [mock_result_req, mock_result_usage]

        mock_db_mgr = MagicMock(spec=DatabaseManager)
        mock_db_mgr.session.return_value.__aenter__.return_value = mock_session

        repo = UsageRepository(db_mgr=mock_db_mgr)
        t_start = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
        t_completed = datetime(2026, 10, 1, 12, 0, 2, tzinfo=UTC)
        await repo.create_usage_record(
            request_id="req_exist_2",
            provider="openai",
            model="gpt-4o",
            tenant_id="tenant_abc",
            started_at=t_start,
            latency_ms=250.0,
            completed_at=t_completed,
        )

        assert existing_req.started_at == t_start
        assert existing_req.completed_at == t_completed
        assert existing_req.latency_ms == 250.0

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
