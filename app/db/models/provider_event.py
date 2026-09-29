"""Provider lifecycle and failure event model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.db.models.request import RequestRecord


class ProviderEvent(Base):
    """Stores provider lifecycle transitions, circuit state changes, and error audit events."""

    __tablename__ = "provider_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("requests.request_id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    provider_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    error_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    # Relationships
    request: Mapped["RequestRecord | None"] = relationship(
        "RequestRecord", back_populates="provider_events"
    )

    __table_args__ = (
        Index("ix_provider_events_provider_timestamp", "provider_id", "timestamp"),
        Index("ix_provider_events_type_timestamp", "event_type", "timestamp"),
    )
