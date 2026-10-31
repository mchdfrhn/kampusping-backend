"""messages queue

Revision ID: 006
Revises: 005
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "messages",
        id_column(),
        sa.Column("student_id", FK_TYPE, nullable=False),
        sa.Column("invoice_id", FK_TYPE, nullable=False),
        sa.Column("rule_id", FK_TYPE),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("batch_ref", sa.String(50)),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("cancel_reason", sa.String(20)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("provider_message_id", sa.String(100)),
        sa.Column("error", sa.String(500)),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        *audit_columns(),
        sa.UniqueConstraint("invoice_id", "rule_id", name="uq_messages_invoice_id_rule_id"),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["student_id"], ["students.id"], name="fk_messages_student_id_students"
            ),
            sa.ForeignKeyConstraint(
                ["invoice_id"], ["invoices.id"], name="fk_messages_invoice_id_invoices"
            ),
            sa.ForeignKeyConstraint(
                ["rule_id"], ["schedule_rules.id"], name="fk_messages_rule_id_schedule_rules"
            ),
        ),
    )
    op.create_index("ix_messages_status_scheduled_date", "messages", ["status", "scheduled_date"])
    op.create_index("ix_messages_student_id_sent_at", "messages", ["student_id", "sent_at"])
    op.create_index("ix_messages_provider_message_id", "messages", ["provider_message_id"])


def downgrade() -> None:
    op.drop_index("ix_messages_provider_message_id", table_name="messages")
    op.drop_index("ix_messages_student_id_sent_at", table_name="messages")
    op.drop_index("ix_messages_status_scheduled_date", table_name="messages")
    op.drop_table("messages")
