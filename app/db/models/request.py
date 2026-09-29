"""Historical request audit record model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.provider_event import ProviderEvent
    from app.db.models.tenant import Tenant
    from app.db.models.usage import UsageRecord


class RequestRecord(Base):
    """Stores historical request metadata for auditing, latency, and routing investigation.

    Explicitly excludes sensitive prompt and completion texts.
    """

    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    feature: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default="chat",
    )
    requested_model: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    final_provider: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    final_model: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    tenant: Mapped["Tenant | None"] = relationship("Tenant", back_populates="requests")
    usage_records: Mapped[list["UsageRecord"]] = relationship(
        "UsageRecord", back_populates="request"
    )
    provider_events: Mapped[list["ProviderEvent"]] = relationship(
        "ProviderEvent", back_populates="request"
    )

    __table_args__ = (Index("ix_requests_tenant_started_at", "tenant_id", "started_at"),)
