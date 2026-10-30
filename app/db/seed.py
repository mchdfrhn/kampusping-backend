"""Idempotent reference data: permissions and system roles. Safe to run on every deploy."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import ADMIN_ROLE, PERMISSIONS, SYSTEM_ROLES
from app.modules.roles.model import Permission, Role


async def seed_reference_data(db: AsyncSession) -> None:
    existing = {row.code: row for row in (await db.scalars(select(Permission))).all()}
    for definition in PERMISSIONS:
        row = existing.get(definition.code)
        if row is None:
            row = Permission(code=definition.code, description=definition.description)
            db.add(row)
            existing[definition.code] = row
        elif row.description != definition.description:
            row.description = definition.description
    await db.flush()

    for name, role_def in SYSTEM_ROLES.items():
        role = await db.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(
                name=name,
                description=role_def.description,
                is_system=True,
                permissions=[existing[code] for code in role_def.permissions],
                created_by="system",
                updated_by="system",
            )
            db.add(role)
        elif name == ADMIN_ROLE:
            # `admin` must always be able to do everything, including features added later.
            role.permissions = [existing[code] for code in role_def.permissions]
        role.is_system = True
    await db.commit()
