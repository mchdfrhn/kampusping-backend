from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND, VALIDATION_ERROR
from app.core.exceptions import AppException
from app.core.permissions import ADMIN_ROLE
from app.modules.users.model import User, user_roles
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .model import Permission, Role
from .repository import PermissionRepository, RoleRepository
from .schema import RoleCreate, RoleUpdate


class RoleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.roles = RoleRepository(db)
        self.permissions = PermissionRepository(db)

    async def list(self, params: ListParams) -> tuple[Sequence[Role], int]:
        return await self.roles.search(params)

    async def list_permissions(self) -> Sequence[Permission]:
        return await self.permissions.list_all()

    async def get(self, role_id: int) -> Role:
        role = await self.roles.get(role_id)
        if role is None:
            raise AppException("Peran tidak ditemukan.", status_code=404, code=NOT_FOUND)
        return role

    async def create(self, data: RoleCreate, actor: User) -> Role:
        await self._ensure_name_available(data.name)
        role = Role(
            name=data.name,
            description=data.description,
            is_system=False,
            permissions=list(await self._resolve_permissions(data.permissions)),
            created_by=actor.email,
            updated_by=actor.email,
        )
        await self.roles.add(role)
        await self.db.commit()
        return role

    async def update(self, role_id: int, data: RoleUpdate, actor: User) -> Role:
        role = await self.get(role_id)
        if role.is_system:
            if data.name != role.name:
                raise AppException(
                    "Peran sistem tidak dapat diganti namanya.", status_code=409, code=CONFLICT
                )
            if role.name == ADMIN_ROLE:
                raise AppException(
                    "Peran admin selalu memiliki seluruh izin.", status_code=409, code=CONFLICT
                )
        elif data.name != role.name:
            await self._ensure_name_available(data.name, exclude_id=role.id)

        role.name = data.name
        role.description = data.description
        role.permissions = list(await self._resolve_permissions(data.permissions))
        role.updated_by = actor.email
        role.updated_at = utc_now()  # a permissions-only change does not touch any column
        await self.db.commit()
        return role

    async def delete(self, role_id: int, actor: User) -> None:
        role = await self.get(role_id)
        if role.is_system:
            raise AppException("Peran sistem tidak dapat dihapus.", status_code=409, code=CONFLICT)
        in_use = await self.db.scalar(
            select(func.count())
            .select_from(user_roles)
            .join(User, User.id == user_roles.c.user_id)
            .where(user_roles.c.role_id == role.id, User.deleted_at.is_(None))
        )
        if in_use:
            raise AppException(
                f"Peran masih digunakan oleh {in_use} pengguna. "
                "Pindahkan penggunanya terlebih dahulu.",
                status_code=409,
                code=CONFLICT,
            )
        await self.roles.soft_delete(role, actor.email)
        await self.db.commit()

    async def _ensure_name_available(self, name: str, *, exclude_id: int | None = None) -> None:
        if await self.roles.get_by_name(name, exclude_id=exclude_id):
            raise AppException("Peran dengan nama ini sudah ada.", status_code=409, code=CONFLICT)

    async def _resolve_permissions(self, codes: Sequence[str]) -> Sequence[Permission]:
        unique_codes = set(codes)
        found = await self.permissions.get_by_codes(unique_codes)
        unknown = unique_codes - {permission.code for permission in found}
        if unknown:
            raise AppException(
                "Kode izin tidak dikenal: " + ", ".join(sorted(unknown)),
                status_code=422,
                code=VALIDATION_ERROR,
            )
        return found
