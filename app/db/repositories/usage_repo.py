"""Usage repository for persisting usage records to PostgreSQL."""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select

from app.db.models.request import RequestRecord
from app.db.models.usage import UsageRecord
from app.db.session import DatabaseManager, database_manager

logger = logging.getLogger(__name__)


class UsageRepository:
    """Async repository managing database persistence of UsageRecord entities."""

    def __init__(self, db_mgr: DatabaseManager | None = None) -> None:
        self.db_mgr = db_mgr or database_manager

    async def create_usage_record(
        self,
        request_id: str,
        provider: str,
        model: str,
        tenant_id: str | None = None,
        feature: str | None = "chat",
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost: Decimal | None = None,
        requested_model: str | None = None,
    ) -> UsageRecord | None:
        """Persist a UsageRecord safely to PostgreSQL.

        Ensures idempotent insertion and handles parent RequestRecord creation if needed.
        Catches any database/connectivity error, logs a warning, and returns None
        without raising to caller (guaranteeing LLM inference remains uninterrupted).
        """
        try:
            async with self.db_mgr.session() as session:
                # 1. Ensure parent RequestRecord exists to satisfy FK constraint
                req_stmt = select(RequestRecord).where(RequestRecord.request_id == request_id)
                res = await session.execute(req_stmt)
                req = res.scalar_one_or_none()

                if req is None:
                    # Create placeholder RequestRecord
                    req = RequestRecord(
                        request_id=request_id,
                        tenant_id=tenant_id,
                        feature=feature or "chat",
                        requested_model=requested_model or model,
                        final_provider=provider,
                        final_model=model,
                        status="success",
                    )
                    session.add(req)
                    await session.flush()

                # 2. Check for duplicate/existing UsageRecord for idempotency
                usage_stmt = select(UsageRecord).where(UsageRecord.request_id == request_id)
                usage_res = await session.execute(usage_stmt)
                existing_usage = usage_res.scalar_one_or_none()
                if existing_usage is not None:
                    logger.debug(
                        "Usage record already exists for request_id=%s, skipping insert", request_id
                    )
                    return existing_usage

                # 3. Create new UsageRecord
                usage_record = UsageRecord(
                    request_id=request_id,
                    tenant_id=tenant_id,
                    feature=feature or "chat",
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    estimated_cost=estimated_cost,
                )
                session.add(usage_record)
                # Session commit is performed automatically by session() context manager
                return usage_record

        except Exception as exc:
            logger.warning(
                "Failed to persist usage record for request_id=%s (provider=%s, model=%s): %s",
                request_id,
                provider,
                model,
                exc,
            )
            return None


# Global usage repository instance
usage_repository = UsageRepository()
