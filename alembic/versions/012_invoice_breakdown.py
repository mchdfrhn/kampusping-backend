"""invoice breakdown components on invoices

Revision ID: 012
Revises: 011
Create Date: 2026-11-18

Adds optional ``breakdown`` (list of component ``{"label", "amount"}`` dicts)
to ``invoices`` so SIAKAD fee details can be rendered in reminder messages.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("breakdown", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "breakdown")
