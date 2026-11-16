"""worker heartbeat timestamp on app_settings

Revision ID: 010
Revises: 009
Create Date: 2026-11-16

Adds ``worker_last_seen_at`` to ``app_settings`` so the API and UI can report
whether ``kampusping-worker`` is alive and ticking.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("worker_last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("app_settings", "worker_last_seen_at")
