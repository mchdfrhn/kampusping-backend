"""auth and rbac: users, roles, permissions, refresh tokens

Revision ID: 001
Revises:
Create Date: 2026-10-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.migration_helpers import BIGINT_PK, audit_columns, foreign_keys, id_column

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        id_column(),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.UniqueConstraint("code", name="uq_permissions_code"),
    )

    op.create_table(
        "roles",
        id_column(),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        *audit_columns(),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "users",
        id_column(),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("password_changed_at", sa.DateTime(timezone=True)),
        *audit_columns(),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "role_permissions",
        sa.Column("role_id", BIGINT_PK, nullable=False),
        sa.Column("permission_id", BIGINT_PK, nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_id", name="pk_role_permissions"),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["role_id"], ["roles.id"], name="fk_role_permissions_role_id_roles", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["permission_id"],
                ["permissions.id"],
                name="fk_role_permissions_permission_id_permissions",
                ondelete="CASCADE",
            ),
        ),
    )

    op.create_table(
        "user_roles",
        sa.Column("user_id", BIGINT_PK, nullable=False),
        sa.Column("role_id", BIGINT_PK, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], name="fk_user_roles_user_id_users", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["role_id"], ["roles.id"], name="fk_user_roles_role_id_roles", ondelete="CASCADE"
            ),
        ),
    )

    op.create_table(
        "refresh_tokens",
        id_column(),
        sa.Column("user_id", BIGINT_PK, nullable=False),
        sa.Column("family_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("user_agent", sa.String(255)),
        sa.Column("ip_address", sa.String(45)),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        *foreign_keys(
            sa.ForeignKeyConstraint(
                ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id_users", ondelete="CASCADE"
            ),
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_table("permissions")
