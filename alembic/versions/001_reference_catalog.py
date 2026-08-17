"""create app_allowed_roles and menus_mapping

Revision ID: 001_reference_catalog
Revises:
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001_reference_catalog"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_allowed_roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("iam_role_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("auth_slug", sa.String(length=128), nullable=True),
        sa.Column("role_type", sa.String(length=64), nullable=False, server_default="admin"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("iam_role_id", name="uq_app_allowed_roles_iam_role_id"),
    )
    op.create_table(
        "menus_mapping",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("menu_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("menu_id", name="uq_menus_mapping_menu_id"),
    )


def downgrade() -> None:
    op.drop_table("menus_mapping")
    op.drop_table("app_allowed_roles")
