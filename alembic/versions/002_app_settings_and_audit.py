"""app settings (single row) and the immutable audit log

Revision ID: 002
Revises: 001
Create Date: 2026-10-31
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
        "app_settings",
        id_column(),
        sa.Column("campus_name", sa.String(150), nullable=False, server_default=""),
        sa.Column("finance_contact", sa.String(255), nullable=False, server_default=""),
        sa.Column("payment_link_siakad", sa.String(500), nullable=False, server_default=""),
        sa.Column("payment_guide_sipema", sa.String(1000), nullable=False, server_default=""),
        sa.Column("window_start", sa.Time(), nullable=False, server_default="08:00:00"),
        sa.Column("window_end", sa.Time(), nullable=False, server_default="17:00:00"),
        sa.Column("working_days", sa.String(20), nullable=False, server_default="1,2,3,4,5"),
        sa.Column("auto_send_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_cap", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("throttle_min_s", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("throttle_max_s", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("consecutive_failure_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("escalation_offset_days", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("paused_reason", sa.String(500)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        *audit_columns(),
    )

    op.create_table(
        "audit_events",
        id_column(),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(100)),
        sa.Column("detail", sa.JSON()),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_entity", "audit_events", ["entity"])
    op.create_index("ix_audit_events_at", "audit_events", ["at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("app_settings")
