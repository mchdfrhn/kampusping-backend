from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import ClockDep, DbSession, require_permissions
from app.core.permissions import MESSAGES_CANCEL, MESSAGES_READ, MESSAGES_SEND
from app.modules.users.model import User
from app.shared.pagination import ListQuery, Page

from .constants import MessageStatus
from .manual_service import ManualMessageService
from .schema import (
    ManualFilterOptionsRead,
    ManualMessagePreviewRead,
    ManualMessagePreviewRequest,
    ManualMessageSendRead,
    ManualMessageSendRequest,
    MessageRead,
)
from .service import MessageService

router = APIRouter(prefix="/messages", tags=["Messages"])

CanRead = Annotated[User, Depends(require_permissions(MESSAGES_READ))]
CanSend = Annotated[User, Depends(require_permissions(MESSAGES_SEND))]
CanCancel = Annotated[User, Depends(require_permissions(MESSAGES_CANCEL))]


@router.get("", response_model=Page[MessageRead], summary="List messages (the send log)")
async def list_messages(
    db: DbSession,
    _: CanRead,
    params: ListQuery,
    status: MessageStatus | None = None,
    student_id: Annotated[int | None, Query(ge=1)] = None,
    invoice_id: Annotated[int | None, Query(ge=1)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[MessageRead]:
    rows, total = await MessageService(db).list(
        params,
        status=status,
        student_id=student_id,
        invoice_id=invoice_id,
        date_from=date_from,
        date_to=date_to,
    )
    return Page(
        items=[MessageRead.from_message(row) for row in rows],
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get(
    "/manual/options",
    response_model=ManualFilterOptionsRead,
    summary="Get filter options for manual sending",
)
async def get_manual_options(db: DbSession, _: CanSend) -> ManualFilterOptionsRead:
    return await ManualMessageService(db).get_filter_options()


@router.post(
    "/manual/preview",
    response_model=ManualMessagePreviewRead,
    summary="Preview recipients and exclusions for manual message sending",
)
async def preview_manual_messages(
    payload: ManualMessagePreviewRequest, db: DbSession, _: CanSend, clock: ClockDep
) -> ManualMessagePreviewRead:
    return await ManualMessageService(db, clock).preview(payload)


@router.post(
    "/manual/send",
    response_model=ManualMessageSendRead,
    summary="Queue manual messages for eligible recipients",
)
async def send_manual_messages(
    payload: ManualMessageSendRequest, db: DbSession, actor: CanSend, clock: ClockDep
) -> ManualMessageSendRead:
    return await ManualMessageService(db, clock).send(payload, actor)


@router.get("/{message_id}", response_model=MessageRead, summary="Get one message")
async def get_message(message_id: int, db: DbSession, _: CanRead) -> MessageRead:
    return MessageRead.from_message(await MessageService(db).get(message_id))


@router.post("/{message_id}/cancel", response_model=MessageRead, summary="Cancel a queued message")
async def cancel_message(
    message_id: int, db: DbSession, actor: CanCancel, clock: ClockDep
) -> MessageRead:
    return MessageRead.from_message(await MessageService(db, clock).cancel(message_id, actor))
