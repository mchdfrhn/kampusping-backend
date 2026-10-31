from collections.abc import Sequence

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .model import ScheduleRule


class ScheduleRuleRepository(BaseRepository[ScheduleRule]):
    model = ScheduleRule
    sort_fields = {
        "offset_days": ScheduleRule.offset_days,
        "name": ScheduleRule.name,
        "is_active": ScheduleRule.is_active,
        "created_at": ScheduleRule.created_at,
        "updated_at": ScheduleRule.updated_at,
    }
    default_sort = "offset_days"

    async def search(
        self, params: ListParams, *, is_active: bool | None = None
    ) -> tuple[Sequence[ScheduleRule], int]:
        stmt = self.select()
        if params.search:
            stmt = stmt.where(ScheduleRule.name.ilike(like_pattern(params.search), escape="\\"))
        if is_active is not None:
            stmt = stmt.where(ScheduleRule.is_active.is_(is_active))
        return await self.paginate(stmt, params)

    async def get_by_offset(
        self, offset_days: int, *, exclude_id: int | None = None
    ) -> ScheduleRule | None:
        stmt = self.select().where(ScheduleRule.offset_days == offset_days)
        if exclude_id is not None:
            stmt = stmt.where(ScheduleRule.id != exclude_id)
        return await self.db.scalar(stmt.limit(1))

    async def exists_for_template(self, template_id: int) -> bool:
        """Whether any non-deleted rule (active or not) points at the template."""
        stmt = self.select().where(ScheduleRule.template_id == template_id)
        return await self.db.scalar(stmt.limit(1)) is not None
