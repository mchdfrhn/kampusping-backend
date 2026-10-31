from collections.abc import Mapping, Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute

from app.core.constants import VALIDATION_ERROR
from app.core.exceptions import AppException
from app.db.base import AuditedModel
from app.shared.pagination import ListParams, SortOrder
from app.shared.utils import utc_now

ModelT = TypeVar("ModelT", bound=AuditedModel)


class BaseRepository(Generic[ModelT]):
    """Soft-delete aware data access. Subclasses declare ``model`` and ``sort_fields``.

    Repositories only ``flush``; the service owns the transaction and calls ``commit``.
    """

    model: type[ModelT]
    sort_fields: Mapping[str, InstrumentedAttribute[Any]] = {}
    default_sort = "created_at"

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    def select(self) -> Select[tuple[ModelT]]:
        """Base query that already excludes soft-deleted rows."""
        return select(self.model).where(self.model.deleted_at.is_(None))

    async def get(self, item_id: int) -> ModelT | None:
        return await self.db.scalar(self.select().where(self.model.id == item_id))

    async def add(self, instance: ModelT) -> ModelT:
        self.db.add(instance)
        await self.db.flush()
        return instance

    async def soft_delete(self, instance: ModelT, deleted_by: str) -> None:
        instance.deleted_at = utc_now()
        instance.deleted_by = deleted_by
        await self.db.flush()

    async def paginate(
        self, stmt: Select[tuple[ModelT]], params: ListParams
    ) -> tuple[Sequence[ModelT], int]:
        total = await self.db.scalar(
            select(func.count()).select_from(stmt.order_by(None).subquery())
        )

        sort_name = params.sort_by or self.default_sort
        column = self.sort_fields.get(sort_name)
        if column is None:
            allowed = ", ".join(sorted(self.sort_fields))
            raise AppException(
                f"Kolom pengurutan '{sort_name}' tidak didukung. Pilihan: {allowed}.",
                status_code=422,
                code=VALIDATION_ERROR,
            )
        ordering = column.asc() if params.sort_order is SortOrder.ASC else column.desc()

        result = await self.db.scalars(
            stmt.order_by(ordering, self.model.id.desc())
            .limit(params.page_size)
            .offset(params.offset)
        )
        return result.all(), total or 0
