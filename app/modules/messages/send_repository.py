"""Queries behind the send phase (see ``sender.py``). Everything here only flushes; the caller
commits.

Every ``UPDATE`` sets ``updated_at`` explicitly so the injected clock is honoured (the column's
``onupdate`` would otherwise stamp the real time).
"""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.invoices.model import Invoice
from app.modules.settings.constants import SETTINGS_ROW_ID
from app.modules.settings.model import AppSettings
from app.modules.students.constants import PhoneStatus
from app.modules.students.model import Student

from .constants import (
    CIRCUIT_BREAKER_REASON,
    SENT_STATUSES,
    STALE_SENDING_MINUTES,
    SYSTEM_ACTOR,
    MessageKind,
    MessageStatus,
)
from .model import Message


def _rowcount(result: Any) -> int:
    assert isinstance(result, CursorResult)  # noqa: S101 - narrows the type for pyright
    return max(result.rowcount, 0)


def auto_message_busy(
    student_id: Any, message_id: Any, *, day_start: datetime, day_end: datetime
) -> ColumnElement[bool]:
    """True when the student already has another automatic message today (AB-03).

    "Today" is the local-day interval ``[day_start, day_end)`` in UTC. A message that is being
    sent right now (``sending``) also counts. This check is only safe with a single worker: a
    second worker is unsupported (ADR 0003), because two of them could both pass the check
    before either records its message.
    """
    other = aliased(Message)
    return exists().where(
        other.student_id == student_id,
        other.id != message_id,
        other.kind == MessageKind.AUTO.value,
        or_(
            other.status == MessageStatus.SENDING.value,
            and_(
                other.status.in_(SENT_STATUSES),
                other.sent_at >= day_start,
                other.sent_at < day_end,
            ),
        ),
    )


class SendRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def count_sent_between(self, start: datetime, end: datetime) -> int:
        """Messages handed to the provider in ``[start, end)``: the daily cap counter.

        A message in ``sending`` counts whatever its ``sent_at`` (that is only written when the
        outcome is recorded), like the AB-03 predicate; the other statuses count by ``sent_at``.
        """
        count = await self.db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                or_(
                    Message.status == MessageStatus.SENDING.value,
                    and_(
                        Message.status.in_(SENT_STATUSES),
                        Message.sent_at >= start,
                        Message.sent_at < end,
                    ),
                )
            )
        )
        return count or 0

    async def next_candidate(
        self, *, now: datetime, today: date, day_start: datetime, day_end: datetime
    ) -> int | None:
        """The id of the next sendable message, or None.

        Queued, due (``scheduled_date`` not in the future, back-off over), oldest scheduled date
        first, then the invoice whose due date is oldest, then the id. Automatic messages of a
        student who already got one today wait for the next working day (AB-03); they stay
        queued. On PostgreSQL the row is locked with ``SKIP LOCKED`` so parallel workers pick
        different messages.
        """
        stmt = (
            select(Message.id)
            .join(Invoice, Invoice.id == Message.invoice_id)
            .where(
                Message.status == MessageStatus.QUEUED.value,
                Message.deleted_at.is_(None),
                Message.scheduled_date <= today,
                or_(Message.next_attempt_at.is_(None), Message.next_attempt_at <= now),
                or_(
                    Message.kind != MessageKind.AUTO.value,
                    ~auto_message_busy(
                        Message.student_id, Message.id, day_start=day_start, day_end=day_end
                    ),
                ),
            )
            .order_by(Message.scheduled_date, Invoice.due_date, Message.id)
            .limit(1)
        )
        if self.db.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True, of=Message)
        return await self.db.scalar(stmt)

    async def claim(self, message_id: int, *, now: datetime) -> bool:
        """``queued -> sending`` for exactly this message; False when someone else won.

        A soft-deleted message is never claimed.
        """
        result = await self.db.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status == MessageStatus.QUEUED.value,
                Message.deleted_at.is_(None),
            )
            .values(status=MessageStatus.SENDING.value, updated_at=now, updated_by=SYSTEM_ACTOR)
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result) == 1

    async def student_has_auto_message_today(
        self, student_id: int, message_id: int, *, day_start: datetime, day_end: datetime
    ) -> bool:
        busy = auto_message_busy(student_id, message_id, day_start=day_start, day_end=day_end)
        return bool(await self.db.scalar(select(busy)))

    async def update_message(self, message_id: int, *, now: datetime, **values: Any) -> None:
        await self.db.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(updated_at=now, updated_by=SYSTEM_ACTOR, **values)
            .execution_options(synchronize_session=False)
        )

    async def record_outcome(self, message_id: int, *, now: datetime, **values: Any) -> bool:
        """Write the outcome of a provider call, only while the message is still ``sending``.

        Returns False (and writes nothing) when someone else changed the message in the
        meantime, for example staff cancelled it while the provider call was in flight.
        """
        result = await self.db.execute(
            update(Message)
            .where(Message.id == message_id, Message.status == MessageStatus.SENDING.value)
            .values(updated_at=now, updated_by=SYSTEM_ACTOR, **values)
            .execution_options(synchronize_session=False)
        )
        return _rowcount(result) == 1

    async def fail_unprocessed(self, message_id: int, *, now: datetime, error: str) -> None:
        """Mark a message that hit an internal error ``failed`` unless it already moved on."""
        await self.db.execute(
            update(Message)
            .where(
                Message.id == message_id,
                Message.status.in_([MessageStatus.QUEUED.value, MessageStatus.SENDING.value]),
            )
            .values(
                status=MessageStatus.FAILED.value,
                error=error,
                next_attempt_at=None,
                updated_at=now,
                updated_by=SYSTEM_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )

    async def mark_phone_invalid(self, student_id: int, *, now: datetime) -> None:
        await self.db.execute(
            update(Student)
            .where(Student.id == student_id)
            .values(phone_status=PhoneStatus.INVALID.value, updated_at=now, updated_by=SYSTEM_ACTOR)
            .execution_options(synchronize_session=False)
        )

    async def record_failure(self, *, now: datetime, limit: int) -> bool:
        """Count one failure towards the circuit breaker; True when it trips (or has tripped).

        The counter is bumped in SQL so concurrent workers never lose an increment. Bumping it
        leaves ``updated_at`` alone (that column shows the last staff edit); tripping the breaker
        is a real state change and is stamped.
        """
        row = AppSettings.id == SETTINGS_ROW_ID
        await self.db.execute(
            update(AppSettings)
            .where(row)
            .values(
                consecutive_failures=AppSettings.consecutive_failures + 1,
                updated_at=AppSettings.updated_at,
            )
            .execution_options(synchronize_session=False)
        )
        failures = await self.db.scalar(select(AppSettings.consecutive_failures).where(row))
        if failures is None or failures < limit:
            return False
        await self.db.execute(
            update(AppSettings)
            .where(row, AppSettings.paused_reason.is_(None))
            .values(paused_reason=CIRCUIT_BREAKER_REASON, updated_at=now, updated_by=SYSTEM_ACTOR)
            .execution_options(synchronize_session=False)
        )
        return True

    async def reset_failures(self) -> None:
        await self.db.execute(
            update(AppSettings)
            .where(AppSettings.id == SETTINGS_ROW_ID, AppSettings.consecutive_failures != 0)
            .values(consecutive_failures=0, updated_at=AppSettings.updated_at)
            .execution_options(synchronize_session=False)
        )

    async def recover_stale(self, *, now: datetime) -> tuple[int, int]:
        """Messages left in ``sending`` by a worker that died: returns ``(sent, requeued)``.

        With a ``provider_message_id`` the provider accepted the message, so it counts as sent
        (at the time of its last update); without one it goes back to the queue.
        """
        stale = (
            Message.status == MessageStatus.SENDING.value,
            Message.updated_at < now - timedelta(minutes=STALE_SENDING_MINUTES),
        )
        sent = await self.db.execute(
            update(Message)
            .where(*stale, Message.provider_message_id.is_not(None))
            .values(
                status=MessageStatus.SENT.value,
                sent_at=func.coalesce(Message.sent_at, Message.updated_at),
                updated_at=now,
                updated_by=SYSTEM_ACTOR,
            )
            .execution_options(synchronize_session=False)
        )
        requeued = await self.db.execute(
            update(Message)
            .where(*stale, Message.provider_message_id.is_(None))
            .values(status=MessageStatus.QUEUED.value, updated_at=now, updated_by=SYSTEM_ACTOR)
            .execution_options(synchronize_session=False)
        )
        return _rowcount(sent), _rowcount(requeued)
