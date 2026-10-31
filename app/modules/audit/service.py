from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import VALIDATION_ERROR
from app.core.exceptions import AppException
from app.shared.pagination import ListParams, SortOrder

from .model import AuditEvent

SORT_FIELDS = {"at": AuditEvent.at}
DEFAULT_SORT = "at"


async def record_audit(
    db: AsyncSession,
    *,
    actor: str,
    action: str,
    entity: str,
    entity_id: int | str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the session and flush. The caller owns the commit."""
    db.add(
        AuditEvent(
            actor=actor,
            action=action,
            entity=entity,
            entity_id=None if entity_id is None else str(entity_id),
            detail=detail,
        )
    )
    await db.flush()


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self, params: ListParams, *, action: str | None = None, entity: str | None = None
    ) -> tuple[Sequence[AuditEvent], int]:
        stmt = select(AuditEvent)
        if action:
            stmt = stmt.where(AuditEvent.action == action)
        if entity:
            stmt = stmt.where(AuditEvent.entity == entity)

        sort_name = params.sort_by or DEFAULT_SORT
        column = SORT_FIELDS.get(sort_name)
        if column is None:
            raise AppException(
                f"Kolom pengurutan '{sort_name}' tidak didukung. "
                f"Pilihan: {', '.join(sorted(SORT_FIELDS))}.",
                status_code=422,
                code=VALIDATION_ERROR,
            )
        descending = params.sort_order is SortOrder.DESC
        # `id` breaks ties between events recorded in the same instant.
        ordering = (
            (column.desc(), AuditEvent.id.desc())
            if descending
            else (column.asc(), AuditEvent.id.asc())
        )

        total = await self.db.scalar(select(func.count()).select_from(stmt.subquery()))
        result = await self.db.scalars(
            stmt.order_by(*ordering).limit(params.page_size).offset(params.offset)
        )
        return result.all(), total or 0
