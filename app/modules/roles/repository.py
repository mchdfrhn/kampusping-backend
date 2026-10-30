from collections.abc import Collection, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .model import Permission, Role


class RoleRepository(BaseRepository[Role]):
    model = Role
    sort_fields = {
        "name": Role.name,
        "created_at": Role.created_at,
        "updated_at": Role.updated_at,
    }

    async def search(self, params: ListParams) -> tuple[Sequence[Role], int]:
        stmt = self.select()
        if params.search:
            pattern = like_pattern(params.search)
            stmt = stmt.where(
                or_(
                    Role.name.ilike(pattern, escape="\\"),
                    Role.description.ilike(pattern, escape="\\"),
                )
            )
        return await self.paginate(stmt, params)

    async def get_by_name(self, name: str, *, exclude_id: int | None = None) -> Role | None:
        stmt = select(Role).where(func.lower(Role.name) == name.lower())
        if exclude_id is not None:
            stmt = stmt.where(Role.id != exclude_id)
        return await self.db.scalar(stmt)

    async def get_many(self, role_ids: Collection[int]) -> Sequence[Role]:
        if not role_ids:
            return []
        result = await self.db.scalars(self.select().where(Role.id.in_(set(role_ids))))
        return result.all()

    async def get_by_names(self, names: Collection[str]) -> Sequence[Role]:
        if not names:
            return []
        result = await self.db.scalars(self.select().where(Role.name.in_(set(names))))
        return result.all()


class PermissionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_all(self) -> Sequence[Permission]:
        result = await self.db.scalars(select(Permission).order_by(Permission.code))
        return result.all()

    async def get_by_codes(self, codes: Collection[str]) -> Sequence[Permission]:
        if not codes:
            return []
        result = await self.db.scalars(select(Permission).where(Permission.code.in_(set(codes))))
        return result.all()
