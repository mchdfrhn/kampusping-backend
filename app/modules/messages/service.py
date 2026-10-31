from collections.abc import Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CONFLICT, NOT_FOUND
from app.core.exceptions import AppException
from app.modules.audit.service import record_audit
from app.modules.users.model import User
from app.shared.clock import Clock
from app.shared.pagination import ListParams
from app.shared.utils import utc_now

from .constants import (
    AUDIT_ENTITY_MESSAGE,
    AUDIT_MESSAGE_CANCELLED,
    MSG_NOT_FOUND,
    MSG_ONLY_QUEUED_CANCELLABLE,
    MessageStatus,
)
from .log_repository import MessageLogRepository
from .model import Message
from .schema import ReceiptState


class MessageService:
    def __init__(self, db: AsyncSession, clock: Clock = utc_now) -> None:
        self.db = db
        self.clock = clock
        self.messages = MessageLogRepository(db)

    async def list(
        self,
        params: ListParams,
        *,
        status: MessageStatus | None = None,
        student_id: int | None = None,
        invoice_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[Sequence[Message], int]:
        return await self.messages.search(
            params,
            status=status,
            student_id=student_id,
            invoice_id=invoice_id,
            date_from=date_from,
            date_to=date_to,
        )

    async def get(self, message_id: int) -> Message:
        message = await self.messages.get(message_id)
        if message is None:
            raise AppException(MSG_NOT_FOUND, status_code=404, code=NOT_FOUND)
        return message

    async def cancel(self, message_id: int, actor: User) -> Message:
        """Cancel a queued message by hand. Anything else (already claimed, sent...) is a 409."""
        changed = await self.messages.cancel_if_queued(
            message_id, actor=actor.email, now=self.clock()
        )
        if not changed:
            # Nothing was updated: tell a missing message apart from one that is no longer queued.
            await self.get(message_id)
            raise AppException(MSG_ONLY_QUEUED_CANCELLABLE, status_code=409, code=CONFLICT)
        await record_audit(
            self.db,
            actor=actor.email,
            action=AUDIT_MESSAGE_CANCELLED,
            entity=AUDIT_ENTITY_MESSAGE,
            entity_id=message_id,
            detail={"message_id": message_id},
        )
        await self.db.commit()
        message = await self.messages.reload(message_id)
        assert message is not None  # noqa: S101 - it was just updated
        return message

    async def apply_receipt(self, provider_message_id: str, state: ReceiptState) -> None:
        """Apply a delivery receipt from the gateway; unknown ids and stale receipts are no-ops."""
        now = self.clock()
        if state == "delivered":
            await self.messages.mark_delivered(provider_message_id, now=now)
        elif state == "read":
            await self.messages.mark_read(provider_message_id, now=now)
        else:
            await self.messages.mark_failed(provider_message_id, now=now)
        await self.db.commit()
