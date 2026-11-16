"""import batch kind and preview details

Revision ID: 011
Revises: 010
Create Date: 2026-11-16

Adds ``import_batches.kind`` (``standard`` or ``rekap``) and ``import_batches.details`` (the
new / changed / suspected-paid lists shown in the preview; NULL once the batch is applied or
discarded). Existing batches become ``standard`` with no details.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(10), nullable=False, server_default="standard")
        )
        batch_op.add_column(sa.Column("details", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("import_batches") as batch_op:
        batch_op.drop_column("details")
        batch_op.drop_column("kind")
