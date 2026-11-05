"""report permissions for the existing system roles

Revision ID: 007
Revises: 006
Create Date: 2026-11-05

The seed only syncs permissions into `admin`; the other system roles are created once, so a
database that already has them would never gain the report permissions. This migration creates the
two permission rows and links them to `staf_keuangan` and `pimpinan` once, without touching any
other role. A role edited in the UI afterwards keeps its edits (the seed does not re-grant them).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with app/core/permissions.py on purpose: a migration must not change when the
# application code does.
REPORT_PERMISSIONS = (
    ("reports:read", "Melihat laporan penagihan"),
    ("reports:export", "Mengunduh laporan penagihan (Excel/PDF)"),
)
GRANTED_ROLES = ("staf_keuangan", "pimpinan")

permissions = sa.table(
    "permissions",
    sa.column("id", sa.BigInteger),
    sa.column("code", sa.String),
    sa.column("description", sa.String),
)
roles = sa.table(
    "roles",
    sa.column("id", sa.BigInteger),
    sa.column("name", sa.String),
    sa.column("deleted_at", sa.DateTime),
)
role_permissions = sa.table(
    "role_permissions",
    sa.column("role_id", sa.BigInteger),
    sa.column("permission_id", sa.BigInteger),
)


def _report_permission_ids(connection: sa.Connection) -> dict[str, int]:
    rows = connection.execute(
        sa.select(permissions.c.code, permissions.c.id).where(
            permissions.c.code.in_([code for code, _ in REPORT_PERMISSIONS])
        )
    )
    return {code: permission_id for code, permission_id in rows}


def grant_reports_permissions(connection: sa.Connection) -> None:
    existing = _report_permission_ids(connection)
    for code, description in REPORT_PERMISSIONS:
        if code not in existing:
            connection.execute(sa.insert(permissions).values(code=code, description=description))
    ids = _report_permission_ids(connection)

    for role_name in GRANTED_ROLES:
        role_id = connection.scalar(
            sa.select(roles.c.id).where(roles.c.name == role_name, roles.c.deleted_at.is_(None))
        )
        if role_id is None:
            continue
        linked = set(
            connection.scalars(
                sa.select(role_permissions.c.permission_id).where(
                    role_permissions.c.role_id == role_id
                )
            )
        )
        for permission_id in ids.values():
            if permission_id not in linked:
                connection.execute(
                    sa.insert(role_permissions).values(role_id=role_id, permission_id=permission_id)
                )


def revoke_reports_permissions(connection: sa.Connection) -> None:
    ids = list(_report_permission_ids(connection).values())
    if not ids:
        return
    connection.execute(sa.delete(role_permissions).where(role_permissions.c.permission_id.in_(ids)))
    connection.execute(sa.delete(permissions).where(permissions.c.id.in_(ids)))


def upgrade() -> None:
    grant_reports_permissions(op.get_bind())


def downgrade() -> None:
    revoke_reports_permissions(op.get_bind())
