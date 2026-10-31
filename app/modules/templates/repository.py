from collections.abc import Sequence

from sqlalchemy import func

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .model import MessageTemplate


class TemplateRepository(BaseRepository[MessageTemplate]):
    model = MessageTemplate
    sort_fields = {
        "name": MessageTemplate.name,
        "is_active": MessageTemplate.is_active,
        "created_at": MessageTemplate.created_at,
        "updated_at": MessageTemplate.updated_at,
    }
    default_sort = "name"

    async def search(
        self, params: ListParams, *, is_active: bool | None = None
    ) -> tuple[Sequence[MessageTemplate], int]:
        stmt = self.select()
        if params.search:
            stmt = stmt.where(MessageTemplate.name.ilike(like_pattern(params.search), escape="\\"))
        if is_active is not None:
            stmt = stmt.where(MessageTemplate.is_active.is_(is_active))
        return await self.paginate(stmt, params)

    async def get_by_name(
        self, name: str, *, exclude_id: int | None = None
    ) -> MessageTemplate | None:
        stmt = self.select().where(func.lower(MessageTemplate.name) == name.lower())
        if exclude_id is not None:
            stmt = stmt.where(MessageTemplate.id != exclude_id)
        return await self.db.scalar(stmt.limit(1))
