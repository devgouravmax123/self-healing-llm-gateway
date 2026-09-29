"""Async PostgreSQL database session and connection management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.sql import text

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages the async SQLAlchemy engine, connection pool, and session factory."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._is_connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Return whether the database is currently reported as connected."""
        return self._is_connected

    @property
    def engine(self) -> AsyncEngine | None:
        """Return the active async engine instance if initialized."""
        return self._engine

    def get_sessionmaker(self) -> async_sessionmaker[AsyncSession] | None:
        """Return the async session factory if initialized."""
        return self._sessionmaker

    async def initialize(self) -> bool:
        """Initialize the async engine and session factory with connection pooling.

        Performs a bounded test connection check. If connection fails, logs a warning
        and sets is_connected=False (allowing the gateway to start in degraded persistence mode).
        """
        try:
            logger.info(
                "Initializing PostgreSQL engine for %s", self.config.database_url.split("@")[-1]
            )
            self._engine = create_async_engine(
                self.config.database_url,
                pool_size=self.config.db_pool_size,
                max_overflow=self.config.db_max_overflow,
                pool_timeout=self.config.db_pool_timeout,
                pool_pre_ping=True,
                echo=False,
            )
            self._sessionmaker = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=False,
            )
            # Test connectivity with bounded query
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._is_connected = True
            logger.info("PostgreSQL database connection established successfully.")
            return True
        except Exception as exc:
            logger.warning(
                "PostgreSQL connection check failed during startup: %s. "
                "Operating in degraded persistence mode (database features unavailable).",
                exc,
            )
            self._is_connected = False
            return False

    async def ping(self) -> bool:
        """Ping the database server using a lightweight query.

        Updates the internal `is_connected` status accordingly.
        """
        if self._engine is None:
            self._is_connected = False
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._is_connected = True
            return True
        except Exception as exc:
            logger.warning("PostgreSQL ping check failed: %s", exc)
            self._is_connected = False
            return False

    async def close(self) -> None:
        """Gracefully dispose of the engine and release all pooled connections."""
        self._is_connected = False
        if self._engine is not None:
            try:
                await self._engine.dispose()
                logger.info("PostgreSQL engine disposed successfully.")
            except Exception as exc:
                logger.debug("Error while disposing PostgreSQL engine: %s", exc)
            self._engine = None
            self._sessionmaker = None

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide an async transactional session context with automatic rollback on error."""
        if self._sessionmaker is None:
            if self._engine is None:
                self._engine = create_async_engine(
                    self.config.database_url,
                    pool_size=self.config.db_pool_size,
                    max_overflow=self.config.db_max_overflow,
                    pool_timeout=self.config.db_pool_timeout,
                    pool_pre_ping=True,
                )
            self._sessionmaker = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                class_=AsyncSession,
                autoflush=False,
            )

        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Global database manager instance
database_manager = DatabaseManager()


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Convenience dependency / context manager for acquiring a database session."""
    async with database_manager.session() as session:
        yield session
