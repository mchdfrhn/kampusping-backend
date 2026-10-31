"""students and invoices

Revision ID: 003
Revises: 002
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import audit_columns, foreign_keys, id_column

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "students",
        id_column(),
        sa.Column("nim", sa.String(30), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("study_program", sa.String(150)),
        sa.Column("cohort", sa.String(20)),
        sa.Column("phone", sa.String(20)),
        sa.Column("phone_status", sa.String(10), nullable=False, server_default="missing"),
        sa.Column("opted_out_at", sa.DateTime(timezone=True)),
        sa.Column("opted_out_reason", sa.String(500)),
        *audit_columns(),
        sa.UniqueConstraint("nim", name="uq_students_nim"),
    )
    op.create_index("ix_students_phone_status", "students", ["phone_status"])

    op.create_table(
        "invoices",
        id_column(),
        sa.Column("invoice_code", sa.String(50), nullable=False),
        sa.Column("student_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("semester", sa.String(50), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="unpaid"),
        sa.Column("paid_at", sa.Date()),
        sa.Column("paid_via", sa.String(10)),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("exempt_reason", sa.String(500)),
        sa.Column("exempt_until", sa.Date()),
        sa.Column("escalated_at", sa.DateTime(timezone=True)),
        *audit_columns(),
        sa.UniqueConstraint("invoice_code", name="uq_invoices_invoice_code"),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["student_id"], ["students.id"], name="fk_invoices_student_id_students"
            )
        ),
    )
    op.create_index("ix_invoices_student_id", "invoices", ["student_id"])
    op.create_index("ix_invoices_status_due_date", "invoices", ["status", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_invoices_status_due_date", table_name="invoices")
    op.drop_index("ix_invoices_student_id", table_name="invoices")
    op.drop_table("invoices")
    op.drop_index("ix_students_phone_status", table_name="students")
    op.drop_table("students")
