"""create example_items

Revision ID: 002
Revises: 001
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, id_column

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "example_items",
        id_column(),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        *audit_columns(),
        sa.UniqueConstraint("code", name="uq_example_items_code"),
    )
    op.create_index(
        "ix_example_items_status_updated_at", "example_items", ["status", "updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_example_items_status_updated_at", table_name="example_items")
    op.drop_table("example_items")
