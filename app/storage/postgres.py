"""PostgreSQL health utility and diagnostic reporting."""

from __future__ import annotations

import logging
from typing import Any

from app.db.session import DatabaseManager, database_manager

logger = logging.getLogger(__name__)


class PostgresHealthUtility:
    """Provides connectivity health check and status reporting for PostgreSQL."""

    def __init__(self, db_mgr: DatabaseManager | None = None) -> None:
        self.db_mgr = db_mgr or database_manager

    @property
    def is_connected(self) -> bool:
        """Return cached connectivity state."""
        return self.db_mgr.is_connected

    async def check_health(self) -> dict[str, Any]:
        """Perform a live ping check against PostgreSQL and return diagnostic info."""
        is_ok = await self.db_mgr.ping()
        return {
            "status": "connected" if is_ok else "disconnected",
            "available": is_ok,
            "engine_initialized": self.db_mgr.engine is not None,
        }


# Global health utility instance
postgres_health = PostgresHealthUtility()
