"""create app_allowed_roles and menus_mapping (reference catalog)

Revision ID: 001_reference_catalog
Revises:
Create Date: 2026-08-18

Pola migrasi mengikuti training-backend-ecertificate (002, 004):
- id BIGINT AUTO_INCREMENT dimulai dari 1 (SingleStore ROWSTORE REFERENCE TABLE)
- Dukungan PostgreSQL/MySQL via op.create_table
- Seed data project di migration atau lewat API settings — jangan hardcode ID di controller
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001_reference_catalog"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Contoh seed — ganti iam_role_id / menu_id sesuai IAM project Anda.
EXAMPLE_APP_ROLES = (
    {
        "iam_role_id": 1001,
        "display_name": "Example Super Admin",
        "auth_slug": "example_super_admin",
        "role_type": "super_admin",
        "sort_order": 10,
    },
    {
        "iam_role_id": 1002,
        "display_name": "Example Admin",
        "auth_slug": "example_admin",
        "role_type": "admin",
        "sort_order": 20,
    },
)

EXAMPLE_MENU_MAPPINGS = (
    {"menu_id": 9001, "slug": "example"},
)


def _is_singlestore() -> bool:
    bind = op.get_bind()
    return bind.dialect.name in ("singlestoredb", "mysql")


def _create_app_allowed_roles_singlestore() -> None:
    op.execute(
        sa.text(
            """
            CREATE ROWSTORE REFERENCE TABLE IF NOT EXISTS app_allowed_roles (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                iam_role_id INT NOT NULL,
                display_name VARCHAR(255) NOT NULL,
                auth_slug VARCHAR(128) NULL,
                role_type VARCHAR(64) NOT NULL DEFAULT 'admin',
                sort_order INT NOT NULL DEFAULT 0,
                is_active TINYINT NOT NULL DEFAULT 1,
                UNIQUE KEY uq_app_allowed_roles_iam_role_id (iam_role_id)
            )
            """
        )
    )
    for row in EXAMPLE_APP_ROLES:
        op.execute(
            sa.text(
                """
                INSERT INTO app_allowed_roles
                    (iam_role_id, display_name, auth_slug, role_type, sort_order, is_active)
                VALUES
                    (:iam_role_id, :display_name, :auth_slug, :role_type, :sort_order, 1)
                ON DUPLICATE KEY UPDATE
                    display_name = VALUES(display_name),
                    auth_slug = VALUES(auth_slug),
                    role_type = VALUES(role_type),
                    sort_order = VALUES(sort_order),
                    is_active = 1
                """
            ).bindparams(**row)
        )


def _create_menus_mapping_singlestore() -> None:
    op.execute(
        sa.text(
            """
            CREATE ROWSTORE REFERENCE TABLE IF NOT EXISTS menus_mapping (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                menu_id INT NOT NULL,
                slug VARCHAR(255) NOT NULL,
                UNIQUE KEY uq_menus_mapping_menu_id (menu_id)
            )
            """
        )
    )
    for row in EXAMPLE_MENU_MAPPINGS:
        op.execute(
            sa.text(
                """
                INSERT INTO menus_mapping (menu_id, slug)
                VALUES (:menu_id, :slug)
                ON DUPLICATE KEY UPDATE slug = VALUES(slug)
                """
            ).bindparams(**row)
        )


def upgrade() -> None:
    if _is_singlestore():
        _create_app_allowed_roles_singlestore()
        _create_menus_mapping_singlestore()
        return

    op.create_table(
        "app_allowed_roles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("iam_role_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("auth_slug", sa.String(length=128), nullable=True),
        sa.Column("role_type", sa.String(length=64), server_default="admin", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.SmallInteger(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("iam_role_id", name="uq_app_allowed_roles_iam_role_id"),
    )
    op.bulk_insert(
        sa.table(
            "app_allowed_roles",
            sa.column("iam_role_id"),
            sa.column("display_name"),
            sa.column("auth_slug"),
            sa.column("role_type"),
            sa.column("sort_order"),
            sa.column("is_active"),
        ),
        [{**row, "is_active": 1} for row in EXAMPLE_APP_ROLES],
    )

    op.create_table(
        "menus_mapping",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("menu_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("menu_id", name="uq_menus_mapping_menu_id"),
    )
    op.bulk_insert(
        sa.table("menus_mapping", sa.column("menu_id"), sa.column("slug")),
        list(EXAMPLE_MENU_MAPPINGS),
    )


def downgrade() -> None:
    if _is_singlestore():
        op.execute(sa.text("DROP TABLE IF EXISTS menus_mapping"))
        op.execute(sa.text("DROP TABLE IF EXISTS app_allowed_roles"))
        return

    op.drop_table("menus_mapping")
    op.drop_table("app_allowed_roles")
