"""import change log and rollback columns

Revision ID: 008
Revises: 007
Create Date: 2026-11-10

``import_changes`` records every student and invoice an applied import created or changed, with
the tracked fields before and after, so an import can be rolled back. Imports applied before this
migration have ``has_change_log = false`` and cannot be rolled back. No permission is granted here:
``imports:rollback`` reaches ``admin`` through the seed only.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batches",
        sa.Column("has_change_log", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("import_batches", sa.Column("rolled_back_at", sa.DateTime(timezone=True)))

    op.create_table(
        "import_changes",
        id_column(),
        sa.Column("batch_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("entity", sa.String(10), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("before", sa.JSON(none_as_null=True)),
        sa.Column("after", sa.JSON(), nullable=False),
        *audit_columns(),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["batch_id"],
                ["import_batches.id"],
                name="fk_import_changes_batch_id_import_batches",
            )
        ),
    )
    op.create_index("ix_import_changes_batch_id_entity", "import_changes", ["batch_id", "entity"])


def downgrade() -> None:
    op.drop_index("ix_import_changes_batch_id_entity", table_name="import_changes")
    op.drop_table("import_changes")
    op.drop_column("import_batches", "rolled_back_at")
    op.drop_column("import_batches", "has_change_log")
