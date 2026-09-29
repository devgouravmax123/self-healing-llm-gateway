"""Tenant declarative model."""

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.db.models.request import RequestRecord
    from app.db.models.usage import UsageRecord


class Tenant(Base, TimestampMixin):
    """Represents a tenant or organization entity using the gateway."""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    # Relationships (NO ACTION / RESTRICT on delete preserved by FKs in children)
    requests: Mapped[list["RequestRecord"]] = relationship(
        "RequestRecord",
        back_populates="tenant",
    )
    usage_records: Mapped[list["UsageRecord"]] = relationship(
        "UsageRecord",
        back_populates="tenant",
    )
