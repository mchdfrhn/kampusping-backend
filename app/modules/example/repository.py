from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Select, or_, select

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .model import ExampleItem


@dataclass(frozen=True, slots=True)
class ExampleFilters:
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class ExampleRepository(BaseRepository[ExampleItem]):
    model = ExampleItem
    sort_fields = {
        "code": ExampleItem.code,
        "name": ExampleItem.name,
        "status": ExampleItem.status,
        "created_at": ExampleItem.created_at,
        "updated_at": ExampleItem.updated_at,
    }
    default_sort = "updated_at"

    def _filtered(self, params: ListParams, filters: ExampleFilters) -> Select[tuple[ExampleItem]]:
        stmt = self.select()
        if params.search:
            pattern = like_pattern(params.search)
            stmt = stmt.where(
                or_(
                    ExampleItem.code.ilike(pattern, escape="\\"),
                    ExampleItem.name.ilike(pattern, escape="\\"),
                )
            )
        if filters.status:
            stmt = stmt.where(ExampleItem.status == filters.status)
        if filters.date_from:
            start = datetime.combine(filters.date_from, time.min, tzinfo=UTC)
            stmt = stmt.where(ExampleItem.updated_at >= start)
        if filters.date_to:  # inclusive of the whole end day
            end = datetime.combine(filters.date_to + timedelta(days=1), time.min, tzinfo=UTC)
            stmt = stmt.where(ExampleItem.updated_at < end)
        return stmt

    async def search(
        self, params: ListParams, filters: ExampleFilters
    ) -> tuple[Sequence[ExampleItem], int]:
        return await self.paginate(self._filtered(params, filters), params)

    async def list_for_export(
        self, params: ListParams, filters: ExampleFilters, limit: int
    ) -> Sequence[ExampleItem]:
        stmt = self._filtered(params, filters).order_by(ExampleItem.updated_at.desc()).limit(limit)
        return (await self.db.scalars(stmt)).all()

    async def get_by_code(self, code: str, *, exclude_id: int | None = None) -> ExampleItem | None:
        """Includes soft-deleted rows because the unique constraint does too."""
        stmt = select(ExampleItem).where(ExampleItem.code == code)
        if exclude_id is not None:
            stmt = stmt.where(ExampleItem.id != exclude_id)
        return await self.db.scalar(stmt)
