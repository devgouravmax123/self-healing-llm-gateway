"""API Key repository for database queries and management."""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.base import utc_now
from app.db.models.api_key import ApiKey
from app.db.models.tenant import Tenant
from app.db.session import DatabaseManager, database_manager

logger = logging.getLogger(__name__)


class ApiKeyRepository:
    """Async repository managing database operations for ApiKey entities."""

    def __init__(self, db_mgr: DatabaseManager | None = None) -> None:
        self.db_mgr = db_mgr or database_manager

    async def get_by_hashed_key(self, hashed_key: str) -> ApiKey | None:
        """Query an ApiKey by its SHA-256 hash including eager loaded Tenant.

        Raises any underlying database exception so that caller can distinguish
        between database connectivity failure vs genuine missing key.
        """
        async with self.db_mgr.session() as session:
            stmt = (
                select(ApiKey)
                .options(selectinload(ApiKey.tenant))
                .where(ApiKey.hashed_key == hashed_key)
            )
            res = await session.execute(stmt)
            return res.scalar_one_or_none()

    async def create_api_key(
        self,
        tenant_id: str,
        key_prefix: str,
        hashed_key: str,
        name: str = "default",
        is_admin: bool = False,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        """Create and persist a new ApiKey entity."""
        async with self.db_mgr.session() as session:
            # Verify tenant exists
            tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
            tenant_res = await session.execute(tenant_stmt)
            tenant = tenant_res.scalar_one_or_none()
            if tenant is None:
                raise ValueError(f"Tenant '{tenant_id}' does not exist.")

            api_key = ApiKey(
                tenant_id=tenant_id,
                key_prefix=key_prefix,
                hashed_key=hashed_key,
                name=name,
                is_admin=is_admin,
                status="active",
                expires_at=expires_at,
            )
            session.add(api_key)
            await session.flush()
            await session.refresh(api_key, ["tenant"])
            return api_key

    async def revoke_api_key(self, api_key_id: UUID) -> str | None:
        """Revoke an API key by ID and return its hashed_key if found."""
        async with self.db_mgr.session() as session:
            stmt = select(ApiKey).where(ApiKey.id == api_key_id)
            res = await session.execute(stmt)
            key = res.scalar_one_or_none()
            if key is None:
                return None
            key.status = "revoked"
            key.revoked_at = utc_now()
            return key.hashed_key


# Global API key repository singleton
api_key_repository = ApiKeyRepository()
