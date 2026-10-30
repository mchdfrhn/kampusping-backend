import csv
import io
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND
from app.core.exceptions import AppException
from app.modules.users.model import User
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import EXPORT_ROW_LIMIT
from .model import ExampleItem
from .repository import ExampleFilters, ExampleRepository
from .schema import ExampleWrite

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    """Neutralise spreadsheet formula injection in exported cells."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


class ExampleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.items = ExampleRepository(db)

    async def list(
        self, params: ListParams, filters: ExampleFilters
    ) -> tuple[Sequence[ExampleItem], int]:
        return await self.items.search(params, filters)

    async def get(self, item_id: int) -> ExampleItem:
        item = await self.items.get(item_id)
        if item is None:
            raise AppException("Item not found.", status_code=404, code=NOT_FOUND)
        return item

    async def create(self, data: ExampleWrite, actor: User) -> ExampleItem:
        await self._ensure_code_available(data.code)
        item = ExampleItem(
            code=data.code,
            name=data.name,
            status=data.status,
            created_by=actor.email,
            updated_by=actor.email,
        )
        await self.items.add(item)
        await self.db.commit()
        return item

    async def update(self, item_id: int, data: ExampleWrite, actor: User) -> ExampleItem:
        item = await self.get(item_id)
        if data.code != item.code:
            await self._ensure_code_available(data.code, exclude_id=item.id)
        item.code = data.code
        item.name = data.name
        item.status = data.status
        item.updated_by = actor.email
        item.updated_at = utc_now()
        await self.db.commit()
        return item

    async def delete(self, item_id: int, actor: User) -> None:
        item = await self.get(item_id)
        await self.items.soft_delete(item, actor.email)
        await self.db.commit()

    async def export_csv(self, params: ListParams, filters: ExampleFilters) -> str:
        rows = await self.items.list_for_export(params, filters, EXPORT_ROW_LIMIT)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["ID", "Code", "Name", "Status", "Created at", "Updated at"])
        for row in rows:
            writer.writerow(
                [
                    row.id,
                    _csv_safe(row.code),
                    _csv_safe(row.name),
                    row.status,
                    row.created_at.isoformat(),
                    row.updated_at.isoformat(),
                ]
            )
        return buffer.getvalue()

    async def _ensure_code_available(self, code: str, *, exclude_id: int | None = None) -> None:
        if await self.items.get_by_code(code, exclude_id=exclude_id):
            raise AppException(
                "An item with this code already exists.", status_code=409, code=CONFLICT
            )
