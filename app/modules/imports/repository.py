from collections.abc import Sequence

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import undefer

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository
from app.shared.utils import like_pattern

from .constants import ImportStatus
from .model import ImportBatch, ImportRowError


class ImportBatchRepository(BaseRepository[ImportBatch]):
    model = ImportBatch
    sort_fields = {
        "created_at": ImportBatch.created_at,
        "file_name": ImportBatch.file_name,
        "status": ImportBatch.status,
    }
    default_sort = "created_at"

    async def search(self, params: ListParams) -> tuple[Sequence[ImportBatch], int]:
        stmt = self.select()
        if params.search:
            stmt = stmt.where(ImportBatch.file_name.ilike(like_pattern(params.search), escape="\\"))
        return await self.paginate(stmt, params)

    async def get_with_rows(self, batch_id: int) -> ImportBatch | None:
        """The batch including its stored rows, refreshed from the database."""
        return await self.db.scalar(
            self.select()
            .where(ImportBatch.id == batch_id)
            .options(undefer(ImportBatch.rows))
            .execution_options(populate_existing=True)
        )

    async def claim_preview(self, batch_id: int, values: dict[str, object]) -> bool:
        """Atomically move a batch from ``preview`` to another status; ``False`` if it is not.

        ``UPDATE ... WHERE status = 'preview'`` lets exactly one of several concurrent callers win
        (the others see zero rows changed), and the row lock it takes is held until commit.
        """
        result = await self.db.execute(
            update(ImportBatch)
            .where(
                ImportBatch.id == batch_id,
                ImportBatch.status == ImportStatus.PREVIEW.value,
                ImportBatch.deleted_at.is_(None),
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        assert isinstance(result, CursorResult)  # noqa: S101 - narrows the type for pyright
        return result.rowcount > 0


class ImportRowErrorRepository(BaseRepository[ImportRowError]):
    model = ImportRowError
    sort_fields = {"row_number": ImportRowError.row_number, "field": ImportRowError.field}
    default_sort = "row_number"

    async def search_for_batch(
        self, batch_id: int, params: ListParams
    ) -> tuple[Sequence[ImportRowError], int]:
        return await self.paginate(self.select().where(ImportRowError.batch_id == batch_id), params)
