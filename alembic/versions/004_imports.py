"""import batches and row errors

Revision ID: 004
Revises: 003
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        id_column(),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("semester", sa.String(50)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(none_as_null=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        *audit_columns(),
    )
    op.create_index("ix_import_batches_status", "import_batches", ["status"])

    op.create_table(
        "import_row_errors",
        id_column(),
        sa.Column("batch_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(30), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        *audit_columns(),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["batch_id"],
                ["import_batches.id"],
                name="fk_import_row_errors_batch_id_import_batches",
            )
        ),
    )
    op.create_index(
        "ix_import_row_errors_batch_id_row_number",
        "import_row_errors",
        ["batch_id", "row_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_import_row_errors_batch_id_row_number", table_name="import_row_errors")
    op.drop_table("import_row_errors")
    op.drop_index("ix_import_batches_status", table_name="import_batches")
    op.drop_table("import_batches")
