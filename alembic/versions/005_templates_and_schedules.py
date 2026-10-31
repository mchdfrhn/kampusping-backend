"""message templates and schedule rules

Revision ID: 005
Revises: 004
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No unique constraints on name / offset_days: the services enforce uniqueness among
    # non-deleted rows so a soft-deleted row never blocks reuse.
    op.create_table(
        "message_templates",
        id_column(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *audit_columns(),
    )

    op.create_table(
        "schedule_rules",
        id_column(),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column(
            "template_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *audit_columns(),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["template_id"],
                ["message_templates.id"],
                name="fk_schedule_rules_template_id_message_templates",
            )
        ),
    )
    op.create_index("ix_schedule_rules_offset_days", "schedule_rules", ["offset_days"])
    op.create_index("ix_schedule_rules_template_id", "schedule_rules", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_schedule_rules_template_id", table_name="schedule_rules")
    op.drop_index("ix_schedule_rules_offset_days", table_name="schedule_rules")
    op.drop_table("schedule_rules")
    op.drop_table("message_templates")
