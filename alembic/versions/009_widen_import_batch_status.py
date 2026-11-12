"""widen import_batches status column to varchar(20)

Revision ID: 009
Revises: 008
Create Date: 2026-11-12

Widens ``import_batches.status`` from String(10) to String(20) to accommodate
``rolled_back`` (11 characters).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(10),
            type_=sa.String(20),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(20),
            type_=sa.String(10),
            existing_nullable=False,
        )
