"""Queries behind the message log and the delivery receipts. Only flushes; the caller commits."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import selectinload

from app.shared.pagination import ListParams
from app.shared.repository import BaseRepository

from .constants import (
    ERROR_DELIVERY_FAILED,
    WEBHOOK_ACTOR,
    CancelReason,
    MessageStatus,
)
from .model import Message


def _rowcount(result: Any) -> int:
    assert isinstance(result, CursorResult)  # noqa: S101 - narrows the type for pyright
    return max(result.rowcount, 0)


class MessageLogRepository(BaseRepository[Message]):
    model = Message
    sort_fields = {
        "queued_at": Message.queued_at,
        "sent_at": Message.sent_at,
        "scheduled_date": Message.scheduled_date,
        "status": Message.status,
        "id": Message.id,
    }
    default_sort = "queued_at"

    def select(self) -> Select[tuple[Message]]:
        """Non-deleted messages with their student loaded in one extra query."""
        return super().select().options(selectinload(Message.student))

    async def reload(self, message_id: int) -> Message | None:
        """Re-read a message, refreshing an instance a bulk UPDATE changed behind the session."""
        return await self.db.scalar(
            self.select().where(Message.id == message_id).execution_options(populate_existing=True)
        )

    async def search(
        self,
        params: ListParams,
        *,
        status: MessageStatus | None = None,
        student_id: int | None = None,
        invoice_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[Sequence[Message], int]:
        stmt = self.select()
        if status:
            stmt = stmt.where(Message.status == status.value)
        if student_id is not None:
            stmt = stmt.where(Message.student_id == student_id)
        if invoice_id is not None:
            stmt = stmt.where(Message.invoice_id == invoice_id)
        if date_from:
            stmt = stmt.where(Message.scheduled_date >= date_from)
        if date_to:
            stmt = stmt.where(Message.scheduled_date <= date_to)
        return await self.paginate(stmt, params)

    async def cancel_if_queued(self, message_id: int, *, actor: str, now: datetime) -> int:
        """Cancel the message only while it is still ``queued``; returns the rows changed (0 or 1).

        One conditional UPDATE, never read-then-write: the sender claims queued messages
        concurrently, and a claimed (``sending``) message must not be cancelled.
        """
        result = await self.db.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == MessageStatus.QUEUED.value,
                Message.deleted_at.is_(None),
            )
            .values(
                status=MessageStatus.CANCELLED.value,
                cancel_reason=CancelReason.MANUAL.value,
                updated_at=now,
                updated_by=actor,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    # --- Delivery receipts (conditional UPDATEs, so a late or duplicate receipt never regresses) --

    async def mark_delivered(self, provider_message_id: str, *, now: datetime) -> int:
        result = await self.db.execute(
            update(Message)
            .where(*self._by_provider_id(provider_message_id, MessageStatus.SENT))
            .values(
                status=MessageStatus.DELIVERED.value,
                delivered_at=func.coalesce(Message.delivered_at, now),
                updated_at=now,
                updated_by=WEBHOOK_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    async def mark_read(self, provider_message_id: str, *, now: datetime) -> int:
        result = await self.db.execute(
            update(Message)
            .where(
                *self._by_provider_id(
                    provider_message_id, MessageStatus.SENT, MessageStatus.DELIVERED
                )
            )
            .values(
                status=MessageStatus.READ.value,
                read_at=func.coalesce(Message.read_at, now),
                delivered_at=func.coalesce(Message.delivered_at, now),
                updated_at=now,
                updated_by=WEBHOOK_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    async def mark_failed(self, provider_message_id: str, *, now: datetime) -> int:
        result = await self.db.execute(
            update(Message)
            .where(*self._by_provider_id(provider_message_id, MessageStatus.SENT))
            .values(
                status=MessageStatus.FAILED.value,
                error=ERROR_DELIVERY_FAILED,
                updated_at=now,
                updated_by=WEBHOOK_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result)

    @staticmethod
    def _by_provider_id(provider_message_id: str, *from_statuses: MessageStatus) -> list[Any]:
        return [
            Message.provider_message_id == provider_message_id,
            Message.deleted_at.is_(None),
            Message.status.in_([status.value for status in from_statuses]),
        ]
