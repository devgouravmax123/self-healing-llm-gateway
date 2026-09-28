"""Async Redis connection manager with connection pooling and health checks."""

from __future__ import annotations

import logging
from typing import Any

from redis.asyncio.client import Redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError

from app.core.config import Settings, settings

logger = logging.getLogger(__name__)


class RedisManager:
    """Manages the async Redis connection pool and provides health check helpers."""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._pool: ConnectionPool[Any] | None = None
        self._client: Redis[Any] | None = None
        self._is_connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Return whether Redis is currently reported as connected."""
        return self._is_connected

    async def initialize(self) -> bool:
        """Initialize the Redis connection pool and perform a bounded health check.

        Returns True if connected successfully, False otherwise (degraded mode).
        """
        try:
            logger.info("Initializing Redis connection pool for %s", self.config.redis_url)
            self._pool = ConnectionPool.from_url(
                self.config.redis_url,
                max_connections=20,
                decode_responses=True,
                socket_timeout=2.0,
                socket_connect_timeout=2.0,
            )
            self._client = Redis(connection_pool=self._pool)
            # Verify connectivity with a bounded ping
            await self._client.ping()
            self._is_connected = True
            logger.info("Redis connection established successfully.")
            return True
        except (RedisError, OSError, Exception) as exc:
            logger.warning(
                "Redis connection failed on startup: %s. Operating in in-memory degraded mode.",
                exc,
            )
            self._is_connected = False
            return False

    def get_client(self) -> Redis[Any] | None:
        """Return the active Redis async client instance if connected, else None."""
        if self._client is not None:
            return self._client
        return None

    async def ping(self) -> bool:
        """Ping the Redis server with a bounded timeout.

        Updates the internal `is_connected` status accordingly.
        """
        if self._client is None:
            self._is_connected = False
            return False
        try:
            res: Any = await self._client.ping()
            self._is_connected = bool(res)
            return self._is_connected
        except (RedisError, OSError, Exception) as exc:
            logger.warning("Redis ping check failed: %s", exc)
            self._is_connected = False
            return False

    async def close(self) -> None:
        """Gracefully close the Redis client and connection pool."""
        self._is_connected = False
        if self._client is not None:
            try:
                close_func = getattr(self._client, "aclose", None) or getattr(
                    self._client, "close", None
                )
                if close_func is not None:
                    res = close_func()
                    if hasattr(res, "__await__"):
                        await res
            except Exception as exc:
                logger.debug("Error while closing Redis client: %s", exc)
            self._client = None
        if self._pool is not None:
            try:
                close_pool_func = getattr(self._pool, "aclose", None) or getattr(
                    self._pool, "disconnect", None
                )
                if close_pool_func is not None:
                    res = close_pool_func()
                    if hasattr(res, "__await__"):
                        await res
            except Exception as exc:
                logger.debug("Error while closing Redis connection pool: %s", exc)
            self._pool = None
        logger.info("Redis client closed.")


# Global Redis manager singleton
redis_manager = RedisManager()
