"""Unit tests for DatabaseManager and session lifecycle management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings
from app.db.session import DatabaseManager
from app.storage.postgres import PostgresHealthUtility


class TestDatabaseManager:
    """Test suite for DatabaseManager unit behavior."""

    def test_default_state(self) -> None:
        """Verify initial default state of manager before initialization."""
        mgr = DatabaseManager()
        assert not mgr.is_connected
        assert mgr.engine is None
        assert mgr.get_sessionmaker() is None

    @pytest.mark.asyncio
    async def test_initialize_success(self) -> None:
        """Verify successful initialization connects and sets is_connected=True."""
        cfg = Settings(
            DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/test_db",
            DB_POOL_SIZE=5,
            DB_MAX_OVERFLOW=10,
            DB_POOL_TIMEOUT=15.0,
        )
        mgr = DatabaseManager(config=cfg)

        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__.return_value = mock_conn

        with patch("app.db.session.create_async_engine", return_value=mock_engine):
            res = await mgr.initialize()
            assert res is True
            assert mgr.is_connected is True
            assert mgr.engine is mock_engine
            assert mgr.get_sessionmaker() is not None

    @pytest.mark.asyncio
    async def test_initialize_failure_degraded(self) -> None:
        """Verify initialization connection error fails gracefully to degraded mode."""
        cfg = Settings(
            DATABASE_URL="postgresql+asyncpg://invalid_user:invalid_pass@localhost:5432/nonexistent"
        )
        mgr = DatabaseManager(config=cfg)

        mock_engine = MagicMock()
        mock_engine.connect.side_effect = ConnectionRefusedError("Connection refused")

        with patch("app.db.session.create_async_engine", return_value=mock_engine):
            res = await mgr.initialize()
            assert res is False
            assert mgr.is_connected is False

    @pytest.mark.asyncio
    async def test_ping_success_and_failure(self) -> None:
        """Verify ping reports connectivity properly."""
        mgr = DatabaseManager()
        # Uninitialized ping returns False
        assert await mgr.ping() is False

        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_engine.connect.return_value.__aenter__.return_value = mock_conn
        mgr._engine = mock_engine

        assert await mgr.ping() is True
        assert mgr.is_connected is True

        # Ping failure
        mock_engine.connect.side_effect = Exception("DB timeout")
        assert await mgr.ping() is False
        assert mgr.is_connected is False

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """Verify close properly disposes the engine."""
        mgr = DatabaseManager()
        mock_engine = AsyncMock()
        mgr._engine = mock_engine
        mgr._is_connected = True

        await mgr.close()
        mock_engine.dispose.assert_awaited_once()
        assert mgr.engine is None
        assert mgr.get_sessionmaker() is None
        assert not mgr.is_connected

    @pytest.mark.asyncio
    async def test_session_commit_and_rollback(self) -> None:
        """Verify session context commits on success and rolls back on exception."""
        mgr = DatabaseManager()
        mock_session = AsyncMock()
        mock_sessionmaker = MagicMock(return_value=mock_session)
        mgr._sessionmaker = mock_sessionmaker

        # Test successful commit
        async with mgr.session() as s:
            assert s is mock_session
        mock_session.commit.assert_awaited_once()
        mock_session.close.assert_awaited_once()

        # Test rollback on exception
        mock_session.reset_mock()
        with pytest.raises(ValueError, match="Test error"):
            async with mgr.session() as s:
                raise ValueError("Test error")
        mock_session.rollback.assert_awaited_once()
        mock_session.close.assert_awaited_once()


class TestPostgresHealthUtility:
    """Test suite for PostgresHealthUtility status checks."""

    @pytest.mark.asyncio
    async def test_health_check_reporting(self) -> None:
        """Verify check_health maps ping state into diagnostic structure."""
        mock_mgr = AsyncMock(spec=DatabaseManager)
        mock_mgr.ping.return_value = True
        mock_mgr.engine = MagicMock()

        util = PostgresHealthUtility(db_mgr=mock_mgr)
        res = await util.check_health()
        assert res["status"] == "connected"
        assert res["available"] is True
        assert res["engine_initialized"] is True

        mock_mgr.ping.return_value = False
        res = await util.check_health()
        assert res["status"] == "disconnected"
        assert res["available"] is False
