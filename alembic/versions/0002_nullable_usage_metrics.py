"""Make usage_records metric and cost columns nullable.

Revision ID: 0002_nullable_usage_metrics
Revises: 0001_initial_schema
Create Date: 2026-09-29 18:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_nullable_usage_metrics"
down_revision: str | Sequence[str] | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("usage_records", "input_tokens", nullable=True, server_default=None)
    op.alter_column("usage_records", "output_tokens", nullable=True, server_default=None)
    op.alter_column("usage_records", "total_tokens", nullable=True, server_default=None)
    op.alter_column("usage_records", "estimated_cost", nullable=True, server_default=None)


def downgrade() -> None:
    op.alter_column("usage_records", "estimated_cost", nullable=False, server_default="0.000000")
    op.alter_column("usage_records", "total_tokens", nullable=False, server_default="0")
    op.alter_column("usage_records", "output_tokens", nullable=False, server_default="0")
    op.alter_column("usage_records", "input_tokens", nullable=False, server_default="0")
